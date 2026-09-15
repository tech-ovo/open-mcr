"""Sorting a scanned batch into individual sheets.

A convention batch is normally scanned as one big PDF: ten students becomes a
single twenty-page file. This module decides which pages belong together.

The guiding rule is that **every sheet that can be graded is graded**. A page
that cannot be used does not take the ones around it with it; it is set aside
with a reason, and every page of the batch - used or not - ends up in the page
report with a status. A batch is refused outright only when nothing in it
could be read at all.

Pages are matched on what is printed and bubbled on them, not on where they
happen to sit in the file:

* each page carries a solid **page-code** bubble saying which side it is, so
  the reader never has to assume that page 3 is a front page;
* the back page repeats the **Student ID** from the front, and a front and a
  back become a sheet only when their IDs agree. An unreadable digit reads as
  ``?`` and matches anything: a student who left the same digit faint on both
  sides has done nothing wrong, and their paper should still be graded;
* a page with no ink on it at all is **blank** - the empty reverse a duplex
  scanner produces for a single-sided original - and is skipped rather than
  puzzled over.
"""

import dataclasses
import pathlib
import typing as tp

from . import image_utils

#: ``position_in_sheet`` values: which side of the paper a page is.
FRONT = 0
BACK = 1

SIDE_NAMES = ("front", "back")

# --- what became of each page --------------------------------------------
#
# Every page of the batch gets exactly one of these. They are written to the
# page report as-is, so they are phrased for the person reading it.

#: Paired up (or, on a one-sided scan, taken on its own) and read.
GRADED = "Graded"
#: No ink at all, and a blank page was expected here. Skipped.
BLANK = "Blank"
#: No ink, where the scan was not supposed to contain one.
UNEXPECTED_BLANK = "Unexpected blank"
#: The corner marks could not be found, so there was no grid to read.
UNREADABLE = "Unreadable"
#: A good page with no partner - a front with no back, or a back with no
#: front. Its answers cannot be trusted to belong to anybody.
UNPAIRED = "Unpaired"
#: A front and a back that sit together but disagree about whose paper it is.
ID_MISMATCH = "ID mismatch"
#: The other side of the sheet from the one being graded.
WRONG_SIDE = "Wrong side"

#: The statuses that mean a page contributed nothing to the results.
SET_ASIDE = (UNEXPECTED_BLANK, UNREADABLE, UNPAIRED, ID_MISMATCH, WRONG_SIDE)


class PageOrderError(RuntimeError):
    """A batch could not be made sense of at all."""


class PageRef(tp.NamedTuple):
    """One page of one input file, once it is known where it belongs."""

    path: pathlib.Path
    page_index: int
    """0-based index of this page within its own file."""

    pages_in_file: int
    position_in_sheet: int
    """0 for a front page, 1 for a back page."""

    sheet_index: int
    """0-based index of the sheet this page belongs to, across the batch."""

    @property
    def label(self) -> str:
        if self.pages_in_file == 1:
            return self.path.name
        return f"{self.path.name} (page {self.page_index + 1})"


class Sheet(tp.NamedTuple):
    index: int
    pages: tp.Tuple[PageRef, ...]

    @property
    def label(self) -> str:
        return f"sheet {self.index + 1}"


@dataclasses.dataclass
class PageReport:
    """One line of the page report: what this page was, and what became of it.

    Filled in over the course of a run - the scanning pass says whether the
    page was blank or readable and which side it is, the pairing pass says
    what it was used for.
    """

    path: pathlib.Path
    page_index: int
    pages_in_file: int

    blank: bool = False
    readable: bool = True
    note: str = ""
    """Why, in a sentence, for anything a person would want explained."""

    side: tp.Optional[int] = None
    """Read from the printed page-code bubble. None if it could not be read,
    which is rare: it is printed solid rather than bubbled in."""

    student_id: str = ""
    test_ids: tp.Tuple[str, ...] = ()
    status: str = ""
    sheet_index: tp.Optional[int] = None
    partner: tp.Optional[int] = None
    """Index in the report of the page this one was paired with."""

    @property
    def label(self) -> str:
        if self.pages_in_file == 1:
            return self.path.name
        return f"{self.path.name} (page {self.page_index + 1})"

    @property
    def side_name(self) -> str:
        return "" if self.side is None else SIDE_NAMES[self.side]


# --- reading the pages in --------------------------------------------------


def list_pages(image_paths: tp.Sequence[pathlib.Path]
               ) -> tp.List[PageReport]:
    """One report per page of every input file, in order.

    Files are taken in the order given, and pages keep their order within a
    file, so a batch is one flat sequence of pages however it was split across
    files.
    """
    reports: tp.List[PageReport] = []
    unreadable: tp.List[str] = []
    for path in image_paths:
        try:
            page_count = image_utils.count_image_pages(path)
        except image_utils.UnsupportedImageError as error:
            unreadable.append(f"{path.name}: {error}")
            continue
        for page_index in range(page_count):
            reports.append(PageReport(path=path, page_index=page_index,
                                      pages_in_file=page_count))
    if unreadable:
        raise PageOrderError("Could not read some input files:\n  " +
                             "\n  ".join(unreadable))
    return reports


def iter_pages(image_paths: tp.Sequence[pathlib.Path]
               ) -> tp.Iterator[tp.Tuple[pathlib.Path, int, tp.Any]]:
    """Yield ``(path, page_index, image)`` for every page, in order.

    Pages are decoded lazily and each file is opened once, so a
    several-hundred-page PDF never has more than one page in memory.
    """
    for path in image_paths:
        for page_index, image in enumerate(image_utils.iter_image_pages(path)):
            yield path, page_index, image


# --- matching the two sides of a sheet -------------------------------------


def ids_compatible(first: str, second: str) -> bool:
    """Could these two readings be the same Student ID?

    ``?`` stands for a digit that could not be read and matches anything. A
    student who filled only the first four digits leaves the same gap on both
    sides of the sheet; that is one thing to ask them about later, not a
    reason to refuse to grade the paper.
    """
    if not first or not second:
        return True
    if len(first) != len(second):
        return False
    return all(a == b or a == "?" or b == "?"
               for a, b in zip(first, second))


def merge_ids(first: str, second: str) -> str:
    """The best reading of an ID given both sides of the sheet.

    Only called once the two are known to be compatible, so wherever one side
    is unsure the other side decides.
    """
    if not first:
        return second
    if not second:
        return first
    return "".join(a if a != "?" else b for a, b in zip(first, second))


def _fits(side: tp.Optional[int], wanted: int) -> bool:
    """Is this page the side wanted - or silent about which side it is?"""
    return side is None or side == wanted


# --- blanks ----------------------------------------------------------------


def mark_blanks(reports: tp.Sequence[PageReport], skip_blanks: bool) -> None:
    """Decide which blank pages were expected and which were not.

    With ``skip_blanks`` on, the batch is taken to be a one-sided original
    scanned on a duplex scanner, so the pages come in twos with one side
    printed and the other empty. Which of the two is empty is not fixed - a
    stack can be fed either way up, and can change part way - so the rule
    checked is only that **each consecutive pair holds exactly one blank**.
    That accepts ``[marked, blank, blank, marked]`` while still catching a
    page that has genuinely gone missing.
    """
    blanks = [report for report in reports if report.blank]
    if not skip_blanks:
        for report in blanks:
            report.note = ("This page has nothing on it. Blank pages are not "
                           "expected unless the scan is one-sided.")
        return

    for start in range(0, len(reports) - 1, 2):
        pair = reports[start:start + 2]
        empty = [report for report in pair if report.blank]
        if len(empty) == 1:
            continue
        # Either both sides came through blank or neither did, so the
        # one-printed-one-empty rhythm has broken somewhere near here.
        for report in pair:
            if report.blank:
                report.note = (
                    "A blank page where the scan should have had a printed "
                    "one. Check that no sheet was missed at the scanner.")
    if len(reports) % 2:
        last = reports[-1]
        if last.blank:
            last.note = ("A blank page left over at the end of the batch, "
                         "with nothing to pair it with.")


# --- pairing ---------------------------------------------------------------


def pair_pages(reports: tp.List[PageReport], sides: tp.Sequence[int]
               ) -> tp.List[Sheet]:
    """Work out which pages make up which sheet, and why the rest do not.

    Every report is given a status. Pages that pair up become sheets; the
    others are set aside individually, so one bad page costs one paper rather
    than the batch.

    Args:
        sides: which sides of the paper this scan contains - ``(0, 1)`` for an
            ordinary duplex scan, ``(0,)`` for fronts only, ``(1,)`` for backs.
    """
    for report in reports:
        if report.blank:
            report.status = BLANK if not report.note else UNEXPECTED_BLANK
        elif not report.readable:
            report.status = UNREADABLE

    usable = [report for report in reports
              if report.readable and not report.blank]
    # By identity, not by value: PageReport is a plain dataclass, so list.index
    # would compare field by field and could match the wrong page.
    position_of = {id(report): index for index, report in enumerate(reports)}
    sheets: tp.List[Sheet] = []

    def add_sheet(members: tp.Sequence[PageReport]) -> None:
        index = len(sheets)
        pages = tuple(
            PageRef(path=report.path, page_index=report.page_index,
                    pages_in_file=report.pages_in_file,
                    position_in_sheet=sides[offset], sheet_index=index)
            for offset, report in enumerate(members))
        for report in members:
            report.status = GRADED
            report.sheet_index = index
        sheets.append(Sheet(index=index, pages=pages))

    if len(sides) == 1:
        only = sides[0]
        for report in usable:
            if not _fits(report.side, only):
                report.status = WRONG_SIDE
                report.note = (
                    f"This is the {report.side_name} of a sheet, but the scan "
                    f"was set to contain {SIDE_NAMES[only]} pages only.")
                continue
            add_sheet([report])
        return sheets

    position = 0
    while position < len(usable):
        first = usable[position]
        second = usable[position + 1] if position + 1 < len(usable) else None

        if second is not None and _fits(first.side, FRONT) \
                and _fits(second.side, BACK):
            if ids_compatible(first.student_id, second.student_id):
                merged = merge_ids(first.student_id, second.student_id)
                first.student_id = second.student_id = merged
                first.partner = position_of[id(second)]
                second.partner = position_of[id(first)]
                add_sheet([first, second])
                position += 2
                continue
            for one, other in ((first, second), (second, first)):
                one.status = ID_MISMATCH
                one.partner = position_of[id(other)]
                one.note = (
                    f"The front page reads Student ID {first.student_id} and "
                    f"the back reads {second.student_id}. Either two students' "
                    "sheets have been interleaved, or one side was bubbled "
                    "wrongly. Neither page was graded.")
            position += 2
            continue

        first.status = UNPAIRED
        if first.side == BACK:
            first.note = ("A back page with no front page before it, so "
                          "there is no Student ID to attach it to.")
        elif second is None:
            first.note = ("A front page at the end of the batch with no back "
                          "page after it.")
        else:
            first.note = (
                f"A {first.side_name or 'page'} followed by a "
                f"{second.side_name or 'page'}, which cannot be a sheet. "
                "Every front page must be followed by its own back page.")
        position += 1

    return sheets


def describe(reports: tp.Sequence[PageReport]) -> str:
    """A one-line summary of what happened to the batch."""
    counts: tp.Dict[str, int] = {}
    for report in reports:
        counts[report.status] = counts.get(report.status, 0) + 1
    graded = counts.get(GRADED, 0)
    parts = [f"{graded} page(s) graded"]
    for status in (BLANK, ) + SET_ASIDE:
        if counts.get(status):
            parts.append(f"{counts[status]} {status.lower()}")
    return ", ".join(parts)
