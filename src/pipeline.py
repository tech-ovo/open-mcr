"""Reading a batch of scans and writing the results.

The whole run in one place: plan the pages, calibrate a cutoff per input file,
read every page, apply any human corrections, score against the key, and write
the output files.
"""

import csv
import dataclasses
import pathlib
import typing as tp

from . import annotation
from . import answer_key as keys_module
from . import batching
from . import console as console_module
from . import corner_finding
from . import grid_info as grid_i
from . import image_utils
from . import reading
from . import review as review_module
from . import sheet_layout as layout
from . import thresholds as th

RESULTS_FILENAME = "Results.csv"
STATS_FILENAME = "Question Stats.csv"
PAGES_FILENAME = "Pages.csv"
ANNOTATED_DIRNAME = "Annotated"

#: How many pages of each file are read to calibrate its cutoffs. One front
#: and one back page, so both layouts are represented.
CALIBRATION_PAGES = 2

#: How many pages must have found their own corner marks before the batch is
#: allowed to tell a page that could not where to look. One good page is not a
#: consensus.
MINIMUM_PRIOR_PAGES = 3

#: How far a page's grid may sit from where the rest of the batch put theirs,
#: as a fraction of the page, before it is worth mentioning. Pages off the
#: same scanner agree to a fraction of a percent, so this is a long way out -
#: far enough that it is nearly always a real misalignment rather than an
#: ordinary crooked feed.
CORNER_DEVIATION_NOTE = 0.03


def _corner_deviation(corners, median) -> float:
    """The largest distance between a page's corners and the batch's."""
    return max(
        ((point[0] - other[0]) ** 2 + (point[1] - other[1]) ** 2) ** 0.5
        for point, other in zip(corners, median))


def _median_corners(found: tp.Sequence[tp.Sequence[tp.Tuple[float, float]]]
                    ) -> tp.List[tp.Tuple[float, float]]:
    """Where this batch puts its four corner marks, as page fractions.

    The median rather than the mean: one page read against a wrong grid should
    not drag the answer, and with a couple of hundred pages there is no
    shortage of samples.
    """
    import statistics

    return [
        (statistics.median(page[index][0] for page in found),
         statistics.median(page[index][1] for page in found))
        for index in range(4)
    ]


NEWLINE = chr(10)

STATUS_OK = ""
STATUS_NEEDS_REVIEW = "NEEDS REVIEW"


class BreakingError(RuntimeError):
    """A problem that means no output should be produced at all."""


@dataclasses.dataclass
class TestRow:
    """One student's attempt at one test - one row of Results.csv."""

    batch: str
    source_file: str
    page: int
    student_id: str
    latin_level: str
    test_id: str
    marked: tp.List[tp.Set[str]]
    needs_review: bool = False

    test_name: str = ""
    test_number: int = 0
    """Which test on the sheet this row is, counted from 1 across both sides.
    Not written to Results.csv; it is here so the marked-up scans can put each
    test's own key on its own column."""
    status: str = STATUS_OK
    key: tp.Optional[keys_module.Key] = None
    """The key this row was scored against. Not written to Results.csv; it is
    here so the question statistics can group by the key actually used, which
    matters once one Test ID can carry several."""
    points: tp.Optional[int] = None
    out_of: tp.Optional[int] = None
    per_question: tp.List[str] = dataclasses.field(default_factory=list)

    @property
    def score(self) -> tp.Optional[float]:
        if self.points is None or not self.out_of:
            return None
        return round(self.points * 100 / self.out_of, 2)


def _marks_to_text(marked: tp.Set[str]) -> str:
    return "".join(sorted(marked))


def _text_to_marks(text: str) -> tp.Set[str]:
    return {ch for ch in text.strip().upper() if ch in layout.OPTIONS}


# --- scoring -------------------------------------------------------------


def score_rows(rows: tp.Sequence[TestRow],
               keys: tp.Optional[keys_module.KeySet]) -> None:
    """Fill in each row's score, in place."""
    for row in rows:
        row.key = None
        if keys is None:
            row.status = STATUS_NEEDS_REVIEW if row.needs_review else STATUS_OK
            continue
        variants = keys.variants(row.test_id)
        if not variants:
            row.status = keys_module.TEST_NOT_FOUND
            continue
        key = keys.lookup(row.test_id, row.latin_level)
        if key is None:
            # Several keys share this ID and the level that would choose
            # between them is unreadable, or no key takes this level at all.
            row.test_name = variants[0].name if len(variants) == 1 else ""
            row.status = (keys_module.LEVEL_NEEDED
                          if keys.is_ambiguous(row.test_id, row.latin_level)
                          else keys_module.TEST_NOT_ALLOWED)
            continue
        row.key = key
        row.test_name = key.name
        points, out_of, detail = key.score(row.marked)
        row.points, row.out_of, row.per_question = points, out_of, detail
        row.status = STATUS_NEEDS_REVIEW if row.needs_review else STATUS_OK


# --- output files --------------------------------------------------------


def results_header(questions: int) -> tp.List[str]:
    # "Test" numbers the tests across the whole sheet, from 1. Without it a
    # row on the back page cannot be told from the one beside it, since both
    # number their questions from 1.
    return ([
        "Batch", "File", "Page", "Test", "Student ID", "Latin Level",
        "Test ID", "Test Name", "Status", "Points", "Out Of", "Score (%)"
    ] + [str(number) for number in range(1, questions + 1)])


def write_results(path: pathlib.Path, rows: tp.Sequence[TestRow],
                  questions: int) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(results_header(questions))
        for row in rows:
            score = row.score
            writer.writerow([
                row.batch, row.source_file, row.page, row.test_number,
                row.student_id, row.latin_level, row.test_id, row.test_name,
                row.status,
                "" if row.points is None else row.points,
                "" if row.out_of is None else row.out_of,
                "" if score is None else f"{score:.2f}",
            ] + [
                _marks_to_text(row.marked[index]) if index < len(row.marked)
                else "" for index in range(questions)
            ])
    return path


def read_results(path: pathlib.Path,
                 questions: int = layout.QUESTIONS_PER_TEST
                 ) -> tp.List[TestRow]:
    """Read a Results.csv back, so a batch can be re-scored without touching
    the scans again."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise BreakingError(f"Could not read '{path}': {error}")
    table = [row for row in csv.reader(text.splitlines())
             if any(cell.strip() for cell in row)]
    if not table:
        raise BreakingError(f"'{path.name}' is empty.")
    header = [cell.strip() for cell in table[0]]
    lookup = {name: index for index, name in enumerate(header)}
    for required in ("File", "Page", "Student ID", "Test ID"):
        if required not in lookup:
            raise BreakingError(
                f"'{path.name}' has no '{required}' column, so it is not a "
                "results file this can re-score.")

    def cell(row: tp.List[str], name: str) -> str:
        index = lookup.get(name)
        return row[index].strip() if index is not None and index < len(row) \
            else ""

    rows: tp.List[TestRow] = []
    for line in table[1:]:
        page_text = cell(line, "Page")
        rows.append(
            TestRow(batch=cell(line, "Batch"),
                    source_file=cell(line, "File"),
                    page=int(float(page_text)) if page_text else 0,
                    test_number=int(float(cell(line, "Test") or 0)),
                    student_id=cell(line, "Student ID"),
                    latin_level=cell(line, "Latin Level"),
                    test_id=cell(line, "Test ID"),
                    marked=[
                        _text_to_marks(cell(line, str(number)))
                        for number in range(1, questions + 1)
                    ],
                    needs_review=cell(line, "Status") == STATUS_NEEDS_REVIEW))
    return rows


def write_question_stats(path: pathlib.Path, rows: tp.Sequence[TestRow],
                         keys: tp.Optional[keys_module.KeySet],
                         questions: int) -> pathlib.Path:
    """How each question behaved, across everyone who took it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Grouped by the key each row was scored against, not by Test ID: two keys
    # may share an ID, and their questions are different questions.
    by_key: tp.Dict[tp.Tuple[str, str], tp.List[TestRow]] = {}
    order: tp.List[tp.Tuple[str, str]] = []
    for row in rows:
        if row.status in (keys_module.TEST_NOT_FOUND,
                          keys_module.TEST_NOT_ALLOWED,
                          keys_module.LEVEL_NEEDED):
            continue
        handle_key = (row.test_id, row.key.name if row.key else "")
        if handle_key not in by_key:
            by_key[handle_key] = []
            order.append(handle_key)
        by_key[handle_key].append(row)

    header = (["Test ID", "Test Name", "Question", "Key"] +
              list(layout.OPTIONS) + ["Correct", "Incorrect", "Blank"])
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for handle_key in sorted(order):
            test_id, name = handle_key
            group = by_key[handle_key]
            key = group[0].key
            for index in range(questions):
                counts = {option: 0 for option in layout.OPTIONS}
                blank = correct = incorrect = 0
                scored = key is not None and index < len(key.answers) \
                    and bool(key.answers[index])
                for row in group:
                    marked = row.marked[index] if index < len(row.marked) \
                        else set()
                    for option in marked:
                        if option in counts:
                            counts[option] += 1
                    if not marked:
                        blank += 1
                    elif scored:
                        if frozenset(marked) in key.answers[index]:
                            correct += 1
                        else:
                            incorrect += 1
                writer.writerow([
                    test_id, name, index + 1,
                    key.accepted_text(index) if key else ""
                ] + [counts[option] for option in layout.OPTIONS] + [
                    correct if scored else "",
                    incorrect if scored else "",
                    blank,
                ])
    return path


def write_page_report(path: pathlib.Path,
                      reports: tp.Sequence[batching.PageReport],
                      batch: str) -> pathlib.Path:
    """One row per scanned page: what it was, and what became of it.

    Written on every run, including the ones where nothing went wrong, so that
    "did every paper I put in come out again?" has an answer that does not
    depend on anybody having noticed a warning.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Batch", "File", "Page", "Side", "Student ID",
                         "Test IDs", "Sheet", "Paired With", "Status",
                         "Note"])
        for report in reports:
            partner = reports[report.partner] if report.partner is not None \
                else None
            writer.writerow([
                batch,
                report.path.name,
                report.page_index + 1,
                report.side_name,
                report.student_id,
                " ".join(report.test_ids),
                "" if report.sheet_index is None else report.sheet_index + 1,
                "" if partner is None else f"page {partner.page_index + 1}",
                report.status,
                report.note,
            ])
    return path


# --- applying human corrections ------------------------------------------


def _same_id(given: str, actual: str) -> bool:
    """Do two readings of an identifier agree?

    Lenient in the two ways that matter. A spreadsheet strips the leading zero
    from ``04275``, so the shorter one is padded before comparing; and ``?``
    stands for a digit the reader could not call, so it matches anything.
    """
    given = given.strip()
    if not given:
        return True
    if len(given) < len(actual):
        given = given.zfill(len(actual))
    elif len(actual) < len(given):
        actual = actual.zfill(len(given))
    return batching.ids_compatible(given, actual)


def _matches(row: TestRow, where: review_module.Address) -> bool:
    """Could this correction be about this test?"""
    if where.source_file and where.source_file != row.source_file:
        return False
    if where.page is not None and where.page != row.page:
        return False
    if where.test_number is not None and where.test_number != row.test_number:
        return False
    if not _same_id(where.test_id, row.test_id):
        return False
    return _same_id(where.student_id, row.student_id)


def _field_test_number(field: str) -> tp.Optional[int]:
    """The test a "Test 2 ID" row is about, or None for a whole-sheet field."""
    parts = field.split()
    if len(parts) == 3 and parts[0].lower() == "test" \
            and parts[2].lower() == "id" and parts[1].isdigit():
        return int(parts[1])
    return None


class AppliedOverrides(tp.NamedTuple):
    changed: int
    """How many readings a person actually overturned. A correction agreeing
    with what the reader already had is not counted."""

    settled: tp.Set[tp.Tuple[str, int, int, str]]
    """The (file, page, test, question) of every answer row that was applied,
    so the ones still outstanding can be worked out."""

    settled_values: tp.Set[tp.Tuple[str, str]]
    """The (file, field) of every missing-field row that was filled in."""

    problems: tp.List[str]
    """Corrections that could not be matched to exactly one test, in the words
    they should be shown in."""

    origins: tp.Set[str]
    """Which review-sheet lines were actually used. Everything else in the
    uploaded sheets is still outstanding, which is what makes a second round
    of corrections possible."""


def apply_overrides(rows: tp.Sequence[TestRow],
                    overrides: review_module.Overrides) -> AppliedOverrides:
    """Fold corrected review sheets into the rows.

    Each correction is matched to the rows it could be about by whichever
    identifying columns it filled in. An answer has to name exactly one test,
    since it changes one question; a whole-field correction such as a Student
    ID applies to every test on that sheet. Anything matching nothing, or
    matching several tests when it should match one, is reported rather than
    guessed at or dropped.
    """
    changed = 0
    settled: tp.Set[tp.Tuple[str, int, int, str]] = set()
    settled_values: tp.Set[tp.Tuple[str, str]] = set()
    problems: tp.List[str] = []
    origins: tp.Set[str] = set()
    touched: tp.Set[int] = set()

    for correction in overrides.answers:
        number = correction.question.strip()
        if not number.isdigit():
            continue        # a metadata row; the Missing sheet handles those
        index = int(number) - 1
        candidates = [row for row in rows if _matches(row, correction.where)
                      and index < len(row.marked)]
        if not candidates:
            problems.append(
                f"{correction.origin}: no test matches "
                f"{correction.where.describe()}, so question {number} was not "
                "changed.")
            continue
        if len(candidates) > 1:
            problems.append(
                f"{correction.origin}: {correction.where.describe()} matches "
                f"{len(candidates)} tests, so it is not clear which "
                f"question {number} is meant. Add a Test or Test ID column.")
            continue
        row = candidates[0]
        if set(correction.chosen) != row.marked[index]:
            row.marked[index] = set(correction.chosen)
            changed += 1
        settled.add((row.source_file, row.page, row.test_number, number))
        origins.add(correction.origin)
        touched.add(id(row))

    for correction in overrides.values:
        wanted_test = _field_test_number(correction.field)
        candidates = [
            row for row in rows
            if _matches(row, correction.where)
            and (wanted_test is None or row.test_number == wanted_test)
        ]
        if not candidates:
            problems.append(
                f"{correction.origin}: no test matches "
                f"{correction.where.describe()}, so '{correction.field}' was "
                "not changed.")
            continue
        value = correction.value.strip()
        for row in candidates:
            if not value:
                continue
            if correction.field.lower().startswith("student"):
                if value != row.student_id:
                    row.student_id = value
                    changed += 1
            elif correction.field.lower().startswith("latin"):
                if value != row.latin_level:
                    row.latin_level = value
                    changed += 1
            elif wanted_test is not None:
                if value != row.test_id:
                    row.test_id = value
                    changed += 1
            touched.add(id(row))
        settled_values.add((candidates[0].source_file, correction.field))
        origins.add(correction.origin)

    # Only the rows a person actually settled stop needing review. Clearing
    # the flag on every row, as this used to, made a second round of
    # corrections impossible: nothing was left marked as still doubtful.
    for row in rows:
        if id(row) in touched:
            row.needs_review = False

    return AppliedOverrides(changed=changed, settled=settled,
                            settled_values=settled_values, problems=problems,
                            origins=origins)


# --- the run -------------------------------------------------------------


class RunOptions(tp.NamedTuple):
    input_folder: pathlib.Path
    output_folder: pathlib.Path
    batch: tp.Optional[str] = None
    key_file: tp.Optional[pathlib.Path] = None
    override_files: tp.Sequence[pathlib.Path] = ()
    threshold_spec: tp.Optional[str] = None
    annotate: bool = False
    annotate_students: tp.Optional[tp.Union[str, tp.Tuple[str, ...]]] = None
    """Which papers get a marked-up copy: None for all of them,
    ``annotation.UNKNOWN_IDS`` for the ones whose Student ID could not be read
    in full, or the Student IDs to include."""

    debug: bool = False
    sheet_text: tp.Optional[layout.SheetText] = None
    """The wording printed on the sheets being read. Only the Latin level
    names matter here, but they matter twice: to validate the key's Allowed
    row, and to write the level into the results."""

    tests: tp.Optional[tp.Tuple[int, ...]] = None
    """Which of the sheet's tests to grade, numbered from 1 across the whole
    sheet. None grades all of them. A test left out produces no result row,
    and nothing about it is asked for on the review sheets - its columns are
    simply not read, which is what makes it useful when one test of the three
    is scored somewhere else or not at all."""

    sides: tp.Tuple[int, ...] = (0, 1)
    """Which sides of the paper this scan contains: both, fronts only, or
    backs only. A one-sided scan is graded a page at a time rather than in
    pairs, and a page of the other side is set aside rather than read."""

    skip_blanks: bool = False
    """Ignore pages with nothing on them, which is what a duplex scanner
    hands back for the empty reverse of a one-sided original. Only sensible
    on a one-sided scan; blanks are reported either way."""

    require_done: bool = True
    """Refuse a corrected review sheet with rows nobody has ticked off. Turned
    off for the case where somebody worked through every row but forgot to
    tick them, which is otherwise an hour of clicking to recover from."""


def _sample_page_indexes(reports: tp.Sequence[batching.PageReport],
                         path: pathlib.Path) -> tp.List[int]:
    """One front and one back page of this file, if it has both.

    Taken from the sides actually read off the pages, so a file that turns out
    to hold only backs still calibrates from pages it really contains.
    """
    wanted: tp.Dict[tp.Optional[int], int] = {}
    for report in reports:
        if report.path != path or report.blank or not report.readable:
            continue
        if report.side not in wanted:
            wanted[report.side] = report.page_index
        if len(wanted) >= CALIBRATION_PAGES:
            break
    return sorted(wanted.values())


def parse_tests(spec: tp.Optional[str], available: int
                ) -> tp.Optional[tp.Tuple[int, ...]]:
    """Read "2" or "1,3" into the test numbers to grade.

    Blank, or every test listed, means None - grade all of them, which keeps
    the common case out of the results and the reports.
    """
    text = (spec or "").strip()
    if not text:
        return None
    wanted: tp.List[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= available:
            raise BreakingError(
                f"'{part}' is not a test on this sheet. It carries "
                f"{available} tests, numbered 1 to {available}.")
        if int(part) not in wanted:
            wanted.append(int(part))
    if not wanted:
        return None
    return None if len(wanted) == available else tuple(sorted(wanted))


def resolve_batch_folder(base: pathlib.Path,
                         batch: tp.Optional[str]) -> pathlib.Path:
    """Scans and results live in `Batch N` subfolders; keys do not."""
    return base if batch is None else base / f"Batch {batch}"


class RunResult(tp.NamedTuple):
    override_problems: tp.List[str]
    """Corrections that named no test, or more than one."""

    reports: tp.List[batching.PageReport]
    """Every page of the batch and what became of it."""

    sheets: tp.List[batching.Sheet]
    rows: tp.List[TestRow]
    unclear: tp.List[review_module.UnclearRow]
    missing: tp.List[review_module.MissingRow]
    results_path: pathlib.Path
    written: tp.List[pathlib.Path]
    thresholds: tp.List[tp.Tuple[str, th.Thresholds]]
    supplied_thresholds: bool
    annotated: tp.List[pathlib.Path] = []


def _interpret(scan: reading.PageScan, page: batching.PageRef, batch: str,
               thresholds: th.Thresholds, front_id: str, front_level: str,
               tests_before: int,
               wanted: tp.Optional[tp.Tuple[int, ...]] = None
               ) -> tp.Tuple[tp.List[TestRow],
                             tp.List[review_module.UnclearRow],
                             tp.List[str], str, str,
                             tp.List[tp.Tuple[int, int, int, float]]]:
    """Turn one measured page into result rows plus anything needing review.

    The last item lists any test that had to be read against its own cutoff
    rather than the batch's, as (test number, doubtful before, doubtful
    after, cutoff), so the calibration report can say it happened.
    """
    rows: tp.List[TestRow] = []
    unclear: tp.List[review_module.UnclearRow] = []
    missing: tp.List[str] = []
    rescues: tp.List[tp.Tuple[int, int, int, float]] = []
    page_number = page.page_index + 1
    name = page.path.name

    own_id = reading.read_digits(scan.student_id, thresholds)
    student_id = front_id if (page.position_in_sheet > 0 and front_id) \
        else own_id
    latin_level = reading.read_choice(scan.latin_level, thresholds) \
        or front_level

    def note_unclear(group: reading.BubbleGroup, test_id: str,
                     test_number: int = 0,
                     cutoffs: tp.Optional[th.Thresholds] = None):
        unclear.append(
            review_module.UnclearRow(batch=batch,
                                     source_file=name,
                                     page=page_number,
                                     student_id=student_id,
                                     test_id=test_id,
                                     location=group.location,
                                     test_number=test_number,
                                     guess=frozenset(
                                         group.selected(cutoffs
                                                        or thresholds))))

    def note_missing(field: str):
        if field not in missing:
            missing.append(field)

    # Metadata: both blank and borderline are worth a person's time. A
    # borderline bubble goes to the Unclear sheet naming the exact digit; a
    # blank one only says that the whole field needs typing in again.
    for group in scan.student_id:
        if group.unclear(thresholds):
            note_unclear(group, "")
        elif len(group.selected(thresholds)) != 1:
            note_missing("Student ID")
    if scan.latin_level is not None:
        if scan.latin_level.unclear(thresholds):
            note_unclear(scan.latin_level, "")
        elif len(scan.latin_level.selected(thresholds)) != 1:
            note_missing("Latin level")

    for column_index, (_, questions) in enumerate(scan.tests):
        test_number = tests_before + column_index + 1
        if wanted is not None and test_number not in wanted:
            continue
        digits = scan.test_id_digits[column_index] \
            if column_index < len(scan.test_id_digits) else ()
        test_id = reading.read_digits(digits, thresholds)
        for group in digits:
            if group.unclear(thresholds):
                note_unclear(group, test_id, test_number)
            elif len(group.selected(thresholds)) != 1:
                note_missing(f"Test {test_number} ID")

        # A student who pressed lightly throughout leaves every one of their
        # marks under a cutoff drawn from a batch of people who did not. When
        # nearly the whole test is borderline, ask this test's own bubbles
        # where the line is; if they answer clearly, believe them.
        cutoffs = thresholds
        doubtful = th.unclear_count(questions, thresholds)
        if (len(questions) >= th.RESCUE_MINIMUM_QUESTIONS
                and doubtful >= th.RESCUE_UNCLEAR_FRACTION * len(questions)):
            rescued = th.rescue(
                [fill for group in questions for fill in group.fills],
                thresholds)
            if rescued is not None and th.unclear_count(questions, rescued) \
                    <= th.RESCUE_IMPROVEMENT * doubtful:
                cutoffs = rescued
                rescues.append((test_number, doubtful,
                                th.unclear_count(questions, rescued),
                                rescued.answer_select))

        marked: tp.List[tp.Set[str]] = []
        needs_review = False
        for group in questions:
            marked.append(group.selected(cutoffs))
            # A question left blank is the student's choice, not an error.
            if group.unclear(cutoffs):
                needs_review = True
                note_unclear(group, test_id, test_number, cutoffs)
        rows.append(
            TestRow(batch=batch,
                    source_file=name,
                    page=page_number,
                    student_id=student_id,
                    latin_level=latin_level,
                    test_id=test_id,
                    test_number=test_number,
                    marked=marked,
                    needs_review=needs_review))

    return rows, unclear, missing, own_id, latin_level, rescues


def run(options: RunOptions,
        console: console_module.Console,
        form_variant: tp.Optional[grid_i.TwoSidedFormVariant] = None
        ) -> RunResult:
    """Read a batch and write every output file.

    Every sheet that can be read is read. A page that cannot be used is set
    aside with a reason and reported in ``Pages.csv``, rather than taking the
    rest of the batch down with it, so one bad scan costs one paper.

    Raises BreakingError only when there is nothing worth writing: an unusable
    key file, no scans at all, or a batch in which not one sheet could be
    read.
    """
    from . import file_handling

    text = options.sheet_text or layout.SheetText()
    # The grid is derived from the wording, so a sheet printed with a
    # different number of Latin levels is read with a matching number.
    variant = form_variant or grid_i.form_for(len(text.latin_levels))
    questions = variant.questions_per_column
    batch_label = options.batch or ""
    keys = None
    if options.key_file is not None:
        try:
            keys = keys_module.load(options.key_file, questions,
                                    latin_levels=text.latin_levels)
        except keys_module.AnswerKeyError as error:
            raise BreakingError(str(error))

    notes: tp.List[str] = []
    scans = resolve_batch_folder(options.input_folder, options.batch)
    if not scans.is_dir():
        raise BreakingError(f"There is no folder '{scans}' to read scans from.")
    image_paths = sorted(
        file_handling.filter_images(file_handling.list_file_paths(scans)))
    if not image_paths:
        raise BreakingError(f"No scans found in '{scans}'.")

    output = resolve_batch_folder(options.output_folder, options.batch)
    output.mkdir(parents=True, exist_ok=True)

    names = [path.name for path in image_paths]
    console.line(f"Found {console_module.plural(len(names), 'file')}: "
                 f"{console_module.quoted_list(names)}.")

    try:
        reports = batching.list_pages(image_paths)
    except batching.PageOrderError as error:
        raise BreakingError(str(error))
    if not reports:
        raise BreakingError(f"No pages found in '{scans}'.")
    by_page = {(report.path, report.page_index): report
               for report in reports}

    pinned: tp.Optional[th.PartialThresholds] = None
    if options.threshold_spec:
        try:
            pinned = th.parse_spec(options.threshold_spec)
        except th.ThresholdError as error:
            raise BreakingError(str(error))

    # --- measure every bubble, judging nothing yet ---
    #
    # Which side each page is comes off the page itself, from the solid
    # page-code mark, so nothing here depends on the pages having been fed in
    # the right order.
    scans_by_page: tp.Dict[tp.Tuple[pathlib.Path, int], reading.PageScan] = {}
    found_corners: tp.List[tp.Tuple[tp.Tuple[float, float], ...]] = []
    with console.progress("Reading pages.", len(reports)) as bar:
        for path, page_index, image in batching.iter_pages(image_paths):
            report = by_page.get((path, page_index))
            if report is None:
                continue
            if image_utils.is_blank(image):
                report.blank = True
                bar.step()
                continue
            try:
                scan = reading.scan_page_either_side(
                    image, variant, latin_levels=text.latin_levels)
            except corner_finding.CornerFindingError as error:
                report.readable = False
                report.note = (
                    f"{error} Re-scan this page flat, with auto-crop and "
                    "auto-rotate switched off.")
                bar.step()
                continue
            scans_by_page[(path, page_index)] = scan
            report.side = scan.page_side
            if scan.corners:
                found_corners.append(scan.corners)
            bar.step()

    # --- a second chance, using where the batch put its corners ---
    #
    # A page fed in crooked, or with a mark drawn over, can defeat shape
    # matching on its own while every other sheet in the batch found its
    # corners without trouble. Those sheets say where to look.
    stuck = [report for report in reports
             if not report.readable and not report.blank]
    if stuck and len(found_corners) >= MINIMUM_PRIOR_PAGES:
        prior = _median_corners(found_corners)
        rescued = 0
        wanted = {(report.path, report.page_index): report
                  for report in stuck}
        for path, page_index, image in batching.iter_pages(
                sorted({report.path for report in stuck})):
            report = wanted.get((path, page_index))
            if report is None:
                continue
            try:
                scan = reading.scan_page_with_corners(
                    image, variant, prior, latin_levels=text.latin_levels)
            except corner_finding.CornerFindingError:
                continue
            scans_by_page[(path, page_index)] = scan
            report.readable = True
            report.note = ""
            report.side = scan.page_side
            rescued += 1
        if rescued:
            console.line(
                f"{console_module.plural(rescued, 'page')} could not find "
                "their own corner marks and were read using where the rest "
                "of the batch put theirs.")
            notes.append(
                f"{console_module.plural(rescued, 'page')} were read using "
                "corner positions taken from the rest of the batch.")

    # --- does every page's grid sit where the others' do? ---
    if len(found_corners) >= MINIMUM_PRIOR_PAGES:
        median = _median_corners(found_corners)
        astray = 0
        for report in reports:
            scan = scans_by_page.get((report.path, report.page_index))
            if scan is None or not scan.corners:
                continue
            drift = _corner_deviation(scan.corners, median)
            if drift <= CORNER_DEVIATION_NOTE:
                continue
            astray += 1
            report.note = (
                f"The grid on this page sits {drift * 100:.1f}% of the page "
                "away from where the rest of the batch found theirs. It was "
                "read, but check it against the marked-up scan before "
                "trusting the answers."
            ) + ((" " + report.note) if report.note else "")
        if astray:
            console.line(
                console_module.plural(astray, "page")
                + " read against a grid well away from the rest of the "
                f"batch. See '{PAGES_FILENAME}'.")

    # --- a cutoff per input file ---
    per_file: tp.List[tp.Tuple[str, th.Thresholds]] = []
    thresholds_by_file: tp.Dict[pathlib.Path, th.Thresholds] = {}
    for path in image_paths:
        if pinned is not None and pinned.is_complete:
            thresholds_by_file[path] = pinned.apply_to(
                th.Thresholds(0.3, 0.1, 0.3, 0.1))
            per_file.append((path.name, thresholds_by_file[path]))
            continue
        answer_fills: tp.List[float] = []
        metadata_fills: tp.List[float] = []
        for index in _sample_page_indexes(reports, path):
            scan = scans_by_page.get((path, index))
            if scan is None:
                continue
            answer_fills.extend(scan.answer_fills)
            metadata_fills.extend(scan.metadata_fills)
        calibrated, file_notes = th.calibrate(answer_fills, metadata_fills)
        if pinned is not None:
            calibrated = pinned.apply_to(calibrated)
        thresholds_by_file[path] = calibrated
        per_file.append((path.name, calibrated))
        notes.extend(f"{path.name}: {note}" for note in file_notes)

    # --- read the identifiers, so that pages can be matched on them ---
    for report in reports:
        scan = scans_by_page.get((report.path, report.page_index))
        if scan is None:
            continue
        thresholds = thresholds_by_file[report.path]
        report.student_id = reading.read_digits(scan.student_id, thresholds)
        report.test_ids = tuple(
            reading.read_digits(digits, thresholds)
            for digits in scan.test_id_digits)

    # --- work out which pages make up which sheet ---
    batching.mark_blanks(reports, options.skip_blanks)
    sheets = batching.pair_pages(reports, options.sides)
    set_aside = [report for report in reports
                 if report.status in batching.SET_ASIDE]
    if not sheets:
        raise BreakingError(
            "Not one sheet in this batch could be read."
            + "".join(f"{NEWLINE}  {report.label}: {report.note}"
                      for report in set_aside[:10])
            + (f"{NEWLINE}  ... and "
               f"{len(set_aside) - 10} more" if len(set_aside) > 10 else ""))
    if set_aside:
        console.line(
            console_module.plural(len(set_aside), "page")
            + " could not be graded and "
            + ("is" if len(set_aside) == 1 else "are")
            + f" listed in {PAGES_FILENAME}.")

    # --- now judge ---
    rows: tp.List[TestRow] = []
    unclear: tp.List[review_module.UnclearRow] = []
    missing: tp.List[review_module.MissingRow] = []
    for sheet in sheets:
        # field name -> the pages of this sheet it could not be read on
        sheet_missing: tp.Dict[str, tp.List[int]] = {}
        sheet_file = ""
        # Whatever the two sides agreed on between them, settled in pairing.
        front_id = by_page[(sheet.pages[0].path,
                            sheet.pages[0].page_index)].student_id
        front_level = ""
        for page in sheet.pages:
            scan = scans_by_page.get((page.path, page.page_index))
            if scan is None:
                continue
            thresholds = thresholds_by_file[page.path]
            tests_before = sum(
                len(variant.variant_for_page(index).question_columns)
                for index in range(page.position_in_sheet))
            (page_rows, page_unclear, page_missing, _, level,
             page_rescues) = _interpret(
                scan, page, batch_label, thresholds, front_id, front_level,
                tests_before, options.tests)
            for number, before, after, cutoff in page_rescues:
                notes.append(
                    f"{page.path.name} page {page.page_index + 1}, test "
                    f"{number}: {before} of {len(scan.tests[0][1])} questions "
                    "were too faint to call against the batch cutoff, so this "
                    f"test was read against its own ({cutoff:.4f}), leaving "
                    f"{after}.")
            if page.position_in_sheet == 0:
                front_level = level
            rows.extend(page_rows)
            unclear.extend(page_unclear)
            sheet_file = page.path.name
            for field in page_missing:
                sheet_missing.setdefault(field, []).append(page.page_index + 1)

        for field, pages in sheet_missing.items():
            missing.append(
                review_module.MissingRow(batch=batch_label,
                                         source_file=sheet_file,
                                         pages=tuple(sorted(set(pages))),
                                         student_id=front_id,
                                         field=field))

    # --- human corrections, then scoring ---
    override_problems: tp.List[str] = []
    if options.override_files:
        try:
            overrides = review_module.load(
                list(options.override_files),
                require_done=options.require_done)
        except review_module.OverrideError as error:
            raise BreakingError(str(error))
        applied = apply_overrides(rows, overrides)
        override_problems = applied.problems
        unclear = [row for row in unclear if row.key() not in applied.settled]
        missing = [row for row in missing
                   if (row.source_file, row.field) not in
                   applied.settled_values]
    score_rows(rows, keys)

    written: tp.List[pathlib.Path] = []
    results_path = write_results(output / RESULTS_FILENAME, rows, questions)
    written.append(results_path)
    written.append(
        write_question_stats(output / STATS_FILENAME, rows, keys, questions))
    written.append(
        write_page_report(output / PAGES_FILENAME, reports, batch_label))

    prefix = (f"Batch {batch_label}{review_module.BATCH_SEPARATOR}"
              if batch_label else "")
    if unclear:
        written.append(
            review_module.write_unclear(
                output / f"{prefix}{review_module.UNCLEAR_BASENAME}.csv",
                unclear))
    if missing:
        written.append(
            review_module.write_missing(
                output / f"{prefix}{review_module.MISSING_BASENAME}.csv",
                missing))

    th.write_report(output / th.CALIBRATION_FILENAME, per_file, notes,
                    supplied=pinned is not None and pinned.is_complete,
                    batch=batch_label or None,
                    pinned=pinned.names if pinned else ())
    written.append(output / th.CALIBRATION_FILENAME)

    annotated: tp.List[pathlib.Path] = []
    if options.annotate:
        annotated = annotation.write_marked_up(
            image_paths, scans_by_page, sheets, rows, thresholds_by_file, keys,
            output / ANNOTATED_DIRNAME, console, options.tests,
            options.annotate_students)
        written.extend(annotated)

    return RunResult(reports=reports,
                     override_problems=override_problems,
                     sheets=sheets,
                     rows=rows,
                     unclear=unclear,
                     missing=missing,
                     results_path=results_path,
                     written=written,
                     thresholds=per_file,
                     supplied_thresholds=(pinned is not None
                                          and pinned.is_complete),
                     annotated=annotated)
