import pytest
import requests
from gen3.auth import Gen3Auth


@pytest.fixture(autouse=True, scope="session")
def auth() -> dict:
    """
    Sets up writer and reader permissions for the user so that pytests can pass. Note: user must be an admin
    for them to be able to execute these requestor SIGN commands

    If the authenticated user already has signed reader and writer policies on the test project this function will return early. If auth errors
    persist there is probably an issue in arborist. /authz/mapping should be checked.
    """
    auth = Gen3Auth()
    authz = auth.curl('/requestor/request/user').json()
    is_writer, is_reader = False, False
    for record in authz:
        if (record["policy_id"] == "programs.ohsu.projects.pytest_fhir_server_writer" and
           record["status"] == "SIGNED"):
            is_writer = True
        if (record["policy_id"] == "programs.ohsu.projects.pytest_fhir_server_reader" and
           record["status"] == "SIGNED"):
            is_reader = True

    if is_writer and is_reader:
        return

    print("Adding read/write permissions for test project ohsu-test_fhir_server")
    response = requests.post(
        auth.endpoint + "/" + "requestor/request",
        json={'role_ids': ['writer'], 'resource_paths': ['/programs/ohsu/projects/pytest_fhir_server']},
        auth=auth
    )
    response.raise_for_status()
    response = response.json()
    print(response)

    request_id = response["request_id"]
    update_response = requests.put(
        auth.endpoint + "/" + f'requestor/request/{request_id}', json={"status": "SIGNED"}, auth=auth
    )
    update_response.raise_for_status()
    update_response = update_response.json()
    print(update_response)

    response = requests.post(
        auth.endpoint + "/" + "requestor/request",
        json={'role_ids': ['reader'], 'resource_paths': ['/programs/ohsu/projects/pytest_fhir_server']},
        auth=auth
    )
    response.raise_for_status()
    response = response.json()
    print(response)

    request_id = response["request_id"]
    update_response = requests.put(
        auth.endpoint + "/" + f'requestor/request/{request_id}', json={"status": "SIGNED"}, auth=auth
    )
    print(update_response)
    update_response.raise_for_status()


@pytest.fixture
def valid_claim() -> dict:
    """Return a list of plugins."""
    return {
        "id": "b7793c1a-690e-5b7b-8b5b-867555936d06",
        "resourceType": "Claim",
        "status": "active",
        "created": "2014-08-16",
        "use": "claim",
        "type": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/claim-type",
                    "code": "oral",
                }
            ]
        },
        "patient": {"reference": "Patient/1"},
    }


@pytest.fixture
def valid_patient() -> dict:
    return {
        "id": "b7793c1a-690e-5b7b-8b5b-867555936d06",
        "resourceType": "Patient",
        "identifier": [{"system": "https://example.org/my_id", "value": "ohsu-pytest_fhir_server"}],
    }


@pytest.fixture
def valid_bundle() -> dict:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "identifier": {"system": "https://aced-idp.org/project_id", "value": "ohsu-pytest_fhir_server"},
        "entry": [
            {
                "resource": None,
                "request": {
                    "method": "PUT",
                    "url": "Claim"
                },
            }
        ],
    }


@pytest.fixture
def valid_delete() -> dict:
    return {
        "resourceType": "Bundle",
        "id": "cf014752-28f2-5838-9b72-29afe11392a7",
        "identifier": {
            "use": "official",
            "system": "https://aced-idp.org/project_id",
            "value": "ohsu-pytest_fhir_server"
        },
        "type": "transaction",
        "entry": [
            {
                "request": {
                    "method": "DELETE",
                    "url": None
                }
            },
        ]
    }
