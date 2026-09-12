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
back with ``--overrides``. Rows are matched on file, page and location, never
on the Student ID, because a spreadsheet will silently turn ``04275`` into
``4275``.
"""

import csv
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

#: Columns before the per-option ones. `Location` is what the row is about:
#: a question number, or the name of a field such as "Student ID digit 2".
UNCLEAR_COLUMNS = ("Batch", "File", "Page", "Student ID", "Test ID",
                   "Question")
#: The Missing sheet asks for a whole field, so it carries no Test ID column
#: of its own - which test is meant is said in `Field`.
MISSING_COLUMNS = ("Batch", "File", "Page", "Student ID", "Field", "Value",
                   "Done")

DONE_COLUMN = "Done"


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

    def key(self) -> tp.Tuple[str, int, str]:
        return (self.source_file, self.page, self.location)

    def as_row(self, options: str = layout.OPTIONS) -> tp.List[str]:
        return [
            self.batch, self.source_file, str(self.page), self.student_id,
            self.test_id, self.location
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


class Overrides(tp.NamedTuple):
    """What a human decided, keyed by (file, page, location)."""

    answers: tp.Dict[tp.Tuple[str, int, str], tp.Set[str]]
    """Corrected option letters for an unclear mark."""

    values: tp.Dict[tp.Tuple[str, int, str], str]
    """Corrected text for a missing field."""

    @property
    def count(self) -> int:
        return len(self.answers) + len(self.values)

    def answer_for(self, source_file: str, page: int, location: str
                   ) -> tp.Optional[tp.Set[str]]:
        return self.answers.get((source_file, page, location))

    def value_for(self, source_file: str, page: int, field: str
                  ) -> tp.Optional[str]:
        return self.values.get((source_file, page, field))


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


def load(paths: tp.Sequence[pathlib.Path],
         options: str = layout.OPTIONS,
         require_done: bool = True) -> Overrides:
    """Read corrected Unclear / Missing sheets.

    Which sheet is which is worked out from the header, so the two files can
    be given in any order, and a file that has been through a spreadsheet
    (extra columns, reordered columns, changed number formats) still works.

    Every row must have its `Done` box ticked. An unticked row means somebody
    has not looked at it yet, and silently folding in the machine's own guess
    would defeat the point of asking.
    """
    answers: tp.Dict[tp.Tuple[str, int, str], tp.Set[str]] = {}
    values: tp.Dict[tp.Tuple[str, int, str], str] = {}
    unfinished: tp.List[str] = []

    for path in collect(paths):
        header, rows = _read_rows(path)
        if not header:
            continue
        lookup = {name.strip().lower(): index for index, name in
                  enumerate(header)}

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
            source_file = cell(row, "File")
            page_text = cell(row, "Page")
            if not source_file or not page_text:
                continue
            pages: tp.List[int] = []
            for part in page_text.replace(";", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    pages.append(int(float(part)))
                except ValueError:
                    raise OverrideError(
                        f"'{path.name}' line {line_number}: page "
                        f"'{page_text}' is not a page number.")
            if not pages:
                continue

            if require_done and "done" in lookup and \
                    not _is_true(cell(row, DONE_COLUMN)):
                what = cell(row, "Question") or cell(row, "Field") or "?"
                unfinished.append(
                    f"{path.name} line {line_number}: {source_file} page "
                    f"{page_text}, {what}")
                continue

            if is_unclear:
                location = cell(row, "Question")
                if not location:
                    continue
                chosen = {
                    option
                    for option in options if _is_true(cell(row, option))
                }
                for page in pages:
                    answers[(source_file, page, location)] = chosen
            else:
                field = cell(row, "Field")
                if not field:
                    continue
                for page in pages:
                    values[(source_file, page, field)] = cell(row, "Value")

    if unfinished:
        shown = unfinished[:10]
        more = len(unfinished) - len(shown)
        raise NotFinishedError(
            f"{len(unfinished)} review row(s) have not been ticked off in the "
            "Done column, so nobody has settled them yet:"
            + "".join(chr(10) + "  " + item for item in shown)
            + (chr(10) + f"  ... and {more} more" if more else "")
            + chr(10) + "Tick Done on every row, or delete the rows you do "
            "not want to change.")

    return Overrides(answers=answers, values=values)
