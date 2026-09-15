"""Build filled-in CAJCL sheets for the tests.

The blank sheet is rendered by the real generator, rasterised, and then marked
by stamping discs at the bubble centres that `sheet_layout` says exist. That
keeps the tests honest: if the printed sheet and the reader's idea of the grid
ever diverge, these tests stop passing.
"""

import functools
import pathlib
import shutil
import tempfile
import typing as tp

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas as pdfcanvas

from src import sheet_generation
from src import sheet_layout as layout

#: Rasterisation scale for the synthetic "scans" (72 dpi x this).
SCAN_SCALE = 3


class SheetData(tp.NamedTuple):
    """What to bubble onto one two-page sheet."""

    student_id: str
    back_student_id: tp.Optional[str] = None
    """What the back page says, when that differs from the front. A digit
    written as a non-digit (``04?75``) is simply left unbubbled, which is what
    a student who skipped it would leave behind."""

    latin_level: tp.Optional[str] = None
    test_ids: tp.Sequence[str] = ("1001", "1002", "1003")
    answers: tp.Sequence[tp.Sequence[str]] = ()
    """Three sequences of 80 answers ("A".."E", or "" to leave blank)."""

    #: Question numbers (1-based, per test) to mark only partially, keyed by
    #: test index. Used to exercise the unclear-mark detection.
    faint: tp.Mapping[int, tp.Sequence[int]] = {}
    #: Extra bubbles to fill, as (test index, question number, option letter).
    extra_marks: tp.Sequence[tp.Tuple[int, int, str]] = ()
    #: Fill fraction used for the "faint" marks (of the bubble's area).
    faint_fraction: float = 0.18


@functools.lru_cache(maxsize=4)
def _blank_pages(text: tp.Optional[layout.SheetText] = None
                 ) -> tp.Tuple[np.ndarray, ...]:
    """Rasterise the two blank sheet pages as grayscale images.

    Cached on the wording: rendering the sheet is by far the slowest part of
    building a batch, and every sheet with the same wording starts from the
    same blank. SheetText is a frozen dataclass of tuples, so it hashes.
    """
    directory = pathlib.Path(tempfile.mkdtemp(prefix="open-mcr-blank-"))
    try:
        path = directory / "blank.pdf"
        canvas = pdfcanvas.Canvas(str(path), pagesize=LETTER)
        for page_index in range(layout.PAGES_PER_SHEET):
            sheet_generation.draw_page(canvas, page_index, text)
            canvas.showPage()
        canvas.save()
        document = pdfium.PdfDocument(str(path))
        try:
            return tuple(
                np.array(page.render(scale=SCAN_SCALE).to_pil().convert("L"))
                for page in document)
        finally:
            document.close()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _pixels_per_inch(image: np.ndarray) -> float:
    return image.shape[1] / layout.PAGE_WIDTH_IN


def _stamp(image: np.ndarray, column: float, row: float,
           fraction: float = 1.0):
    """Fill the bubble at a grid cell, covering `fraction` of its area."""
    x_in, y_in = layout.cell_to_inches(column + 0.5, row + 0.5)
    scale = _pixels_per_inch(image)
    centre = (int(round(x_in * scale)),
              int(round((layout.PAGE_HEIGHT_IN - y_in) * scale)))
    full_radius = layout.bubble_radius_in() * scale * 0.86
    radius = max(int(round(full_radius * (fraction**0.5))), 1)
    cv2.circle(image, centre, radius, (0, ), -1, lineType=cv2.LINE_AA)


def _stamp_digits(image: np.ndarray, first_column: int, value: str):
    for offset, character in enumerate(value):
        if not character.isdigit():
            continue
        _stamp(image, first_column + offset,
               layout.ID_FIRST_BUBBLE_ROW + int(character))


def _answer_cell(page_index: int, test_on_page: int,
                 question_number: int) -> tp.Tuple[int, int]:
    """Grid cell of the first option of a question (1-based number)."""
    index = question_number - 1
    subcolumn = index // layout.ROWS_PER_SUBCOLUMN
    row_offset = index % layout.ROWS_PER_SUBCOLUMN
    column = layout.PAGE_SUBCOLUMNS[page_index][test_on_page][subcolumn]
    return column, layout.MCQ_FIRST_ROW + row_offset


def _test_position(test_index: int) -> tp.Tuple[int, int]:
    """Map a test number (0-based) onto (page index, test on that page)."""
    seen = 0
    for page_index, tests in enumerate(layout.PAGE_SUBCOLUMNS):
        for test_on_page in range(len(tests)):
            if seen == test_index:
                return page_index, test_on_page
            seen += 1
    raise IndexError(test_index)


def render_sheet(sheet: SheetData,
                 text: tp.Optional[layout.SheetText] = None
                 ) -> tp.List[np.ndarray]:
    """Return the two filled-in page images for one sheet."""
    pages = [page.copy() for page in _blank_pages(text)]
    levels = text.latin_levels if text else layout.LATIN_LEVELS

    ids = [sheet.student_id,
           sheet.student_id if sheet.back_student_id is None
           else sheet.back_student_id]
    for index, page in enumerate(pages):
        _stamp_digits(page, layout.STUDENT_ID_COLUMN,
                      ids[index] if index < len(ids) else sheet.student_id)

    if sheet.latin_level:
        index = levels.index(sheet.latin_level)
        _stamp(pages[0], layout.LATIN_LEVEL_COLUMN,
               layout.LATIN_LEVEL_FIRST_ROW + index)

    for test_index, test_id in enumerate(sheet.test_ids):
        page_index, test_on_page = _test_position(test_index)
        _stamp_digits(pages[page_index],
                      layout.PAGE_TEST_ID_COLUMNS[page_index][test_on_page],
                      test_id)

    for test_index, answers in enumerate(sheet.answers):
        page_index, test_on_page = _test_position(test_index)
        faint = set(sheet.faint.get(test_index, ()))
        for offset, answer in enumerate(answers):
            answer = (answer or "").strip().upper()
            # Careful: "" is a substring of "ABCDE", so a blank answer has to
            # be rejected by length before any membership test.
            if len(answer) != 1 or answer not in layout.OPTIONS:
                continue
            number = offset + 1
            column, row = _answer_cell(page_index, test_on_page, number)
            _stamp(pages[page_index],
                   column + layout.OPTIONS.index(answer),
                   row,
                   fraction=sheet.faint_fraction if number in faint else 1.0)

    for test_index, number, option in sheet.extra_marks:
        page_index, test_on_page = _test_position(test_index)
        column, row = _answer_cell(page_index, test_on_page, number)
        _stamp(pages[page_index], column + layout.OPTIONS.index(option), row)

    return pages


def blank_page(text: tp.Optional[layout.SheetText] = None) -> np.ndarray:
    """An empty sheet of paper, the size of a scanned page.

    What a duplex scanner produces for the reverse of a single-sided
    original.
    """
    return np.full_like(_blank_pages(text)[0], 255)


def write_pdf(pages: tp.Sequence[np.ndarray], path: pathlib.Path
              ) -> pathlib.Path:
    """Save a sequence of page images as a multi-page PDF."""
    images = [Image.fromarray(page).convert("RGB") for page in pages]
    images[0].save(str(path), "PDF", save_all=True, append_images=images[1:],
                   resolution=float(SCAN_SCALE * 72))
    return path


def write_batch(sheets: tp.Sequence[SheetData], path: pathlib.Path,
                page_order: tp.Optional[tp.Sequence[int]] = None,
                text: tp.Optional[layout.SheetText] = None
                ) -> pathlib.Path:
    """Render several sheets into one PDF, front page of each sheet first.

    `page_order` optionally permutes the finished page list, so a test can
    hand the reader a mis-collated batch.
    """
    pages: tp.List[np.ndarray] = []
    for sheet in sheets:
        pages.extend(render_sheet(sheet, text))
    if page_order is not None:
        pages = [pages[index] for index in page_order]
    return write_pdf(pages, path)


# --- damaging a page, to exercise the corner-finding fallbacks -----------


def _corner_pixels(page: np.ndarray, which: str) -> tp.Tuple[int, int]:
    """Roughly where one corner mark sits, in pixels."""
    height, width = page.shape[:2]
    inset = layout.CORNER_INSET_IN
    x = inset if which in ("tl", "bl") else layout.PAGE_WIDTH_IN - inset
    y = inset if which in ("tl", "tr") else layout.PAGE_HEIGHT_IN - inset
    return (int(x / layout.PAGE_WIDTH_IN * width),
            int(y / layout.PAGE_HEIGHT_IN * height))


def scribble_over(page: np.ndarray, which: str,
                  gray: int = 120) -> np.ndarray:
    """Draw a squiggle across one corner mark.

    Composited with a minimum, because that is what ink on paper does: it can
    only darken. Drawing the stroke straight onto the page would *lighten* the
    black mark underneath, which no pen can do, and would be testing damage
    that cannot happen.
    """
    x, y = _corner_pixels(page, which)
    stroke = np.full_like(page, 255)
    points = np.array([[x - 60, y - 10], [x - 20, y + 35],
                       [x + 25, y - 30], [x + 70, y + 20]], np.int32)
    cv2.polylines(stroke, [points], False, int(gray), thickness=9,
                  lineType=cv2.LINE_AA)
    return np.minimum(page, stroke)


def shade_out(page: np.ndarray, which: str) -> np.ndarray:
    """Black one corner mark out completely, so it has no shape left.

    Drawn over the mark's own footprint and a little wider, which is what
    somebody filling one in with a pen would leave behind.
    """
    page = page.copy()
    x, y = _corner_pixels(page, which)
    per_inch = page.shape[1] / layout.PAGE_WIDTH_IN
    if which == "tl":
        half = int(layout.L_MARK_SIZE_IN * per_inch) // 2   # hangs inwards
        cv2.rectangle(page, (x - half - 4, y - half - 4),
                      (x + half + 4, y + 4), 0, -1)
        cv2.rectangle(page, (x - half - 4, y - half - 4),
                      (x + 4, y + half + 4), 0, -1)
    else:
        half = int(layout.CORNER_SQUARE_SIZE_IN * per_inch / 2) + 5
        cv2.rectangle(page, (x - half, y - half), (x + half, y + half), 0, -1)
    return page


def bury(page: np.ndarray, which: str) -> np.ndarray:
    """Scribble densely over one corner mark, the way a bored student does.

    Not a single stroke but a mass of overlapping loops, which is what defeats
    shape matching: the mark and the pen merge into one blob that is neither
    square nor solid, so nothing recognises it and nothing measures it.
    """
    x, y = _corner_pixels(page, which)
    ink = np.full_like(page, 255)
    for index in range(14):
        angle = index * 25
        cv2.ellipse(ink, (x + (index % 5) * 6 - 12, y + (index % 3) * 6 - 6),
                    (46, 16), angle, 0, 360, 0, 3, cv2.LINE_AA)
    return np.minimum(page, ink)


def doodle_in_margin(page: np.ndarray) -> np.ndarray:
    """Idle scribbles well away from the marks."""
    page = page.copy()
    height, width = page.shape[:2]
    for offset in range(5):
        y = int(height * 0.35) + offset * 26
        cv2.line(page, (30, y), (int(width * 0.06), y + 18), 90, 7)
    cv2.putText(page, "hi!!", (24, int(height * 0.55)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, 70, 3)
    return page


def competing(test_index: int, question: int,
              answers: tp.Sequence[str], count: int = 2
              ) -> tp.List[tp.Tuple[int, int, str]]:
    """Extra marks on other options of one question, to make it ambiguous.

    A single faint bubble is not ambiguous - it towers over its neighbours and
    is read as the answer, which is the whole point of judging a row against
    itself. Genuine doubt looks like a pen dragged across several bubbles, so
    that is what a test needing a review row has to produce.
    """
    intended = (answers[question - 1] or "").strip().upper()
    others = [option for option in layout.OPTIONS if option != intended]
    return [(test_index, question, option) for option in others[:count]]


def answer_key(letter: str = "A") -> tp.List[str]:
    return [letter] * layout.QUESTIONS_PER_TEST
