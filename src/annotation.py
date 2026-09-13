"""Marked-up copies of the scans, for checking the grading by eye.

Every bubble the reader acted on is ringed, so the whole reading is visible at
a glance - not only the answers but the Student ID, Latin level, Test IDs and
page code that decide whose paper it is and which key it is scored against.

* **green** - the key says this is correct
* **red** - the student filled this and it is not correct
* **blue** - read as filled, with no key to judge it against (all the ID and
  metadata bubbles, and every answer when no key was supplied)
* **amber** - too close to the cutoff to call, and listed in the review sheet
"""

import pathlib
import shutil
import tempfile
import typing as tp

import cv2
import numpy as np

from . import image_utils

# BGR, because that is what OpenCV draws in.
CORRECT_COLOR = (60, 150, 40)
INCORRECT_COLOR = (48, 48, 210)
READ_COLOR = (170, 110, 40)
UNCLEAR_COLOR = (20, 160, 235)
TEXT_COLOR = (40, 40, 40)

RING_FRACTION = 0.22
FILL_OPACITY = 0.30


def _ring(image: np.ndarray, circle: tp.Tuple[float, float, float],
          color: tp.Tuple[int, int, int]):
    x, y, radius = circle
    centre = (int(round(x)), int(round(y)))
    radius_px = max(int(round(radius)), 3)
    thickness = max(int(round(radius_px * RING_FRACTION)), 2)
    overlay = image.copy()
    cv2.circle(overlay, centre, radius_px, color, -1, lineType=cv2.LINE_AA)
    cv2.addWeighted(overlay, FILL_OPACITY, image, 1 - FILL_OPACITY, 0, image)
    cv2.circle(image, centre, radius_px, color, thickness,
               lineType=cv2.LINE_AA)


def _legend(image: np.ndarray, heading: str, scored: bool):
    height, width = image.shape[:2]
    scale = width / 1700.0
    band_top = int(height - (0.44 * height / 11.0))
    cv2.rectangle(image, (0, band_top), (width, height), (255, 255, 255), -1)

    radius = max(int(round(11 * scale)), 5)
    x = int(round(60 * scale))
    y = band_top + int(round((height - band_top) * 0.55))
    entries = ([(CORRECT_COLOR, "correct"),
                (INCORRECT_COLOR, "marked, not correct")] if scored else
               []) + [(READ_COLOR, "read as filled"),
                      (UNCLEAR_COLOR, "unclear (ringed on the question "
                                      "number) - see review sheet")]
    for color, text in entries:
        cv2.circle(image, (x, y), radius, color, -1, lineType=cv2.LINE_AA)
        x += radius * 2
        cv2.putText(image, text, (x, y + int(round(6 * scale))),
                    cv2.FONT_HERSHEY_DUPLEX, 0.52 * scale, TEXT_COLOR,
                    max(int(round(1.3 * scale)), 1), cv2.LINE_AA)
        x += int(round(
            cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, 0.52 * scale,
                            1)[0][0] + 40 * scale))
    if heading:
        cv2.putText(image, heading,
                    (int(round(60 * scale)), band_top + int(round(24 * scale))),
                    cv2.FONT_HERSHEY_DUPLEX, 0.55 * scale, TEXT_COLOR,
                    max(int(round(1.3 * scale)), 1), cv2.LINE_AA)


def annotate_page(image: np.ndarray, scan, thresholds, key, heading: str,
                  tests_before: int = 0, only_tests=None) -> np.ndarray:
    """Draw one page's reading onto a copy of the scan."""
    annotated = image.copy()
    if annotated.ndim == 2:
        annotated = cv2.cvtColor(annotated, cv2.COLOR_GRAY2BGR)

    select = thresholds.metadata_select
    review = thresholds.metadata_review

    # Metadata: ring whatever was read as filled, and only the individual
    # bubbles that were too close to call - never the whole block.
    for group in scan.all_groups():
        if group.is_answer:
            continue
        chosen = group.selected(thresholds)
        for label, fill, circle in zip(group.labels, group.fills,
                                       group.circles):
            if label in chosen:
                _ring(annotated, circle, READ_COLOR)
            elif review <= fill <= select:
                _ring(annotated, circle, UNCLEAR_COLOR)

    # Answers. A test left out of the run was never read, so there is
    # nothing truthful to draw on it.
    for column_index, (_, questions) in enumerate(scan.tests):
        if only_tests is not None and \
                tests_before + column_index + 1 not in only_tests:
            continue
        accepted_for = key.answers if key is not None else None
        for index, group in enumerate(questions):
            chosen = group.selected(thresholds)
            alternatives = (accepted_for[index]
                            if accepted_for is not None and index < len(
                                accepted_for) else ())
            # Any letter that could form part of a correct answer.
            accepted = set().union(*alternatives) if alternatives else set()
            if accepted:
                for label, circle in zip(group.labels, group.circles):
                    if label in accepted:
                        _ring(annotated, circle, CORRECT_COLOR)
                    elif label in chosen:
                        _ring(annotated, circle, INCORRECT_COLOR)
            else:
                for label, circle in zip(group.labels, group.circles):
                    if label in chosen:
                        _ring(annotated, circle, READ_COLOR)
            # Ring the printed question number rather than all five options:
            # five amber circles in a row say nothing about which one is the
            # problem, and drown out the answer they surround.
            if group.unclear(thresholds) and group.marker is not None:
                _ring(annotated, group.marker, UNCLEAR_COLOR)

    _legend(annotated, heading, scored=key is not None)
    return annotated


def write_marked_up(image_paths, scans_by_page, sheets, rows,
                    thresholds_by_file, keys, output_folder: pathlib.Path,
                    console, only_tests=None) -> tp.List[pathlib.Path]:
    """One marked-up PDF per input file. Pages are re-read from the source and
    spooled to disk, so a large batch stays within bounded memory."""
    from PIL import Image

    output_folder.mkdir(parents=True, exist_ok=True)

    # Which key applies to each (file, page)? Take it from the graded rows.
    key_for_page: tp.Dict[tp.Tuple[str, int], tp.Any] = {}
    id_for_page: tp.Dict[tp.Tuple[str, int], str] = {}
    for row in rows:
        id_for_page.setdefault((row.source_file, row.page), row.student_id)
        # row.key is the one the row was actually scored against; a Test ID
        # can name more than one.
        if row.key is not None:
            key_for_page.setdefault((row.source_file, row.page), row.key)

    pages_by_file: tp.Dict[pathlib.Path, tp.List] = {}
    tests_before: tp.Dict[tp.Tuple[pathlib.Path, int], int] = {}
    for sheet in sheets:
        seen = 0
        for page in sheet.pages:
            pages_by_file.setdefault(page.path, []).append(page)
            tests_before[(page.path, page.page_index)] = seen
            scan = scans_by_page.get((page.path, page.page_index))
            seen += len(scan.tests) if scan is not None else 0

    written: tp.List[pathlib.Path] = []
    spool = pathlib.Path(tempfile.mkdtemp(prefix="open-mcr-annotated-"))
    try:
        for path in image_paths:
            pages = pages_by_file.get(path, [])
            if not pages:
                continue
            wanted = {page.page_index: page for page in pages}
            last = max(wanted)
            spooled: tp.List[pathlib.Path] = []
            with console.progress(f"Annotating '{path.name}'.",
                                  len(pages)) as bar:
                for index, image in enumerate(
                        image_utils.iter_image_pages(path)):
                    if index in wanted:
                        page = wanted[index]
                        scan = scans_by_page.get((path, index))
                        if scan is not None:
                            heading = f"{page.label}"
                            student = id_for_page.get((path.name, index + 1))
                            if student:
                                heading += f"   Student ID {student}"
                            annotated = annotate_page(
                                image, scan, thresholds_by_file[path],
                                key_for_page.get((path.name, index + 1)),
                                heading,
                                tests_before.get((path, index), 0),
                                only_tests)
                            spool_path = spool / f"{len(spooled):05d}.jpg"
                            cv2.imwrite(str(spool_path), annotated,
                                        [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                            spooled.append(spool_path)
                        bar.step()
                    if index >= last:
                        break
            if not spooled:
                continue
            target = output_folder / f"{path.stem}_annotated.pdf"
            first = Image.open(str(spooled[0])).convert("RGB")
            rest = [Image.open(str(item)).convert("RGB")
                    for item in spooled[1:]]
            first.save(str(target), "PDF", save_all=True, append_images=rest,
                       resolution=float(image_utils.PDF_RENDER_SCALE * 72))
            first.close()
            for opened in rest:
                opened.close()
            written.append(target)
    finally:
        shutil.rmtree(spool, ignore_errors=True)
    return written
