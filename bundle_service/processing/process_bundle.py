import tempfile
import orjson
import subprocess
import os
import time
from typing import List
from gen3.auth import Gen3Auth, decode_token

from aced_submission.grip_load import bulk_load, bulk_delete
from aced_submission.meta_flat_load import DEFAULT_ELASTIC, load_flat
from aced_submission.fhir_store import fhir_put
from gen3_tracker.meta.dataframer import LocalFHIRDatabase


async def _is_valid_token(access_token: str) -> bool | str:
    """The FHIR server needs some way of checking if the token is valid before passing it to gen3Auth"""
    try:
        token = decode_token(access_token)
    except Exception as e:
        return False, str(e)

    # Return false if token has expired
    current_time = int(time.time())
    if not (current_time >= token['iat'] and current_time <= token['exp']):
        return False, "Token has expired"

    return True, None


async def _can_create(access_token: str, project_id: str) -> bool | str | int:
    """General resource path Gen3 permissions checking given a valid token"""

    valid_token, msg = await _is_valid_token(access_token)
    if not valid_token:
        return False, msg, 401

    auth = Gen3Auth(access_token=access_token)
    user = auth.curl('/user/user').json()
    program, project = project_id.split("-")

    required_resources = [
        f"/programs/{program}",
        f"/programs/{program}/projects"
    ]
    for required_resource in required_resources:
        if required_resource not in user['resources']:
            return False, f"{required_resource} not found in user resources", 403

    required_services = [
        f"/programs/{program}/projects/{project}"
    ]
    for required_service in required_services:
        if required_service not in user['authz']:
            return False, f"{required_service} not found in user authz", 403
        else:
            if {'method': 'create', 'service': '*'} not in user['authz'][required_service]:
                return False, f"create not found in user authz for {required_service}", 403

    return True, f"HAS SERVICE create on resource {required_service}", None


async def process(rows: List[dict], project_id: str, access_token: str) -> list[str]:
    """Processes a bundle into a temp directory of NDJSON files
    that are compatible with existing loading functions

    Currently Supports PUT and DELETE methods only

    TODO: write new loading functions that load from ram instead of writing
    and reading to disk"""

    server_errors = []
    temp_files = {}
    logs = {"logs": []}
    delete_body = {"graph": "CALIPER", "edges": [], "vertices": []}
    files_written = False
    with tempfile.TemporaryDirectory() as temp_dir:
        for row in rows:
            if row["request"]["method"] == "PUT":
                file_name = row["resource"]["resourceType"] + ".ndjson"
                if file_name not in temp_files:
                    temp_file_path = os.path.join(temp_dir, file_name)
                    temp_files[file_name] = open(temp_file_path, mode='ab+')
                temp_files[file_name].write(orjson.dumps(row["resource"], option=orjson.OPT_APPEND_NEWLINE))
                files_written = True
            elif row["request"]["method"] == "DELETE":
                delete_body["vertices"].append(row["request"]["url"].split("/")[1])

        if len(delete_body["edges"]) > 0 or len(delete_body["vertices"]) > 0:
            res = bulk_delete("CALIPER", project_id=project_id, vertices=delete_body["vertices"],
                              edges=delete_body["edges"], output=logs, access_token=access_token)
            if int(res["status"]) != 200:
                server_errors.append(res["message"])

            # TODO add elastic edge level deletion
            # take row ids, fetch records into RAM, for each index do a dataframe pivot,
            # delete resulting entries from each index depending on what the pivot produces

        for temp_file in temp_files.values():
            temp_file.close()

        if files_written:
            subprocess.run(["jsonschemagraph", "gen-dir", "iceberg/schemas/graph", f"{temp_dir}", f"{temp_dir}/OUT", "--project_id", f"{project_id}", "--gzip_files"])
            res = bulk_load("CALIPER", project_id, f"{temp_dir}/OUT", logs, access_token)
            if int(res[0]["status"]) != 200:
                server_errors.append(res[0]["message"])

            try:
                db = LocalFHIRDatabase(db_name=f"{temp_dir}/local_fhir.db")
                db.load_ndjson_from_dir(path=temp_dir)

                load_flat(project_id=project_id, index='researchsubject',
                          generator=db.flattened_research_subjects(),
                          limit=None, elastic_url=DEFAULT_ELASTIC,
                          output_path=None)

                load_flat(project_id=project_id, index='observation',
                          generator=db.flattened_observations(),
                          limit=None, elastic_url=DEFAULT_ELASTIC,
                          output_path=None)

                load_flat(project_id=project_id, index='file',
                          generator=db.flattened_document_references(),
                          limit=None, elastic_url=DEFAULT_ELASTIC,
                          output_path=None)

                logs = fhir_put(project_id, path=temp_dir,
                                elastic_url=DEFAULT_ELASTIC)

            except Exception as e:
                server_errors.append(str(e))

        return server_errors
