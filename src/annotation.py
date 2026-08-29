"""Marked-up copies of the scanned sheets, for checking the grading by eye.

With annotation switched on, the reader remembers where every answer bubble
was found on every page. Once the answer keys are known, it re-opens the
scans and draws on them:

* **green** on the bubble the key says is correct;
* **red** on a bubble the student filled that is not the correct one;
* **amber** on a mark the reader could not confidently call either way
  (the same marks listed in the review report).

The result is one PDF per input file, page for page alongside the original, so
a grader can flip through and confirm the machine read what they read.
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
UNCLEAR_COLOR = (20, 160, 235)
#: Used when there is no key to score a column against, so "wrong" would be a
#: claim the software cannot make - the mark is simply shown as read.
UNSCORED_COLOR = (170, 110, 40)
LEGEND_TEXT_COLOR = (40, 40, 40)

#: Ring thickness as a fraction of the bubble radius.
RING_FRACTION = 0.22
#: Opacity of the wash drawn inside a highlighted bubble.
FILL_OPACITY = 0.30


class QuestionMarks(tp.NamedTuple):
    """Where one question's bubbles are, and what was read from them."""

    number: int
    """1-based question number within its test."""

    circles: tp.Tuple[tp.Tuple[float, float, float], ...]
    """``(x, y, radius)`` in source-image pixels, one per option."""

    chosen: tp.Tuple[int, ...]
    """Indexes of the options the reader considered filled."""

    unclear: bool


class ColumnAnnotation(tp.NamedTuple):
    """One test's worth of questions on one page."""

    form_code: str
    questions: tp.Tuple[QuestionMarks, ...]
    caption_at: tp.Tuple[float, float]
    """Pixel position for the per-test caption drawn above the column."""

    caption: str


class PageAnnotation(tp.NamedTuple):
    path: pathlib.Path
    page_index: int
    label: str
    student_id: str
    columns: tp.Tuple[ColumnAnnotation, ...]


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


def _caption(image: np.ndarray, position: tp.Tuple[float, float], text: str):
    if not text:
        return
    scale = image.shape[1] / 1700.0
    origin = (int(round(position[0])), int(round(position[1])))
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_DUPLEX, 0.7 * scale,
                LEGEND_TEXT_COLOR, max(int(round(1.6 * scale)), 1),
                cv2.LINE_AA)


def _legend(image: np.ndarray, heading: str, scored: bool = True):
    """Draw a key to the colours across the foot of the page."""
    height, width = image.shape[:2]
    scale = width / 1700.0
    band_top = int(height - (0.44 * height / 11.0))
    cv2.rectangle(image, (0, band_top), (width, height), (255, 255, 255), -1)

    radius = max(int(round(11 * scale)), 5)
    x = int(round(60 * scale))
    y = band_top + int(round((height - band_top) * 0.55))
    if scored:
        entries = (
            (CORRECT_COLOR, "correct answer"),
            (INCORRECT_COLOR, "marked, not correct"),
            (UNCLEAR_COLOR, "unclear - check by hand"),
        )
    else:
        entries = (
            (UNSCORED_COLOR, "marked (no answer key found)"),
            (UNCLEAR_COLOR, "unclear - check by hand"),
        )
    for color, text in entries:
        cv2.circle(image, (x, y), radius, color, -1, lineType=cv2.LINE_AA)
        x += radius * 2
        cv2.putText(image, text, (x, y + int(round(6 * scale))),
                    cv2.FONT_HERSHEY_DUPLEX, 0.52 * scale, LEGEND_TEXT_COLOR,
                    max(int(round(1.3 * scale)), 1), cv2.LINE_AA)
        x += int(round(cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX,
                                       0.52 * scale, 1)[0][0] + 44 * scale))
    if heading:
        cv2.putText(image, heading, (int(round(60 * scale)),
                                     band_top + int(round(24 * scale))),
                    cv2.FONT_HERSHEY_DUPLEX, 0.55 * scale, LEGEND_TEXT_COLOR,
                    max(int(round(1.3 * scale)), 1), cv2.LINE_AA)


def annotate_page(image: np.ndarray, page: PageAnnotation,
                  keys: tp.Mapping[str, tp.Sequence[str]],
                  options: str) -> np.ndarray:
    """Return a copy of ``image`` with the grading drawn onto it."""
    annotated = image.copy()
    if annotated.ndim == 2:
        annotated = cv2.cvtColor(annotated, cv2.COLOR_GRAY2BGR)

    for column in page.columns:
        key = keys.get("*") or keys.get(column.form_code)
        for question in column.questions:
            correct_index: tp.Optional[int] = None
            if key is not None and question.number - 1 < len(key):
                correct_letter = key[question.number - 1].strip().upper()
                if len(correct_letter) == 1 and correct_letter in options:
                    correct_index = options.index(correct_letter)
            if correct_index is not None and correct_index < len(
                    question.circles):
                _ring(annotated, question.circles[correct_index],
                      CORRECT_COLOR)
            wrong_color = INCORRECT_COLOR if key is not None else UNSCORED_COLOR
            for chosen in question.chosen:
                if chosen == correct_index or chosen >= len(question.circles):
                    continue
                _ring(annotated, question.circles[chosen], wrong_color)
            if question.unclear:
                for circle in question.circles:
                    _ring(annotated, circle, UNCLEAR_COLOR)
        _caption(annotated, column.caption_at, column.caption)

    heading = f"{page.label}"
    if page.student_id:
        heading += f"   Student ID {page.student_id}"
    scored = any(
        keys.get("*") or keys.get(column.form_code) for column in page.columns)
    _legend(annotated, heading, scored=scored)
    return annotated


def write_annotated_pdfs(pages: tp.Sequence[PageAnnotation],
                         keys: tp.Mapping[str, tp.Sequence[str]],
                         output_folder: pathlib.Path, options: str,
                         progress: tp.Optional[tp.Callable[[str], None]] = None
                         ) -> tp.List[pathlib.Path]:
    """Re-open every annotated page and write one marked-up PDF per input file.

    Pages are re-read from the source rather than kept in memory, and each
    annotated page is spooled to a temporary JPEG, so a large batch stays
    within a bounded amount of memory.
    """
    from PIL import Image

    if not pages:
        return []

    output_folder.mkdir(parents=True, exist_ok=True)
    by_source: tp.Dict[pathlib.Path, tp.List[PageAnnotation]] = {}
    for page in pages:
        by_source.setdefault(page.path, []).append(page)

    written: tp.List[pathlib.Path] = []
    spool = pathlib.Path(tempfile.mkdtemp(prefix="open-mcr-annotated-"))
    try:
        for source, source_pages in by_source.items():
            wanted = {page.page_index: page for page in source_pages}
            last_wanted = max(wanted)
            spooled: tp.List[pathlib.Path] = []
            for page_index, image in enumerate(
                    image_utils.iter_image_pages(source)):
                if page_index in wanted:
                    page = wanted[page_index]
                    if progress:
                        progress(f"Marking up '{page.label}'.")
                    annotated = annotate_page(image, page, keys, options)
                    spool_path = spool / f"{len(spooled):05d}.jpg"
                    cv2.imwrite(str(spool_path), annotated,
                                [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    spooled.append(spool_path)
                if page_index >= last_wanted:
                    break
            if not spooled:
                continue
            target = output_folder / f"{source.stem}_annotated.pdf"
            first = Image.open(str(spooled[0])).convert("RGB")
            rest = [
                Image.open(str(path)).convert("RGB") for path in spooled[1:]
            ]
            first.save(str(target), "PDF", save_all=True, append_images=rest,
                       resolution=float(image_utils.PDF_RENDER_SCALE * 72))
            first.close()
            for opened in rest:
                opened.close()
            for path in spooled:
                path.unlink(missing_ok=True)
            written.append(target)
    finally:
        shutil.rmtree(spool, ignore_errors=True)
    return written
