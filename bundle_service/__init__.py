import re

"""Bundle submission should be less than 50 MB
see https://cloud.google.com/healthcare-api/quotas"""
MAX_REQUEST_SIZE = 50 * 1024 * 1024

FHIR_JSON_CONTENT_HEADERS = {"Content-Type": "application/fhir+json"}

UUID_PATTERN = re.compile(r'^[\da-f]{8}-([\da-f]{4}-){3}[\da-f]{12}$', re.IGNORECASE)
VALID_RESOURCE_TYPES = [
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
