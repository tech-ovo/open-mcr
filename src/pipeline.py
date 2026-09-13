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
from . import reading
from . import review as review_module
from . import sheet_layout as layout
from . import thresholds as th

RESULTS_FILENAME = "Results.csv"
STATS_FILENAME = "Question Stats.csv"
ANNOTATED_DIRNAME = "Annotated"

#: How many pages of each file are read to calibrate its cutoffs. One front
#: and one back page, so both layouts are represented.
CALIBRATION_PAGES = 2

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
    return ([
        "Batch", "File", "Page", "Student ID", "Latin Level", "Test ID",
        "Test Name", "Status", "Points", "Out Of", "Score (%)"
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
                row.batch, row.source_file, row.page, row.student_id,
                row.latin_level, row.test_id, row.test_name, row.status,
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


# --- applying human corrections ------------------------------------------


def apply_overrides(rows: tp.Sequence[TestRow],
                    overrides: review_module.Overrides) -> int:
    """Fold corrected review sheets into the rows.

    Returns the number of values that actually *changed*. A correction that
    agrees with what the reader already had is not counted, so the number
    reported is the number of readings a person overturned.
    """
    changed = 0
    for row in rows:
        for question_index in range(len(row.marked)):
            corrected = overrides.answer_for(row.source_file, row.page,
                                             str(question_index + 1))
            if corrected is None:
                continue
            if set(corrected) != row.marked[question_index]:
                row.marked[question_index] = set(corrected)
                changed += 1

        student = overrides.value_for(row.source_file, row.page, "Student ID")
        if student and student.strip() != row.student_id:
            row.student_id = student.strip()
            changed += 1
        level = overrides.value_for(row.source_file, row.page, "Latin level")
        if level and level.strip() != row.latin_level:
            row.latin_level = level.strip()
            changed += 1
        # A Test ID row names its test, so only the matching row is updated.
        for field, value in overrides.values.items():
            if field[0] != row.source_file or field[1] != row.page:
                continue
            if not field[2].startswith("Test ") or not field[2].endswith(
                    " ID"):
                continue
            if value.strip() and value.strip() != row.test_id:
                row.test_id = value.strip()
                changed += 1
        row.needs_review = False
    return changed


# --- the run -------------------------------------------------------------


class RunOptions(tp.NamedTuple):
    input_folder: pathlib.Path
    output_folder: pathlib.Path
    batch: tp.Optional[str] = None
    key_file: tp.Optional[pathlib.Path] = None
    override_files: tp.Sequence[pathlib.Path] = ()
    threshold_spec: tp.Optional[str] = None
    annotate: bool = False
    debug: bool = False
    sheet_text: tp.Optional[layout.SheetText] = None
    """The wording printed on the sheets being read. Only the Latin level
    names matter here, but they matter twice: to validate the key's Excluded
    row, and to write the level into the results."""


def _sample_page_indexes(sheets: tp.Sequence[batching.Sheet],
                         path: pathlib.Path) -> tp.List[int]:
    """One front and one back page of this file, if it has both."""
    wanted: tp.Dict[int, int] = {}
    for sheet in sheets:
        for page in sheet.pages:
            if page.path != path:
                continue
            if page.position_in_sheet not in wanted:
                wanted[page.position_in_sheet] = page.page_index
            if len(wanted) >= CALIBRATION_PAGES:
                break
    return sorted(wanted.values())


def resolve_batch_folder(base: pathlib.Path,
                         batch: tp.Optional[str]) -> pathlib.Path:
    """Scans and results live in `Batch N` subfolders; keys do not."""
    return base if batch is None else base / f"Batch {batch}"


class RunResult(tp.NamedTuple):
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
               tests_before: int
               ) -> tp.Tuple[tp.List[TestRow],
                             tp.List[review_module.UnclearRow],
                             tp.List[str], str, str]:
    """Turn one measured page into result rows plus anything needing review."""
    rows: tp.List[TestRow] = []
    unclear: tp.List[review_module.UnclearRow] = []
    missing: tp.List[str] = []
    page_number = page.page_index + 1
    name = page.path.name

    own_id = reading.read_digits(scan.student_id, thresholds)
    student_id = front_id if (page.position_in_sheet > 0 and front_id) \
        else own_id
    latin_level = reading.read_choice(scan.latin_level, thresholds) \
        or front_level

    def note_unclear(group: reading.BubbleGroup, test_id: str):
        unclear.append(
            review_module.UnclearRow(batch=batch,
                                     source_file=name,
                                     page=page_number,
                                     student_id=student_id,
                                     test_id=test_id,
                                     location=group.location,
                                     guess=frozenset(
                                         group.selected(thresholds))))

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
        digits = scan.test_id_digits[column_index] \
            if column_index < len(scan.test_id_digits) else ()
        test_id = reading.read_digits(digits, thresholds)
        test_number = tests_before + column_index + 1
        for group in digits:
            if group.unclear(thresholds):
                note_unclear(group, test_id)
            elif len(group.selected(thresholds)) != 1:
                note_missing(f"Test {test_number} ID")

        marked: tp.List[tp.Set[str]] = []
        needs_review = False
        for group in questions:
            marked.append(group.selected(thresholds))
            # A question left blank is the student's choice, not an error.
            if group.unclear(thresholds):
                needs_review = True
                note_unclear(group, test_id)
        rows.append(
            TestRow(batch=batch,
                    source_file=name,
                    page=page_number,
                    student_id=student_id,
                    latin_level=latin_level,
                    test_id=test_id,
                    marked=marked,
                    needs_review=needs_review))

    return rows, unclear, missing, own_id, latin_level


def run(options: RunOptions,
        console: console_module.Console,
        form_variant: tp.Optional[grid_i.TwoSidedFormVariant] = None
        ) -> RunResult:
    """Read a batch and write every output file.

    Raises BreakingError only for problems that mean nothing should be written
    at all: an unusable key file, a mis-collated batch, an unreadable page.
    """
    from . import file_handling

    variant = form_variant or grid_i.form_cajcl
    questions = variant.questions_per_column
    batch_label = options.batch or ""

    text = options.sheet_text or layout.SheetText()
    keys = None
    if options.key_file is not None:
        try:
            keys = keys_module.load(options.key_file, questions,
                                    latin_levels=text.latin_levels)
        except keys_module.AnswerKeyError as error:
            raise BreakingError(str(error))

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
        sheets = batching.plan_batch(image_paths, variant.pages_per_sheet)
    except batching.PageOrderError as error:
        raise BreakingError(str(error))

    pinned: tp.Optional[th.PartialThresholds] = None
    if options.threshold_spec:
        try:
            pinned = th.parse_spec(options.threshold_spec)
        except th.ThresholdError as error:
            raise BreakingError(str(error))

    # --- measure every bubble, judging nothing yet ---
    by_file: tp.Dict[pathlib.Path, tp.List[batching.PageRef]] = {}
    for sheet in sheets:
        for page in sheet.pages:
            by_file.setdefault(page.path, []).append(page)

    scans_by_page: tp.Dict[tp.Tuple[pathlib.Path, int], reading.PageScan] = {}
    unreadable: tp.List[str] = []
    for path in image_paths:
        pages = by_file.get(path, [])
        with console.progress(f"Processing '{path.name}'.", len(pages)) as bar:
            for page, image in batching.iter_batch_pages(
                    [batching.Sheet(0, tuple(pages))]):
                page_variant = variant.variant_for_page(page.position_in_sheet)
                try:
                    scans_by_page[(path, page.page_index)] = reading.scan_page(
                        image, page_variant, page.position_in_sheet,
                        latin_levels=text.latin_levels)
                except corner_finding.CornerFindingError as error:
                    unreadable.append(f"{page.label}: {error}")
                bar.step()

    if unreadable:
        raise BreakingError(
            "The corner marks could not be found on "
            + console_module.plural(len(unreadable), "page")
            + ", so the grid could not be established:"
            + "".join(f"{NEWLINE}  {item}" for item in unreadable)
            + f"{NEWLINE}Re-scan those pages flat, with auto-crop and "
            "auto-rotate switched off.")

    # --- a cutoff per input file ---
    per_file: tp.List[tp.Tuple[str, th.Thresholds]] = []
    thresholds_by_file: tp.Dict[pathlib.Path, th.Thresholds] = {}
    notes: tp.List[str] = []
    for path in image_paths:
        if pinned is not None and pinned.is_complete:
            thresholds_by_file[path] = pinned.apply_to(
                th.Thresholds(0.3, 0.1, 0.3, 0.1))
            per_file.append((path.name, thresholds_by_file[path]))
            continue
        answer_fills: tp.List[float] = []
        metadata_fills: tp.List[float] = []
        for index in _sample_page_indexes(sheets, path):
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

    # --- now judge ---
    rows: tp.List[TestRow] = []
    unclear: tp.List[review_module.UnclearRow] = []
    missing: tp.List[review_module.MissingRow] = []
    front_id = front_level = ""
    for sheet in sheets:
        # field name -> the pages of this sheet it could not be read on
        sheet_missing: tp.Dict[str, tp.List[int]] = {}
        sheet_file = ""
        for page in sheet.pages:
            scan = scans_by_page.get((page.path, page.page_index))
            if scan is None:
                continue
            thresholds = thresholds_by_file[page.path]
            page_variant = variant.variant_for_page(page.position_in_sheet)
            if grid_i.Field.PAGE_CODE in page_variant.fields:
                side = reading.read_page_side(scan, thresholds)
                try:
                    batching.check_page_side(page, side,
                                             ("front page", "back page"))
                except batching.PageOrderError as error:
                    raise BreakingError(str(error))
            if page.position_in_sheet == 0:
                front_id = front_level = ""
            tests_before = sum(
                len(variant.variant_for_page(index).question_columns)
                for index in range(page.position_in_sheet))
            page_rows, page_unclear, page_missing, own_id, level = _interpret(
                scan, page, batch_label, thresholds, front_id, front_level,
                tests_before)
            if page.position_in_sheet == 0:
                front_id, front_level = own_id, level
            else:
                try:
                    batching.check_student_id(page, front_id, own_id)
                except batching.PageOrderError as error:
                    raise BreakingError(str(error))
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
    if options.override_files:
        try:
            overrides = review_module.load(list(options.override_files))
        except review_module.OverrideError as error:
            raise BreakingError(str(error))
        apply_overrides(rows, overrides)
        unclear = [row for row in unclear
                   if row.key() not in overrides.answers]
        missing = [
            row for row in missing
            if not any(key in overrides.values for key in row.keys())
        ]
    score_rows(rows, keys)

    written: tp.List[pathlib.Path] = []
    results_path = write_results(output / RESULTS_FILENAME, rows, questions)
    written.append(results_path)
    written.append(
        write_question_stats(output / STATS_FILENAME, rows, keys, questions))

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
            output / ANNOTATED_DIRNAME, console)
        written.extend(annotated)

    return RunResult(rows=rows,
                     unclear=unclear,
                     missing=missing,
                     results_path=results_path,
                     written=written,
                     thresholds=per_file,
                     supplied_thresholds=(pinned is not None
                                          and pinned.is_complete),
                     annotated=annotated)
