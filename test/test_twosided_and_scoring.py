"""Scoring rules and the two-sided reading flow, independent of image data."""

import pytest

from src import batching, data_exporting, grid_info, process_input, scoring


def test_scoring_multi_marked_answers_incorrect():
    """A question with more than one answer marked scores zero, whether it was
    recorded as '[A|B]' or collapsed to 'F'."""
    key_fields = [grid_info.Field.TEST_FORM_CODE, grid_info.Field.IMAGE_FILE]
    keys_sheet = data_exporting.OutputSheet(key_fields, 3)
    keys_sheet.add(
        {
            grid_info.Field.TEST_FORM_CODE: "101",
            grid_info.Field.IMAGE_FILE: "key.pdf"
        }, ["A", "B", "C"])

    student_fields = [
        grid_info.Field.STUDENT_ID, grid_info.Field.TEST_FORM_CODE,
        grid_info.Field.IMAGE_FILE
    ]
    results_sheet = data_exporting.OutputSheet(student_fields, 3)
    for student_id, first_answer in (("0001", "[A|B]"), ("0002", "F"),
                                     ("0003", "A")):
        results_sheet.add(
            {
                grid_info.Field.STUDENT_ID: student_id,
                grid_info.Field.TEST_FORM_CODE: "101",
                grid_info.Field.IMAGE_FILE: f"{student_id}.pdf"
            }, [first_answer, "B", "C"])

    scored = scoring.score_results(results_sheet, keys_sheet, 3)
    q1 = scored.data[0].index("Q1")
    points = scored.data[0].index("Total Points")

    assert [row[q1] for row in scored.data[1:]] == ["0", "0", "1"]
    assert [row[points] for row in scored.data[1:]] == ["2", "2", "3"]


def test_two_sided_variant_alternates_page_layouts():
    variant = grid_info.form_cajcl
    front, back = variant.page_variants
    assert variant.variant_for_page(0) is front
    assert variant.variant_for_page(1) is back
    assert variant.variant_for_page(2) is front
    assert variant.variant_for_page(11) is back


def test_digit_blocks_keep_column_positions():
    """An unreadable digit must not silently shift the others along."""
    assert process_input._values_to_digits([[0], [4], [2], [7]]) == "0427"
    assert process_input._values_to_digits([[0], [], [2], [7]]) == "0?27"
    assert process_input._values_to_digits([[], [], [], []]) == "????"


def test_page_side_check_reports_the_offending_page():
    page = batching.PageRef(path=__import__("pathlib").Path("batch.pdf"),
                            page_index=2,
                            pages_in_file=4,
                            position_in_sheet=0,
                            sheet_index=1)
    # A back page where a front page was expected.
    with pytest.raises(batching.PageOrderError) as error:
        batching.check_page_side(page, 1, ("front page", "back page"))
    assert "batch.pdf (page 3)" in str(error.value)
    assert "sheet 2" in str(error.value)

    # An unreadable page code is also an error, not a silent pass.
    with pytest.raises(batching.PageOrderError):
        batching.check_page_side(page, None, ("front page", "back page"))

    # The right side in the right place passes.
    batching.check_page_side(page, 0, ("front page", "back page"))


def test_student_id_check_only_fires_when_both_sides_were_read():
    page = batching.PageRef(path=__import__("pathlib").Path("batch.pdf"),
                            page_index=1,
                            pages_in_file=2,
                            position_in_sheet=1,
                            sheet_index=0)
    batching.check_student_id(page, "0427", "0427")
    batching.check_student_id(page, "", "0427")
    batching.check_student_id(page, "0427", "")
    with pytest.raises(batching.PageOrderError, match="0031"):
        batching.check_student_id(page, "0427", "0031")
