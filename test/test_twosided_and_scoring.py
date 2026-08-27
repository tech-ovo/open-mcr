import pathlib
import pytest
from src import scoring, data_exporting, grid_info


def test_scoring_multi_marked_answers_incorrect():
    """Verify that if correct answer is 'A' and student marked multiple answers (e.g. [A|B] or F),
    they are marked incorrect (0 points)."""
    # Create key sheet: Form code "101", Q1="A", Q2="B", Q3="C"
    key_fields = [grid_info.Field.TEST_FORM_CODE, grid_info.Field.IMAGE_FILE]
    keys_sheet = data_exporting.OutputSheet(key_fields, 3)
    keys_sheet.add({grid_info.Field.TEST_FORM_CODE: "101", grid_info.Field.IMAGE_FILE: "key.pdf"}, ["A", "B", "C"])

    # Create student results
    student_fields = [grid_info.Field.STUDENT_ID, grid_info.Field.TEST_FORM_CODE, grid_info.Field.IMAGE_FILE]
    results_sheet = data_exporting.OutputSheet(student_fields, 3)
    
    # Student 1: marks "[A|B]" for Q1 (when correct is "A") -> should be 0
    results_sheet.add({grid_info.Field.STUDENT_ID: "00001", grid_info.Field.TEST_FORM_CODE: "101", grid_info.Field.IMAGE_FILE: "s1.pdf"}, ["[A|B]", "B", "C"])
    
    # Student 2: marks "F" for Q1 (multi converted to F) -> should be 0
    results_sheet.add({grid_info.Field.STUDENT_ID: "00002", grid_info.Field.TEST_FORM_CODE: "101", grid_info.Field.IMAGE_FILE: "s2.pdf"}, ["F", "B", "C"])
    
    # Student 3: marks "A" for Q1 -> should be 1
    results_sheet.add({grid_info.Field.STUDENT_ID: "00003", grid_info.Field.TEST_FORM_CODE: "101", grid_info.Field.IMAGE_FILE: "s3.pdf"}, ["A", "B", "C"])

    scored = scoring.score_results(results_sheet, keys_sheet, 3)

    # Check student 1 (Q1: [A|B] vs A -> 0; Q2: B vs B -> 1; Q3: C vs C -> 1; total points = 2)
    s1_row = scored.data[1]
    q1_idx = scored.data[0].index("Q1")
    points_idx = scored.data[0].index("Total Points")
    assert s1_row[q1_idx] == "0"
    assert s1_row[points_idx] == "2"

    # Check student 2 (Q1: F vs A -> 0; Q2: B vs B -> 1; Q3: C vs C -> 1; total points = 2)
    s2_row = scored.data[2]
    assert s2_row[q1_idx] == "0"
    assert s2_row[points_idx] == "2"

    # Check student 3 (Q1: A vs A -> 1; total points = 3)
    s3_row = scored.data[3]
    assert s3_row[q1_idx] == "1"
    assert s3_row[points_idx] == "3"


def test_two_sided_variant_page_indexing():
    """Verify that TwoSidedFormVariant cycles through page variants for multi-page PDFs."""
    variant = grid_info.form_two_sided_240q
    assert variant.variant_for_page(0) is grid_info.form_240q_page1
    assert variant.variant_for_page(1) is grid_info.form_240q_page2
    assert variant.variant_for_page(2) is grid_info.form_240q_page1
    assert variant.variant_for_page(3) is grid_info.form_240q_page2
    assert variant.variant_for_page(10) is grid_info.form_240q_page1
    assert variant.variant_for_page(11) is grid_info.form_240q_page2


def test_process_input_twosided_multipage(monkeypatch, tmp_path):
    """Verify that process_input correctly isolates student IDs across multiple two-sided tests in 1 file."""
    from src import process_input as pi
    import numpy as np

    # Mock 4 pages: Student 1 (p0, p1), Student 2 (p2, p3)
    dummy_pages = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(4)]
    monkeypatch.setattr("src.image_utils.load_image_pages", lambda path: dummy_pages)

    extracted_calls = []

    def mock_extract_page_results(image, image_label, form_variant, debug_path=None, multi_answers_as_f=False, carry_over_fields=None):
        extracted_calls.append({
            "image_label": image_label,
            "form_variant": form_variant,
            "carry_over_fields": dict(carry_over_fields) if carry_over_fields else None,
        })
        # Determine student ID based on page
        if "page 1" in image_label:
            sid = "11111"
            rows = [{grid_info.Field.STUDENT_ID: sid, grid_info.Field.TEST_FORM_CODE: "101", grid_info.Field.IMAGE_FILE: image_label}]
            answers = [["A"] * 80]
            return rows, answers, [], [], sid
        elif "page 2" in image_label:
            sid = carry_over_fields.get(grid_info.Field.STUDENT_ID, "") if carry_over_fields else ""
            rows = [
                {grid_info.Field.STUDENT_ID: sid, grid_info.Field.TEST_FORM_CODE: "102", grid_info.Field.IMAGE_FILE: f"{image_label} (col 1)"},
                {grid_info.Field.STUDENT_ID: sid, grid_info.Field.TEST_FORM_CODE: "103", grid_info.Field.IMAGE_FILE: f"{image_label} (col 2)"},
            ]
            answers = [["B"] * 80, ["C"] * 80]
            return rows, answers, [], [], ""
        elif "page 3" in image_label:
            sid = "22222"
            rows = [{grid_info.Field.STUDENT_ID: sid, grid_info.Field.TEST_FORM_CODE: "101", grid_info.Field.IMAGE_FILE: image_label}]
            answers = [["A"] * 80]
            return rows, answers, [], [], sid
        else: # page 4
            sid = carry_over_fields.get(grid_info.Field.STUDENT_ID, "") if carry_over_fields else ""
            rows = [
                {grid_info.Field.STUDENT_ID: sid, grid_info.Field.TEST_FORM_CODE: "102", grid_info.Field.IMAGE_FILE: f"{image_label} (col 1)"},
                {grid_info.Field.STUDENT_ID: sid, grid_info.Field.TEST_FORM_CODE: "103", grid_info.Field.IMAGE_FILE: f"{image_label} (col 2)"},
            ]
            answers = [["B"] * 80, ["C"] * 80]
            return rows, answers, [], [], ""

    monkeypatch.setattr("src.process_input._extract_page_results", mock_extract_page_results)

    test_file = tmp_path / "combined_students.pdf"
    test_file.touch()
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    pi.process_input(
        image_paths=[test_file],
        output_folder=out_dir,
        multi_answers_as_f=False,
        empty_answers_as_g=False,
        keys_file=None,
        arrangement_file=None,
        sort_results=False,
        output_mcta=False,
        debug_mode_on=False,
        form_variant=grid_info.form_two_sided_240q,
        progress_tracker=None,
        files_timestamp=None
    )

    # Check that variants alternated: p0=page1, p1=page2, p2=page1, p3=page2
    assert extracted_calls[0]["form_variant"] is grid_info.form_240q_page1
    assert extracted_calls[1]["form_variant"] is grid_info.form_240q_page2
    assert extracted_calls[2]["form_variant"] is grid_info.form_240q_page1
    assert extracted_calls[3]["form_variant"] is grid_info.form_240q_page2

    # Check carry-over: p0 None, p1 has 11111, p2 None (reset!), p3 has 22222
    assert extracted_calls[0]["carry_over_fields"] is None
    assert extracted_calls[1]["carry_over_fields"] == {grid_info.Field.STUDENT_ID: "11111"}
    assert extracted_calls[2]["carry_over_fields"] is None
    assert extracted_calls[3]["carry_over_fields"] == {grid_info.Field.STUDENT_ID: "22222"}

    # Check results CSV
    results_csv = out_dir / "results.csv"
    assert results_csv.exists()
    content = results_csv.read_text().splitlines()
    # Header + 6 student rows (3 rows per student)
    assert len(content) == 7
    # Rows for student 1
    assert "11111" in content[1]
    assert "11111" in content[2]
    assert "11111" in content[3]
    # Rows for student 2
    assert "22222" in content[4]
    assert "22222" in content[5]
    assert "22222" in content[6]

