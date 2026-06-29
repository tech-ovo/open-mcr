"""Render a 2-page 240q bubble sheet PDF directly with reportlab.

Layout Requirements:
- Two sides, each with two columns (effectively 4 columns across the sheet).
- 3 of these columns should have 80 MCQ slots (5 options each) and a Test ID slot at the top.
- The 1st column (Page 1, Col 1) should have a Student ID slot and a blank for the name.
- Each MCQ column is split into 2 subcolumns (40 questions each).
"""

from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import LETTER

PAGE_WIDTH_IN, PAGE_HEIGHT_IN = 8.5, 11.0

# Grid layout
GRID_COLS = 24
GRID_ROWS = 60

MARGIN_IN = 0.5
# TOP_MARGIN_IN must equal CORNER_INSET_IN (0.5) so that the PDF cell grid
# aligns exactly with the grid detected from the corner marks.
TOP_MARGIN_IN = 0.5  # = CORNER_INSET_IN
USABLE_WIDTH = PAGE_WIDTH_IN - 2 * MARGIN_IN
# Height is the full span between top and bottom corner marks: 11 - 0.5 - 0.5 = 10 in
USABLE_HEIGHT = PAGE_HEIGHT_IN - TOP_MARGIN_IN - MARGIN_IN  # = 10.0 in

CELL_WIDTH = USABLE_WIDTH / GRID_COLS
CELL_HEIGHT = USABLE_HEIGHT / GRID_ROWS  # = 10/60 = 0.1667 in

# Increase bubble size relative to cell
BUBBLE_FRAC = 0.40

L_MARK_W = 0.3125
L_MARK_H = 0.3125
CORNER_SQUARE_SIZE = 0.15625
CORNER_INSET_IN = 0.5


def cell_to_xy(col, row):
    x = MARGIN_IN + col * CELL_WIDTH
    y = PAGE_HEIGHT_IN - TOP_MARGIN_IN - row * CELL_HEIGHT
    return x, y


def bubble_at(c, col, row):
    cx, cy = cell_to_xy(col + 0.5, row + 0.5)
    r = min(CELL_WIDTH, CELL_HEIGHT) * BUBBLE_FRAC * inch
    c.circle(cx * inch, cy * inch, r, stroke=1, fill=0)


def text_at(c, x, y, s, size=9, bold=False):
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    c.drawString(x * inch, y * inch, s)


def text_centered_at_cell(c, col, row, s, size=8, bold=False, dy=0.0):
    cx, cy = cell_to_xy(col + 0.5, row + 0.5)
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    c.drawCentredString(cx * inch, (cy - 0.03 + dy) * inch, s)


def header_label(c, col_start, row, n_cells, label):
    x, y = cell_to_xy(col_start, row)
    text_at(c, x, y + 0.1, label, size=10, bold=True)


def field_digits(c, col_start, row_start, n_fields, label=""):
    header_label(c, col_start, row_start, n_fields, label)
    for field_idx in range(n_fields):
        col = col_start + field_idx
        for digit in range(10):
            row = row_start + 1 + digit
            bubble_at(c, col, row)
            text_centered_at_cell(c, col, row, str(digit), size=7)


def mcq_column(c, col_start, row_start, num_questions, options="ABCDE",
               first_q_number=1, question_label_col=None):
    if question_label_col is None:
        question_label_col = col_start - 1
    for q in range(num_questions):
        row = row_start + q
        cx, cy = cell_to_xy(question_label_col + 0.8, row + 0.5)
        text_at(c, cx, cy - 0.04, str(q + first_q_number), size=7)
        for o, letter in enumerate(options):
            bubble_at(c, col_start + o, row)
            text_centered_at_cell(c, col_start + o, row, letter, size=7)


def l_mark(c, x, y, w, h):
    cs = w / 2
    points = [
        (x, y + h),
        (x + w, y + h),
        (x + w, y + h - cs),
        (x + cs, y + h - cs),
        (x + cs, y),
        (x, y),
    ]
    path = c.beginPath()
    path.moveTo(points[0][0] * inch, points[0][1] * inch)
    for px, py in points[1:]:
        path.lineTo(px * inch, py * inch)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


def corner_square(c, cx, cy):
    s = CORNER_SQUARE_SIZE
    c.rect((cx - s / 2) * inch, (cy - s / 2) * inch,
           s * inch, s * inch, stroke=0, fill=1)


def page_corner_marks(c):
    l_mark(c, CORNER_INSET_IN, PAGE_HEIGHT_IN - CORNER_INSET_IN - L_MARK_H,
           L_MARK_W, L_MARK_H)
    corner_square(c, PAGE_WIDTH_IN - CORNER_INSET_IN, PAGE_HEIGHT_IN - CORNER_INSET_IN)
    corner_square(c, CORNER_INSET_IN, CORNER_INSET_IN)
    corner_square(c, PAGE_WIDTH_IN - CORNER_INSET_IN, CORNER_INSET_IN)


def page_footer(c, label):
    font = "Helvetica"
    c.setFont(font, 8)
    width = c.stringWidth(label, font, 8)
    x = (PAGE_WIDTH_IN - width / 72) / 2
    text_at(c, x, 0.08, label, size=8)

# Layout constants
# Content must start well below the top-left L-mark (bottom at y≈10.19in).
# With CELL_HEIGHT=10/60=0.1667in and TID_ROW=5:
#   header y = 11 - 0.5 - 5*0.1667 = 9.67in (safely below the L-mark)
SID_ROW = 5
TID_ROW = 5
MCQ_FIRST_ROW = 19

def draw_page1(c):
    page_corner_marks(c)

    # Header
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(4.25 * inch, 10.4 * inch, "CAJCL State Convention 2027")

    # Col 1: Student ID (5 digits)
    field_digits(c, col_start=2, row_start=SID_ROW, n_fields=5, label="Student ID")
    
    x_pos = 2 * CELL_WIDTH + MARGIN_IN
    text_at(c, x_pos, 6.5, "First Name: _________________________", size=10, bold=True)
    text_at(c, x_pos, 5.5, "Last Name: _________________________", size=10, bold=True)
    text_at(c, x_pos, 4.5, "School: _________________________", size=10, bold=True)
    text_at(c, x_pos, 3.5, "Latin Level: _________________________", size=10, bold=True)

    # Test 1
    mcq_col_1_start = 13 
    field_digits(c, col_start=mcq_col_1_start, row_start=TID_ROW, n_fields=4, label="Test ID 1")
    mcq_column(c, col_start=mcq_col_1_start, row_start=MCQ_FIRST_ROW, num_questions=40, first_q_number=1)

    mcq_col_2_start = 19 
    mcq_column(c, col_start=mcq_col_2_start, row_start=MCQ_FIRST_ROW, num_questions=40, first_q_number=41)

    page_footer(c, "Page 1 / 2")


def draw_page2(c):
    page_corner_marks(c)

    # Header
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(4.25 * inch, 10.4 * inch, "CAJCL State Convention 2027")

    # Test 2: Shifted LEFT
    t2_sub1_start = 0 
    t2_sub2_start = 6 
    field_digits(c, col_start=t2_sub1_start, row_start=TID_ROW, n_fields=4, label="Test ID 2")
    mcq_column(c, col_start=t2_sub1_start, row_start=MCQ_FIRST_ROW, num_questions=40, first_q_number=1)
    mcq_column(c, col_start=t2_sub2_start, row_start=MCQ_FIRST_ROW, num_questions=40, first_q_number=41)

    # Test 3
    t3_sub1_start = 13 
    t3_sub2_start = 19
    field_digits(c, col_start=t3_sub1_start, row_start=TID_ROW, n_fields=4, label="Test ID 3")
    mcq_column(c, col_start=t3_sub1_start, row_start=MCQ_FIRST_ROW, num_questions=40, first_q_number=1)
    mcq_column(c, col_start=t3_sub2_start, row_start=MCQ_FIRST_ROW, num_questions=40, first_q_number=41)

    page_footer(c, "Page 2 / 2")


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/240q_twosided.pdf"
    c = canvas.Canvas(out, pagesize=LETTER)
    draw_page1(c)
    c.showPage()
    draw_page2(c)
    c.showPage()
    c.save()
    print(f"Wrote {out}")
