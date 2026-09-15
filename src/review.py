"""The two review sheets, and reading a corrected copy back in.

Anything the reader could not call confidently ends up in one of two CSVs:

``Batch N — Unclear.csv``
    A bubble whose darkness sat too close to the cutoff to call. One row per
    affected question or field, with a TRUE/FALSE column per option. The
    machine's best guess is pre-ticked.

``Batch N — Missing.csv``
    A required field - the Student ID, the Latin level, a Test ID - that could
    not be read. One row per *field*, not per bubble: the whole value is asked
    for again, so a Student ID that failed on both sides of a sheet is one row
    reading ``Page 1,2`` rather than ten rows about individual digits. A
    question left blank is not an error; that is the student's choice.

Both are written flat, one fact per column, so they can be uploaded straight
to a Google Sheet, corrected by several people at once, downloaded, and fed
back with ``--overrides``.

A row is matched back to the test it came from by whichever of ``File``,
``Page``, ``Test``, ``Test ID`` and ``Student ID`` it carries - as long as
together they name exactly one test. The software fills in all of them, so its
own rows always match; a row **added by hand** needs only enough to be
unambiguous, which is normally the Student ID and the Test ID. That is how to
correct a bubble that was read wrongly but never flagged as doubtful.

``Test`` matters more than it looks. The back of the sheet carries two tests
side by side, both numbering their questions from 1, so "page 2, question 5"
names two different questions and correcting one used to rewrite the other.
"""

import csv
import dataclasses
import pathlib
import typing as tp

from . import sheet_layout as layout

UNCLEAR_BASENAME = "Unclear"
MISSING_BASENAME = "Missing"

#: Joins the batch to the sheet name, e.g. "Batch 3 — Unclear.csv".
BATCH_SEPARATOR = " — "

#: Written in the option columns. Google Sheets reads these as booleans, so
#: the columns can be turned into real checkboxes in one step.
TRUE = "TRUE"
FALSE = "FALSE"

#: Columns before the per-option ones. `Question` is what the row is about:
#: a question number, or the name of a field such as "Student ID digit 2".
#: `Test` numbers the tests across the whole sheet, from 1.
UNCLEAR_COLUMNS = ("Batch", "File", "Page", "Test", "Student ID", "Test ID",
                   "Question")
#: The Missing sheet asks for a whole field, so it carries no Test ID column
#: of its own - which test is meant is said in `Field`.
MISSING_COLUMNS = ("Batch", "File", "Page", "Student ID", "Field", "Value",
                   "Done")

DONE_COLUMN = "Done"

#: Columns any row may use to say which test it is about. None is required on
#: its own; together they have to pick out exactly one.
ADDRESS_COLUMNS = ("File", "Page", "Test", "Test ID", "Student ID")


def unclear_header(options: str = layout.OPTIONS) -> tp.List[str]:
    return list(UNCLEAR_COLUMNS) + list(options) + [DONE_COLUMN]


class UnclearRow(tp.NamedTuple):
    """One mark that needs a person to decide what it says."""

    batch: str
    source_file: str
    page: int
    student_id: str
    test_id: str
    location: str
    """A question number as a string, or a field name."""

    guess: tp.FrozenSet[str]
    """Options the reader would have picked, pre-ticked in the sheet."""

    test_number: int = 0
    """Which test on the sheet, counted from 1 across both sides. 0 for a row
    about the sheet rather than a test, such as a Student ID digit."""

    def key(self) -> tp.Tuple[str, int, int, str]:
        return (self.source_file, self.page, self.test_number, self.location)

    def as_row(self, options: str = layout.OPTIONS) -> tp.List[str]:
        return [
            self.batch, self.source_file, str(self.page),
            str(self.test_number) if self.test_number else "",
            self.student_id, self.test_id, self.location
        ] + [(TRUE if option in self.guess else FALSE) for option in options
             ] + [FALSE]


class MissingRow(tp.NamedTuple):
    """A required field that could not be read, asked for in full."""

    batch: str
    source_file: str
    pages: tp.Tuple[int, ...]
    """Every page the field was unreadable on. A Student ID that failed on
    both sides of a sheet gives one row reading "1,2"."""

    student_id: str
    field: str
    """What is wanted, in full: "Student ID", "Latin level", "Test 2 ID"."""

    @property
    def page_text(self) -> str:
        return ",".join(str(page) for page in self.pages)

    def key(self) -> tp.Tuple[str, int, str]:
        return (self.source_file, self.pages[0], self.field)

    def keys(self) -> tp.List[tp.Tuple[str, int, str]]:
        """Every (file, page, field) this row answers for."""
        return [(self.source_file, page, self.field) for page in self.pages]

    def as_row(self) -> tp.List[str]:
        return [
            self.batch, self.source_file, self.page_text, self.student_id,
            self.field, "", FALSE
        ]


def write_unclear(path: pathlib.Path, rows: tp.Sequence[UnclearRow],
                  options: str = layout.OPTIONS) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(unclear_header(options))
        for row in rows:
            writer.writerow(row.as_row(options))
    return path


def write_missing(path: pathlib.Path,
                  rows: tp.Sequence[MissingRow]) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(MISSING_COLUMNS))
        for row in rows:
            writer.writerow(row.as_row())
    return path


# --- reading a corrected copy back ---------------------------------------


def _is_true(cell: str) -> bool:
    return cell.strip().upper() in ("TRUE", "T", "YES", "Y", "1", "X", "✓")


class NotFinishedError(ValueError):
    """A review sheet came back with rows nobody has ticked off."""


@dataclasses.dataclass(frozen=True)
class Address:
    """Whichever of the identifying columns a row filled in.

    Any subset will do, as long as together they pick out one test. The
    software writes all of them; a person adding a row by hand usually writes
    the Student ID and the Test ID and leaves the rest empty.
    """

    source_file: str = ""
    page: tp.Optional[int] = None
    test_number: tp.Optional[int] = None
    test_id: str = ""
    student_id: str = ""

    @property
    def is_empty(self) -> bool:
        return not any((self.source_file, self.page, self.test_number,
                        self.test_id, self.student_id))

    def describe(self) -> str:
        parts = []
        if self.source_file:
            parts.append(self.source_file)
        if self.page is not None:
            parts.append(f"page {self.page}")
        if self.test_number is not None:
            parts.append(f"test {self.test_number}")
        if self.test_id:
            parts.append(f"Test ID {self.test_id}")
        if self.student_id:
            parts.append(f"Student ID {self.student_id}")
        return ", ".join(parts) or "no identifying columns"


@dataclasses.dataclass(frozen=True)
class AnswerCorrection:
    """One question, settled by a person."""

    where: Address
    question: str
    chosen: tp.FrozenSet[str]
    origin: str
    """Which file and line it came from, for anything that has to be said
    about it afterwards."""


@dataclasses.dataclass(frozen=True)
class ValueCorrection:
    """One whole field - a Student ID, a Latin level, a Test ID - typed in
    again."""

    where: Address
    field: str
    value: str
    origin: str


class Overrides(tp.NamedTuple):
    """Everything a person decided, still to be matched against the rows."""

    answers: tp.List[AnswerCorrection]
    values: tp.List[ValueCorrection]
    unfinished: tp.List[str]
    """Rows nobody ticked off, when they were allowed through anyway."""

    @property
    def count(self) -> int:
        return len(self.answers) + len(self.values)


class OverrideError(ValueError):
    """The corrected review sheets could not be understood."""


def _read_rows(path: pathlib.Path) -> tp.Tuple[tp.List[str],
                                               tp.List[tp.List[str]]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise OverrideError(f"Could not read '{path.name}': {error}")
    rows = [row for row in csv.reader(text.splitlines())
            if any(cell.strip() for cell in row)]
    if not rows:
        return [], []
    return [cell.strip() for cell in rows[0]], rows[1:]


def collect(paths: tp.Sequence[pathlib.Path]) -> tp.List[pathlib.Path]:
    """Expand any folder given to --overrides into the review sheets in it.

    The sheets are named with an em dash, which a legacy Windows console
    cannot print, so a copied command line would carry a broken path. Naming
    the folder instead avoids the character entirely.
    """
    found: tp.List[pathlib.Path] = []
    for path in paths:
        if path.is_dir():
            for candidate in sorted(path.iterdir()):
                if candidate.suffix.lower() != ".csv":
                    continue
                stem = candidate.stem.lower()
                if (stem.endswith(UNCLEAR_BASENAME.lower())
                        or stem.endswith(MISSING_BASENAME.lower())):
                    found.append(candidate)
        else:
            found.append(path)
    return found


def _whole_number(text: str) -> tp.Optional[int]:
    """Read a cell a spreadsheet may have turned into '2.0'."""
    text = text.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _pages_in(text: str, origin: str) -> tp.List[tp.Optional[int]]:
    """A Page cell, which may name several pages: "1,2"."""
    text = text.strip()
    if not text:
        return [None]
    pages: tp.List[tp.Optional[int]] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        number = _whole_number(part)
        if number is None:
            raise OverrideError(
                f"{origin}: page '{text}' is not a page number.")
        pages.append(number)
    return pages or [None]


def load(paths: tp.Sequence[pathlib.Path],
         options: str = layout.OPTIONS,
         require_done: bool = True) -> Overrides:
    """Read corrected Unclear / Missing sheets.

    Which sheet is which is worked out from the header, so the two files can
    be given in any order, and a file that has been through a spreadsheet
    (extra columns, reordered columns, changed number formats) still works.

    Rows are *not* resolved to particular tests here - that happens against
    the results, in :func:`pipeline.apply_overrides`, because only there is it
    known what tests exist.

    Args:
        require_done: refuse the file if any row is not ticked off. An
            unticked row means nobody has looked at it, and folding in the
            machine's own guess would defeat the point of asking. Turning this
            off is for the case where somebody worked through every row and
            forgot to tick them.
    """
    answers: tp.List[AnswerCorrection] = []
    values: tp.List[ValueCorrection] = []
    unfinished: tp.List[str] = []

    for path in collect(paths):
        header, rows = _read_rows(path)
        if not header:
            continue
        lookup = {name.strip().lower(): index
                  for index, name in enumerate(header)}

        def cell(row: tp.List[str], name: str) -> str:
            index = lookup.get(name.lower())
            if index is None or index >= len(row):
                return ""
            return row[index].strip()

        is_unclear = all(option.lower() in lookup for option in options)
        is_missing = "field" in lookup and "value" in lookup
        if not is_unclear and not is_missing:
            raise OverrideError(
                f"'{path.name}' does not look like a review sheet. Expected "
                f"either columns {', '.join(options)} (the Unclear sheet) or "
                "columns Field and Value (the Missing sheet).")

        for line_number, row in enumerate(rows, start=2):
            origin = f"{path.name} line {line_number}"
            what = cell(row, "Question") or cell(row, "Field")
            if not what:
                continue

            if "done" in lookup and not _is_true(cell(row, DONE_COLUMN)):
                unfinished.append(
                    f"{origin}: {cell(row, 'File') or 'this batch'}, {what}")
                if require_done:
                    continue

            base = dict(
                source_file=cell(row, "File"),
                test_number=_whole_number(cell(row, "Test")),
                test_id=cell(row, "Test ID"),
                student_id=cell(row, "Student ID"),
            )
            for page in _pages_in(cell(row, "Page"), origin):
                where = Address(page=page, **base)
                if where.is_empty:
                    raise OverrideError(
                        f"{origin}: this row says nothing about which test it "
                        "belongs to. Fill in at least a Student ID and a Test "
                        f"ID, or any of: {', '.join(ADDRESS_COLUMNS)}.")
                if is_unclear:
                    answers.append(AnswerCorrection(
                        where=where,
                        question=cell(row, "Question"),
                        chosen=frozenset(option for option in options
                                         if _is_true(cell(row, option))),
                        origin=origin))
                else:
                    values.append(ValueCorrection(
                        where=where,
                        field=cell(row, "Field"),
                        value=cell(row, "Value"),
                        origin=origin))

    # An unticked row is one nobody has looked at, and folding in the
    # machine's own guess for it would defeat the point of asking - so it is
    # left out and comes back next time. A sheet with *nothing* ticked is
    # something else: almost always somebody who did the work and forgot the
    # column, and quietly applying none of it would be its own trap.
    if require_done and unfinished and not (answers or values):
        shown = unfinished[:10]
        more = len(unfinished) - len(shown)
        raise NotFinishedError(
            f"None of the {len(unfinished)} row(s) in that review sheet have "
            "been ticked off in the Done column, so there is nothing to "
            "apply:"
            + "".join(chr(10) + "  " + item for item in shown)
            + (chr(10) + f"  ... and {more} more" if more else "")
            + chr(10) + "Tick Done on the rows you have settled. If every row "
            "really has been looked at, re-run with the Done column ignored."
        )

    return Overrides(answers=answers, values=values, unfinished=unfinished)


def outstanding(paths: tp.Sequence[pathlib.Path],
                used_origins: tp.Set[str]
                ) -> tp.List[tp.Tuple[str, tp.List[str], tp.List[tp.List[str]]]]:
    """What is left of each uploaded review sheet once the used rows are gone.

    Returned as ``(filename, header, rows)`` so the leftovers can be written
    back out in the same shape they arrived in, extra spreadsheet columns and
    all. That is what makes a second round of corrections the same gesture as
    the first: whatever nobody settled comes back as a shorter file.
    """
    left: tp.List[tp.Tuple[str, tp.List[str], tp.List[tp.List[str]]]] = []
    for path in collect(paths):
        header, rows = _read_rows(path)
        if not header:
            continue
        keep = [row for index, row in enumerate(rows, start=2)
                if f"{path.name} line {index}" not in used_origins]
        if keep:
            left.append((path.name, header, keep))
    return left
