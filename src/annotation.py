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
from . import sheet_layout as layout

GRID_ACROSS = layout.GRID_COLUMNS
GRID_DOWN = layout.GRID_ROWS

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


def _box(image: np.ndarray,
         circles: tp.Sequence[tp.Tuple[float, float, float]],
         color: tp.Tuple[int, int, int]):
    """Outline the whole run of bubbles a field is made of.

    Used where there is no single bubble to point at - a required field that
    came back blank - so the gap itself is what gets marked.

    The box follows the bubbles rather than the edges of the page. A sheet fed
    in crooked is read against a grid that is crooked to match, and an
    upright box drawn over it sits visibly askew of the row it is pointing
    at - which makes the reader doubt the reading rather than the bubbling.
    """
    if not circles:
        return
    radius = max(circle[2] for circle in circles)
    pad = radius * 0.55
    points = np.array([[circle[0], circle[1]] for circle in circles],
                      dtype=np.float32)
    thickness = max(int(round(radius * RING_FRACTION)), 2)
    if len(circles) == 1:
        centre, half = points[0], radius + pad
        corners = np.array([[centre[0] - half, centre[1] - half],
                            [centre[0] + half, centre[1] - half],
                            [centre[0] + half, centre[1] + half],
                            [centre[0] - half, centre[1] + half]],
                           dtype=np.float32)
    else:
        # minAreaRect finds the rectangle the bubbles actually lie in, at
        # whatever angle the page went through the scanner at.
        (centre_x, centre_y), (width, height), angle = cv2.minAreaRect(points)
        grown = (width + 2 * (radius + pad), height + 2 * (radius + pad))
        corners = cv2.boxPoints(((centre_x, centre_y), grown, angle))
    cv2.polylines(image, [np.int32(np.round(corners))], True, color,
                  thickness, lineType=cv2.LINE_AA)


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
                      (UNCLEAR_COLOR,
                       "needs a person: ringed on the question number if "
                       "unclear, boxed if a required field is blank")]
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


def annotate_page(image: np.ndarray, scan, thresholds, keys, heading: str,
                  tests_before: int = 0, only_tests=None,
                  show_grid: bool = False, decided=None,
                  in_review=None) -> np.ndarray:
    """Draw one page's reading onto a copy of the scan.

    ``keys`` maps a test number to the key it was scored against, so each
    column on the page is marked against its own answers.

    ``decided`` maps a test number to the answers actually recorded for it,
    and ``in_review`` to the question numbers that went to the review sheet.
    Both come from the reader rather than being worked out again here: a test
    may have been read against its own cutoff, or row by row, and a drawing
    that quietly disagreed with the results would be believed over them.
    """
    annotated = image.copy()
    if annotated.ndim == 2:
        annotated = cv2.cvtColor(annotated, cv2.COLOR_GRAY2BGR)

    # Underneath everything else, so the rings stay legible over it.
    if show_grid and getattr(scan, "corners", None):
        _draw_grid(annotated, scan.corners, GRID_ACROSS, GRID_DOWN)

    select = thresholds.metadata_select
    review = thresholds.metadata_review

    # Metadata: ring whatever was read as filled, and only the individual
    # bubbles that were too close to call - never the whole block.
    for group in scan.all_groups():
        if group.is_answer:
            continue
        chosen = group.selected(thresholds)
        # Nothing filled in a field that needs a value: box the whole group, so
        # the empty column is visible rather than merely unmarked.
        if group.required and not chosen:
            _box(annotated, group.circles, UNCLEAR_COLOR)
        for label, fill, circle in zip(group.labels, group.fills,
                                       group.circles):
            if label in chosen:
                _ring(annotated, circle, READ_COLOR)
            elif review <= fill <= select:
                _ring(annotated, circle, UNCLEAR_COLOR)

    # Answers. A test left out of the run was never read, so there is
    # nothing truthful to draw on it.
    #
    # Tracked rather than taken from the last loop variable: on a page whose
    # tests were all left out of the run the loop never runs at all, and
    # reading `key` afterwards raised UnboundLocalError.
    scored = False
    for column_index, (_, questions) in enumerate(scan.tests):
        number = tests_before + column_index + 1
        if only_tests is not None and number not in only_tests:
            continue
        key = keys.get(number) if keys else None
        scored = scored or key is not None
        accepted_for = key.answers if key is not None else None
        recorded = (decided or {}).get(number)
        flagged = (in_review or {}).get(number)
        for index, group in enumerate(questions):
            chosen = (recorded[index] if recorded is not None
                      and index < len(recorded)
                      else group.selected(thresholds))
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
            doubtful = (str(index + 1) in flagged if flagged is not None
                        else group.unclear(thresholds))
            if doubtful and group.marker is not None:
                _ring(annotated, group.marker, UNCLEAR_COLOR)

    _legend(annotated, heading, scored=scored)
    return annotated


#: Passed as ``only_students`` to mark up just the papers whose Student ID
#: could not be read in full.
UNKNOWN_IDS = "unknown"

#: Colour of the cell lattice, when it is drawn. Deliberately not one of the
#: reading colours: the grid is the software showing its working, not a
#: judgement about a bubble.
GRID_COLOR = (200, 120, 200)


def _draw_grid(image: np.ndarray,
               corners: tp.Sequence[tp.Tuple[float, float]],
               across: int, down: int):
    """Overlay the cell lattice the page was read against.

    Interpolated across the quadrilateral the corner marks define, which is
    how the reader lays its cells out in the first place, so what is drawn is
    what was used rather than an idealised version of it.
    """
    if not corners or len(corners) != 4:
        return
    height, width = image.shape[:2]
    top_left, top_right, bottom_right, bottom_left = [
        (x * width, y * height) for x, y in corners]

    def along(first, second, fraction):
        return (first[0] + (second[0] - first[0]) * fraction,
                first[1] + (second[1] - first[1]) * fraction)

    for column in range(across + 1):
        fraction = column / across
        start = along(top_left, top_right, fraction)
        end = along(bottom_left, bottom_right, fraction)
        cv2.line(image, (int(start[0]), int(start[1])),
                 (int(end[0]), int(end[1])), GRID_COLOR, 1, cv2.LINE_AA)
    for row in range(down + 1):
        fraction = row / down
        start = along(top_left, bottom_left, fraction)
        end = along(top_right, bottom_right, fraction)
        cv2.line(image, (int(start[0]), int(start[1])),
                 (int(end[0]), int(end[1])), GRID_COLOR, 1, cv2.LINE_AA)


def wants_page(page_number: int, only_pages) -> bool:
    """Should this page get a marked-up copy, by its number in the batch?"""
    if only_pages is None:
        return True
    return page_number in only_pages


def _normalise_id(student: str) -> str:
    return (student or "").strip().lstrip("0") or "0"


def wants_student(student: tp.Optional[str], only_students) -> bool:
    """Should this paper get a marked-up copy?

    ``only_students`` is None for all of them, :data:`UNKNOWN_IDS` for the
    ones whose Student ID has a digit the reader could not call, or a
    collection of IDs. Leading zeros are ignored on the way in, since a
    spreadsheet will have eaten them.
    """
    if only_students is None:
        return True
    if not student:
        # Nothing was read here at all, which is exactly the case somebody
        # asking for the unreadable ones wants to see.
        return only_students == UNKNOWN_IDS
    if only_students == UNKNOWN_IDS:
        return "?" in student
    wanted = {_normalise_id(item) for item in only_students}
    return _normalise_id(student) in wanted


def write_marked_up(image_paths, scans_by_page, sheets, rows,
                    thresholds_by_file, keys, output_folder: pathlib.Path,
                    console, only_tests=None, only_students=None,
                    only_pages=None, show_grid=False, unclear=()
                    ) -> tp.List[pathlib.Path]:
    """One marked-up PDF per input file. Pages are re-read from the source and
    spooled to disk, so a large batch stays within bounded memory.

    ``only_students`` narrows it to the papers worth looking at - see
    :func:`wants_student`. Marking up a whole convention takes real time and
    produces a document nobody reads; marking up the nine papers whose
    Student ID did not come through is a job somebody can actually do.
    """
    from PIL import Image

    output_folder.mkdir(parents=True, exist_ok=True)

    # Which key applies to each (file, page)? Take it from the graded rows.
    # Keyed by test, not by page: the back page carries two tests, and giving
    # both of them the first row's key marks the second one against the wrong
    # answers.
    key_for_test: tp.Dict[tp.Tuple[str, int, int], tp.Any] = {}
    id_for_page: tp.Dict[tp.Tuple[str, int], str] = {}
    # What the reader actually recorded, and what it sent to review.
    marks_for_test: tp.Dict[tp.Tuple[str, int, int], tp.List] = {}
    review_for_test: tp.Dict[tp.Tuple[str, int, int], tp.Set[str]] = {}
    for item in unclear:
        if item.location.isdigit():
            review_for_test.setdefault(
                (item.source_file, item.page, item.test_number),
                set()).add(item.location)
    for row in rows:
        marks_for_test[(row.source_file, row.page, row.test_number)] = \
            row.marked
        id_for_page.setdefault((row.source_file, row.page), row.student_id)
        # row.key is the one the row was actually scored against; a Test ID
        # can name more than one.
        if row.key is not None:
            key_for_test[(row.source_file, row.page, row.test_number)] = row.key

    pages_by_file: tp.Dict[pathlib.Path, tp.List] = {}
    tests_before: tp.Dict[tp.Tuple[pathlib.Path, int], int] = {}
    for sheet in sheets:
        seen = 0
        # Whole sheets are kept or dropped together: a front page without its
        # back is not much use to the person checking it.
        wanted = any(
            wants_student(id_for_page.get((page.path.name,
                                           page.page_index + 1)),
                          only_students)
            and wants_page(page.page_index + 1, only_pages)
            for page in sheet.pages)
        for page in sheet.pages:
            scan = scans_by_page.get((page.path, page.page_index))
            if wanted:
                pages_by_file.setdefault(page.path, []).append(page)
                tests_before[(page.path, page.page_index)] = seen
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
                            before = tests_before.get((path, index), 0)
                            keys_here = {
                                number: key_for_test[(path.name, index + 1,
                                                      number)]
                                for number in range(
                                    before + 1,
                                    before + len(scan.tests) + 1)
                                if (path.name, index + 1,
                                    number) in key_for_test
                            }
                            span = range(before + 1,
                                         before + len(scan.tests) + 1)
                            decided = {
                                number: marks_for_test[(path.name, index + 1,
                                                        number)]
                                for number in span
                                if (path.name, index + 1,
                                    number) in marks_for_test
                            }
                            flagged = {
                                number: review_for_test.get(
                                    (path.name, index + 1, number), set())
                                for number in span
                                if (path.name, index + 1,
                                    number) in marks_for_test
                            }
                            annotated = annotate_page(
                                image, scan, thresholds_by_file[path],
                                keys_here, heading, before, only_tests,
                                show_grid=show_grid, decided=decided,
                                in_review=flagged)
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
