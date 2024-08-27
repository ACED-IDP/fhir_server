import copy
import os
import json
import base64

from fastapi.testclient import TestClient
from requests import Response

from bundle_service.main import app
from gen3.auth import decode_token


class CustomTestClient(TestClient):
    def delete_with_payload(self,  **kwargs):
        return self.request(method="DELETE", **kwargs)


client = CustomTestClient(app)


ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN', None)
HEADERS = {"Authorization": f"{ACCESS_TOKEN}"}


def test_read_main():
    """The main page should return a 404."""
    response = client.get("/")
    assert response.status_code == 404, response.status_code


def test_read_health():
    """The health page should return a 200."""
    response = client.get("/_status")
    assert response.status_code == 200, response.status_code


def test_read_bundle():
    """A GET bundle page should return a 405."""
    response = client.get("/Bundle")
    assert response.status_code == 405, response.status_code


def mock_encode_token(payload: dict) -> str:
    """Encodes only the payload, and provides a mock header, signature"""
    json_str = json.dumps(payload)
    base64_bytes = base64.urlsafe_b64encode(json_str.encode('utf-8'))
    base64_str = base64_bytes.decode('utf-8').rstrip("=")
    token = f"header.{base64_str}.signature"
    return token


def assert_bundle_response(
    response: Response,
    expected_status_code: int,
    bundle_diagnostic: str = None,
    entry_diagnostic: str = None,
):
    """Check that a bundle response is valid."""
    assert response.status_code == expected_status_code, response.status_code
    response_bundle = response.json()
    print("RESP BUNDLE: ", response_bundle)
    assert "resourceType" in response_bundle, response_bundle
    assert response_bundle["resourceType"] == "Bundle", response_bundle
    assert response_bundle["type"] == "transaction-response", response_bundle
    response_bundle["issues"]["resourceType"] == "OperationOutcome", response_bundle[
        "issues"
    ]
    if bundle_diagnostic:
        actual_bundle_diagnostic = sorted(
            [_["diagnostics"] for _ in response_bundle["issues"]["issue"]]
        )
        assert bundle_diagnostic in actual_bundle_diagnostic, response_bundle
    if entry_diagnostic:
        actual_entry_diagnostic = sorted(
            [
                _["diagnostics"]
                for _ in response_bundle["entry"][0]["response"]["outcome"]["issue"]
            ]
        )
        assert entry_diagnostic in actual_entry_diagnostic, response_bundle


def create_request_bundle(
    valid_bundle: dict,
    valid_resource: dict
) -> dict:
    """create a bundle request."""
    _ = copy.deepcopy(valid_bundle)
    _["entry"][0]["resource"] = valid_resource
    return _


def create_delete_bundle(
    valid_delete_bundle: dict,
    valid_delete_url: str
) -> dict:
    """create a bundle request."""
    _ = copy.deepcopy(valid_delete_bundle)
    _["entry"][0]["request"]["url"] = valid_delete_url
    return _


def test_write_bundle_no_data():
    """A PUT bundle without data should return a 422."""
    response = client.put("/Bundle", json={}, headers=HEADERS)
    assert_bundle_response(response, 422, bundle_diagnostic="Bundle missing body")


def test_write_bundle_no_auth_header():
    """A PUT bundle with data, but no Auth header should return a 401."""
    response = client.put("/Bundle", json={"resourceType": "Bundle"})
    assert_bundle_response(
        response, 401, bundle_diagnostic="Missing Authorization header"
    )


def test_write_bundle_with_no_project_permissions(valid_bundle, valid_patient):
    """A PUT with data but insufficient perms for the project that is being submitted to returns a 401"""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    request_bundle["identifier"]["value"] = "ohsu-this_proj_has_no_perms"
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 403, bundle_diagnostic="/programs/ohsu/projects/this_proj_has_no_perms not found in user authz")


def test_write_misc_resource():
    """A PUT bundle with data, but not a Bundle should return a 422."""
    response = client.put("/Bundle", json={"resourceType": "Foo"}, headers=HEADERS)
    assert_bundle_response(
        response, 422, bundle_diagnostic="Body must be a FHIR Bundle, not Foo"
    )


def test_write_bundle_missing_entry(valid_bundle, valid_patient):
    """A PUT bundle missing `entry` should return a 422."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    del request_bundle["entry"]
    response = client.put("/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 422, bundle_diagnostic="Bundle missing entry")

    request_bundle = create_request_bundle(valid_bundle, valid_patient)
    request_bundle["entry"] = []
    response = client.put("/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 422, bundle_diagnostic="Bundle missing entry")


def test_write_bundle_missing_identifier(valid_bundle, valid_patient):
    """A PUT bundle missing `identifier` should return a 422."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    del request_bundle["identifier"]
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 422, bundle_diagnostic="Bundle missing identifier")

    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    request_bundle["identifier"] = {"system": "https://foo.bar", "value": "foo"}
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(
        response,
        422,
        bundle_diagnostic="Bundle missing identifier https://aced-idp.org/project_id",
    )


def test_write_content_larger_than_50MB(valid_bundle, valid_patient):
    """A PUT bundle body that is larger than 50 BM should produce 422"""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    response = client.put(url="/Bundle", json=request_bundle, headers={"Authorization": f"{ACCESS_TOKEN}", "Content-Length": str(51*1024*1024)})
    assert_bundle_response(
        response,
        422,
    )


def test_write_bundle_incorrect_method(valid_bundle, valid_patient):
    """A POST bundle entry without PUT or DELETE should return a 422."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    request_bundle["entry"][0]["request"]["method"] = "POST"
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(
        response,
        422,
        entry_diagnostic="Invalid entry.method POST for entry None, must be PUT or DELETE",
    )


def test_write_bundle_unsupported_resource(valid_bundle, valid_claim):
    """A PUT bundle entry without an unsupported resource should return a 422."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_claim)
    import pprint

    pprint.pprint(request_bundle)
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 422, entry_diagnostic="Unsupported resource Claim")


def test_write_bundle_patient_missing_identifier(valid_bundle):
    """A PUT bundle entry.resource without identifier should produce 422."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource={"resourceType": "Patient", "id": "b7793c1a-690e-5b7b-8b5b-867555936d06"})
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(
        response, 422, entry_diagnostic="Missing identifier for Patient with b7793c1a-690e-5b7b-8b5b-867555936d06"
    )


def test_write_bundle_patient_missing_id(valid_bundle):
    """A PUT bundle entry.resource without id should produce 422."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource={"resourceType": "Patient", "identifier":  [{"system": "https://example.org/my_id", "value": "test-foo"}]})
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(
        response, 422, entry_diagnostic="Resource missing id"
    )


def test_write_bundle_simple_ok(valid_bundle, valid_patient):
    """A PUT bundle without type should produce 201."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 201)
    response_bundle = response.json()
    assert response_bundle["entry"][0]["response"]["status"] in [
        "200",
        "201",
    ], response_bundle
    response.headers[
        "Location"
    ] == f'https://aced-idp.org/Bundle/{response_bundle["id"]}', "Response header Location should be set to the new Bundle ID"


def test_write_bundle_missing_type(valid_bundle, valid_patient):
    """A PUT bundle without type should produce 422."""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    del request_bundle["type"]
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(
        response,
        422,
        bundle_diagnostic="Bundle must be of type `transaction`, not None",
    )


def test_write_bundle_expired_token(valid_bundle, valid_patient):
    """A PUT bundle with an expired token should produce a 401."""
    token = decode_token(ACCESS_TOKEN)
    token['exp'] = token['iat']
    expired_token = mock_encode_token(token)
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    response = client.put(url="/Bundle", json=request_bundle, headers={"Authorization": expired_token})
    assert_bundle_response(response, 401, bundle_diagnostic="Token has expired")


def test_write_partial_invalid_bundle_resources(valid_bundle, valid_patient):
    """A PUT bundle with an invalid FHIR resource and a valid FHIR resource in the same bundle should produce 202 signifying a partial success for 1/2 entries"""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    _ = copy.deepcopy(request_bundle["entry"][0])
    request_bundle["entry"].append(_)
    request_bundle["entry"][0]["resource"]["fewfwefewf"] = "dsfdfdsf"
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 202, entry_diagnostic="Validation error on None with id: None")


def test_write_partial_invalid_bundle_method(valid_bundle, valid_patient):
    """A PUT bundle with a valid method and an invalid PUT method entry should produce 202 signifying a partial success for 1/2 entries"""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    _ = copy.deepcopy(request_bundle["entry"][0])
    request_bundle["entry"].append(_)
    request_bundle["entry"][0]["request"] = {"method": "PUT", "url": "Patient"},
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 202, entry_diagnostic="Validation error on None with id: None")


def test_all_invalid_bundle_resources(valid_bundle, valid_patient):
    """A PUT bundle with all invalid FHIR resources in the bundle should produce a 422 for all resources"""
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    _ = copy.deepcopy(request_bundle["entry"][0])
    request_bundle["entry"].append(_)
    request_bundle["entry"][0]["resource"]["fewfwefewf"] = "dsfdfdsf"
    request_bundle["entry"][1]["resource"]["fewfwefewf"] = "dsfdfdsf"
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 422, bundle_diagnostic="non fatal entry")


def test_simple_delete(valid_bundle, valid_patient, valid_delete):
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 201)

    delete_bundle = create_delete_bundle(valid_delete, "Patient/b7793c1a-690e-5b7b-8b5b-867555936d06")
    response = client.delete(url="/Bundle", json=delete_bundle, headers=HEADERS)
    assert_bundle_response(response, 201)


def test_delete_with_invalid_url_uuid(valid_bundle, valid_patient, valid_delete):
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 201)

    delete_bundle = create_delete_bundle(valid_delete, "Patient/b7793c1a-690e-5bb-867555936d06")
    response = client.delete(url="/Bundle", json=delete_bundle, headers=HEADERS)
    assert_bundle_response(response, 422)


def test_delete_with_invalid_url_resource(valid_bundle, valid_patient, valid_delete):
    request_bundle = create_request_bundle(valid_bundle=valid_bundle, valid_resource=valid_patient)
    response = client.put(url="/Bundle", json=request_bundle, headers=HEADERS)
    assert_bundle_response(response, 201)

    delete_bundle = create_delete_bundle(valid_delete, "Claim/b7793c1a-690e-5bb-867555936d06")
    response = client.delete(url="/Bundle", json=delete_bundle, headers=HEADERS)
    assert_bundle_response(response, 422)


def test_openapi_ui():
    response = client.get(url="/redoc")
    assert response.status_code == 200, response.status_code


def test_openapi_json():
    response = client.get(url="/openapi.json")
    assert response.status_code == 200, response.status_code
    assert response.json()
