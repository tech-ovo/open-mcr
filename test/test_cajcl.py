"""Tests for the CAJCL State Convention sheet: layout, batch splitting,
unclear-mark detection, annotation, and end-to-end grading.

The sheets these tests read are produced by the real PDF generator and then
bubbled in at the coordinates `sheet_layout` describes, so the tests fail if
the printed sheet and the reader's idea of the grid ever drift apart.
"""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

import synthetic_sheets as ss
from src import batching, grid_info, mark_quality, process_input
from src import sheet_layout as layout

REPO_ROOT = Path(__file__).parent.parent
QUESTIONS = layout.QUESTIONS_PER_TEST
TEST_IDS = ("1001", "1002", "1003")


def cycled(offset: int) -> list:
    """A deterministic answer sequence: A, B, C, D, E, A, ... rotated."""
    return [layout.OPTIONS[(i + offset) % len(layout.OPTIONS)]
            for i in range(QUESTIONS)]


def key_sheet() -> ss.SheetData:
    return ss.SheetData(student_id=layout.KEY_STUDENT_ID,
                        latin_level=None,
                        test_ids=TEST_IDS,
                        answers=[cycled(0), cycled(1), cycled(2)])


def read_csv(path: Path):
    with open(path, newline="") as handle:
        return list(csv.reader(handle))


def rows_by_form_code(path: Path):
    rows = read_csv(path)
    header = rows[0]
    return {row[header.index("Test Form Code")]: dict(zip(header, row))
            for row in rows[1:]}


def grade(tmp_path: Path, sheets, page_order=None, **options):
    """Render `sheets` into one batch PDF and run the reader over it."""
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    ss.write_batch(sheets, source / "batch.pdf", page_order=page_order)
    process_input.process_input([source / "batch.pdf"], output, False, False,
                                None, None, False, False, False,
                                grid_info.form_cajcl, None, None, **options)
    return output


# --- layout ---------------------------------------------------------------


def test_sheet_has_three_eighty_question_tests():
    variant = grid_info.form_cajcl
    assert variant.pages_per_sheet == 2
    assert variant.tests_per_sheet == 3
    assert variant.questions_per_column == 80
    assert [len(page.question_columns) for page in variant.page_variants] == [
        1, 2
    ]


def test_grid_questions_sit_where_the_sheet_prints_them():
    """Every question the reader looks for must be at a bubble the generator
    actually draws."""
    for page_index, page in enumerate(grid_info.form_cajcl.page_variants):
        for test_on_page, column in enumerate(page.question_columns):
            assert len(column) == QUESTIONS
            for question_index, info in enumerate(column):
                subcolumn = question_index // layout.ROWS_PER_SUBCOLUMN
                offset = question_index % layout.ROWS_PER_SUBCOLUMN
                expected_column = layout.PAGE_SUBCOLUMNS[page_index][
                    test_on_page][subcolumn]
                assert info.horizontal_start == expected_column
                assert info.vertical_start == layout.MCQ_FIRST_ROW + offset
                assert info.field_length == len(layout.OPTIONS)


def test_student_id_is_four_digits_in_the_same_place_on_both_pages():
    blocks = [
        page.fields[grid_info.Field.STUDENT_ID]
        for page in grid_info.form_cajcl.page_variants
    ]
    for block in blocks:
        assert block.num_fields == 4
        assert block.horizontal_start == layout.STUDENT_ID_COLUMN
        assert block.vertical_start == layout.ID_FIRST_BUBBLE_ROW
    assert grid_info.form_cajcl.key_student_id == "9999"


def test_latin_level_has_seven_named_options():
    block = grid_info.form_cajcl.page_variants[0].fields[
        grid_info.Field.LATIN_LEVEL]
    assert block.field_length == 7
    assert layout.LATIN_LEVELS == ("MS-1", "MS-2", "MS-3", "HS-1", "HS-2",
                                   "HS-3", "HS-ADV")
    assert grid_info.latin_level_name("0") == "MS-1"
    assert grid_info.latin_level_name("6") == "HS-ADV"
    assert grid_info.latin_level_name("") == ""
    assert grid_info.latin_level_name("9") == ""


def test_page_code_is_not_an_output_column():
    fields = grid_info.form_cajcl.output_fields
    assert grid_info.Field.PAGE_CODE not in fields
    assert grid_info.Field.STUDENT_ID in fields
    assert grid_info.Field.LATIN_LEVEL in fields


# --- unclear-mark analysis (no image processing) --------------------------


def levels(blank=0.05, mark=0.60, threshold=0.35):
    return mark_quality.PageLevels(threshold=threshold, blank_level=blank,
                                   mark_level=mark)


def test_clean_answer_is_not_flagged():
    assert mark_quality.review_answer([0.60, 0.05, 0.05, 0.05, 0.05],
                                      levels(), list(layout.OPTIONS)) is None


def test_unanswered_question_is_not_flagged():
    assert mark_quality.review_answer([0.05] * 5, levels(),
                                      list(layout.OPTIONS)) is None


def test_half_filled_answer_is_flagged():
    verdict = mark_quality.review_answer([0.33, 0.05, 0.05, 0.05, 0.05],
                                         levels(), list(layout.OPTIONS))
    assert verdict is not None
    assert verdict[0] == "borderline"
    assert "A" in verdict[1]


def test_two_filled_answers_are_flagged():
    verdict = mark_quality.review_answer([0.60, 0.60, 0.05, 0.05, 0.05],
                                         levels(), list(layout.OPTIONS))
    assert verdict is not None
    assert verdict[0] == "multiple"


def test_light_but_consistent_marks_are_not_flagged():
    """A student who presses lightly moves both reference levels together, so
    nothing should look ambiguous."""
    light = levels(blank=0.04, mark=0.18, threshold=0.11)
    assert mark_quality.review_answer([0.18, 0.04, 0.04, 0.04, 0.04], light,
                                      list(layout.OPTIONS)) is None


def test_required_choice_block_must_be_filled():
    verdict = mark_quality.review_choice_block([0.01] * 10,
                                               [str(d) for d in range(10)])
    assert verdict is not None and verdict[0] == "blank"
    assert mark_quality.review_choice_block(
        [0.01] * 10, [str(d) for d in range(10)], required=False) is None


def test_choice_block_with_two_similar_bubbles_is_flagged():
    fills = [0.55, 0.50] + [0.02] * 8
    verdict = mark_quality.review_choice_block(fills,
                                               [str(d) for d in range(10)])
    assert verdict is not None and verdict[0] == "multiple"


def test_estimate_levels_uses_medians():
    fills = [0.60, 0.62, 0.58] + [0.05, 0.04, 0.06, 0.05]
    estimated = mark_quality.estimate_levels(fills, 0.3)
    assert estimated.mark_level == pytest.approx(0.60)
    assert estimated.blank_level == pytest.approx(0.05)
    assert estimated.darkness(0.60) == pytest.approx(1.0)
    assert estimated.darkness(0.05) == pytest.approx(0.0)


# --- batch planning (page counts only) ------------------------------------


def test_plan_batch_pairs_pages_into_sheets(tmp_path):
    path = tmp_path / "two_sheets.pdf"
    ss.write_batch([ss.SheetData(student_id="0001"),
                    ss.SheetData(student_id="0002")], path)
    sheets = batching.plan_batch([path], 2)
    assert len(sheets) == 2
    assert [page.page_index for page in sheets[0].pages] == [0, 1]
    assert [page.page_index for page in sheets[1].pages] == [2, 3]
    assert [page.position_in_sheet for page in sheets[1].pages] == [0, 1]
    assert sheets[1].pages[0].label == "two_sheets.pdf (page 3)"


def test_plan_batch_rejects_a_file_with_an_odd_page_count(tmp_path):
    path = tmp_path / "one_page.pdf"
    ss.write_pdf(ss.render_sheet(ss.SheetData(student_id="0001"))[:1], path)
    with pytest.raises(batching.PageOrderError, match="whole number"):
        batching.plan_batch([path], 2)


def test_plan_batch_accepts_single_page_variants(tmp_path):
    path = tmp_path / "one_page.pdf"
    ss.write_pdf(ss.render_sheet(ss.SheetData(student_id="0001"))[:1], path)
    assert len(batching.plan_batch([path], 1)) == 1


# --- reading real sheets --------------------------------------------------


def test_reads_identity_and_answers_off_one_sheet(tmp_path):
    sheet = ss.SheetData(student_id="0427",
                         latin_level="HS-2",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    output = grade(tmp_path, [sheet])
    rows = rows_by_form_code(output / "results.csv")

    assert set(rows) == set(TEST_IDS)
    for offset, test_id in enumerate(TEST_IDS):
        row = rows[test_id]
        assert row["Student ID"] == "0427"
        assert row["Latin Level"] == "HS-2"
        answers = [row[f"Q{i + 1}"] for i in range(QUESTIONS)]
        assert answers == cycled(offset)


def test_a_batch_of_ten_sheets_is_split_into_ten(tmp_path):
    sheets = [key_sheet()] + [
        ss.SheetData(student_id=f"{index:04d}",
                     latin_level=layout.LATIN_LEVELS[index %
                                                     len(layout.LATIN_LEVELS)],
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)])
        for index in range(1, 10)
    ]
    output = grade(tmp_path, sheets)

    results = read_csv(output / "results.csv")
    # Nine students, three tests each; the tenth sheet is the answer key.
    assert len(results) == 1 + (9 * 3)
    header = results[0]
    ids = {row[header.index("Student ID")] for row in results[1:]}
    assert ids == {f"{index:04d}" for index in range(1, 10)}

    assert len(read_csv(output / "keys.csv")) == 1 + 3
    scores = read_csv(output / "scores.csv")
    points = scores[0].index("Total Points")
    assert {row[points] for row in scores[1:]} == {str(QUESTIONS)}


def test_out_of_order_pages_are_rejected(tmp_path):
    sheet = ss.SheetData(student_id="0427", latin_level="MS-1",
                         answers=[cycled(0), cycled(1), cycled(2)])
    with pytest.raises(batching.PageOrderError) as error:
        grade(tmp_path, [sheet], page_order=[1, 0])
    message = str(error.value)
    assert "back page" in message and "front page" in message


def test_interleaved_students_are_rejected(tmp_path):
    """Front of one student followed by the back of another."""
    first = ss.SheetData(student_id="0427", latin_level="MS-1",
                         answers=[cycled(0), cycled(1), cycled(2)])
    second = ss.SheetData(student_id="0031", latin_level="HS-1",
                          answers=[cycled(0), cycled(1), cycled(2)])
    with pytest.raises(batching.PageOrderError, match="Student ID"):
        grade(tmp_path, [first, second], page_order=[0, 3, 2, 1])


def test_a_missing_back_page_is_rejected(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    pages = ss.render_sheet(ss.SheetData(student_id="0427"))
    ss.write_pdf(pages[:1], source / "batch.pdf")
    with pytest.raises(batching.PageOrderError, match="whole number"):
        process_input.process_input([source / "batch.pdf"],
                                    tmp_path / "output", False, False, None,
                                    None, False, False, False,
                                    grid_info.form_cajcl, None, None)


# --- unclear marks, end to end --------------------------------------------


def test_a_half_erased_answer_stops_the_batch(tmp_path):
    sheet = ss.SheetData(student_id="0427",
                         latin_level="HS-3",
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [3, 7], 2: [50]},
                         faint_fraction=0.5)
    with pytest.raises(mark_quality.AmbiguousMarkError) as error:
        grade(tmp_path, [sheet])

    flagged = {issue.location for issue in error.value.issues}
    assert any(location.startswith("Q3 ") for location in flagged)
    assert any(location.startswith("Q7 ") for location in flagged)
    assert any(location.startswith("Q50 ") for location in flagged)

    report = read_csv(tmp_path / "output" / "review_required.csv")
    assert report[0] == mark_quality.REPORT_HEADER
    assert len(report) == 1 + len(error.value.issues)


def test_two_answers_on_one_question_stop_the_batch(tmp_path):
    # Q9 of test 2 is answered "E"; adding a second, different mark to the
    # same row is the double-mark case.
    assert cycled(1)[8] != "A"
    sheet = ss.SheetData(student_id="0427",
                         latin_level="HS-3",
                         answers=[cycled(0), cycled(1), cycled(2)],
                         extra_marks=[(1, 9, "A")])
    with pytest.raises(mark_quality.AmbiguousMarkError) as error:
        grade(tmp_path, [sheet])
    kinds = {issue.kind for issue in error.value.issues}
    assert "multiple" in kinds
    assert any(issue.location.startswith("Q9 ")
               for issue in error.value.issues)


def test_allow_unclear_marks_still_writes_the_report(tmp_path):
    sheet = ss.SheetData(student_id="0427",
                         latin_level="HS-3",
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [3]},
                         faint_fraction=0.5)
    output = grade(tmp_path, [sheet], allow_unclear_marks=True)
    assert (output / "review_required.csv").exists()
    assert (output / "results.csv").exists()


def test_clean_sheets_raise_nothing(tmp_path):
    sheets = [key_sheet(),
              ss.SheetData(student_id="0427", latin_level="HS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])]
    output = grade(tmp_path, sheets)
    assert not (output / "review_required.csv").exists()


# --- scoring and annotation -----------------------------------------------


def test_scores_are_computed_against_the_key(tmp_path):
    student = ss.SheetData(
        student_id="0427",
        latin_level="HS-2",
        test_ids=TEST_IDS,
        answers=[
            cycled(0),                                   # all correct
            ["A"] * 10 + cycled(1)[10:],                 # first 10 forced to A
            cycled(2)[:-5] + [""] * 5,                   # last 5 left blank
        ],
    )
    output = grade(tmp_path, [key_sheet(), student], allow_unclear_marks=True)
    scores = rows_by_form_code(output / "scores.csv")

    assert scores["1001"]["Total Points"] == str(QUESTIONS)
    # Of the ten forced A's, the two where A was the right answer still count.
    assert scores["1002"]["Total Points"] == str(QUESTIONS - 8)
    assert scores["1003"]["Total Points"] == str(QUESTIONS - 5)


def test_annotated_pdfs_are_written(tmp_path):
    sheets = [key_sheet(),
              ss.SheetData(student_id="0427", latin_level="HS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])]
    output = grade(tmp_path, sheets, annotate=True)
    annotated = output / "annotated" / "batch_annotated.pdf"
    assert annotated.exists()

    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(str(annotated))
    try:
        assert len(document) == 4
    finally:
        document.close()


# --- the command line -----------------------------------------------------


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.main", *[str(arg) for arg in args]],
        cwd=str(REPO_ROOT), capture_output=True, text=True)


def test_cli_grades_a_batch(tmp_path):
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    ss.write_batch([key_sheet(),
                    ss.SheetData(student_id="0427", latin_level="MS-3",
                                 test_ids=TEST_IDS,
                                 answers=[cycled(0), cycled(1), cycled(2)])],
                   source / "batch.pdf")
    result = run_cli(source, output, "--variant", "cajcl",
                     "--disable-timestamps")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / "scores.csv").exists()


def test_cli_exit_code_2_for_unclear_marks(tmp_path):
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    ss.write_batch([ss.SheetData(student_id="0427", latin_level="MS-3",
                                 test_ids=TEST_IDS,
                                 answers=[cycled(0), cycled(1), cycled(2)],
                                 faint={0: [12]}, faint_fraction=0.5)],
                   source / "batch.pdf")
    result = run_cli(source, output, "--variant", "cajcl",
                     "--disable-timestamps")
    assert result.returncode == 2, result.stdout + result.stderr
    assert (output / "review_required.csv").exists()


def test_cli_exit_code_3_for_out_of_order_pages(tmp_path):
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    ss.write_batch([ss.SheetData(student_id="0427", latin_level="MS-3",
                                 test_ids=TEST_IDS,
                                 answers=[cycled(0), cycled(1), cycled(2)])],
                   source / "batch.pdf", page_order=[1, 0])
    result = run_cli(source, output, "--variant", "cajcl",
                     "--disable-timestamps")
    assert result.returncode == 3, result.stdout + result.stderr


# --- printable margins ----------------------------------------------------


def test_nothing_is_printed_closer_to_an_edge_than_the_corner_marks():
    """The corner marks are the one thing that must survive printing, so they
    set the sheet's real margin requirement. Anything printed closer to an edge
    than they are would be clipped on a printer that still renders them."""
    from src import sheet_generation as generation

    from reportlab.pdfbase import pdfmetrics

    needed = layout.MARK_EDGE_CLEARANCE_IN
    _, title_baseline = layout.cell_to_inches(0, generation.TITLE_ROW)
    title_top = title_baseline + (11 * 0.662 / 72)

    descender = abs(pdfmetrics.getFont(generation.SERIF_ITALIC).face.descent)
    footer_bottom = generation._footer_baseline() - (
        descender / 1000 * generation.FOOTER_FONT_SIZE / 72)
    bar_left = layout.CORNER_INSET_IN + generation.COLLATION_BAR_INSET_IN
    bar_right = bar_left + generation.COLLATION_BAR_LENGTH_IN

    assert layout.PAGE_HEIGHT_IN - title_top >= needed
    assert footer_bottom >= needed - 1e-9
    assert generation.COLLATION_BAR_BOTTOM_IN >= needed - 1e-9
    assert bar_left >= needed
    assert layout.PAGE_WIDTH_IN - bar_right >= needed

    # And they should be flush with the bottom of the corner marks, not
    # floating above them.
    assert generation.COLLATION_BAR_BOTTOM_IN == pytest.approx(needed)
    assert footer_bottom == pytest.approx(needed)


def test_footer_and_collation_bar_stay_out_of_the_last_answer_row(tmp_path):
    """The reader averages a circle inside each cell that is wider than the
    cell is tall, so it reaches into neighbouring rows. Ink from the footer or
    the collation bar must not land inside the circle belonging to the bottom
    row of answers, or Q40 and Q80 would read as filled on a blank sheet."""
    from src import corner_finding, grid_reading, image_utils
    import numpy as np

    for page_index, raw in enumerate(ss._blank_pages()):
        prepared = image_utils.prepare_scan_for_processing(
            np.stack([raw] * 3, axis=-1))
        corners, basis = corner_finding.find_corner_marks(
            prepared,
            basis_width=layout.BASIS_WIDTH,
            basis_height=layout.BASIS_HEIGHT,
            l_mark_offset=layout.L_MARK_OFFSET_FRACTION)
        variant = grid_info.form_cajcl.variant_for_page(page_index)
        grid = grid_reading.Grid(corners, variant.horizontal_cells,
                                 variant.vertical_cells,
                                 image_utils.dilate(prepared),
                                 basis_transformer=basis)

        for column_index in range(len(variant.question_columns)):
            fills = grid_reading.get_answer_fill_percents_for_column(
                column_index, grid, variant)
            darkest_per_row = [max(question[0]) for question in fills]
            bottom_of_block = [darkest_per_row[39], darkest_per_row[79]]
            everything_else = [
                value for index, value in enumerate(darkest_per_row)
                if index not in (39, 79)
            ]
            # On a blank sheet every row is just the printed ring and letter,
            # so the bottom rows must not be measurably darker than the rest.
            assert max(bottom_of_block) <= max(everything_else) + 0.01
