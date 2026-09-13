"""Tests for the CAJCL State Convention pipeline.

The sheets these read are produced by the real PDF generator and then bubbled
in at the coordinates `sheet_layout` describes, so the tests fail if the
printed sheet and the reader's idea of the grid ever drift apart.
"""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

import synthetic_sheets as ss
from src import answer_key, console, grid_info, pipeline, reading
from src import review, sheet_layout as layout, thresholds as th

REPO_ROOT = Path(__file__).parent.parent
QUESTIONS = layout.QUESTIONS_PER_TEST
TEST_IDS = ("1001", "1002", "1003")


def cycled(offset: int) -> list:
    return [layout.OPTIONS[(i + offset) % len(layout.OPTIONS)]
            for i in range(QUESTIONS)]


def read_csv(path: Path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def write_key(path: Path, tests=None) -> Path:
    """Write a key file. It runs down the page: one row per field, one
    column per test."""
    tests = tests or [
        ("Latin Literature", "1001", "", cycled(0)),
        ("Reading Comprehension 1", "1002", "MS-1, MS-2, MS-3, HS-1, HS-2, HS-3", cycled(1)),
        ("Mythology", "1003", "", cycled(2)),
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([answer_key.NAME_ROW] + [t[0] for t in tests])
        writer.writerow([answer_key.TEST_ID_ROW] + [t[1] for t in tests])
        writer.writerow([answer_key.ALLOWED_ROW] + [t[2] for t in tests])
        for number in range(1, QUESTIONS + 1):
            writer.writerow([str(number)] +
                            [t[3][number - 1] for t in tests])
    return path


def grade(tmp_path: Path, sheets, batch=None, key=None, page_order=None,
          **options):
    scans = pipeline.resolve_batch_folder(tmp_path / "scans", batch)
    scans.mkdir(parents=True, exist_ok=True)
    ss.write_batch(sheets, scans / "batch.pdf", page_order=page_order)
    run_options = pipeline.RunOptions(input_folder=tmp_path / "scans",
                                      output_folder=tmp_path / "out",
                                      batch=batch, key_file=key, **options)
    result = pipeline.run(run_options, console.Console(enabled=False))
    return result, pipeline.resolve_batch_folder(tmp_path / "out", batch)


# --- layout ---------------------------------------------------------------


def test_student_id_is_five_digits_in_the_same_place_on_both_pages():
    for page in grid_info.form_cajcl.page_variants:
        block = page.fields[grid_info.Field.STUDENT_ID]
        assert block.num_fields == 5
        assert block.horizontal_start == layout.STUDENT_ID_COLUMN
        assert block.vertical_start == layout.ID_FIRST_BUBBLE_ROW


def test_test_ids_are_four_digits():
    for page in grid_info.form_cajcl.page_variants:
        block = page.fields[grid_info.Field.TEST_FORM_CODE]
        for instance in (block if isinstance(block, list) else [block]):
            assert instance.num_fields == 4


def test_grid_questions_sit_where_the_sheet_prints_them():
    for page_index, page in enumerate(grid_info.form_cajcl.page_variants):
        for test_on_page, column in enumerate(page.question_columns):
            assert len(column) == QUESTIONS
            for question_index, info in enumerate(column):
                subcolumn = question_index // layout.ROWS_PER_SUBCOLUMN
                offset = question_index % layout.ROWS_PER_SUBCOLUMN
                assert info.horizontal_start == layout.PAGE_SUBCOLUMNS[
                    page_index][test_on_page][subcolumn]
                assert info.vertical_start == layout.MCQ_FIRST_ROW + offset


# --- thresholds -----------------------------------------------------------


def test_split_ignores_class_imbalance():
    """Five percent filled must not drag the cutoff into the blank cluster,
    which is what textbook Otsu's class-size weighting does."""
    values = [0.05] * 950 + [0.60] * 50
    split = th.find_split(values)
    assert split is not None
    assert 0.05 < split.cutoff < 0.60
    assert split.filled_count == 50


def test_split_declines_when_there_is_nothing_to_split():
    assert th.find_split([0.05] * 500) is None
    assert th.find_split([0.05, 0.06]) is None


def test_calibrate_falls_back_and_says_so():
    thresholds, notes = th.calibrate([0.05] * 500, [0.05] * 500)
    assert notes and any("fell back" in note for note in notes)
    assert thresholds.answer_select > 0


def test_threshold_spec_round_trips():
    base = th.Thresholds(0.1, 0.05, 0.1, 0.05)
    parsed = th.parse_spec("0.42,0.08,0.3,0.06")
    assert parsed.is_complete
    assert parsed.apply_to(base) == th.Thresholds(0.42, 0.08, 0.3, 0.06)
    assert th.parse_spec(
        parsed.apply_to(base).to_spec()).apply_to(base) == parsed.apply_to(
            base)


def test_threshold_spec_accepts_two_or_one_number():
    base = th.Thresholds(0.1, 0.05, 0.1, 0.05)
    assert th.parse_spec("0.42,0.30").apply_to(base).answer_select == 0.42
    assert th.parse_spec("0.42").apply_to(base).metadata_select == 0.42


def test_threshold_spec_can_set_only_some_values():
    """Anything left out stays as calibrated."""
    calibrated = th.Thresholds(0.31, 0.19, 0.33, 0.20)
    pinned = th.parse_spec("answer=0.42")
    assert not pinned.is_complete
    assert pinned.apply_to(calibrated) == th.Thresholds(0.42, 0.19, 0.33, 0.20)

    blanks = th.parse_spec("0.42,0.19,,")
    assert blanks.apply_to(calibrated) == th.Thresholds(0.42, 0.19, 0.33, 0.20)

    named = th.parse_spec("metadata-review=0.05,metadata=0.4")
    assert named.apply_to(calibrated) == th.Thresholds(0.31, 0.19, 0.4, 0.05)


def test_threshold_spec_rejects_an_unknown_name():
    with pytest.raises(th.ThresholdError, match="not a threshold name"):
        th.parse_spec("banana=0.4")


@pytest.mark.parametrize("bad", ["nonsense", "1.4,0.1,0.3,0.1", "1,2,3,4,5"])
def test_bad_threshold_specs_are_rejected(bad):
    with pytest.raises(th.ThresholdError):
        th.parse_spec(bad)


def test_a_bubble_is_selected_purely_by_cutoff():
    """No 'darkest wins': two dark bubbles means two answers."""
    group = reading.BubbleGroup("1", ("A", "B", "C", "D", "E"),
                                (0.7, 0.65, 0.05, 0.05, 0.05),
                                ((0, 0, 1), ) * 5, True, False)
    thresholds = th.Thresholds(0.35, 0.05, 0.35, 0.05)
    assert group.selected(thresholds) == {"A", "B"}


# --- the answer key -------------------------------------------------------


def test_key_rejects_duplicate_test_ids(tmp_path):
    path = write_key(tmp_path / "k.csv", [
        ("One", "1001", "", cycled(0)),
        ("Two", "1001", "", cycled(1)),
    ])
    with pytest.raises(answer_key.AnswerKeyError, match="used twice"):
        answer_key.load(path)


def test_one_test_id_can_carry_a_key_per_level(tmp_path):
    """The same printed test, marked differently for different levels."""
    path = write_key(tmp_path / "k.csv", [
        ("Reading Comp (lower)", "1002", "MS-1, MS-2, MS-3", cycled(0)),
        ("Reading Comp (upper)", "1002", "HS-1, HS-2, HS-3, HS-Adv", cycled(1)),
    ])
    keys = answer_key.load(path)
    assert len(keys.variants("1002")) == 2

    lower = keys.lookup("1002", "MS-2")
    upper = keys.lookup("1002", "HS-3")
    assert lower is not None and upper is not None
    assert lower.name == "Reading Comp (lower)"
    assert upper.name == "Reading Comp (upper)"

    # A middle school sheet marked with the lower key scores full marks, and
    # the upper key would score it zero.
    marked = [{letter} for letter in cycled(0)]
    assert lower.score(marked)[0] == QUESTIONS
    assert upper.score(marked)[0] == 0


def test_key_rejects_two_tests_sharing_an_id_and_a_level(tmp_path):
    path = write_key(tmp_path / "k.csv", [
        ("Lower", "1002", "MS-1, MS-2, HS-1", cycled(0)),
        ("Upper", "1002", "HS-1, HS-2", cycled(1)),
    ])
    with pytest.raises(answer_key.AnswerKeyError, match="HS-1"):
        answer_key.load(path)


def test_key_rejects_a_shared_id_where_one_allows_every_level(tmp_path):
    """A blank Allowed cell means every level, so it overlaps anything."""
    path = write_key(tmp_path / "k.csv", [
        ("Everyone", "1002", "", cycled(0)),
        ("Upper", "1002", "HS-1", cycled(1)),
    ])
    with pytest.raises(answer_key.AnswerKeyError, match="used twice"):
        answer_key.load(path)


def test_key_refuses_the_old_excluded_row(tmp_path):
    """Reading it as Allowed would score exactly the wrong students."""
    path = tmp_path / "old.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([answer_key.NAME_ROW, "One"])
        writer.writerow([answer_key.TEST_ID_ROW, "1001"])
        writer.writerow(["Excluded", "HS-Adv"])
        for number in range(1, QUESTIONS + 1):
            writer.writerow([str(number), "A"])
    with pytest.raises(answer_key.AnswerKeyError, match="was replaced by"):
        answer_key.load(path)


def test_key_rejects_an_unknown_latin_level(tmp_path):
    path = write_key(tmp_path / "k.csv",
                     [("One", "1001", "HS-9", cycled(0))])
    with pytest.raises(answer_key.AnswerKeyError, match="not a Latin level"):
        answer_key.load(path)


def test_key_rejects_a_bad_option_letter(tmp_path):
    answers = cycled(0)
    answers[3] = "Z"
    path = write_key(tmp_path / "k.csv", [("One", "1001", "", answers)])
    with pytest.raises(answer_key.AnswerKeyError, match="not one of"):
        answer_key.load(path)


def test_several_letters_means_all_of_them(tmp_path):
    """'ABD' is one answer requiring all three bubbles, not three
    alternatives."""
    answers = cycled(0)
    answers[0] = "ABD"
    keys = answer_key.load(write_key(tmp_path / "k.csv",
                                     [("One", "1001", "", answers)]))
    key = keys["1001"]
    rest = [set(a) for a in answers[1:]]
    assert key.answers[0] == (frozenset("ABD"), )
    assert key.score([{"A", "B", "D"}] + rest)[0] == QUESTIONS
    assert key.score([{"A"}] + rest)[0] == QUESTIONS - 1
    assert key.score([{"A", "B"}] + rest)[0] == QUESTIONS - 1


def test_a_pipe_separates_alternatives(tmp_path):
    answers = cycled(0)
    answers[0] = "A|BD"
    keys = answer_key.load(write_key(tmp_path / "k.csv",
                                     [("One", "1001", "", answers)]))
    key = keys["1001"]
    rest = [set(a) for a in answers[1:]]
    assert key.answers[0] == (frozenset("A"), frozenset("BD"))
    assert key.score([{"A"}] + rest)[0] == QUESTIONS
    assert key.score([{"B", "D"}] + rest)[0] == QUESTIONS
    assert key.score([{"B"}] + rest)[0] == QUESTIONS - 1
    assert key.accepted_text(0) == "A|BD"


def test_key_rejects_a_stray_pipe(tmp_path):
    answers = cycled(0)
    answers[0] = "A|"
    with pytest.raises(answer_key.AnswerKeyError, match="empty alternative"):
        answer_key.load(write_key(tmp_path / "k.csv",
                                  [("One", "1001", "", answers)]))


def test_key_leaves_a_blank_cell_unscored(tmp_path):
    answers = cycled(0)
    answers[0] = ""
    keys = answer_key.load(write_key(tmp_path / "k.csv",
                                     [("One", "1001", "", answers)]))
    points, out_of, detail = keys["1001"].score(
        [set() for _ in range(QUESTIONS)])
    assert out_of == QUESTIONS - 1
    assert detail[0] == ""


def test_key_treats_x_as_a_retired_question(tmp_path):
    """An X says "do not score this" out loud, where a blank could be an
    unfinished cell."""
    answers = cycled(0)
    answers[3] = "X"
    answers[4] = "x"
    keys = answer_key.load(write_key(tmp_path / "k.csv",
                                     [("One", "1001", "", answers)]))
    points, out_of, detail = keys["1001"].score(
        [set() for _ in range(QUESTIONS)])
    assert out_of == QUESTIONS - 2
    assert detail[3] == "" and detail[4] == ""


def test_key_template_round_trips(tmp_path):
    path = answer_key.write_template(tmp_path / "template.csv")
    rows = read_csv(path)
    labels = [row[0] for row in rows]
    assert labels[:3] == list(answer_key.REQUIRED_ROWS)
    assert labels[3:] == [str(n) for n in range(1, QUESTIONS + 1)]
    # Blank answers everywhere means nothing is scored yet, not that
    # everything is wrong.
    keys = answer_key.load(path)
    assert all(key.score([set()] * QUESTIONS)[1] == 0 for key in keys)


# --- end to end -----------------------------------------------------------


def test_reads_identity_and_answers(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-2",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, _ = grade(tmp_path, [sheet])
    assert len(result.rows) == 3
    for offset, row in enumerate(sorted(result.rows, key=lambda r: r.test_id)):
        assert row.student_id == "04275"
        assert row.latin_level == "HS-2"
        assert row.test_id == TEST_IDS[offset]
        assert ["".join(sorted(m)) for m in row.marked] == cycled(offset)
    assert not result.unclear and not result.missing


def test_scores_against_the_key_and_honours_allowed_levels(tmp_path):
    sheets = [
        ss.SheetData(student_id="04275", latin_level="HS-2",
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)]),
        ss.SheetData(student_id="00031", latin_level="HS-Adv",
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)]),
    ]
    result, _ = grade(tmp_path, sheets, key=write_key(tmp_path / "k.csv"))
    by_student = {}
    for row in result.rows:
        by_student.setdefault(row.student_id, {})[row.test_id] = row

    assert by_student["04275"]["1001"].points == QUESTIONS
    assert by_student["04275"]["1002"].status == ""
    # HS-Adv is not among the levels allowed to take Reading Comprehension 1.
    assert by_student["00031"]["1002"].status == answer_key.TEST_NOT_ALLOWED
    assert by_student["00031"]["1002"].points is None
    assert by_student["00031"]["1003"].points == QUESTIONS


def test_students_are_scored_against_the_key_for_their_own_level(tmp_path):
    """One printed test, two keys. Each student meets only their own."""
    key = write_key(tmp_path / "k.csv", [
        ("Latin Literature", "1001", "", cycled(0)),
        ("Reading Comp (lower)", "1002", "MS-1, MS-2, MS-3", cycled(1)),
        ("Reading Comp (upper)", "1002", "HS-1, HS-2, HS-3, HS-Adv",
         cycled(3)),
        ("Mythology", "1003", "", cycled(2)),
    ])
    sheets = [
        # The middle school student answers the lower key; the high school
        # student answers the upper one. Both should score full marks.
        ss.SheetData(student_id="04275", latin_level="MS-2",
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)]),
        ss.SheetData(student_id="00031", latin_level="HS-2",
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(3), cycled(2)]),
    ]
    result, _ = grade(tmp_path, sheets, key=key)
    rows = {(row.student_id, row.test_id): row for row in result.rows}

    lower = rows[("04275", "1002")]
    upper = rows[("00031", "1002")]
    assert lower.points == QUESTIONS
    assert upper.points == QUESTIONS
    assert lower.test_name == "Reading Comp (lower)"
    assert upper.test_name == "Reading Comp (upper)"

    # The statistics keep the two apart, rather than pooling answers to
    # questions that are not the same questions.
    stats = read_csv(pipeline.resolve_batch_folder(tmp_path / "out", None) /
                     pipeline.STATS_FILENAME)
    names = {row[1] for row in stats[1:] if row[0] == "1002"}
    assert names == {"Reading Comp (lower)", "Reading Comp (upper)"}


def test_only_the_chosen_tests_are_graded(tmp_path):
    """--tests 2 reads the second test and leaves the others alone."""
    sheets = [ss.SheetData(student_id="04275", latin_level="HS-2",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])]
    result, folder = grade(tmp_path, sheets, key=write_key(tmp_path / "k.csv"),
                           tests=(2,))
    assert [row.test_id for row in result.rows] == ["1002"]
    assert result.rows[0].points == QUESTIONS

    # Nothing about the tests left out reaches the review sheets either.
    assert all(row.test_id == "1002" for row in result.unclear)
    assert not any("Test 1 ID" in row.field or "Test 3 ID" in row.field
                   for row in result.missing)

    rows = read_csv(folder / pipeline.RESULTS_FILENAME)
    assert len(rows) == 2          # header plus the one test


def test_parse_tests_normalises_the_spec():
    assert pipeline.parse_tests("2", 3) == (2,)
    assert pipeline.parse_tests("3,1", 3) == (1, 3)
    # Every test listed is the same as no restriction.
    assert pipeline.parse_tests("1,2,3", 3) is None
    assert pipeline.parse_tests("", 3) is None
    assert pipeline.parse_tests(None, 3) is None
    with pytest.raises(pipeline.BreakingError, match="not a test"):
        pipeline.parse_tests("4", 3)
    with pytest.raises(pipeline.BreakingError, match="not a test"):
        pipeline.parse_tests("two", 3)


def test_unknown_test_id_is_recorded_not_fatal(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=("9876", "1002", "1003"),
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, _ = grade(tmp_path, [sheet], key=write_key(tmp_path / "k.csv"))
    statuses = {row.test_id: row.status for row in result.rows}
    assert statuses["9876"] == answer_key.TEST_NOT_FOUND
    assert statuses["1003"] == ""


def test_a_faint_mark_goes_to_the_unclear_sheet(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7]}, faint_fraction=0.5)
    result, out = grade(tmp_path, [sheet], batch="3")
    assert result.unclear
    assert any(row.location == "7" for row in result.unclear)

    rows = read_csv(out / f"Batch 3{review.BATCH_SEPARATOR}Unclear.csv")
    assert rows[0] == review.unclear_header()
    assert rows[1][0] == "3"
    assert set(rows[1][6:11]) <= {"TRUE", "FALSE"}


def test_a_blank_question_is_not_an_error(tmp_path):
    answers = cycled(0)
    answers[10] = ""
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[answers, cycled(1), cycled(2)])
    result, _ = grade(tmp_path, [sheet])
    assert not result.missing
    assert not any(row.location == "11" for row in result.unclear)


def test_a_blank_latin_level_is_reported_as_missing(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level=None,
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, out = grade(tmp_path, [sheet], batch="1")
    assert any(row.field == "Latin level" for row in result.missing)
    rows = read_csv(out / f"Batch 1{review.BATCH_SEPARATOR}Missing.csv")
    assert rows[0] == list(review.MISSING_COLUMNS)
    assert "Test ID" not in rows[0]


def test_a_missing_student_id_is_asked_for_once_not_per_digit(tmp_path):
    """The whole field is wanted, and a sheet whose ID failed on both sides
    gives one row reading '1,2' rather than ten rows about digits."""
    sheet = ss.SheetData(student_id="", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, out = grade(tmp_path, [sheet], batch="1")
    student_rows = [row for row in result.missing
                    if row.field == "Student ID"]
    assert len(student_rows) == 1
    assert student_rows[0].page_text == "1,2"

    rows = read_csv(out / f"Batch 1{review.BATCH_SEPARATOR}Missing.csv")
    body = [row for row in rows[1:] if row[4] == "Student ID"]
    assert len(body) == 1 and body[0][2] == "1,2"


def test_out_of_order_pages_are_breaking(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         answers=[cycled(0), cycled(1), cycled(2)])
    with pytest.raises(pipeline.BreakingError, match="back page"):
        grade(tmp_path, [sheet], page_order=[1, 0])


def test_batch_folders_are_used(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    _, out = grade(tmp_path, [sheet], batch="7")
    assert out == tmp_path / "out" / "Batch 7"
    assert (out / pipeline.RESULTS_FILENAME).exists()
    assert (out / th.CALIBRATION_FILENAME).exists()
    assert (out / pipeline.STATS_FILENAME).exists()


def test_supplied_thresholds_are_used_and_reported(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, out = grade(tmp_path, [sheet], threshold_spec="0.35,0.05,0.35,0.05")
    assert result.supplied_thresholds
    assert "supplied on the command line" in (
        out / th.CALIBRATION_FILENAME).read_text(encoding="utf-8")


def test_annotated_pdf_is_written(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, out = grade(tmp_path, [sheet], key=write_key(tmp_path / "k.csv"),
                        annotate=True)
    assert result.annotated
    assert (out / pipeline.ANNOTATED_DIRNAME / "batch_annotated.pdf").exists()


# --- corrections and regrading -------------------------------------------


def _tick(path, columns=("B", "Done")):
    rows = read_csv(path)
    header = rows[0]
    for row in rows[1:]:
        for column in columns:
            if column in header:
                row[header.index(column)] = "TRUE"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path


def test_overrides_are_folded_in(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7]}, faint_fraction=0.5)
    result, out = grade(tmp_path, [sheet], batch="3")
    unclear_path = _tick(
        out / f"Batch 3{review.BATCH_SEPARATOR}Unclear.csv")

    reloaded = pipeline.read_results(out / pipeline.RESULTS_FILENAME)
    applied = pipeline.apply_overrides(
        reloaded, review.load([unclear_path]))
    assert applied >= 1
    test1 = next(row for row in reloaded if row.test_id == "1001")
    assert test1.marked[6] == {"B"}


def test_an_unticked_review_row_is_refused(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7]}, faint_fraction=0.5)
    _, out = grade(tmp_path, [sheet], batch="3")
    path = out / f"Batch 3{review.BATCH_SEPARATOR}Unclear.csv"
    with pytest.raises(review.NotFinishedError, match="not been ticked"):
        review.load([path])


def test_a_correction_that_changes_nothing_is_not_counted(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7]}, faint_fraction=0.5)
    _, out = grade(tmp_path, [sheet], batch="3")
    path = out / f"Batch 3{review.BATCH_SEPARATOR}Unclear.csv"
    # Tick only Done, leaving every option unticked - which is exactly what
    # the reader already decided, so nothing has changed.
    _tick(path, columns=("Done", ))
    rows = pipeline.read_results(out / pipeline.RESULTS_FILENAME)
    assert pipeline.apply_overrides(rows, review.load([path])) == 0


def test_regrade_rescoring_needs_no_scans(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    _, out = grade(tmp_path, [sheet])
    results_path = out / pipeline.RESULTS_FILENAME

    # A key that now accepts any single letter for question 1 of every test.
    # Note "A|B|C|D|E", not "ABCDE" - the latter would demand all five.
    generous = [(name, test_id, "", ["A|B|C|D|E"] + cycled(offset)[1:])
                for offset, (name, test_id) in enumerate(
                    [("A", "1001"), ("B", "1002"), ("C", "1003")])]
    keys = answer_key.load(write_key(tmp_path / "k2.csv", generous))

    rows = pipeline.read_results(results_path)
    pipeline.score_rows(rows, keys)
    assert all(row.points == QUESTIONS for row in rows)


def test_results_round_trip_through_csv(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, out = grade(tmp_path, [sheet])
    reloaded = pipeline.read_results(out / pipeline.RESULTS_FILENAME)
    assert [r.student_id for r in reloaded] == [
        r.student_id for r in result.rows]
    assert [r.marked for r in reloaded] == [r.marked for r in result.rows]


# --- the command line -----------------------------------------------------


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.main", *[str(arg) for arg in args]],
        cwd=str(REPO_ROOT), capture_output=True, text=True)


def test_cli_grades_a_batch(tmp_path):
    scans = tmp_path / "scans" / "Batch 2"
    scans.mkdir(parents=True)
    ss.write_batch([
        ss.SheetData(student_id="04275", latin_level="MS-3",
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)])
    ], scans / "batch.pdf")
    key = write_key(tmp_path / "k.csv")
    result = run_cli(tmp_path / "scans", tmp_path / "out", "--batch", "2",
                     "--key", key)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All exams processed and saved" in result.stdout
    assert (tmp_path / "out" / "Batch 2" / "Results.csv").exists()


def test_cli_key_template(tmp_path):
    target = tmp_path / "Keys.csv"
    assert run_cli("--key-template", target).returncode == 0
    assert target.exists()


def test_cli_stops_on_a_broken_key(tmp_path):
    scans = tmp_path / "scans"
    scans.mkdir()
    ss.write_batch([ss.SheetData(student_id="04275", latin_level="MS-3")],
                   scans / "batch.pdf")
    bad = write_key(tmp_path / "bad.csv", [("One", "1001", "", cycled(0)),
                                           ("Two", "1001", "", cycled(1))])
    result = run_cli(scans, tmp_path / "out", "--key", bad)
    assert result.returncode == 1
    assert "used twice" in result.stderr
    assert not (tmp_path / "out" / "Results.csv").exists()
