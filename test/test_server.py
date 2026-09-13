"""The grading endpoint, exercised in-process with no Modal deployment.

`grading_api.build_app()` is a plain FastAPI factory precisely so these can
run anywhere. Everything below goes through real HTTP plumbing and real
scans - the only thing missing is Modal itself.
"""

import csv
import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import synthetic_sheets as ss  # noqa: E402
from src import answer_key, sheet_layout as layout  # noqa: E402

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from server import grading_api  # noqa: E402

PASSPHRASE = "open-sesame-for-the-tests"
QUESTIONS = layout.QUESTIONS_PER_TEST
TEST_IDS = ("1001", "1002", "1003")


def cycled(offset: int) -> list:
    return [layout.OPTIONS[(i + offset) % len(layout.OPTIONS)]
            for i in range(QUESTIONS)]


@pytest.fixture(scope="module")
def client():
    return fastapi_testclient.TestClient(grading_api.build_app())


@pytest.fixture(autouse=True)
def passphrase(monkeypatch):
    monkeypatch.setenv("GRADING_PASSPHRASE", PASSPHRASE)


def auth():
    return {"X-Grading-Key": PASSPHRASE}


def key_csv(tests=None) -> bytes:
    tests = tests or [
        ("Latin Literature", "1001", "", cycled(0)),
        ("Reading Comprehension 1", "1002", "MS-1, MS-2, MS-3, HS-1, HS-2, HS-3", cycled(1)),
        ("Mythology", "1003", "", cycled(2)),
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([answer_key.NAME_ROW] + [t[0] for t in tests])
    writer.writerow([answer_key.TEST_ID_ROW] + [t[1] for t in tests])
    writer.writerow([answer_key.ALLOWED_ROW] + [t[2] for t in tests])
    for number in range(1, QUESTIONS + 1):
        writer.writerow([str(number)] + [t[3][number - 1] for t in tests])
    return buffer.getvalue().encode("utf-8")


def batch_pdf(sheets, tmp_path) -> bytes:
    path = tmp_path / "batch.pdf"
    ss.write_batch(sheets, path)
    return path.read_bytes()


def one_student(**kwargs):
    defaults = dict(student_id="04275", latin_level="HS-2",
                    test_ids=TEST_IDS,
                    answers=[cycled(0), cycled(1), cycled(2)])
    defaults.update(kwargs)
    return ss.SheetData(**defaults)


def files_by_name(body):
    return {item["name"]: item for item in body["files"]}


# --- the passphrase -------------------------------------------------------


def test_health_needs_the_passphrase(client):
    assert client.get("/health").status_code == 401
    assert client.get("/health",
                      headers={"X-Grading-Key": "wrong"}).status_code == 401


def test_health_describes_the_sheet(client):
    body = client.get("/health", headers=auth()).json()
    assert body["ok"] is True
    assert body["questions_per_test"] == QUESTIONS
    assert body["tests_per_sheet"] == 3
    assert body["student_id_digits"] == 5
    assert body["latin_level_count"] == layout.LATIN_LEVEL_COUNT
    assert body["defaults"]["latin_levels"] == list(layout.LATIN_LEVELS)
    assert body["max_upload_bytes"] == grading_api.MAX_UPLOAD_BYTES


def test_grading_needs_the_passphrase(client, tmp_path):
    response = client.post(
        "/grade",
        files=[("scans", ("batch.pdf", batch_pdf([one_student()], tmp_path),
                          "application/pdf"))])
    assert response.status_code == 401


# --- the printable sheet --------------------------------------------------


def test_sheet_returns_a_two_page_pdf(client):
    response = client.post("/sheet", headers=auth(), json={})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")

    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(response.content)
    try:
        assert len(document) == layout.PAGES_PER_SHEET
    finally:
        document.close()


def test_sheet_honours_custom_wording(client):
    layout_config = {
        "title": "UHS JCL SPRING CONVENTION",
        "latin_levels": ["Novice", "Int-1", "Int-2", "Adv-1", "Adv-2",
                         "Adv-3", "Open"],
        "directions": ["1.  Fill bubbles darkly.", "2.  Do not fold."],
    }
    response = client.post("/sheet", headers=auth(),
                           json={"layout": layout_config})
    assert response.status_code == 200

    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(response.content)
    try:
        text = document[0].get_textpage().get_text_range()
    finally:
        document.close()
    assert "UHS JCL SPRING CONVENTION" in text
    assert "Novice" in text and "Open" in text
    assert "Do not fold" in text


def test_sheet_takes_a_different_number_of_levels(client):
    """Two is the floor and ten the ceiling; anything between is fine."""
    for count in (2, 10):
        response = client.post(
            "/sheet", headers=auth(),
            json={"layout": {
                "latin_levels": [f"L{n}" for n in range(1, count + 1)]}})
        assert response.status_code == 200, response.text
        assert response.content[:4] == b"%PDF"


def test_sheet_rejects_too_many_levels(client):
    response = client.post(
        "/sheet", headers=auth(),
        json={"layout": {"latin_levels": [f"L{n}" for n in range(1, 12)]}})
    assert response.status_code == 400
    assert "between 2 and 10" in response.json()["detail"]


# --- grading --------------------------------------------------------------


def test_grade_returns_results_and_keeps_nothing(client, tmp_path):
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([one_student()], tmp_path),
                          "application/pdf")),
               ("key", ("Keys.csv", key_csv(), "text/csv"))],
        data={"batch": "1"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["summary"]["rows"] == 3
    assert body["summary"]["students"] == ["04275"]
    assert body["summary"]["thresholds_note"] == "calibrated"
    assert body["summary"]["thresholds"][0]["spec"]

    names = files_by_name(body)
    assert "Results.csv" in names
    assert "Question Stats.csv" in names
    assert "Calibration.txt" in names
    assert names["Results.csv"]["encoding"] == "utf-8"

    rows = list(csv.DictReader(io.StringIO(names["Results.csv"]["data"])))
    assert [row["Test ID"] for row in rows] == list(TEST_IDS)
    assert all(row["Points"] == str(QUESTIONS) for row in rows)

    # Nothing was left behind on disk.
    leftovers = list(pathlib.Path(grading_api.tempfile.gettempdir()).glob(
        "grade-*"))
    assert not leftovers


def test_grade_reports_a_disallowed_level_without_failing(client, tmp_path):
    scans = batch_pdf([one_student(student_id="00031",
                                   latin_level="HS-Adv")], tmp_path)
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", scans, "application/pdf")),
               ("key", ("Keys.csv", key_csv(), "text/csv"))])
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["test_not_allowed"] == 1
    assert body["summary"]["rows"] == 3


def test_grade_reports_a_broken_key_as_422(client, tmp_path):
    broken = key_csv([("One", "1001", "", cycled(0)),
                      ("Two", "1001", "", cycled(1))])
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([one_student()], tmp_path),
                          "application/pdf")),
               ("key", ("Keys.csv", broken, "text/csv"))])
    assert response.status_code == 422
    assert "used twice" in response.json()["error"]


def test_grade_reports_out_of_order_pages_as_422(client, tmp_path):
    path = tmp_path / "swapped.pdf"
    ss.write_batch([one_student()], path, page_order=[1, 0])
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("swapped.pdf", path.read_bytes(),
                          "application/pdf"))])
    assert response.status_code == 422
    assert "back page" in response.json()["error"]


def test_grade_surfaces_unclear_marks(client, tmp_path):
    faint = one_student(faint={0: [7]}, faint_fraction=0.5)
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([faint], tmp_path),
                          "application/pdf"))],
        data={"batch": "2"})
    body = response.json()
    assert body["summary"]["unclear"] >= 1
    review_files = [name for name in files_by_name(body)
                    if "Unclear" in name]
    assert review_files and "Batch 2" in review_files[0]


def test_grade_accepts_custom_levels_in_the_key(client, tmp_path):
    """A renamed level must be accepted in the key's Allowed row and come
    back in the results."""
    levels = ["Novice", "Int-1", "Int-2", "Adv-1", "Adv-2", "Adv-3", "Open"]
    sheet = one_student(latin_level=None)
    # Bubble the fifth level, whatever it is called.
    sheet = ss.SheetData(student_id="04275",
                         latin_level=layout.LATIN_LEVELS[4],
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    key = key_csv([("Latin Literature", "1001",
                    "Novice, Int-1, Int-2, Adv-1, Adv-3, Open", cycled(0)),
                   ("Reading Comprehension 1", "1002", "", cycled(1)),
                   ("Mythology", "1003", "", cycled(2))])
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([sheet], tmp_path),
                          "application/pdf")),
               ("key", ("Keys.csv", key, "text/csv"))],
        data={"layout": json.dumps({"latin_levels": levels})})
    assert response.status_code == 200, response.text
    body = response.json()
    rows = list(csv.DictReader(
        io.StringIO(files_by_name(body)["Results.csv"]["data"])))
    assert rows[0]["Latin Level"] == "Adv-2"
    assert body["summary"]["test_not_allowed"] == 1


def test_grade_accepts_a_pinned_threshold(client, tmp_path):
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([one_student()], tmp_path),
                          "application/pdf"))],
        data={"threshold": "0.35,0.15,0.35,0.15"})
    assert response.status_code == 200
    assert response.json()["summary"]["thresholds_note"] == "supplied"


def test_grade_rejects_a_bad_threshold(client, tmp_path):
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([one_student()], tmp_path),
                          "application/pdf"))],
        data={"threshold": "banana"})
    assert response.status_code == 422
    assert "not a number" in response.json()["error"]


def test_grade_can_annotate(client, tmp_path):
    response = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([one_student()], tmp_path),
                          "application/pdf")),
               ("key", ("Keys.csv", key_csv(), "text/csv"))],
        data={"annotate": "true"})
    assert response.status_code == 200
    pdfs = [item for item in response.json()["files"]
            if item["type"] == "application/pdf"]
    assert pdfs and pdfs[0]["encoding"] == "base64"


# --- regrading ------------------------------------------------------------


def test_regrade_rescores_without_scans(client, tmp_path):
    first = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([one_student()], tmp_path),
                          "application/pdf"))]).json()
    results = files_by_name(first)["Results.csv"]["data"]

    generous = key_csv([
        ("Latin Literature", "1001", "", ["A|B|C|D|E"] + cycled(0)[1:]),
        ("Reading Comprehension 1", "1002", "", cycled(1)),
        ("Mythology", "1003", "", cycled(2)),
    ])
    response = client.post(
        "/regrade",
        headers=auth(),
        files=[("results", ("Results.csv", results.encode(), "text/csv")),
               ("key", ("Keys.csv", generous, "text/csv"))])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["scored"] == 3
    rows = list(csv.DictReader(
        io.StringIO(files_by_name(body)["Results.csv"]["data"])))
    assert all(row["Points"] == str(QUESTIONS) for row in rows)


def test_regrade_refuses_unticked_review_rows(client, tmp_path):
    faint = one_student(faint={0: [7]}, faint_fraction=0.5)
    first = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([faint], tmp_path),
                          "application/pdf"))],
        data={"batch": "3"}).json()
    names = files_by_name(first)
    unclear = next(name for name in names if "Unclear" in name)

    response = client.post(
        "/regrade",
        headers=auth(),
        files=[("results", ("Results.csv", names["Results.csv"]["data"]
                            .encode(), "text/csv")),
               ("overrides", ("Unclear.csv", names[unclear]["data"].encode(),
                              "text/csv"))])
    assert response.status_code == 422
    assert "not been ticked" in response.json()["error"]


def test_regrade_applies_a_ticked_correction(client, tmp_path):
    faint = one_student(faint={0: [7]}, faint_fraction=0.5)
    first = client.post(
        "/grade",
        headers=auth(),
        files=[("scans", ("batch.pdf", batch_pdf([faint], tmp_path),
                          "application/pdf"))],
        data={"batch": "3"}).json()
    names = files_by_name(first)
    unclear_name = next(name for name in names if "Unclear" in name)

    rows = list(csv.reader(io.StringIO(names[unclear_name]["data"])))
    header = rows[0]
    for row in rows[1:]:
        row[header.index("B")] = "TRUE"
        row[header.index("Done")] = "TRUE"
    corrected = io.StringIO()
    csv.writer(corrected).writerows(rows)

    response = client.post(
        "/regrade",
        headers=auth(),
        files=[("results", ("Results.csv",
                            names["Results.csv"]["data"].encode(),
                            "text/csv")),
               ("overrides", ("Unclear.csv",
                              corrected.getvalue().encode(), "text/csv"))])
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["corrections_applied"] == 1
