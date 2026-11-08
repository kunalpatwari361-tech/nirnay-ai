"""models/document_ai: the package api/document.py imports, so the prototype's document route loads."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import sandbox
from models.document_ai.extract_fields import extract_fields, iso_date
from models.document_ai.ocr import extract_text
from models.document_ai.verification import verify_applicant

RAVI = sandbox.PERSONAS["ravi"]


@pytest.fixture(scope="module")
def pan_card(tmp_path_factory):
    path = tmp_path_factory.mktemp("doc") / "pan.jpg"
    path.write_bytes(sandbox.sample_document(RAVI, "pan"))
    return path


def test_reads_identifies_and_matches_a_pan_card(pan_card):
    text = extract_text(pan_card)
    got = extract_fields(text)
    assert got["document_type"] == "pan_card" and got["slot"] == "pan"
    f = got["extracted_fields"]
    assert f["pan"] == "ABCPK1234K" and f["date_of_birth"] == RAVI.dob.isoformat()
    v = verify_applicant({"name": RAVI.name, "date_of_birth": RAVI.dob.strftime("%d/%m/%Y")}, f)
    assert v["verification_status"] == "matched" and v["mismatches"] == []


def test_another_persons_details_are_a_mismatch_not_a_rejection():
    v = verify_applicant({"name": "Sunita Rao", "date_of_birth": "1970-01-01"},
                         {"name": "RAVI KUMAR", "date_of_birth": "1990-08-15"})
    assert v["verification_status"] == "mismatch" and {m["field"] for m in v["mismatches"]} == {"name", "date_of_birth"}
    assert v["action"].startswith("refer")


def test_nothing_to_compare_is_not_checked():
    assert verify_applicant({"name": "", "date_of_birth": ""}, {})["verification_status"] == "not_checked"


def test_a_full_aadhaar_number_is_never_returned():
    got = extract_fields("Government of India\nName Ravi Kumar\nDOB 15/08/1990\nMALE\n1234 5678 4821\nAadhaar")
    assert "4821" == got["extracted_fields"].get("aadhaar_last4")
    assert "12345678" not in str(got).replace(" ", "")


def test_a_blank_photo_asks_for_a_clearer_one(tmp_path):
    from PIL import Image
    path = tmp_path / "blank.jpg"
    Image.new("RGB", (400, 300), (250, 250, 250)).save(path)
    with pytest.raises(ValueError):
        extract_text(path)


def test_dates_in_either_order():
    assert iso_date("15/08/1990") == iso_date("1990-08-15") == date(1990, 8, 15).isoformat()
    assert iso_date("31/02/1990") is None and iso_date("") is None


def test_the_prototype_document_route_now_loads(pan_card):
    from api.main import app
    assert "/api/v1/documents/ocr" in app.openapi()["paths"]
    r = TestClient(app).post("/api/v1/documents/ocr", files={"file": ("pan.jpg", pan_card.read_bytes(), "image/jpeg")},
                             data={"declared_name": RAVI.name, "declared_dob": RAVI.dob.isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document_type"] == "pan_card" and body["verification"]["verification_status"] == "matched"
