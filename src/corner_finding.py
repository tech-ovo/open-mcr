import typing

import cv2
import numpy as np

from . import geometry_utils
from . import image_utils
from . import list_utils
from . import math_utils
import pathlib


class WrongShapeError(ValueError):
    pass


class CornerFindingError(RuntimeError):
    pass


class LMark():
    """An L-shaped polygon.

    Members:
        polygon: The list of points representing the mark. Points are stored in
            a clockwise direction starting with the vertex shared by the longest
            sides.
        unit_length: The estimated grid square unit length that the mark is
            built with.
    """
    def __init__(self, polygon: geometry_utils.Polygon):
        """Create a new LMark. If the points don't form a valid LMark, raises a
        WrongShapeError."""
        if len(polygon) != 6:
            raise WrongShapeError("Incorrect number of points.")

        if not geometry_utils.all_approx_square(polygon):
            raise WrongShapeError("Corners are not square.")

        clockwise_polygon = geometry_utils.polygon_to_clockwise(polygon)
        side_lengths = geometry_utils.calc_side_lengths(clockwise_polygon)
        longest_sides_indexes = list_utils.find_greatest_value_indexes(
            side_lengths, n=2)

        if not list_utils.is_adjacent_indexes(side_lengths,
                                              *longest_sides_indexes):
            raise WrongShapeError("Longest sides are not adjacent.")

        # The longest sides should be about twice the length of the other sides
        unit_lengths = math_utils.divide_some(side_lengths,
                                              longest_sides_indexes, 2)
        if not math_utils.all_approx_equal(unit_lengths):
            raise WrongShapeError(
                "Longest sides are not twice the length of the other sides.")

        self.polygon = list_utils.arrange_index_to_first(
            clockwise_polygon,
            list_utils.determine_which_is_next(polygon,
                                               *longest_sides_indexes))
        self.unit_length = math_utils.mean(unit_lengths)

    def get_origin(self) -> geometry_utils.Point:
        v_right_x = (self.polygon[1].x - self.polygon[0].x) / 2
        v_right_y = (self.polygon[1].y - self.polygon[0].y) / 2
        v_down_x = (self.polygon[5].x - self.polygon[0].x) / 2
        v_down_y = (self.polygon[5].y - self.polygon[0].y) / 2
        return geometry_utils.Point(
            self.polygon[0].x + v_right_x + v_down_x,
            self.polygon[0].y + v_right_y + v_down_y)


class SquareMark:
    """An L-shaped polygon.

    Members:
        polygon: The list of points representing the mark. Points are stored in
            a clockwise direction.
        unit_length: The estimated grid square unit length that the mark is
            built with.
    """
    def __init__(self,
                 polygon: geometry_utils.Polygon,
                 target_size: typing.Optional[float] = None):
        """Create a new Square. If the points don't form a valid square, raises
        a WrongShapeError.

        Args:
            polygon: The polygon to check. Points will be stored such that the
                first point stored is the first point in this polygon, but the
                rest of the polygon may be reversed to clockwise.
            target_size: If provided, will check against this size when checking
                side lengths. Otherwise, it will just make sure they are equal.
        """
        if len(polygon) != 4:
            raise WrongShapeError("Incorrect number of points.")

        if not geometry_utils.all_approx_square(polygon):
            raise WrongShapeError("Corners are not square.")

        side_lengths = geometry_utils.calc_side_lengths(polygon)
        if not math_utils.all_approx_equal(side_lengths, target_size):
            raise WrongShapeError(
                "Side lengths are not equal or too far from target_size.")

        clockwise = geometry_utils.polygon_to_clockwise(polygon)
        if clockwise[0] is polygon[0]:
            self.polygon = clockwise
        else:
            self.polygon = list_utils.arrange_index_to_first(
                clockwise,
                len(clockwise) - 1)
        self.unit_length = math_utils.mean(side_lengths)


#: (dx/W, dy/H): how far inside the top-left grid corner the L-mark's origin
#: sits, as a fraction of the grid's width and height. The stock sheets place
#: the L-mark's outer vertex on the corner with the mark extending inwards, so
#: its origin lands half an L-mark inside on both axes.
DEFAULT_L_MARK_OFFSET = (0.15625 / 7.5, 0.15625 / 10.0)


#: How far the recovered grid may sit from the sheet's true proportions
#: before it is disbelieved. A real scan is off by a percent or so from
#: perspective; a grid built on the wrong marks is usually off by tens.
ASPECT_TOLERANCE = 0.08

#: The grid has to span at least this much of the page it was found on. A
#: "grid" smaller than this is some small feature of the page, not the sheet.
MIN_GRID_SPAN = 0.45

#: A blob is only a candidate corner mark if it fills this much of its own
#: bounding box. Filters out the stray open curves an edge detector finds.
MIN_MARK_SOLIDITY = 0.55

#: And if it is no bigger than this fraction of the page's short side. The
#: real marks are about 2% of it.
MAX_MARK_SPAN = 0.06

#: How much bigger the L-mark is than a plain corner square, by area. Used to
#: tell which corner is the top-left when the marks are found by position.
L_MARK_AREA_RATIO = 3.0


def _grid_span(corners: geometry_utils.Polygon) -> typing.Tuple[float, float]:
    """The mean width and height of a recovered grid, in pixels."""
    top_left, top_right, bottom_right, bottom_left = corners
    width = (geometry_utils.calc_2d_dist(top_left, top_right) +
             geometry_utils.calc_2d_dist(bottom_left, bottom_right)) / 2
    height = (geometry_utils.calc_2d_dist(top_left, bottom_left) +
              geometry_utils.calc_2d_dist(top_right, bottom_right)) / 2
    return width, height


def _grid_is_plausible(corners: geometry_utils.Polygon, image: np.ndarray,
                       aspect: float) -> bool:
    """Does this look like the sheet's grid, rather than some other shape?

    Cheap, but it is the difference between a page that fails honestly and a
    page that is read against a wrong grid and reports confident nonsense.
    """
    width, height = _grid_span(corners)
    if width <= 0 or height <= 0:
        return False
    if not math_utils.is_approx_equal(width / height, aspect,
                                      ASPECT_TOLERANCE):
        return False
    page_height, page_width = image.shape[:2]
    if width < MIN_GRID_SPAN * page_width or \
            height < MIN_GRID_SPAN * page_height:
        return False
    # Opposite sides of a rectangle stay about equal under perspective.
    top_left, top_right, bottom_right, bottom_left = corners
    tops = geometry_utils.calc_2d_dist(top_left, top_right)
    bottoms = geometry_utils.calc_2d_dist(bottom_left, bottom_right)
    lefts = geometry_utils.calc_2d_dist(top_left, bottom_left)
    rights = geometry_utils.calc_2d_dist(top_right, bottom_right)
    return (math_utils.is_approx_equal(tops, bottoms, 0.15)
            and math_utils.is_approx_equal(lefts, rights, 0.15))


def _corner_from_l_mark(l_mark_origin: geometry_utils.Point,
                        bottom_left: geometry_utils.Point,
                        bottom_right: geometry_utils.Point,
                        l_mark_offset: typing.Tuple[float, float]
                        ) -> geometry_utils.Point:
    """Project the top-left grid corner out from the L-mark's own centre.

    The other three corners are marked by squares centred on them, but the
    L-mark hangs inwards, so the point recovered from it sits (dx, dy) inside
    the corner that is actually wanted.

    Writing that corner G in affine coordinates on the triangle
    (C_BL, C_BR, C_TL) - which survive any perspective-free distortion of the
    scan - gives G = C_BL + u*(C_BR - C_BL) + v*(C_TL - C_BL). With the grid W
    wide and H tall, C_BR - C_BL = (W, 0), C_TL - C_BL = (dx, dy - H) and
    G - C_BL = (0, -H), so v = 1 / (1 - dy/H) and u = -v * (dx/W).
    """
    offset_x, offset_y = l_mark_offset
    v = 1.0 / (1.0 - offset_y)
    u = -v * offset_x
    return geometry_utils.Point(
        bottom_left.x + u * (bottom_right.x - bottom_left.x)
        + v * (l_mark_origin.x - bottom_left.x),
        bottom_left.y + u * (bottom_right.y - bottom_left.y)
        + v * (l_mark_origin.y - bottom_left.y))


def _mark_candidates(image: np.ndarray
                     ) -> typing.List[typing.Tuple[geometry_utils.Point,
                                                   float]]:
    """Every small solid blob on the page, as (centroid, area).

    Shape is deliberately not tested here: this is the path taken when a mark
    has been scribbled over or shaded in, so it no longer has one.
    """
    page_height, page_width = image.shape[:2]
    limit = MAX_MARK_SPAN * min(page_height, page_width)
    contours, _ = cv2.findContours(image_utils.detect_edges(image),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        _, _, box_width, box_height = cv2.boundingRect(contour)
        if box_width > limit or box_height > limit:
            continue
        if box_width < 3 or box_height < 3:
            continue
        area = cv2.contourArea(contour)
        if area < MIN_MARK_SOLIDITY * box_width * box_height:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        found.append((geometry_utils.Point(moments["m10"] / moments["m00"],
                                           moments["m01"] / moments["m00"]),
                      area))
    return found


def _find_by_position(image: np.ndarray, aspect: float,
                      l_mark_offset: typing.Tuple[float, float]
                      ) -> typing.Optional[geometry_utils.Polygon]:
    """Last resort: take the outermost blob toward each corner of the page.

    Nothing here recognises a mark by its shape, which is the point - this is
    the path taken when a mark has been shaded in and no longer has one. The
    only thing keeping stray blobs out is the check that the four together
    make a rectangle of the sheet's proportions, so that check is strict.
    """
    candidates = _mark_candidates(image)
    if len(candidates) < 4:
        return None

    # The extreme blob in each diagonal direction, read clockwise from the top
    # left of the image. Which of them is the *sheet's* top left is not known
    # yet: the page may have gone through the scanner turned round.
    extremes = []
    for weight_x, weight_y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        extremes.append(max(
            candidates,
            key=lambda item: weight_x * item[0].x + weight_y * item[0].y))

    points = [point for point, _ in extremes]
    if len({(round(point.x), round(point.y)) for point in points}) != 4:
        return None

    # Try each rotation and keep the ones that come out the right shape. A
    # quarter turn puts the aspect at 1/0.75, far outside tolerance, so at
    # most the two half-turns of each other survive.
    plausible = []
    for turn in range(4):
        ordered = points[turn:] + points[:turn]
        top_right, bottom_right, bottom_left = ordered[1], ordered[2], \
            ordered[3]
        # The three plain squares are centred on their corners, so the fourth
        # corner of the rectangle follows from them exactly. The L-mark is
        # both the one whose centre is not its corner and the one most likely
        # to be a shapeless blob by now, so its own position is not used.
        top_left = geometry_utils.Point(
            top_right.x + bottom_left.x - bottom_right.x,
            top_right.y + bottom_left.y - bottom_right.y)
        corners = [top_left, top_right, bottom_right, bottom_left]
        if _grid_is_plausible(corners, image, aspect):
            plausible.append((extremes[turn][1], corners))

    if not plausible:
        return None
    # The L-mark is about three times the area of a plain corner square, and
    # it is the only thing that tells the sheet's top left from its bottom
    # right once the shape is right.
    return max(plausible, key=lambda item: item[0])[1]


def _find_by_shape(image: np.ndarray,
                   save_path: typing.Optional[pathlib.PurePath],
                   basis_width: float, basis_height: float,
                   l_mark_offset: typing.Tuple[float, float], aspect: float
                   ) -> typing.Optional[geometry_utils.Polygon]:
    """Find the marks by recognising their shapes: an L and three squares.

    Every hexagon that passes for an L-mark is tried, and the grid each one
    recovers is scored and shape-checked. Taking the first that merely looked
    like an L, as this used to, meant a stray shape could carry the whole page
    onto a wrong grid without anything noticing.
    """
    all_polygons = image_utils.find_polygons(image, save_path=save_path)

    # Even though LMark and SquareMark check length, filtering by it first is
    # faster despite the extra pass.
    hexagons: typing.List[geometry_utils.Polygon] = []
    quadrilaterals: typing.List[geometry_utils.Polygon] = []
    for poly in all_polygons:
        if len(poly) == 6:
            hexagons.append(poly)
        elif len(poly) == 4:
            quadrilaterals.append(poly)

    if save_path:
        image_utils.draw_polygons(image, hexagons,
                                  save_path / "all_hexagons.jpg")
        image_utils.draw_polygons(image, quadrilaterals,
                                  save_path / "all_quadrilaterals.jpg")

    best: typing.Optional[typing.Tuple[float, geometry_utils.Polygon]] = None

    for hexagon in hexagons:
        try:
            l_mark = LMark(hexagon)
        except WrongShapeError:
            continue

        # A preliminary basis from the raw L-mark vertices, only good enough
        # to say roughly where to look for the other three marks.
        preliminary = geometry_utils.ChangeOfBasisTransformer(
            l_mark.polygon[0], l_mark.polygon[5], l_mark.polygon[4])
        # Generous, because a basis built from one small mark is very
        # sensitive to noise. The grid check at the end is what has to be
        # strict, not this.
        x_tolerance = 0.5 * basis_width
        y_tolerance = 0.5 * basis_height

        targets = ((basis_width, 0.5), (0.5, basis_height),
                   (basis_width, basis_height))
        buckets: typing.List[typing.List[typing.Tuple[float, SquareMark]]] = [
            [], [], []]
        for quadrilateral in quadrilaterals:
            try:
                square = SquareMark(quadrilateral, l_mark.unit_length)
            except WrongShapeError:
                continue
            centroid = preliminary.to_basis(
                geometry_utils.guess_centroid(square.polygon))
            for index, (target_x, target_y) in enumerate(targets):
                if math_utils.is_within_tolerance(
                        centroid.x, target_x, x_tolerance) and \
                        math_utils.is_within_tolerance(
                            centroid.y, target_y, y_tolerance):
                    buckets[index].append(
                        ((centroid.x - target_x) ** 2 +
                         (centroid.y - target_y) ** 2, square))
                    break

        if not all(buckets):
            continue
        chosen = [min(bucket, key=lambda item: item[0]) for bucket in buckets]
        score = sum(distance for distance, _ in chosen)
        top_right, bottom_left, bottom_right = (
            geometry_utils.guess_centroid(square.polygon)
            for _, square in chosen)

        corners = [
            _corner_from_l_mark(l_mark.get_origin(), bottom_left,
                                bottom_right, l_mark_offset),
            top_right, bottom_right, bottom_left,
        ]
        if not _grid_is_plausible(corners, image, aspect):
            continue
        if best is None or score < best[0]:
            best = (score, corners)

    return None if best is None else best[1]


def find_corner_marks(image: np.ndarray,
                      save_path: typing.Optional[pathlib.PurePath] = None,
                      basis_width: float = 49.5,
                      basis_height: float = 31.75,
                      l_mark_offset: typing.Tuple[float, float] =
                      DEFAULT_L_MARK_OFFSET,
                      alternates: typing.Optional[
                          typing.Callable[[], typing.Iterable[np.ndarray]]]
                      = None
                      ) -> tuple[geometry_utils.Polygon,
                                 geometry_utils.ChangeOfBasisTransformer]:
    """Locate the four grid corners on a prepared (binary) page.

    Args:
        image: the page as black and white, as the first thing to try.
        alternates: an optional callable yielding further renderings of the
            same page - darker thresholdings - tried in turn if the first
            fails. Lazy, because building them costs real time and the great
            majority of pages never need them.

    The search runs in order of how much it assumes, so the cheapest and most
    trustworthy answer always wins:

    1. the marks' own shapes, on the supplied rendering;
    2. the same, on each darker rendering - the marks are printed solid, so a
       pencil scribble lying across one drops out before the mark does;
    3. position alone, taking the outermost blob toward each corner, accepted
       only if the four make a rectangle of the sheet's proportions. This is
       the one that survives a mark somebody has shaded in.

    Raises:
        CornerFindingError: if none of them find a plausible grid.
    """
    # One horizontal basis unit is half the L-mark's long side and one
    # vertical unit is the whole of it, so the grid's true proportions are
    # (basis_width / 2) : basis_height.
    aspect = (basis_width / 2.0) / basis_height

    renderings = [image]
    if alternates is not None:
        renderings.extend(alternates())

    for rendering in renderings:
        corners = _find_by_shape(rendering, save_path, basis_width,
                                 basis_height, l_mark_offset, aspect)
        if corners is not None:
            break
    else:
        for rendering in renderings:
            corners = _find_by_position(rendering, aspect, l_mark_offset)
            if corners is not None:
                break
        else:
            raise CornerFindingError(
                "Couldn't find document corners. The four corner marks must "
                "be present and unobscured: check that the page was not "
                "cropped into them, and that nothing has been written over "
                "them.")

    top_left, top_right, bottom_right, bottom_left = corners
    basis_transformer = geometry_utils.ChangeOfBasisTransformer(
        top_left, bottom_left, bottom_right, top_right)
    if save_path:
        image_utils.draw_polygons(image, [list(corners)],
                                  save_path / "grid_limits.jpg")
    return list(corners), basis_transformer
