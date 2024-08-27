import pytest


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
        "identifier": [{"system": "https://example.org/my_id", "value": "ohsu-test"}],
    }


@pytest.fixture
def valid_bundle() -> dict:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "identifier": {"system": "https://aced-idp.org/project_id", "value": "ohsu-test"},
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
            "value": "ohsu-test"
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
