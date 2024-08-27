
from __future__ import annotations

import uuid
from typing import Optional, Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from cdislogging import get_logger
from fhir.resources.bundle import Bundle
from fhir.resources.operationoutcome import OperationOutcomeIssue

from bundle_service import FHIR_JSON_CONTENT_HEADERS
from bundle_service.processing.process_bundle import process
from bundle_service.bundle_validate import validate_bundle_entries, validate_bundle, _any_fatal_issues

logger = get_logger("fhir_server", log_level="info")

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

    # Look for fatal issues in non-entry Bundle metadata
    outcome = await validate_bundle(body_dict, access_token, content_length)

    fatal_issues, status_code, response = await _any_fatal_issues(outcome)
    if fatal_issues:
        return JSONResponse(
            content=response.dict(), status_code=status_code,
            headers=FHIR_JSON_CONTENT_HEADERS
        )

    # Fetch project id and validate each entry in the bundle.
    response_entries, valid_fhir_rows, project_id = validate_bundle_entries(body_dict)

    status_code = 201

    """This code block aims to capture partial issue severity error bundles where
    some resources are improperly formatted, but others can be executed"""
    invalid_entries = [elem.response.status != "200" for elem in response_entries]
    if any(invalid_entries):
        status_code = 202
        if all(invalid_entries):
            status_code = 422

    errors = await process(valid_fhir_rows, project_id, access_token)
    # Not sure how to write a test for this
    if len(errors):
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
        FHIR_JSON_CONTENT_HEADERS["Location"] = f"https://aced-idp.org/Bundle/{response.id}"

    logger.info(f"[{status_code}] {response.dict()}")
    return JSONResponse(
        content=response.dict(), status_code=status_code, headers=FHIR_JSON_CONTENT_HEADERS
    )

app.delete(
    "/Bundle",
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


async def delete__bundle(
    access_token: Optional[str] = Header(None, alias="Authorization"),
    content_length: Optional[str] = Header(None, alias="Content-Length"),
    body: Request = None,
) -> Any:
    """
    Delete a FHIR Bundle.\n
    * The FHIR Bundle must be of [type](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.type) `transaction` and contain a `https://aced-idp.org/project_id` identifier.\n
    * Bundle entry [method](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.entry.request.method) must be of type `PUT` or `DELETE`.\n
    * Entry [resource](https://hl7.org/fhir/R5/bundle-definitions.html#Bundle.entry.resource) must be one of the `supported types` [here](https://github.com/ACED-IDP/submission/wiki/Submission#valid-resource-types).\n
    """

    body_dict = await body.json()
    outcome = await validate_bundle(body_dict, access_token, content_length)

    fatal_issues, status_code, response = await _any_fatal_issues(outcome)
    if fatal_issues:
        return JSONResponse(
            content=response.dict(), status_code=status_code,
            headers=FHIR_JSON_CONTENT_HEADERS
        )

    response_entries, valid_fhir_rows, project_id = validate_bundle_entries(body_dict)
    status_code = 201

    """This code block aims to capture partial issue severity error bundles where
    some resources are improperly formatted, but others can be executed"""
    invalid_entries = [elem.response.status != "200" for elem in response_entries]
    if any(invalid_entries):
        status_code = 202
        if all(invalid_entries):
            status_code = 422

    errors = await process(valid_fhir_rows, project_id, access_token)
    # Not sure how to write a test for this
    if len(errors):
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
        FHIR_JSON_CONTENT_HEADERS["Location"] = f"https://aced-idp.org/Bundle/{response.id}"

    logger.info(f"[{status_code}] {response.dict()}")
    return JSONResponse(
        content=response.dict(), status_code=status_code, headers=FHIR_JSON_CONTENT_HEADERS
    )


@app.get("/_status", response_model=None, tags=["System"])
def get__status() -> Any:
    """
    Returns if service is healthy or not
    """
    return JSONResponse(
            content={"Message": "Feeling good!"}, status_code=200, headers={"Content-Type": "application/json"}
    )
    pass
