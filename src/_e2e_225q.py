"""Generate a synthetic 2-page PDF for the 225q variant with a known
key (Student ID = 9999999999, all A's) and verify that the grader
correctly produces 3 key rows that can score a student sheet.

This is a quick smoke test for the grader flow on the new variant.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, '/Users/carlliu/open-mcr/src')

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from . import grid_info as grid_i
from .process_input import process_input

# Page dimensions - match _gen_twosided_pdf.py
PAGE_WIDTH_IN = 8.5
PAGE_HEIGHT_IN = 11.0
GRID_COLS = 24
GRID_ROWS = 60
MARGIN_IN = 0.5
TOP_MARGIN_IN = 0.5  # must match _gen_twosided_pdf.py (= CORNER_INSET_IN for grid alignment)
USABLE_WIDTH = PAGE_WIDTH_IN - 2 * MARGIN_IN
USABLE_HEIGHT = PAGE_HEIGHT_IN - TOP_MARGIN_IN - MARGIN_IN
CELL_WIDTH = USABLE_WIDTH / GRID_COLS
CELL_HEIGHT = USABLE_HEIGHT / GRID_ROWS
BUBBLE_FRAC = 0.40


def cell_to_xy(col, row):
    x = MARGIN_IN + col * CELL_WIDTH
    y = PAGE_HEIGHT_IN - TOP_MARGIN_IN - row * CELL_HEIGHT
    return x, y


def render_sheet_pdf(out_pdf, student_id, test_ids, answers):
    """Render a 2-page 225q sheet with the given data filled in.

    answers is a list of 3 lists, each with 75 answer strings ('A'-'E').
    test_ids is a list of 3 test ID strings.
    student_id is a 10-char string of digits.
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib.pagesizes import LETTER
    from _gen_twosided_pdf import draw_page1, draw_page2

    c = canvas.Canvas(out_pdf, pagesize=LETTER)
    draw_page1(c)
    c.showPage()
    draw_page2(c)
    c.showPage()
    c.save()

    # Convert to high-res images
    pdf = pdfium.PdfDocument(out_pdf)
    images = []
    for page in pdf:
        img = page.render(scale=4).to_pil()
        images.append(np.array(img.convert('L')))

    px_per_in = images[0].shape[1] / PAGE_WIDTH_IN
    radius_px = int(BUBBLE_FRAC * min(CELL_WIDTH, CELL_HEIGHT) * px_per_in)
    digit_label_radius_px = int(0.35 * min(CELL_WIDTH, CELL_HEIGHT) * px_per_in)

    page_specs = [
        # page_index, [(test_id_col, [(mcq_col, num_q, first_q, answer_offset), ...]), ...]
        (0, [(13, [(13, 38, 1, 0), (19, 37, 39, 0)])]),
        (1, [
            (0, [(0, 38, 1, 1), (6, 37, 39, 1)]),
            (13, [(13, 38, 1, 2), (19, 37, 39, 2)]),
        ]),
    ]

    for page_idx, page_tests in page_specs:
        img = images[page_idx]

        if page_idx == 0:
            for digit_idx, ch in enumerate(student_id):
                if not ch.isdigit():
                    continue
                digit = int(ch)
                col = 2 + digit_idx
                row = 5 + 1 + digit  # SID_ROW=5, field_digits draws at row_start+1+digit
                cx, cy = cell_to_xy(col + 0.5, row + 0.5)
                cy_pixel = (PAGE_HEIGHT_IN - cy) * px_per_in
                cx_pixel = cx * px_per_in
                cv2.circle(img, (int(cx_pixel), int(cy_pixel)),
                           digit_label_radius_px, (0,), -1)

        for test_idx, (test_id_col, mcq_cols) in enumerate(page_tests):
            test_id = test_ids[test_idx]
            for digit_idx, ch in enumerate(test_id):
                if not ch.isdigit():
                    continue
                digit = int(ch)
                col = test_id_col + digit_idx
                row = 5 + 1 + digit  # TID_ROW=5, same formula
                cx, cy = cell_to_xy(col + 0.5, row + 0.5)
                cy_pixel = (PAGE_HEIGHT_IN - cy) * px_per_in
                cx_pixel = cx * px_per_in
                cv2.circle(img, (int(cx_pixel), int(cy_pixel)),
                           digit_label_radius_px, (0,), -1)

            for mcq_col, num_q, first_q, ans_offset in mcq_cols:
                for q_idx in range(num_q):
                    q_num = first_q + q_idx - 1
                    answer = answers[ans_offset][q_num] if ans_offset < len(answers) else ""
                    if not answer or answer not in "ABCDE":
                        continue
                    opt_idx = "ABCDE".index(answer)
                    row = 19 + q_idx  # MCQ_FIRST_ROW=19
                    col = mcq_col + opt_idx
                    cx, cy = cell_to_xy(col + 0.5, row + 0.5)
                    cy_pixel = (PAGE_HEIGHT_IN - cy) * px_per_in
                    cx_pixel = cx * px_per_in
                    cv2.circle(img, (int(cx_pixel), int(cy_pixel)),
                               radius_px, (0,), -1)

    # Convert back to PIL images and save to PDF
    pil_images = [Image.fromarray(i) for i in images]
    pil_images[0].save(out_pdf, save_all=True, append_images=pil_images[1:])


def main():
    # Step 1: Generate a key sheet (Student ID = 99999, all 'A' for all 3 tests)
    key_pdf = "/tmp/test_key.pdf"
    render_sheet_pdf(
        key_pdf,
        student_id="99999",
        test_ids=["1111", "2222", "3333"],
        answers=[
            ["A"] * 75,
            ["A"] * 75,
            ["A"] * 75,
        ],
    )

    # Step 2: Generate a student sheet (Student ID = 00001, mixed answers)
    student_pdf = "/tmp/test_student.pdf"
    render_sheet_pdf(
        student_pdf,
        student_id="00001",
        test_ids=["1111", "2222", "3333"],
        answers=[
            ["A" if i < 70 else "B" for i in range(75)],  # Test 1: 70 A's + 5 B's = 70 correct
            ["A" if i < 50 else "B" for i in range(75)],  # Test 2: 50 A's + 25 B's = 50 correct
            ["A"] * 75,                                    # Test 3: 75 A's = 75 correct
        ],
    )

    # Step 3: Run process_input on both files
    out_dir = "/tmp/test_225q_output"
    if os.path.exists(out_dir):
        import shutil
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print(f"Processing key + student sheets...")
    process_input(
        image_paths=[Path(key_pdf), Path(student_pdf)],
        output_folder=Path(out_dir),
        multi_answers_as_f=False,
        empty_answers_as_g=False,
        keys_file=None,
        arrangement_file=None,
        sort_results=False,
        output_mcta=False,
        debug_mode_on=True,
        form_variant=grid_i.form_two_sided_225q,
        progress_tracker=None,
        files_timestamp=None,
    )

    # Step 4: Inspect the outputs
    results_path = Path(out_dir) / "results.csv"
    keys_path = Path(out_dir) / "keys.csv"
    scores_path = Path(out_dir) / "scores.csv"

    print("\n=== keys.csv ===")
    print(keys_path.read_text())
    print("\n=== results.csv ===")
    print(results_path.read_text())
    print("\n=== scores.csv ===")
    if scores_path.exists():
        print(scores_path.read_text())
    else:
        print("NO scores.csv!")


if __name__ == "__main__":
    main()