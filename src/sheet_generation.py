"""Render the two-page CAJCL State Convention answer sheet as a PDF.

The sheet carries three 80-question tests:

* **Front (page 1)** - directions, write-in name/school blanks, the 4-digit
  Student ID block, the Latin level block, and Test 1 (Q1-Q80).
* **Back (page 2)** - the Student ID block again (repeated so a separated back
  page can still be matched to its front), Test 2 and Test 3.

Run as a module to (re)generate the printable sheet::

    python -m src.sheet_generation src/assets/cajcl_answer_sheet.pdf

Every coordinate comes from :mod:`sheet_layout`, which is also what
:mod:`grid_info` uses to describe the grid to the reader.
"""

import pathlib
import typing as tp

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdfcanvas

from . import sheet_layout as layout

# The sheet is set in Times, whose letterforms descend from Roman
# inscriptional capitals - a better fit for the Classical League than the
# Helvetica of the stock OpenMCR sheets. Times is one of the PDF base-14
# fonts, so it needs no embedding and renders identically everywhere.
SERIF = "Times-Roman"
SERIF_BOLD = "Times-Bold"
SERIF_ITALIC = "Times-Italic"

ASSETS_DIR = pathlib.Path(__file__).parent / "assets"
LOGO_PATH = ASSETS_DIR / "cajcl.png"
DEFAULT_OUTPUT = ASSETS_DIR / "cajcl_answer_sheet.pdf"

TITLE = "CALIFORNIA JUNIOR CLASSICAL LEAGUE"

# The title, footer, and collation bar are placed relative to the *grid*, not
# to the paper edge, so that nothing printed sits closer to an edge than the
# corner marks do (see sheet_layout.MARK_EDGE_CLEARANCE_IN). Earlier the title
# sat in the top margin and the bar 0.13in off the bottom edge, both inside the
# unprintable border of a typical inkjet.

#: Baseline of the title, as a grid row. Row 1.2 keeps it below the corner
#: marks, clear of the L-mark to its left, and above the page-code bubbles.
TITLE_ROW = 1.2
# The footer and the collation bar sit on the same line as the two bottom
# corner marks, their bottoms flush with the marks' bottom edge. That puts them
# as close to the paper edge as the sheet ever prints - no closer than the
# marks the reader depends on - and well below the circle the reader averages
# for the last answer row, which reaches down to y=0.660in. Ink any higher
# would be read as part of Q40 and Q80.

#: Bottom edge of the collation bar, flush with the bottom corner marks.
COLLATION_BAR_BOTTOM_IN = layout.MARK_EDGE_CLEARANCE_IN
COLLATION_BAR_LENGTH_IN = 1.8
COLLATION_BAR_THICKNESS_IN = 0.07
#: Point size of the footer, needed to work out where its baseline goes.
FOOTER_FONT_SIZE = 7
#: Inset of the bar from the grid's left/right edge, enough to keep it clear
#: of the bottom corner marks.
COLLATION_BAR_INSET_IN = 0.4

DIRECTIONS: tp.Tuple[str, ...] = (
    "1.  Use a No. 2 pencil or a black or blue pen.",
    "2.  Fill each bubble completely and darkly, inside the circle.",
    "3.  To change an answer, erase it completely.",
    "4.  Print your name and school on the lines above, then bubble your Latin level.",
    "5.  Bubble your Student ID on both sides of this sheet.",
    "6.  Copy the 4-digit Test ID printed on each booklet for each test.",
    "7.  Do not fold or crease this sheet.",
)


# --- primitives ----------------------------------------------------------


def _text(c: pdfcanvas.Canvas, x_in: float, y_in: float, string: str,
          size: float = 9, font: str = SERIF, char_space: float = 0.0):
    c.setFont(font, size)
    c.drawString(x_in * inch, y_in * inch, string, charSpace=char_space)


def _text_centered(c: pdfcanvas.Canvas, x_in: float, y_in: float, string: str,
                   size: float = 9, font: str = SERIF,
                   char_space: float = 0.0):
    c.setFont(font, size)
    c.drawCentredString(x_in * inch, y_in * inch, string, charSpace=char_space)


def _bubble(c: pdfcanvas.Canvas, column: int, row: int, filled: bool = False):
    x, y = layout.cell_to_inches(column + 0.5, row + 0.5)
    r = layout.bubble_radius_in()
    c.circle(x * inch, y * inch, r * inch, stroke=1, fill=1 if filled else 0)


def _label_in_cell(c: pdfcanvas.Canvas, column: int, row: int, string: str,
                   size: float = 7, white: bool = False):
    """Draw a short label centred inside a grid cell (used inside bubbles)."""
    x, y = layout.cell_to_inches(column + 0.5, row + 0.5)
    c.setFont(SERIF, size)
    if white:
        c.setFillGray(1.0)
    c.drawCentredString(x * inch, (y - 0.03) * inch, string)
    if white:
        c.setFillGray(0.0)


def _caption(c: pdfcanvas.Canvas, column: int, row: int, string: str,
             size: float = 8.5):
    """Draw a letter-spaced block caption above a bubble block."""
    x, y = layout.cell_to_inches(column, row)
    _text(c, x, y + 0.06, string.upper(), size=size, font=SERIF_BOLD,
          char_space=0.6)


# --- registration marks --------------------------------------------------


def _l_mark(c: pdfcanvas.Canvas):
    # The L's outer vertex sits exactly on the grid's top-left corner and the
    # mark extends inwards, which is what `corner_finding` assumes when it
    # projects the grid corner back out of the mark (see
    # `sheet_layout.L_MARK_ORIGIN_OFFSET_IN`). Keeping it inside the corner
    # inset also keeps it clear of any printer's unprintable margin.
    size = layout.L_MARK_SIZE_IN
    arm = size / 2
    x = layout.CORNER_INSET_IN
    y = layout.PAGE_HEIGHT_IN - layout.CORNER_INSET_IN - size
    points = [(x, y + size), (x + size, y + size), (x + size, y + size - arm),
              (x + arm, y + size - arm), (x + arm, y), (x, y)]
    path = c.beginPath()
    path.moveTo(points[0][0] * inch, points[0][1] * inch)
    for px, py in points[1:]:
        path.lineTo(px * inch, py * inch)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


def _corner_square(c: pdfcanvas.Canvas, cx: float, cy: float):
    s = layout.CORNER_SQUARE_SIZE_IN
    c.rect((cx - s / 2) * inch, (cy - s / 2) * inch, s * inch, s * inch,
           stroke=0, fill=1)


def _registration_marks(c: pdfcanvas.Canvas):
    inset = layout.CORNER_INSET_IN
    right = layout.PAGE_WIDTH_IN - inset
    top = layout.PAGE_HEIGHT_IN - inset
    _l_mark(c)
    _corner_square(c, right, top)
    _corner_square(c, inset, inset)
    _corner_square(c, right, inset)


# --- header, page code, collation mark -----------------------------------


def _header(c: pdfcanvas.Canvas):
    """Just the title. Which side you are looking at is said by the page-code
    bubbles immediately below and again by the footer."""
    _, y = layout.cell_to_inches(0, TITLE_ROW)
    _text_centered(c, layout.PAGE_WIDTH_IN / 2, y, TITLE, size=11,
                   font=SERIF_BOLD, char_space=1.4)


def _page_code(c: pdfcanvas.Canvas, page_index: int):
    """Two bubbles, the one matching this side printed solid.

    The reader uses this to prove that every front page in a batch is
    immediately followed by its own back page.
    """
    first = layout.PAGE_CODE_FIRST_COLUMN
    row = layout.PAGE_CODE_ROW
    x, y = layout.cell_to_inches(first, row + 0.5)
    c.setFont(SERIF_BOLD, 7.5)
    c.drawRightString((x - 0.06) * inch, (y - 0.035) * inch, "PAGE",
                      charSpace=0.5)
    for option in range(layout.PAGE_CODE_OPTIONS):
        filled = option == page_index
        _bubble(c, first + option, row, filled=filled)
        _label_in_cell(c, first + option, row, str(option + 1), size=7,
                       white=filled)


def _collation_mark(c: pdfcanvas.Canvas, page_index: int):
    """A solid bar on the last grid row, at a different horizontal position on
    each side. Riffling a printed stack shows at a glance that fronts and
    backs alternate, and which way up a page is.

    It sits on the grid's bottom row rather than out in the paper margin, so it
    survives on any printer that can render the corner marks, and it is inset
    far enough to stay clear of the two bottom marks.
    """
    left = layout.CORNER_INSET_IN + COLLATION_BAR_INSET_IN
    if page_index == 0:
        x = left
    else:
        x = layout.PAGE_WIDTH_IN - left - COLLATION_BAR_LENGTH_IN
    c.rect(x * inch, COLLATION_BAR_BOTTOM_IN * inch,
           COLLATION_BAR_LENGTH_IN * inch, COLLATION_BAR_THICKNESS_IN * inch,
           stroke=0, fill=1)


def _footer_baseline() -> float:
    """Baseline that puts the footer's descenders exactly on the bottom edge of
    the corner marks, so no part of it reaches nearer the paper edge."""
    descender = abs(pdfmetrics.getFont(SERIF_ITALIC).face.descent) / 1000
    return COLLATION_BAR_BOTTOM_IN + (descender * FOOTER_FONT_SIZE / 72)


def _footer(c: pdfcanvas.Canvas, page_index: int):
    side = layout.PAGE_CODE_LABELS[page_index]
    _text_centered(
        c, layout.PAGE_WIDTH_IN / 2, _footer_baseline(),
        f"{side} — page {page_index + 1} of {layout.PAGES_PER_SHEET}",
        size=FOOTER_FONT_SIZE, font=SERIF_ITALIC)


# --- blocks --------------------------------------------------------------


def _digit_block(c: pdfcanvas.Canvas, first_column: int, caption: str):
    _caption(c, first_column, layout.ID_LABEL_ROW, caption)
    for digit_index in range(layout.ID_DIGITS):
        column = first_column + digit_index
        for digit in range(layout.BUBBLES_PER_DIGIT):
            row = layout.ID_FIRST_BUBBLE_ROW + digit
            _bubble(c, column, row)
            _label_in_cell(c, column, row, str(digit))


def _latin_level_block(c: pdfcanvas.Canvas):
    column = layout.LATIN_LEVEL_COLUMN
    _caption(c, column, layout.ID_LABEL_ROW, "Latin Level")
    for index, level in enumerate(layout.LATIN_LEVELS):
        row = layout.LATIN_LEVEL_FIRST_ROW + index
        _bubble(c, column, row)
        x, y = layout.cell_to_inches(column + 1, row + 0.5)
        _text(c, x + 0.02, y - 0.035, level, size=8)


def _write_in_lines(c: pdfcanvas.Canvas):
    left, _ = layout.cell_to_inches(0, 0)
    right, _ = layout.cell_to_inches((layout.GRID_COLUMNS / 2) - 0.5, 0)
    for offset, caption in enumerate(("First Name", "Last Name", "School")):
        _, y = layout.cell_to_inches(0, 21.6 + (offset * 2.6))
        _text(c, left, y, caption, size=9, font=SERIF_BOLD)
        label_width = c.stringWidth(caption, SERIF_BOLD, 9) / 72
        c.setLineWidth(0.6)
        c.line((left + label_width + 0.06) * inch, (y - 0.025) * inch,
               right * inch, (y - 0.025) * inch)


def _directions(c: pdfcanvas.Canvas):
    left, _ = layout.cell_to_inches(0, 0)
    _, heading_y = layout.cell_to_inches(0, 30)
    _text(c, left, heading_y, "DIRECTIONS", size=9, font=SERIF_BOLD,
          char_space=1.0)
    for index, line in enumerate(DIRECTIONS):
        _, y = layout.cell_to_inches(0, 31.4 + (index * 1.1))
        _text(c, left, y, line, size=7.6)


def _logo(c: pdfcanvas.Canvas):
    """Draw a grayscale CAJCL emblem across the foot of the front page."""
    if not LOGO_PATH.exists():
        return
    from PIL import Image

    image = Image.open(str(LOGO_PATH))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flattened = Image.new("RGBA", image.size, (255, 255, 255, 255))
        flattened.alpha_composite(image)
        image = flattened
    grayscale = image.convert("L").convert("RGB")

    size = 1.75
    centre_x, _ = layout.cell_to_inches(layout.GRID_COLUMNS / 4, 0)
    _, top_y = layout.cell_to_inches(0, 45.5)
    c.drawImage(ImageReader(grayscale), (centre_x - size / 2) * inch,
                (top_y - size) * inch, size * inch, size * inch,
                preserveAspectRatio=True, anchor="c")
    _text_centered(c, centre_x, top_y - size - 0.16,
                   "CALIFORNIA JUNIOR CLASSICAL LEAGUE", size=6.5,
                   font=SERIF_BOLD, char_space=0.9)


def _answer_block(c: pdfcanvas.Canvas, first_column: int, first_question: int):
    for index in range(layout.ROWS_PER_SUBCOLUMN):
        row = layout.MCQ_FIRST_ROW + index
        number_x, number_y = layout.cell_to_inches(first_column - 0.15,
                                                   row + 0.5)
        c.setFont(SERIF, 7)
        c.drawRightString(number_x * inch, (number_y - 0.035) * inch,
                          str(first_question + index))
        for option_index, option in enumerate(layout.OPTIONS):
            column = first_column + option_index
            _bubble(c, column, row)
            _label_in_cell(c, column, row, option)


def _test_block(c: pdfcanvas.Canvas, page_index: int, test_on_page: int,
                test_number: int):
    id_column = layout.PAGE_TEST_ID_COLUMNS[page_index][test_on_page]
    _digit_block(c, id_column, f"Test {test_number} ID")
    for subcolumn_index, column in enumerate(
            layout.PAGE_SUBCOLUMNS[page_index][test_on_page]):
        _answer_block(c, column,
                      first_question=layout.question_number(
                          subcolumn_index, 0))


# --- pages ---------------------------------------------------------------


def draw_page(c: pdfcanvas.Canvas, page_index: int):
    _registration_marks(c)
    _header(c)
    _page_code(c, page_index)

    _digit_block(c, layout.STUDENT_ID_COLUMN, "Student ID")

    if page_index == 0:
        _latin_level_block(c)
        _write_in_lines(c)
        _directions(c)
        _logo(c)

    tests_before = sum(
        len(page) for page in layout.PAGE_SUBCOLUMNS[:page_index])
    for test_on_page in range(len(layout.PAGE_SUBCOLUMNS[page_index])):
        _test_block(c, page_index, test_on_page,
                    tests_before + test_on_page + 1)

    _collation_mark(c, page_index)
    _footer(c, page_index)


def render(output_path: pathlib.Path) -> pathlib.Path:
    """Write the complete two-page sheet to ``output_path``."""
    output_path = pathlib.Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(output_path), pagesize=LETTER)
    c.setTitle("CAJCL State Convention Answer Sheet")
    for page_index in range(layout.PAGES_PER_SHEET):
        draw_page(c, page_index)
        c.showPage()
    c.save()
    return output_path


if __name__ == "__main__":
    import sys
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    print(f"Wrote {render(target)}")
