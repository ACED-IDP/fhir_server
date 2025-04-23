import tempfile
import orjson
import subprocess
import os
import time
import traceback
from typing import List
from gen3.auth import Gen3Auth, decode_token

from aced_submission.grip_load import bulk_load, bulk_delete, get_project_data
from aced_submission.meta_flat_load import DEFAULT_ELASTIC, load_flat, delete as meta_flat_delete
from gen3_tracker.meta.dataframer import LocalFHIRDatabase


async def _get_grip_service_name() -> str | None:
    """Get GRIP_SERVICE_NAME from environment"""
    graph_name = os.environ.get('GRIP_SERVICE_NAME', None)
    assert graph_name is not None, "check GRIP_SERVICE_NAME env in helm chart. env var is None"
    return os.environ.get('GRIP_SERVICE_NAME', None)


async def _get_grip_graph_name() -> str | None:
    """Get GRIP_GRAPH_NAME from environment"""
    graph_name = os.environ.get('GRIP_GRAPH_NAME', None)
    assert graph_name is not None, "check GRIP_GRAPH_NAME env in helm chart. env var is None"
    return graph_name


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


async def process(rows: List[dict], project_id: str, access_token: str) -> List[str] | None:
    """Processes a bundle into a temp directory of NDJSON files
    that are compatible with existing loading functions

    Currently Supports PUT and DELETE methods only

    TODO: write new loading functions that load from ram instead of writing
    and reading to disk"""

    server_errors = []
    temp_files = {}
    logs = {"logs": []}
    delete_body = {"graph": await _get_grip_graph_name(), "edges": [], "vertices": []}
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
            res = bulk_delete(await _get_grip_service_name(), await _get_grip_graph_name(), project_id=project_id, vertices=delete_body["vertices"],
                              edges=delete_body["edges"], output=logs, access_token=access_token)
            if int(res["status"]) != 200:
                server_errors.append(res["message"])

        for temp_file in temp_files.values():
            temp_file.close()

        if files_written:
            program, project =project_id.split("-")
            project_str_dict = f'{{"auth_resource_path":"/programs/{program}/projects/{project}"}}'
            subprocess.run(["jsonschemagraph", "gen-dir", "iceberg/schemas/graph", f"{temp_dir}", f"{temp_dir}/OUT", "--extraArgs", project_str_dict, "--gzip_files"])
            res = bulk_load(await _get_grip_service_name(), await _get_grip_graph_name(), project_id, f"{temp_dir}/OUT", logs, access_token)
            if int(res[0]["status"]) != 200:
                server_errors.append(res[0]["message"])

        try:
            db = LocalFHIRDatabase(db_name=f"{temp_dir}/local_fhir.db")
            db.bulk_insert_data(resources=get_project_data(await _get_grip_service_name(), await _get_grip_graph_name(), project_id, logs, access_token, 1024*1024))

            index_generator_dict = {
                'researchsubject': db.flattened_research_subjects,
                'specimen': db.flattened_specimens,
                'file': db.flattened_document_references
            }

            for index in index_generator_dict.keys():
                program, project = project_id.split("-")
                meta_flat_delete(project_id=f"{program}-{project}", index=index)

            for index, generator in index_generator_dict.items():
                load_flat(project_id=project_id, index=index,
                        generator=generator(),
                        limit=None, elastic_url=DEFAULT_ELASTIC,
                        output_path=None)

        except Exception as e:
            tb = traceback.format_exc()
            server_errors.append(tb)
            server_errors.append(str(e))

        return server_errors
