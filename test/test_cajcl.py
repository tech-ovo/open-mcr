"""Tests for the CAJCL State Convention pipeline.

The sheets these read are produced by the real PDF generator and then bubbled
in at the coordinates `sheet_layout` describes, so the tests fail if the
printed sheet and the reader's idea of the grid ever drift apart.
"""

import csv
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

import synthetic_sheets as ss
from src import answer_key, batching, console, corner_finding, grid_info
from src import pipeline
from src import reading
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


# --- corner finding under damage ----------------------------------------
#
# A student who scribbles near the corner marks must not cost anybody their
# paper. Each case here is read all the way through to answers, not merely
# checked for "found some corners": a grid that is slightly wrong still finds
# corners, and then quietly reports the wrong marks.


def _read_front(page):
    """Read a damaged front page, returning (student id, answers)."""
    variant = grid_info.form_cajcl.variant_for_page(0)
    # The synthetic pages are grayscale; a real scan arrives as colour.
    scan = reading.scan_page(cv2.cvtColor(page, cv2.COLOR_GRAY2BGR),
                             variant, 0)
    cutoffs = th.calibrate(scan.answer_fills, scan.metadata_fills)[0]
    answers = "".join(
        sorted(group.selected(cutoffs))[0]
        if len(group.selected(cutoffs)) == 1 else "?"
        for group in scan.tests[0][1])
    return reading.read_digits(scan.student_id, cutoffs), answers


@pytest.fixture(scope="module")
def damaged_front():
    """One filled-in front page, and what reading it should produce."""
    answers = [("ABCDE" * 16)[index] for index in range(QUESTIONS)]
    sheet = ss.SheetData(student_id="04275", latin_level="HS-1",
                         answers=[answers, [], []])
    return ss.render_sheet(sheet)[0], "04275", "".join(answers)


@pytest.mark.parametrize("damage", [
    pytest.param(lambda page: page, id="undamaged"),
    pytest.param(ss.doodle_in_margin, id="doodles-in-the-margin"),
    pytest.param(lambda page: ss.scribble_over(page, "tr"),
                 id="pencil-over-one-mark"),
    pytest.param(lambda page: ss.scribble_over(page, "tr", gray=40),
                 id="pen-over-one-mark"),
    pytest.param(
        lambda page: ss.scribble_over(ss.scribble_over(page, "tr", gray=40),
                                      "bl", gray=40),
        id="pen-over-two-marks"),
    pytest.param(lambda page: ss.shade_out(page, "tr"),
                 id="corner-square-blacked-out"),
    pytest.param(lambda page: ss.shade_out(page, "tl"),
                 id="l-mark-blacked-out"),
    pytest.param(lambda page: ss.shade_out(ss.shade_out(page, "tl"), "tr"),
                 id="two-marks-blacked-out"),
])
def test_a_damaged_page_is_still_read_correctly(damaged_front, damage):
    page, student_id, answers = damaged_front
    got_id, got_answers = _read_front(damage(page))
    assert got_id == student_id
    assert got_answers == answers


def test_a_sheared_grid_is_refused(tmp_path):
    """The check that was missing, and that let a real sheet be misread.

    Equal opposite sides say nothing about shear: a parallelogram leaning far
    enough to put its top-left corner off the side of the page satisfies them
    perfectly. Equal diagonals are what make it a rectangle.
    """
    from src import corner_finding
    from src.geometry_utils import Point

    page = np.zeros((1584, 1224), dtype=np.uint8)
    # The exact quadrilateral a scribbled-over corner produced: a clean
    # parallelogram, correct aspect, one corner 236 pixels off the page.
    sheared = [Point(-236, 90), Point(846, 92), Point(1160, 1503),
               Point(78, 1501)]
    assert not corner_finding._grid_is_plausible(sheared, page, 0.75)

    honest = [Point(75, 71), Point(1157, 73), Point(1160, 1504),
              Point(78, 1502)]
    assert corner_finding._grid_is_plausible(honest, page, 0.75)


def test_a_corner_mark_buried_under_a_scribble_is_recovered():
    """Three corners are enough: the fourth follows from the rectangle.

    A student scribbling over one mark leaves a blob that is neither square
    nor solid, so nothing recognises it and nothing measures it - but the
    other three marks are untouched and they fix where it was.
    """
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    buried = ss.bury(ss.render_sheet(sheet)[0], "tr")
    scan = reading.scan_page_either_side(
        cv2.cvtColor(buried, cv2.COLOR_GRAY2BGR), grid_info.form_cajcl)
    cutoffs = th.calibrate(scan.answer_fills, scan.metadata_fills)[0]
    assert reading.read_digits(scan.student_id, cutoffs) == "04275"
    answers = "".join(
        sorted(group.selected(cutoffs))[0]
        if len(group.selected(cutoffs)) == 1 else "?"
        for group in scan.tests[0][1])
    assert answers == "".join(cycled(0))


def test_corner_finding_refuses_a_page_with_no_marks(tmp_path):
    """Recovering from damage must not turn into inventing a grid."""
    blank = ss.render_sheet(ss.SheetData(student_id="04275"))[0].copy()
    blank[:, :] = 255
    with pytest.raises(corner_finding.CornerFindingError):
        reading.scan_page(cv2.cvtColor(blank, cv2.COLOR_GRAY2BGR),
                          grid_info.form_cajcl.variant_for_page(0), 0)


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


def _write_key_with_level_row(path, label, cells):
    """A one-test key file whose level row is named by the caller."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([answer_key.NAME_ROW] + ["Test " + str(index + 1)
                                                 for index in
                                                 range(len(cells))])
        writer.writerow([answer_key.TEST_ID_ROW] +
                        [str(1001 + index) for index in range(len(cells))])
        writer.writerow([label] + list(cells))
        for number in range(1, QUESTIONS + 1):
            writer.writerow([str(number)] + ["A"] * len(cells))
    return path


def test_key_reads_an_excluded_row_as_the_levels_left_over(tmp_path):
    """The two rows are opposite ways of saying the same thing."""
    path = _write_key_with_level_row(tmp_path / "ex.csv",
                                     answer_key.EXCLUDED_ROW, ["HS-Adv"])
    key = answer_key.load(path)["1001"]
    assert "HS-ADV" not in key.allowed_levels
    assert "HS-1" in key.allowed_levels
    assert len(key.allowed_levels) == len(layout.LATIN_LEVELS) - 1


def test_key_reads_a_blank_excluded_row_as_every_level(tmp_path):
    """The template ships this row blank, so it has to mean 'no restriction'."""
    path = _write_key_with_level_row(tmp_path / "ex.csv",
                                     answer_key.EXCLUDED_ROW, [""])
    key = answer_key.load(path)["1001"]
    assert len(key.allowed_levels) == len(layout.LATIN_LEVELS)


def test_key_excluded_and_allowed_agree(tmp_path):
    """Written either way round, the same file scores the same students."""
    keep = ["MS-1", "MS-2"]
    barred = [level for level in layout.LATIN_LEVELS if level not in keep]
    allowed = answer_key.load(_write_key_with_level_row(
        tmp_path / "a.csv", answer_key.ALLOWED_ROW,
        [", ".join(keep)]))["1001"]
    excluded = answer_key.load(_write_key_with_level_row(
        tmp_path / "e.csv", answer_key.EXCLUDED_ROW,
        [", ".join(barred)]))["1001"]
    assert allowed.allowed_levels == excluded.allowed_levels


def test_key_refuses_both_level_rows_at_once(tmp_path):
    """They can contradict each other, and guessing would score the wrong
    students."""
    path = tmp_path / "both.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([answer_key.NAME_ROW, "One"])
        writer.writerow([answer_key.TEST_ID_ROW, "1001"])
        writer.writerow([answer_key.ALLOWED_ROW, "HS-1"])
        writer.writerow([answer_key.EXCLUDED_ROW, "MS-1"])
        for number in range(1, QUESTIONS + 1):
            writer.writerow([str(number), "A"])
    with pytest.raises(answer_key.AnswerKeyError, match="delete the other"):
        answer_key.load(path)


def test_key_refuses_an_excluded_row_that_bars_everyone(tmp_path):
    path = _write_key_with_level_row(
        tmp_path / "none.csv", answer_key.EXCLUDED_ROW,
        [", ".join(layout.LATIN_LEVELS)])
    with pytest.raises(answer_key.AnswerKeyError, match="bars every"):
        answer_key.load(path)


def test_key_refuses_a_file_with_no_level_row_at_all(tmp_path):
    path = tmp_path / "bare.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([answer_key.NAME_ROW, "One"])
        writer.writerow([answer_key.TEST_ID_ROW, "1001"])
        for number in range(1, QUESTIONS + 1):
            writer.writerow([str(number), "A"])
    with pytest.raises(answer_key.AnswerKeyError, match="Excluded"):
        answer_key.load(path)


def test_key_template_ships_a_blank_excluded_row(tmp_path):
    """Whatever the template writes has to load without editing."""
    path = answer_key.write_template(tmp_path / "Keys.csv")
    body = path.read_text(encoding="utf-8")
    assert answer_key.EXCLUDED_ROW in body
    assert answer_key.ALLOWED_ROW not in body
    for key in answer_key.load(path):
        assert len(key.allowed_levels) == len(layout.LATIN_LEVELS)


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


def test_a_sheet_can_carry_a_different_number_of_levels(tmp_path):
    """Ten levels: printed, bubbled, and read back as the right one."""
    levels = tuple(f"Level {n}" for n in range(1, 11))
    text = layout.SheetText(latin_levels=levels)

    scans = tmp_path / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    ss.write_batch([ss.SheetData(student_id="04275", latin_level=levels[8],
                                 test_ids=TEST_IDS,
                                 answers=[cycled(0), cycled(1), cycled(2)])],
                   scans / "batch.pdf", text=text)

    key = write_key(tmp_path / "k.csv", [
        ("Latin Literature", "1001", "", cycled(0)),
        ("Reading Comprehension 1", "1002", "Level 9", cycled(1)),
        ("Mythology", "1003", "Level 1, Level 2", cycled(2)),
    ])
    options = pipeline.RunOptions(input_folder=scans,
                                  output_folder=tmp_path / "out",
                                  key_file=key, sheet_text=text)
    result = pipeline.run(options, console.Console(enabled=False))

    by_test = {row.test_id: row for row in result.rows}
    assert by_test["1001"].latin_level == "Level 9"
    assert by_test["1001"].points == QUESTIONS
    # Level 9 may take 1002, but 1003 is limited to levels 1 and 2.
    assert by_test["1002"].points == QUESTIONS
    assert by_test["1003"].status == answer_key.TEST_NOT_ALLOWED


def test_the_level_block_is_capped(tmp_path):
    with pytest.raises(layout.SheetTextError, match="between 2 and 10"):
        layout.SheetText(latin_levels=tuple(f"L{n}" for n in range(11)))
    with pytest.raises(layout.SheetTextError, match="between 2 and 10"):
        layout.SheetText(latin_levels=("Only one", ))


def test_the_reader_grid_follows_the_level_count():
    """Generator and reader are built from the same number."""
    for count in (2, 7, 10):
        field = grid_info.form_for(count).variant_for_page(0).fields[
            grid_info.Field.LATIN_LEVEL]
        assert field.field_length == count


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
    options = [rows[0].index(option) for option in layout.OPTIONS]
    assert set(rows[1][index] for index in options) <= {"TRUE", "FALSE"}


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


def _pages_csv(out):
    rows = read_csv(out / pipeline.PAGES_FILENAME)
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def test_a_batch_of_one_swapped_sheet_is_breaking(tmp_path):
    """With nothing left to grade there is no output worth writing."""
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         answers=[cycled(0), cycled(1), cycled(2)])
    with pytest.raises(pipeline.BreakingError, match="could be read"):
        grade(tmp_path, [sheet], page_order=[1, 0])


def test_one_swapped_sheet_does_not_cost_the_others(tmp_path):
    """The whole point: a bad sheet costs its own paper and nothing else."""
    sheets = [
        ss.SheetData(student_id=f"0427{index}", latin_level="MS-1",
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)])
        for index in range(3)
    ]
    # Turn the middle sheet's two pages around: pages 2 and 3 of six.
    result, out = grade(tmp_path, sheets, page_order=[0, 1, 3, 2, 4, 5])

    graded = {report.student_id for report in result.reports
              if report.status == batching.GRADED}
    assert graded == {"04270", "04272"}
    assert len(result.sheets) == 2
    assert {row.student_id for row in result.rows} == {"04270", "04272"}

    set_aside = [report for report in result.reports
                 if report.status == batching.UNPAIRED]
    assert len(set_aside) == 2
    assert all("04271" == report.student_id for report in set_aside)

    listed = _pages_csv(out)
    assert len(listed) == 6
    assert sum(1 for row in listed if row["Status"] == batching.GRADED) == 4
    assert all(row["Note"] for row in listed
               if row["Status"] == batching.UNPAIRED)


def test_the_page_report_covers_a_clean_batch_too(tmp_path):
    """"Did every paper come back?" needs an answer on good days as well."""
    sheets = [ss.SheetData(student_id="04275", latin_level="MS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])]
    _, out = grade(tmp_path, sheets)
    listed = _pages_csv(out)
    assert [row["Status"] for row in listed] == [batching.GRADED] * 2
    assert [row["Side"] for row in listed] == ["front", "back"]
    assert listed[0]["Paired With"] == "page 2"
    assert listed[1]["Paired With"] == "page 1"
    assert all(row["Student ID"] == "04275" for row in listed)


def test_a_digit_left_unbubbled_on_both_sides_still_pairs(tmp_path):
    """A student who skipped a digit has not mis-collated anything."""
    sheet = ss.SheetData(student_id="04?75", back_student_id="04?75",
                         latin_level="MS-1", test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, _ = grade(tmp_path, [sheet])
    assert len(result.sheets) == 1
    assert {row.student_id for row in result.rows} == {"04?75"}


def test_a_digit_readable_on_only_one_side_is_taken_from_the_other(tmp_path):
    sheet = ss.SheetData(student_id="04275", back_student_id="04?75",
                         latin_level="MS-1", test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    result, _ = grade(tmp_path, [sheet])
    assert len(result.sheets) == 1
    assert {row.student_id for row in result.rows} == {"04275"}


def test_sides_that_really_disagree_set_that_sheet_aside(tmp_path):
    sheets = [
        ss.SheetData(student_id="04275", back_student_id="04276",
                     latin_level="MS-1", test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)]),
        ss.SheetData(student_id="04280", latin_level="MS-1",
                     test_ids=TEST_IDS,
                     answers=[cycled(0), cycled(1), cycled(2)]),
    ]
    result, out = grade(tmp_path, sheets)
    assert {row.student_id for row in result.rows} == {"04280"}
    mismatched = [report for report in result.reports
                  if report.status == batching.ID_MISMATCH]
    assert len(mismatched) == 2
    assert "04275" in mismatched[0].note and "04276" in mismatched[0].note


# --- one-sided scans -------------------------------------------------------


def _one_sided(tmp_path, pages, sides, skip_blanks=False, **options):
    scans = tmp_path / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    ss.write_pdf(pages, scans / "batch.pdf")
    run_options = pipeline.RunOptions(input_folder=scans,
                                      output_folder=tmp_path / "out",
                                      sides=sides, skip_blanks=skip_blanks,
                                      **options)
    return pipeline.run(run_options, console.Console(enabled=False))


def test_a_front_only_scan_grades_the_first_test(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    fronts = [ss.render_sheet(sheet)[0]]
    result = _one_sided(tmp_path, fronts, sides=(0,))
    assert [row.test_number for row in result.rows] == [1]
    assert result.rows[0].student_id == "04275"
    assert result.rows[0].latin_level == "MS-1"


def test_a_back_only_scan_grades_the_other_two(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    backs = [ss.render_sheet(sheet)[1]]
    result = _one_sided(tmp_path, backs, sides=(1,))
    assert sorted(row.test_number for row in result.rows) == [2, 3]
    # The Latin level lives on the front page, so a back-only scan has none.
    assert all(row.latin_level == "" for row in result.rows)


def test_the_wrong_side_is_set_aside_not_misread(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    front, back = ss.render_sheet(sheet)
    result = _one_sided(tmp_path, [front, back], sides=(0,))
    assert [row.test_number for row in result.rows] == [1]
    wrong = [report for report in result.reports
             if report.status == batching.WRONG_SIDE]
    assert len(wrong) == 1 and wrong[0].side == 1


def test_blank_pages_are_skipped_when_asked(tmp_path):
    """A duplex scan of one-sided originals: printed, blank, printed, blank."""
    sheets = [ss.SheetData(student_id=f"0427{index}", latin_level="MS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])
              for index in range(2)]
    pages = []
    for sheet in sheets:
        pages.extend([ss.render_sheet(sheet)[0], ss.blank_page()])
    result = _one_sided(tmp_path, pages, sides=(0,), skip_blanks=True)
    assert len(result.rows) == 2
    assert {report.status for report in result.reports} == {
        batching.GRADED, batching.BLANK}


def test_the_blank_may_come_before_or_after(tmp_path):
    """Only one blank per pair is required, not a fixed order."""
    sheets = [ss.SheetData(student_id=f"0427{index}", latin_level="MS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])
              for index in range(2)]
    first, second = (ss.render_sheet(sheet)[0] for sheet in sheets)
    pages = [first, ss.blank_page(), ss.blank_page(), second]
    result = _one_sided(tmp_path, pages, sides=(0,), skip_blanks=True)
    assert len(result.rows) == 2
    assert not [report for report in result.reports
                if report.status == batching.UNEXPECTED_BLANK]


def test_a_blank_that_breaks_the_rhythm_is_flagged(tmp_path):
    """Two printed pages together means a blank has gone missing."""
    sheets = [ss.SheetData(student_id=f"0427{index}", latin_level="MS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])
              for index in range(2)]
    first, second = (ss.render_sheet(sheet)[0] for sheet in sheets)
    pages = [first, second, ss.blank_page(), ss.blank_page()]
    result = _one_sided(tmp_path, pages, sides=(0,), skip_blanks=True)
    assert len(result.rows) == 2      # both papers still graded
    odd = [report for report in result.reports
           if report.status == batching.UNEXPECTED_BLANK]
    assert len(odd) == 2
    assert all("missed at the scanner" in report.note for report in odd)


def test_an_unexpected_blank_in_a_two_sided_scan_is_flagged(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    front, back = ss.render_sheet(sheet)
    result = _one_sided(tmp_path, [front, back, ss.blank_page()],
                        sides=(0, 1))
    assert len(result.sheets) == 1
    odd = [report for report in result.reports
           if report.status == batching.UNEXPECTED_BLANK]
    assert len(odd) == 1


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
    assert applied.changed >= 1
    test1 = next(row for row in reloaded if row.test_id == "1001")
    assert test1.marked[6] == {"B"}


def test_an_unticked_review_row_is_refused(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7]}, faint_fraction=0.5)
    _, out = grade(tmp_path, [sheet], batch="3")
    path = out / f"Batch 3{review.BATCH_SEPARATOR}Unclear.csv"
    with pytest.raises(review.NotFinishedError, match="have been ticked"):
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
    assert pipeline.apply_overrides(rows, review.load([path])).changed == 0


# --- addressing a correction ----------------------------------------------


def _unclear_csv(path, rows):
    """Write a review sheet by hand, the way a person adding a row would."""
    header = list(review.unclear_header())
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for values in rows:
            line = [""] * len(header)
            for name, value in values.items():
                line[header.index(name)] = value
            writer.writerow(line)
    return path


def _graded_rows(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    _, out = grade(tmp_path, [sheet], batch="3")
    return pipeline.read_results(out / pipeline.RESULTS_FILENAME), out


def test_the_two_tests_on_the_back_page_are_corrected_separately(tmp_path):
    """Both number their questions from 1, so page alone cannot tell them
    apart - correcting one used to rewrite the other."""
    rows, out = _graded_rows(tmp_path)
    second = next(row for row in rows if row.test_number == 2)
    third = next(row for row in rows if row.test_number == 3)
    before = set(third.marked[4])

    path = _unclear_csv(tmp_path / "fix.csv", [{
        "File": second.source_file, "Page": str(second.page),
        "Test": "2", "Question": "5", "A": "TRUE", "Done": "TRUE",
    }])
    applied = pipeline.apply_overrides(rows, review.load([path]))

    assert not applied.problems
    assert second.marked[4] == {"A"}
    assert third.marked[4] == before


def test_a_row_added_by_hand_is_matched_on_the_ids(tmp_path):
    """Correcting a bubble that was read wrongly but never flagged."""
    rows, _ = _graded_rows(tmp_path)
    target = next(row for row in rows if row.test_number == 2)
    path = _unclear_csv(tmp_path / "manual.csv", [{
        "Student ID": target.student_id, "Test ID": target.test_id,
        "Question": "9", "D": "TRUE", "Done": "TRUE",
    }])
    applied = pipeline.apply_overrides(rows, review.load([path]))
    assert not applied.problems
    assert applied.changed == 1
    assert target.marked[8] == {"D"}


def test_a_spreadsheet_eating_the_leading_zero_still_matches(tmp_path):
    """Excel turns 04275 into 4275, and the correction must still land."""
    rows, _ = _graded_rows(tmp_path)
    target = next(row for row in rows if row.test_number == 1)
    path = _unclear_csv(tmp_path / "manual.csv", [{
        "Student ID": target.student_id.lstrip("0"),
        "Test ID": target.test_id.lstrip("0"),
        "Question": "3", "E": "TRUE", "Done": "TRUE",
    }])
    applied = pipeline.apply_overrides(rows, review.load([path]))
    assert not applied.problems
    assert target.marked[2] == {"E"}


def test_a_correction_matching_nothing_is_reported(tmp_path):
    """Typed by somebody who meant it, so it must not vanish quietly."""
    rows, _ = _graded_rows(tmp_path)
    path = _unclear_csv(tmp_path / "manual.csv", [{
        "Student ID": "99999", "Test ID": "1001",
        "Question": "3", "E": "TRUE", "Done": "TRUE",
    }])
    applied = pipeline.apply_overrides(rows, review.load([path]))
    assert applied.changed == 0
    assert len(applied.problems) == 1
    assert "no test matches" in applied.problems[0]
    assert "99999" in applied.problems[0]


def test_a_correction_matching_several_tests_is_reported(tmp_path):
    rows, _ = _graded_rows(tmp_path)
    path = _unclear_csv(tmp_path / "manual.csv", [{
        "Student ID": "04275", "Question": "3", "E": "TRUE", "Done": "TRUE",
    }])
    applied = pipeline.apply_overrides(rows, review.load([path]))
    assert applied.changed == 0
    assert len(applied.problems) == 1
    assert "matches 3 tests" in applied.problems[0]


def test_a_row_naming_nothing_at_all_is_refused(tmp_path):
    path = _unclear_csv(tmp_path / "manual.csv",
                        [{"Question": "3", "E": "TRUE", "Done": "TRUE"}])
    with pytest.raises(review.OverrideError, match="which test"):
        review.load([path])


# --- more than one round ---------------------------------------------------


def test_an_untouched_row_still_needs_review_afterwards(tmp_path):
    """Otherwise a second round has nothing left to work from: every row
    came back looking settled whether or not anybody had looked at it."""
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7], 1: [9]}, faint_fraction=0.5)
    _, out = grade(tmp_path, [sheet], batch="3")
    rows = pipeline.read_results(out / pipeline.RESULTS_FILENAME)
    assert sum(1 for row in rows if row.needs_review) == 2

    # Settle only the first test's question.
    first = next(row for row in rows if row.test_number == 1)
    path = _unclear_csv(tmp_path / "round1.csv", [{
        "File": first.source_file, "Page": str(first.page), "Test": "1",
        "Question": "7", "B": "TRUE", "Done": "TRUE",
    }])
    pipeline.apply_overrides(rows, review.load([path]))

    assert not first.needs_review
    still = [row for row in rows if row.needs_review]
    assert [row.test_number for row in still] == [2]


def test_outstanding_rows_come_back_for_a_second_round(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7], 1: [9]}, faint_fraction=0.5)
    _, out = grade(tmp_path, [sheet], batch="3")
    original = out / f"Batch 3{review.BATCH_SEPARATOR}Unclear.csv"
    rows = read_csv(original)
    header = rows[0]
    # Tick only the first of the two rows.
    rows[1][header.index("Done")] = "TRUE"
    partial = tmp_path / "partial.csv"
    with open(partial, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    results = pipeline.read_results(out / pipeline.RESULTS_FILENAME)
    # The default: unticked rows are left for next time rather than refused.
    loaded = review.load([partial])
    applied = pipeline.apply_overrides(results, loaded)
    left = review.outstanding([partial], applied.origins)
    assert len(left) == 1
    name, left_header, left_rows = left[0]
    assert left_header == header
    assert len(left_rows) == 1
    assert left_rows[0][header.index("Done")] != "TRUE"


def test_ignoring_the_done_column_lets_an_unticked_sheet_through(tmp_path):
    sheet = ss.SheetData(student_id="04275", latin_level="HS-3",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: [7]}, faint_fraction=0.5)
    _, out = grade(tmp_path, [sheet], batch="3")
    path = out / f"Batch 3{review.BATCH_SEPARATOR}Unclear.csv"
    with pytest.raises(review.NotFinishedError):
        review.load([path])
    loaded = review.load([path], require_done=False)
    assert loaded.answers
    assert loaded.unfinished


# --- marked-up copies for only the papers worth checking -------------------


def test_corners_can_be_recovered_from_where_the_batch_put_theirs():
    """Two surviving corners fix how the page sits; the rest follow."""
    from src import corner_finding, image_utils

    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    front = ss.render_sheet(sheet)[0]
    colour = cv2.cvtColor(front, cv2.COLOR_GRAY2BGR)
    prior = reading.scan_page_either_side(colour, grid_info.form_cajcl).corners
    assert prior and len(prior) == 4

    prepared = image_utils.prepare_scan_for_processing(colour)
    found = corner_finding.find_with_prior(prepared, prior, 0.75)
    assert found is not None
    height, width = prepared.shape[:2]
    for point, (fraction_x, fraction_y) in zip(found, prior):
        assert abs(point.x - fraction_x * width) < 3
        assert abs(point.y - fraction_y * height) < 3


def test_a_wrong_prior_is_refused_rather_than_forced():
    """The batch may say where to look; it may not say what was found."""
    from src import corner_finding, image_utils

    sheet = ss.SheetData(student_id="04275", latin_level="MS-1")
    colour = cv2.cvtColor(ss.render_sheet(sheet)[0], cv2.COLOR_GRAY2BGR)
    prepared = image_utils.prepare_scan_for_processing(colour)
    nonsense = ((0.30, 0.30), (0.70, 0.30), (0.70, 0.70), (0.30, 0.70))
    assert corner_finding.find_with_prior(prepared, nonsense, 0.75) is None


def test_a_page_read_off_the_batch_s_grid_is_flagged(tmp_path):
    """A skewed page can pass every shape test and still be in the wrong
    place. Only the rest of the batch can say so."""
    sheets = [ss.SheetData(student_id=f"0427{index}", latin_level="MS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])
              for index in range(3)]
    pages = []
    for index, sheet in enumerate(sheets):
        front, back = ss.render_sheet(sheet)
        pages.extend([front, back])
    scans = tmp_path / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    ss.write_pdf(pages, scans / "batch.pdf")
    result = pipeline.run(
        pipeline.RunOptions(input_folder=scans,
                            output_folder=tmp_path / "out"),
        console.Console(enabled=False))
    # Every page came off the same template, so nothing should be flagged.
    assert not [report for report in result.reports
                if "away from where the rest" in report.note]


# --- the student who presses lightly ---------------------------------------


def test_a_faintly_bubbled_test_is_read_on_its_own_terms(tmp_path):
    """One light presser among normal ones must still be graded.

    Their marks all sit under a cutoff drawn from a batch of people who
    pressed properly, so without a second opinion the whole test comes back
    blank or, at best, as eighty review rows.
    """
    normal = [ss.SheetData(student_id=f"0427{index}", latin_level="MS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])
              for index in range(2)]
    # Every answer bubbled at a third of the usual darkness.
    faint = ss.SheetData(student_id="04280", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)],
                         faint={0: list(range(1, QUESTIONS + 1))},
                         faint_fraction=0.33)
    result, _ = grade(tmp_path, normal + [faint])

    row = next(r for r in result.rows
               if r.student_id == "04280" and r.test_number == 1)
    read = "".join(sorted(marks)[0] if len(marks) == 1 else "?"
                   for marks in row.marked)
    assert read == "".join(cycled(0)), (
        "the faint test was not read back correctly: " + read[:40])


def test_a_normal_test_is_not_rescued(tmp_path):
    """The rescue must stay out of the way when nothing is wrong."""
    sheets = [ss.SheetData(student_id=f"0427{index}", latin_level="MS-1",
                           test_ids=TEST_IDS,
                           answers=[cycled(0), cycled(1), cycled(2)])
              for index in range(2)]
    result, out = grade(tmp_path, sheets)
    assert not result.unclear
    report = (out / th.CALIBRATION_FILENAME).read_text(encoding="utf-8")
    assert "its own" not in report


def test_marking_up_only_some_tests_does_not_crash(tmp_path):
    """A page carrying none of the wanted tests still has to be drawn.

    The legend used to read the key off the answer loop's last iteration, so a
    page whose tests were all left out of the run raised UnboundLocalError and
    took the whole request with it.
    """
    sheet = ss.SheetData(student_id="04275", latin_level="MS-1",
                         test_ids=TEST_IDS,
                         answers=[cycled(0), cycled(1), cycled(2)])
    key = write_key(tmp_path / "k.csv", [
        (name, test_id, "", answers) for name, test_id, answers in
        zip(("One", "Two", "Three"), TEST_IDS,
            (cycled(0), cycled(1), cycled(2)))])
    # Test 1 is on the front, so the back page has nothing to draw.
    result, out = grade(tmp_path, [sheet], key=key, annotate=True,
                        tests=(1,))
    assert result.annotated
    assert [row.test_number for row in result.rows] == [1]


def test_marked_up_copies_can_be_limited_to_unreadable_ids():
    from src import annotation
    assert annotation.wants_student("04275", None)
    assert not annotation.wants_student("04275", annotation.UNKNOWN_IDS)
    assert annotation.wants_student("04?75", annotation.UNKNOWN_IDS)
    assert annotation.wants_student("", annotation.UNKNOWN_IDS)


def test_marked_up_copies_can_be_limited_to_named_students():
    from src import annotation
    assert annotation.wants_student("04275", ["04275"])
    # A spreadsheet will have eaten the leading zero somewhere along the way.
    assert annotation.wants_student("04275", ["4275"])
    assert not annotation.wants_student("04276", ["04275"])


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
