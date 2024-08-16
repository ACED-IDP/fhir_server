import tempfile
import orjson
import subprocess
import os
from typing import List

from aced_submission.grip_load import bulk_add
from aced_submission.meta_flat_load import DEFAULT_ELASTIC, load_flat, bulk_delete
from aced_submission.fhir_store import fhir_put
from gen3_tracker.meta.dataframer import LocalFHIRDatabase



async def process(rows: List[dict], project_id: str, access_token: str):
    """Processes a bundle into a temp directory of NDJSON files
    that are compatible with existing loading functions

    Currently Supports PUT and DELETE methods only

    TODO: write new loading functions that load from ram instead of writing
    and reading to disk"""

    print("ACCESS_TOKEN: ", access_token)

    temp_files = {}
    logs = {"logs":[]}
    delete_body = {"graph": "CALIPER", "edges":[], "vertices": []}
    files_written = False
    with tempfile.TemporaryDirectory() as temp_dir:
        delete_file = open(os.path.join(temp_dir, "delete.json"), mode='a+')
        for row in rows:
            if row["request"]["method"] == "PUT":
                file_name = row["resource"]["resourceType"] + ".ndjson"
                if file_name not in temp_files:
                    temp_file_path = os.path.join(temp_dir, file_name)
                    temp_files[file_name] = open(temp_file_path, mode='a+')
                temp_files[file_name].write(orjson.dumps(row["resource"], option=orjson.OPT_APPEND_NEWLINE))
                files_written = True
            elif row["request"]["method"] == "DELETE":
                delete_body["vertices"].append(row["id"])


        if len(delete_body["edges"]) > 0 or len(delete_body["vertices"]) > 0:
            bulk_delete("CALIPER", project_id=project_id, vertices=delete_body["vertices"],
                        edges=delete_body["edges"], output=logs, access_token=access_token)

        if files_written:
            subprocess.run(["jsonschemagraph", "gen-dir", "iceberg/schemas/graph", f"{temp_dir}", f"{temp_dir}/OUT","--project_id", f"{project_id}","--gzip_files"])
            bulk_add("CALIPER", project_id, f"{temp_dir}/OUT", logs)

            db = LocalFHIRDatabase(db_name=temp_dir)
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


            for temp_file in temp_files.values():
                temp_file.close()
