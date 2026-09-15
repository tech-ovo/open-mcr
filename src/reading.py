"""Turning one scanned page into marks.

Everything here is a simple cutoff: a bubble is filled when its darkness is
above the threshold for its kind. No bubble is picked just for being the
darkest of its group, so a student who genuinely means "A and B" is read as
"A and B", and a student who marks two digits in their ID is reported rather
than quietly resolved.
"""

import typing as tp

from . import corner_finding
from . import geometry_utils
from . import grid_info as grid_i
from . import grid_reading as grid_r
from . import image_utils
from . import sheet_layout as layout
from . import thresholds as th


class BubbleGroup(tp.NamedTuple):
    """One set of bubbles that together carry a single value."""

    location: str
    """What it is: a question number, or a field name like 'Latin level'."""

    labels: tp.Tuple[str, ...]
    """The option label of each bubble, in order."""

    fills: tp.Tuple[float, ...]
    circles: tp.Tuple[tp.Tuple[float, float, float], ...]
    is_answer: bool
    required: bool
    """Whether leaving it entirely blank is a problem."""

    marker: tp.Optional[tp.Tuple[float, float, float]] = None
    """Where to draw attention to the whole group - the cell holding the
    printed question number. Ringing that is far easier to read than ringing
    all five options at once."""

    def selected(self, thresholds: th.Thresholds) -> tp.Set[str]:
        cutoff = thresholds.select_for(self.is_answer)
        return {
            label
            for label, fill in zip(self.labels, self.fills) if fill > cutoff
        }

    def unclear(self, thresholds: th.Thresholds) -> bool:
        cutoff = thresholds.select_for(self.is_answer)
        review = thresholds.review_for(self.is_answer)
        return any(review <= fill <= cutoff for fill in self.fills)


class PageScan(tp.NamedTuple):
    """Every bubble found on one page, before any threshold is applied."""

    page_side: tp.Optional[int]
    """0 for a front page, 1 for a back, None if the page code is unreadable
    at the metadata cutoff. Resolved later, once thresholds are known."""

    page_code: tp.Optional[BubbleGroup]
    student_id: tp.Tuple[BubbleGroup, ...]
    latin_level: tp.Optional[BubbleGroup]
    tests: tp.Tuple[tp.Tuple[BubbleGroup, tp.Tuple[BubbleGroup, ...]], ...]
    """Per test on this page: its Test ID digit groups collapsed into one
    entry, and its question groups."""

    test_id_digits: tp.Tuple[tp.Tuple[BubbleGroup, ...], ...]

    corners: tp.Optional[tp.Tuple[tp.Tuple[float, float], ...]] = None
    """Where the grid was found, as fractions of the page. Kept so that a
    page which cannot find its own corners can be retried against the median
    of the pages that could."""

    @property
    def answer_fills(self) -> tp.List[float]:
        return [
            fill for _, questions in self.tests for group in questions
            for fill in group.fills
        ]

    @property
    def metadata_fills(self) -> tp.List[float]:
        groups: tp.List[BubbleGroup] = list(self.student_id)
        if self.latin_level is not None:
            groups.append(self.latin_level)
        if self.page_code is not None:
            groups.append(self.page_code)
        for digits in self.test_id_digits:
            groups.extend(digits)
        return [fill for group in groups for fill in group.fills]

    def all_groups(self) -> tp.List[BubbleGroup]:
        groups: tp.List[BubbleGroup] = []
        if self.page_code is not None:
            groups.append(self.page_code)
        groups.extend(self.student_id)
        if self.latin_level is not None:
            groups.append(self.latin_level)
        for digits in self.test_id_digits:
            groups.extend(digits)
        for _, questions in self.tests:
            groups.extend(questions)
        return groups


def _digit_labels(count: int) -> tp.Tuple[str, ...]:
    return tuple(str(digit) for digit in range(count))


#: A page-code bubble is printed solid rather than bubbled in by hand, so
#: reading it needs no calibration: the filled one measures around 0.59 and
#: the empty one around 0.03. These bounds sit far from both.
PAGE_CODE_MIN_FILL = 0.25
PAGE_CODE_MIN_RATIO = 3.0


def side_of_code(group: tp.Optional[BubbleGroup]) -> tp.Optional[int]:
    """Which side of the sheet a page is, from its page-code mark.

    Deliberately independent of the thresholds: the side has to be known
    before pages can be grouped into sheets, and the thresholds are measured
    from the pages once they are grouped. The mark is printed solid rather
    than bubbled in by hand, so the two bubbles are far enough apart to read
    on their own terms.
    """
    if group is None or len(group.fills) < 2:
        return None
    ranked = sorted(range(len(group.fills)),
                    key=lambda index: group.fills[index], reverse=True)
    best, runner_up = group.fills[ranked[0]], group.fills[ranked[1]]
    if best < PAGE_CODE_MIN_FILL:
        return None
    if runner_up > 0 and best < PAGE_CODE_MIN_RATIO * runner_up:
        return None
    try:
        return int(group.labels[ranked[0]]) - 1
    except (ValueError, IndexError):
        return None


def side_of(scan: PageScan) -> tp.Optional[int]:
    """Which side of the sheet a scanned page is."""
    return side_of_code(scan.page_code)


def _build_grid(image: tp.Any, form_variant: grid_i.FormVariant,
                debug_path: tp.Optional[tp.Any] = None) -> grid_r.Grid:
    """Locate the sheet on the page and lay the cell grid over it.

    The costly half of reading a page, and the half that does not depend on
    which side of the sheet it is: both layouts use the same corner marks and
    the same cell counts.
    """
    prepared = image_utils.prepare_scan_for_processing(image,
                                                       save_path=debug_path)
    corners, basis = corner_finding.find_corner_marks(
        prepared,
        save_path=debug_path,
        basis_width=form_variant.basis_width,
        basis_height=form_variant.basis_height,
        l_mark_offset=form_variant.l_mark_offset,
        # Only built if the ordinary rendering fails to find the corners.
        alternates=lambda: image_utils.darker_renderings(image))
    grid = grid_r.Grid(corners,
                       form_variant.horizontal_cells,
                       form_variant.vertical_cells,
                       image_utils.dilate(prepared, save_path=debug_path),
                       basis_transformer=basis,
                       save_path=debug_path,
                       y_shift=getattr(form_variant, "y_shift", 0.0))
    grid.found_corners = corner_finding.corner_fractions(corners, prepared)
    return grid


def _groups_for(grid: grid_r.Grid, form_variant: grid_i.FormVariant,
                field: grid_i.Field, instance: int, name: str,
                labels: tp.Callable[[int], tp.Tuple[str, ...]],
                required: bool, per_field_name: bool
                ) -> tp.Tuple[BubbleGroup, ...]:
    """Measure one field's bubbles out of an already-built grid."""
    info = form_variant.fields.get(field)
    if info is None:
        return ()
    if isinstance(info, list):
        if instance >= len(info):
            return ()
        info = info[instance]
    fills = grid_r.get_field_fill_percents(field, grid, form_variant)[instance]
    circles = grid_r.get_group_cell_circles(info, grid)
    built: tp.List[BubbleGroup] = []
    for index, (field_fills, field_circles) in enumerate(zip(fills, circles)):
        location = f"{name} digit {index + 1}" if per_field_name else name
        built.append(
            BubbleGroup(location=location,
                        labels=labels(len(field_fills)),
                        fills=tuple(field_fills),
                        circles=tuple(field_circles),
                        is_answer=False,
                        required=required))
    return tuple(built)


def _page_code(grid: grid_r.Grid,
               form_variant: grid_i.FormVariant
               ) -> tp.Optional[BubbleGroup]:
    groups = _groups_for(grid, form_variant, grid_i.Field.PAGE_CODE, 0,
                         "Page code",
                         lambda count: tuple(
                             str(index + 1) for index in range(count)),
                         required=True, per_field_name=False)
    return groups[0] if groups else None


def _extract(grid: grid_r.Grid, form_variant: grid_i.FormVariant,
             latin_levels: tp.Optional[tp.Sequence[str]],
             side: tp.Optional[int]) -> PageScan:
    """Pull every field this layout describes out of a built grid."""
    levels = tuple(latin_levels or layout.LATIN_LEVELS)
    student_id = _groups_for(grid, form_variant, grid_i.Field.STUDENT_ID, 0,
                             "Student ID", _digit_labels, required=True,
                             per_field_name=True)
    latin_groups = _groups_for(grid, form_variant, grid_i.Field.LATIN_LEVEL, 0,
                               "Latin level", lambda count: levels[:count],
                               required=True, per_field_name=False)

    tests: tp.List[tp.Tuple[BubbleGroup, tp.Tuple[BubbleGroup, ...]]] = []
    test_id_digits: tp.List[tp.Tuple[BubbleGroup, ...]] = []
    for column_index, column in enumerate(form_variant.question_columns):
        digits = _groups_for(grid, form_variant, grid_i.Field.TEST_FORM_CODE,
                             column_index, "Test ID", _digit_labels,
                             required=True, per_field_name=True)
        test_id_digits.append(digits)
        answer_fills = grid_r.get_answer_fill_percents_for_column(
            column_index, grid, form_variant)
        questions: tp.List[BubbleGroup] = []
        for question_index, info in enumerate(column):
            circles = grid_r.get_group_cell_circles(info, grid)[0]
            # The question number is printed in the cell to the left of the
            # first option.
            centre, radius = grid.get_cell_circle(info.horizontal_start - 1,
                                                  info.vertical_start)
            # A cell is wider than it is tall, and get_cell_circle sizes on
            # the average of the two, so the ring came out taller than its
            # row: several unclear questions in a row merged into one amber
            # column. Keep it inside the row, and still wide enough to hold a
            # two-digit number.
            (_, _), (top, bottom) = grid.get_cell_range(
                info.horizontal_start - 1, info.vertical_start)
            radius = min(radius, abs(bottom - top) * 0.45)
            questions.append(
                BubbleGroup(location=str(question_index + 1),
                            labels=tuple(
                                layout.OPTIONS[:len(answer_fills[
                                    question_index][0])]),
                            fills=tuple(answer_fills[question_index][0]),
                            circles=tuple(circles),
                            is_answer=True,
                            required=False,
                            marker=(centre.x, centre.y, radius)))
        first = digits[0] if digits else BubbleGroup("Test ID", (), (), (),
                                                     False, False)
        tests.append((first, tuple(questions)))

    return PageScan(corners=getattr(grid, "found_corners", None),
                    page_side=side,
                    page_code=_page_code(grid, form_variant),
                    student_id=student_id,
                    latin_level=latin_groups[0] if latin_groups else None,
                    tests=tuple(tests),
                    test_id_digits=tuple(test_id_digits))


def scan_page(image: tp.Any, form_variant: grid_i.FormVariant,
              page_index_on_sheet: int,
              debug_path: tp.Optional[tp.Any] = None,
              latin_levels: tp.Optional[tp.Sequence[str]] = None) -> PageScan:
    """Measure every bubble on a page of a known side. Applies no thresholds."""
    grid = _build_grid(image, form_variant, debug_path)
    return _extract(grid, form_variant, latin_levels, side=None)


def scan_page_with_corners(image: tp.Any,
                           form: grid_i.TwoSidedFormVariant,
                           prior: tp.Sequence[tp.Tuple[float, float]],
                           latin_levels: tp.Optional[tp.Sequence[str]] = None
                           ) -> PageScan:
    """Read a page using corners borrowed from the rest of the batch.

    For the page that defeats shape-matching on its own - fed in crooked, or
    with a mark somebody has drawn over - while two hundred of its neighbours
    found theirs without trouble.
    """
    front = form.variant_for_page(0)
    prepared = image_utils.prepare_scan_for_processing(image)
    aspect = (front.basis_width / 2.0) / front.basis_height
    found = corner_finding.find_with_prior(
        prepared, prior, aspect, l_mark_offset=front.l_mark_offset)
    if found is None:
        raise corner_finding.CornerFindingError(
            "Could not find the corner marks, even using where the rest of "
            "the batch put theirs.")
    basis = geometry_utils.ChangeOfBasisTransformer(
        found[0], found[3], found[2], found[1])
    grid = grid_r.Grid(found, front.horizontal_cells, front.vertical_cells,
                       image_utils.dilate(prepared),
                       basis_transformer=basis,
                       y_shift=getattr(front, "y_shift", 0.0))
    grid.found_corners = corner_finding.corner_fractions(found, prepared)
    side = side_of_code(_page_code(grid, front))
    variant = form.variant_for_page(side) if side is not None else front
    return _extract(grid, variant, latin_levels, side)


def scan_page_either_side(image: tp.Any, form: grid_i.TwoSidedFormVariant,
                          debug_path: tp.Optional[tp.Any] = None,
                          latin_levels: tp.Optional[tp.Sequence[str]] = None
                          ) -> PageScan:
    """Read a page that has not been said to be a front or a back.

    The grid is built once with the front-page description - either would do,
    since both sides share a grid - the page code is read from it, and only
    then are the fields pulled with the layout the page actually has.
    """
    front = form.variant_for_page(0)
    grid = _build_grid(image, front, debug_path)
    side = side_of_code(_page_code(grid, front))
    variant = form.variant_for_page(side) if side is not None else front
    return _extract(grid, variant, latin_levels, side)


def read_digits(groups: tp.Sequence[BubbleGroup],
                thresholds: th.Thresholds,
                blank: str = "?",
                reference: tp.Optional[float] = None) -> str:
    """Join a digit block, keeping column positions.

    A column with nothing filled, or with more than one bubble filled, becomes
    `blank` rather than being guessed at, so `0?275` is never silently
    reported as the valid-looking `0275`.

    With a `reference` - how dark an unfilled bubble gets on this page - a
    column the cutoff could not resolve is tried again by contrast. Ten
    bubbles with one filled is the clearest instance of that pattern on the
    sheet, and it is the difference between a paper that can be handed back
    and one that cannot.
    """
    characters: tp.List[str] = []
    for group in groups:
        chosen = sorted(group.selected(thresholds))
        if len(chosen) == 1:
            characters.append(chosen[0])
            continue
        settled = (th.read_by_contrast(group, reference)
                   if reference is not None else None)
        characters.append(sorted(settled)[0]
                          if settled and len(settled) == 1 else blank)
    return "".join(characters)


def read_choice(group: tp.Optional[BubbleGroup],
                thresholds: th.Thresholds) -> str:
    """Read a pick-one block such as the Latin level. Blank or ambiguous
    reads as an empty string."""
    if group is None:
        return ""
    chosen = sorted(group.selected(thresholds))
    return chosen[0] if len(chosen) == 1 else ""


def read_page_side(scan: PageScan,
                   thresholds: th.Thresholds) -> tp.Optional[int]:
    if scan.page_code is None:
        return None
    chosen = sorted(scan.page_code.selected(thresholds))
    if len(chosen) != 1:
        return None
    try:
        return int(chosen[0]) - 1
    except ValueError:
        return None
