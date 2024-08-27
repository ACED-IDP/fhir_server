import uuid
from cdislogging import get_logger

from bundle_service.processing.process_bundle import _can_create
from bundle_service import UUID_PATTERN, VALID_RESOURCE_TYPES, MAX_REQUEST_SIZE
from pydantic.v1.error_wrappers import ValidationError
from fhir.resources.operationoutcome import OperationOutcome, OperationOutcomeIssue
from fhir.resources.bundle import Bundle, BundleEntry, BundleEntryResponse


logger = get_logger("fhir_server", log_level="info")


async def _any_fatal_issues(outcome: OperationOutcome) -> bool | int | Bundle:
    any_fatal_issues = any([_ for _ in outcome.issue if _.severity == "fatal"])
    if any_fatal_issues:
        status_code = 422
        if len([_.code for _ in outcome.issue if _.code == "security"]):
            status_code = 401
        if len([_.code for _ in outcome.issue if _.code == "forbidden"]):
            status_code = 403

        response = Bundle(
            type="transaction-response", entry=None, issues=outcome
        )
        response.id = str(uuid.uuid4())
        logger.error(f"[{status_code}] {response.dict()}")

        return True, status_code, response

    return False, None, None


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

    # Delete operations don't require a resource'
    if request_entry.request.method in ["PUT"]:
        resource_type = request_entry.resource.resource_type
        if resource_type not in VALID_RESOURCE_TYPES:
            return OperationOutcomeIssue(
                severity="error",
                code="invariant",
                diagnostics=f"Unsupported resource {resource_type}",
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
        if not request_entry.resource.identifier:
            return OperationOutcomeIssue(
                severity="error",
                code="required",
                diagnostics=f"Missing identifier for {request_entry.resource.resource_type} with {request_entry.resource.id}",
            )
    elif request_entry.request.method in ["DELETE"]:
        request_url = request_entry.request.url
        ref_split = request_url.split("/")
        if len(ref_split) != 2:
            return OperationOutcomeIssue(
                severity="error",
                code="required",
                diagnostics=f"entry.request.url {request_url} does not contain exactly 1 '/' character",
            )
        if ref_split[0] not in VALID_RESOURCE_TYPES:
            return OperationOutcomeIssue(
                severity="error",
                code="required",
                diagnostics=f"entry.request.url {ref_split[0]} in {request_url} is an unsupported resource",
            )
        if not bool(UUID_PATTERN.match(ref_split[1])):
            return OperationOutcomeIssue(
                severity="error",
                code="required",
                diagnostics=f"entry.request.url {ref_split[1]} is not a UUID see\
https://build.fhir.org/datatypes.html#uuid for details",
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

    if content_length is not None and int(content_length) > MAX_REQUEST_SIZE:
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
