"""Splitting a scanned batch into individual two-page sheets.

A convention batch is normally scanned as one big PDF: ten students becomes a
single twenty-page file. This module works out which pages belong to which
sheet before any of them are decoded, and it enforces the one rule that makes
automatic splitting safe - every front page must be immediately followed by
its own back page.

Two independent checks catch a mis-collated stack:

1. *Structural* - the batch must hold a whole number of sheets, and any file
   with more than one page must itself hold a whole number of sheets.
2. *Printed* - each page carries a solid page-code bubble saying which side it
   is, and the back page repeats the Student ID from the front. Both are
   verified as the pages are read (see :func:`check_page_side` and
   :func:`check_student_id`).
"""

import pathlib
import typing as tp

from . import image_utils


class PageOrderError(RuntimeError):
    """Raised when a batch is not a clean sequence of front/back pairs."""


class PageRef(tp.NamedTuple):
    """One page of one input file, located before it is decoded."""

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


def plan_batch(image_paths: tp.Sequence[pathlib.Path],
               pages_per_sheet: int) -> tp.List[Sheet]:
    """Group every page of every input file into sheets, in order.

    Files are taken in the order given (``file_handling.list_file_paths``
    yields them in directory order, so sort beforehand for a predictable
    batch). Pages within a file keep their own order. Sheets are then cut out
    of that single sequence, ``pages_per_sheet`` pages at a time.

    Raises:
        PageOrderError: if the batch cannot be cut into whole sheets.
    """
    if pages_per_sheet < 1:
        raise ValueError("pages_per_sheet must be at least 1")

    flat: tp.List[tp.Tuple[pathlib.Path, int, int]] = []
    unreadable: tp.List[str] = []
    for path in image_paths:
        try:
            page_count = image_utils.count_image_pages(path)
        except image_utils.UnsupportedImageError as error:
            unreadable.append(f"{path.name}: {error}")
            continue
        if pages_per_sheet > 1 and page_count > 1 and (
                page_count % pages_per_sheet):
            raise PageOrderError(
                f"'{path.name}' has {page_count} pages, which is not a whole "
                f"number of {pages_per_sheet}-page sheets. Each scanned file "
                "must contain complete sheets, front page first.")
        for page_index in range(page_count):
            flat.append((path, page_index, page_count))

    if unreadable:
        raise PageOrderError("Could not read some input files:\n  " +
                             "\n  ".join(unreadable))

    if not flat:
        return []

    if len(flat) % pages_per_sheet:
        raise PageOrderError(
            f"The batch has {len(flat)} page(s), which is not a whole number "
            f"of {pages_per_sheet}-page sheets. Every sheet must be scanned "
            "with both of its sides, front page first.")

    sheets: tp.List[Sheet] = []
    for sheet_index in range(len(flat) // pages_per_sheet):
        start = sheet_index * pages_per_sheet
        pages = tuple(
            PageRef(path=path,
                    page_index=page_index,
                    pages_in_file=pages_in_file,
                    position_in_sheet=offset,
                    sheet_index=sheet_index)
            for offset, (path, page_index, pages_in_file) in enumerate(
                flat[start:start + pages_per_sheet]))
        sheets.append(Sheet(index=sheet_index, pages=pages))
    return sheets


def iter_batch_pages(sheets: tp.Sequence[Sheet]
                     ) -> tp.Iterator[tp.Tuple[PageRef, tp.Any]]:
    """Yield ``(page_ref, image)`` for every page of the batch, in order.

    Pages are decoded lazily and each file is opened once, so a
    several-hundred-page PDF never has more than one page in memory.
    """
    pages = [page for sheet in sheets for page in sheet.pages]
    index = 0
    while index < len(pages):
        path = pages[index].path
        end = index
        while end < len(pages) and pages[end].path == path:
            end += 1
        wanted = {page.page_index: page for page in pages[index:end]}
        last_wanted = max(wanted)
        for page_index, image in enumerate(image_utils.iter_image_pages(path)):
            if page_index in wanted:
                yield wanted[page_index], image
            if page_index >= last_wanted:
                break
        index = end


def check_page_side(page: PageRef, observed_side: tp.Optional[int],
                    side_names: tp.Sequence[str]) -> None:
    """Verify a page's printed page-code against the position it was scanned in.

    Raises:
        PageOrderError: if the page is the wrong side for its position.
    """
    expected = page.position_in_sheet
    if observed_side is None:
        raise PageOrderError(
            f"Could not read the page-code mark on '{page.label}'. It should "
            f"be the {side_names[expected]} of sheet {page.sheet_index + 1}. "
            "Re-scan this page, or check that the sheet was printed from the "
            "current template.")
    if observed_side != expected:
        raise PageOrderError(
            f"'{page.label}' is the {side_names[observed_side]} of a sheet, "
            f"but it was scanned where the {side_names[expected]} of sheet "
            f"{page.sheet_index + 1} should be. The pages are out of order: "
            "every front page must be immediately followed by its own back "
            "page.")


def check_student_id(page: PageRef, front_id: str, back_id: str) -> None:
    """Verify that a back page carries the same Student ID as its front page.

    Raises:
        PageOrderError: if the two sides disagree.
    """
    if not front_id or not back_id:
        return
    if front_id != back_id:
        raise PageOrderError(
            f"'{page.label}' has Student ID {back_id}, but the front page of "
            f"sheet {page.sheet_index + 1} has Student ID {front_id}. The "
            "pages are out of order or two students' sheets have been "
            "interleaved.")
