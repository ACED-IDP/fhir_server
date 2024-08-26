
from __future__ import annotations

import uuid
from typing import Optional, Any
import re
import logging


from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from cdislogging import get_logger
from fhir.resources.bundle import Bundle, BundleEntry, BundleEntryResponse
from fhir.resources.operationoutcome import OperationOutcome, OperationOutcomeIssue
from bundle_service.processing.process_bundle import process, _can_create
from pydantic.v1.error_wrappers import ValidationError


logger = get_logger("fhir_server", log_level="info")

"""Bundle submission should be less than 50 MB
see https://cloud.google.com/healthcare-api/quotas"""
MAX_REQUEST_SIZE = 50 * 1024 * 1024

SUCCESS_ISSUE = {
                    "severity": "success",
                    "code": "success",
                }

UUID_PATTERN = re.compile(r'^[\da-f]{8}-([\da-f]{4}-){3}[\da-f]{12}$', re.IGNORECASE)
valid_resource_types = [
    "ResearchStudy",
    "Patient",
    "ResearchSubject",
    "Substance",
    "Specimen",
    "Observation",
    "Condition",
    "Medication",
    "MedicationAdministration",
    "DocumentReference",
    "Task",
    "FamilyMemberHistory",
    "BodyStructure",
    "Organization"
]

tags_metadata = [
    {
        "name": "System",
        "description": "Cluster operations, health, and status.",
    },
    {
        "name": "Submission",
        "description": "Manage **submission**, validate and populate backend data stores and portal.",
    },
]

app = FastAPI(
    title="ACED Submission",
    contact={},
    version="0.0.1",
    description="""ACED FHIR Bundle Implementation""",
    servers=[
        {
            "url": "https://aced-idp.org/Bundle",
            "description": "ACED FHIR Bundle Implementation",
        }
    ],
    openapi_tags=tags_metadata,
)


@app.put(
    "/Bundle",
    # response_model=Any,
    # responses={"default": {"model": Any}},
    status_code=201,
    tags=["Submission"],
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json+fhir": {
                    "schema": {
                        "type": "object",
                        "description": """FHIR [Bundle](https://hl7.org/fhir/R5/bundle.html)"""
                    }
                }
            }
        },
        "responses": {
            201: {
                "description": "Created",
                "content": {
                    "application/json+fhir": {
                        "schema": {
                            "type": "object",
                            "description": "FHIR [Bundle](https://hl7.org/fhir/R5/bundle.html)",
                        }
                    }
                },
            },
            202: {
                "description": "Accepted",
                "content": {
                    "application/json+fhir": {
                        "schema": {
                            "type": "object",
                            "description": "Some of the entries in the bundle were rejected",
                        }
                    }
                },
            },
            422: {
                "description": "Unprocessable Entity",
                "content": {
                    "application/json+fhir": {
                        "schema": {
                            "type": "object",
                            "description": "FHIR [OperationOutcome](https://hl7.org/fhir/R5/operationoutcome.html) issues that apply to [Bundle](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.issues) or [Entry](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.entry.response.outcome)",
                        }
                    }
                },
            },
            401: {
                "description": "Unauthorized",
                "content": {
                    "application/json+fhir": {
                        "schema": {
                            "type": "object",
                            "description": "Authorization header issue",
                        }
                    }
                },
            },
            403: {
                 "description": "Forbidden",
                 "content": {
                     "application/json+fhir": {
                         "schema": {
                             "type": "object",
                             "description": " User is Forbidden from executing operation",
                         }
                     }
                 },
             },
            500: {
                 "description": "Internal Server Error",
                 "content": {
                     "application/json+fhir": {
                         "schema": {
                             "type": "object",
                             "description": "Server encountered a problem while loading the bundle",
                         }
                     }
                 },
             },
        }
    },
)
async def put__bundle(
    access_token: Optional[str] = Header(None, alias="Authorization"),
    content_length: Optional[str] = Header(None, alias="Content-Length"),
    body: Request = None,
) -> Any:
    """
    Import a FHIR Bundle.\n
    * In order to prevent "schema explosion" 🤯, the openapi definitions here are minimal, validation will use complete `R5 Bundle definitions` [here](https://hl7.org/fhir/R5/bundle.html).\n
    * The FHIR Bundle must be of [type](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.type) `transaction` and contain a `https://aced-idp.org/project_id` identifier.\n
    * Bundle entry [method](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.entry.request.method) must be of type `PUT` or `DELETE`.\n
    * Entry [resource](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.entry.resource) must be one of the `supported types` [here](https://github.com/ACED-IDP/submission/wiki/Submission#valid-resource-types).\n
    * See more regarding `use case and validations` [here](https://github.com/ACED-IDP/submission/wiki/Submission).
    """

    body_dict = await body.json()

    # Validate bundle as a whole
    outcome = await validate_bundle(body_dict, access_token, content_length)

    any_fatal_issues = any([_ for _ in outcome.issue if _.severity == "fatal"])
    if any_fatal_issues:
        status_code = 422
        if len([_.code for _ in outcome.issue if _.code == "security"]):
            status_code = 401
        if len([_.code for _ in outcome.issue if _.code == "forbidden"]):
            status_code = 403

        # If fatal error exit early since nothing more can be done.
        response = Bundle(
            type="transaction-response", entry=None, issues=outcome
        )
        response.id = str(uuid.uuid4())
        logger.error(f"[{status_code}] {response.dict()}")
        return JSONResponse(
            content=response.dict(), status_code=status_code,
            headers={"Content-Type": "application/fhir+json"}
        )

    # Validate each entry in the bundle, and get the project id from the bundle.
    # If bundle is invalid, project_id will not exist
    response_entries, valid_fhir_rows, project_id = validate_bundle_entries(body_dict)

    # Set status code
    status_code = 201
    headers = {"Content-Type": "application/fhir+json"}

    all_invalid = all([elem.response.status != "200" for elem in response_entries])
    any_invalid_entries = any([elem.response.status != "200" for elem in response_entries])

    if any_invalid_entries:
        # This if statement aims to capture partial issue severity error bundles where
        #  some resources are improperly formatted, but others can be executed
        status_code = 202
        if all_invalid:
            status_code = 422

    # To continue, bundle cannot have fatal severity issues
    errors = await process(valid_fhir_rows, project_id, access_token)
    # Not sure how to write a test for this
    if len(errors) > 0:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="exception",
                diagnostics=str(errors),
            )
        )
        status_code = 500

    response = Bundle(
        type="transaction-response", entry=response_entries, issues=outcome
    )
    response.id = str(uuid.uuid4())

    # Not sure what this is doing
    Bundle.validate(response)

    if status_code in [201, 202]:
        headers["Location"] = f"https://aced-idp.org/Bundle/{response.id}"

    logger.info(f"[{status_code}] {response.dict()}")
    return JSONResponse(
        content=response.dict(), status_code=status_code, headers=headers
    )


def validate_entry(request_entry: BundleEntry, error_details: dict, entry_dict: dict) -> OperationOutcomeIssue:
    """Validate a single entry, return issue or None"""

    if error_details is not None:
        return OperationOutcomeIssue(
            severity="error",
            code="structure",
            diagnostics=f"Validation error on {entry_dict.get('resource_type', None)} with id: {entry_dict.get('id', None)}",
            details={"text": str(error_details)}
        )

    if request_entry.request.method not in ["PUT", "DELETE"]:
        return OperationOutcomeIssue(
            severity="error",
            code="invariant",
            diagnostics=f"Invalid entry.method {request_entry.request.method} for entry {request_entry.fullUrl}, must be PUT or DELETE",
        )

    request_resource_id = request_entry.resource.id
    if request_resource_id is None:
        return OperationOutcomeIssue(
            severity="error",
            code="invariant",
            diagnostics="Resource missing id",
            details={"text": f"for resource {request_entry.resource}"}
        )

    if not bool(UUID_PATTERN.match(request_resource_id)):
        return OperationOutcomeIssue(
            severity="error",
            code="invariant", diagnostics=f"Resource id {request_entry.resource.id} is not a UUID see\
https://build.fhir.org/datatypes.html#uuid for details"
        )

    resource_type = request_entry.resource.resource_type
    if resource_type not in valid_resource_types:
        return OperationOutcomeIssue(
            severity="error",
            code="invariant",
            diagnostics=f"Unsupported resource {resource_type}",
        )
    if not request_entry.resource.identifier:
        return OperationOutcomeIssue(
            severity="error",
            code="required",
            diagnostics=f"Missing identifier for {request_entry.resource.resource_type} with {request_entry.resource.id}",
        )
    return None


def validate_bundle_entries(body: dict) -> list[BundleEntry] | list[dict]:
    """Ensure bundle entries are valid for our use case, Messages relating to the processing of individual entries (e.g. in a batch or transaction) SHALL be reported in the entry.response.outcome for that entry.
    https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.issues
    raise HTTPException if not"""

    valid_fhir_rows = []
    response_entries = []

    request_entries = body.get("entry", [])
    for entry_dict in request_entries:

        try:
            request_entry = BundleEntry(
                **entry_dict
            )
        except ValidationError as e:
            response_issue = validate_entry(None, error_details=e.json(), entry_dict=entry_dict)

        else:
            response_issue = validate_entry(request_entry, error_details=None, entry_dict=None)

        response_entry = BundleEntry()
        if response_issue is None:
            # If entry passes validation, add it to submission row list
            valid_fhir_rows.append(entry_dict)
            response_status = "200"
        else:
            response_status = "422"
        response_entry.response = BundleEntryResponse(status=response_status)
        if response_issue is not None:
            response_entry.response.outcome = OperationOutcome(issue=[response_issue])
        response_entries.append(response_entry)

    return response_entries, valid_fhir_rows, body.get("identifier", {}).get("value", None)


async def validate_bundle(body: dict, authorization: str, content_length: str) -> OperationOutcome:
    """Ensure bundle is valid for our use case, These issues and warnings must apply to the Bundle as a whole, not to individual entries.
    see https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.issues
    """

    outcome = OperationOutcome(issue=[])

    if content_length is not None and int(content_length) > 1024*1024*50:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics="Bundle body greater than 50 MB",
            )
        )
    if body is None or body == {}:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics="Bundle missing body",
            )
        )

    if authorization is None:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="security",
                diagnostics="Missing Authorization header",
            )
        )

    _ = body.get("resourceType", None)
    if _ != "Bundle":
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics=f"Body must be a FHIR Bundle, not {_}",
            )
        )

    _ = body.get("type", None)
    if _ != "transaction":
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics=f"Bundle must be of type `transaction`, not {_}",
            )
        )

    identifier = body.get("identifier", None)
    project_id = None
    if identifier is None:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics="Bundle missing identifier",
            )
        )

    if (
        identifier
        and identifier.get("system", None) == "https://aced-idp.org/project_id"
    ):
        project_id = identifier.get("value", None)
    if project_id is None:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics="Bundle missing identifier https://aced-idp.org/project_id",
            )
        )
    if project_id is not None and len(project_id.split("-")) != 2:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics="Bundle identifier project id not in the from 'str-str'",
             )
        )

    _ = body.get("entry", None)
    if _ is None or _ == []:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="fatal",
                code="required",
                diagnostics="Bundle missing entry",
            )
        )

    if authorization is not None and project_id is not None:
        can_create, msg, error_code = await _can_create(authorization, project_id)
        if not can_create:
            code_label = "security" if error_code == 401 else "forbidden"
            outcome.issue.append(
                OperationOutcomeIssue(
                    severity="fatal",
                    code=code_label,
                    diagnostics=msg,
                )
            )

    if len(outcome.issue) == 0:
        outcome.issue.append(
            OperationOutcomeIssue(
                severity="success",
                code="success",
                diagnostics="non fatal entry",
            )
        )
    return outcome


@app.get("/_status", response_model=None, tags=["System"])
def get__status() -> Any:
    """
    Returns if service is healthy or not
    """
    return JSONResponse(
            content={"Message": "Feeling good!"}, status_code=200, headers={"Content-Type": "application/json"}
    )
    pass
