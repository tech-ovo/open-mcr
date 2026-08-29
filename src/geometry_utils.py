"""General geometry-related math utilities."""

import enum
import math
import typing as tp

import cv2
import numpy as np

from . import list_utils
from . import math_utils


class Point:
    x: float
    y: float
    """Represents a point on a 2d plane in x, y form."""
    def __init__(self, x: float, y: float):
        """Create a new Point."""
        self.x = x
        self.y = y


Polygon = tp.List[Point]


def contour_to_polygon(contour: np.ndarray) -> Polygon:
    """Convert an OpenCV contour (a numpy array) to a list of points (a
    polygon)."""
    return [Point(vertex[0][0], vertex[0][1]) for vertex in contour]


def polygon_to_contour(polygon: Polygon) -> np.ndarray:
    """Convert polygon (list of points) to an OpenCV contour (numpy array)."""
    return np.array([[[point.x, point.y]] for point in polygon])


def approx_poly(contour: np.ndarray) -> Polygon:
    """Approximate the simple polygon for the contour. Returns a polygon in
    clockwise order."""
    perimeter = cv2.arcLength(contour, True)
    simple = cv2.approxPolyDP(contour, 0.05 * perimeter, True)
    polygon = contour_to_polygon(simple)
    return polygon_to_clockwise(polygon)


def polygon_to_clockwise(polygon: Polygon) -> Polygon:
    """Returns the given polygon in clockwise direction."""
    clockwise = cv2.contourArea(polygon_to_contour(polygon), True) >= 0
    if clockwise:
        return polygon
    else:
        return list(reversed(polygon))


def calc_2d_dist(point_a: Point, point_b: Point) -> float:
    """Calculate the Euclidean distance between two 2d points."""
    return math.sqrt((point_a.x - point_b.x)**2 + (point_a.y - point_b.y)**2)


def calc_angle(end_a: Point, shared: Point, end_b: Point) -> float:
    """Calculate the internal angle between the two vectors (always in 0-180
    range)."""
    mag_a = calc_2d_dist(shared, end_a)
    mag_b = calc_2d_dist(shared, end_b)
    dist_ab = calc_2d_dist(end_a, end_b)
    cosine = (mag_a**2 + mag_b**2 - dist_ab**2) / (2 * mag_a * mag_b)
    angle = abs(math.acos(round(cosine, 4)))
    return angle if angle <= 180 else angle - 180


def calc_corner_angles(contour: Polygon) -> tp.List[float]:
    """For a list of points, returns a list of numbers, where each element with
    index `i` is the angle between points `i-1`, `i`, and `i+1`."""
    result = []
    for i, point in enumerate(contour):
        previous_point = contour[list_utils.prev_index(contour, i)]
        next_point = contour[list_utils.next_index(contour, i)]
        result.append(calc_angle(previous_point, point, next_point))
    return result


def calc_side_lengths(contour: Polygon) -> tp.List[float]:
    """For a list of points, returns a list of numbers, where each element with
    index `i` is the distance from point `i` to point `i+1`."""
    result = []
    for i, point in enumerate(contour):
        next_point = contour[list_utils.next_index(contour, i)]
        result.append(calc_2d_dist(point, next_point))
    return result


def all_approx_square(contour: Polygon) -> bool:
    """Returns true if every angle in `contour` is approximately right
    (90deg)."""
    angles = calc_corner_angles(contour)
    return math_utils.all_approx_equal(angles, math.pi / 2)


class ChangeOfBasisTransformer():
    """Transforms points to/from their 2D coordinate system into a new one, such that the passed
    `origin` point becomes `0.0`, the `bottom_left` point becomes `0,1`, and the `bottom_right`
    point because `1,1`."""
    def __init__(self, origin: Point, bottom_left: Point, bottom_right: Point, top_right: tp.Optional[Point] = None):
        self._is_perspective = top_right is not None
        if self._is_perspective:
            assert top_right is not None
            src_pts = np.array([
                [0.0, 0.0],
                [1.0, 0.0],
                [1.0, 1.0],
                [0.0, 1.0]
            ], dtype=np.float32)
            dst_pts = np.array([
                [origin.x, origin.y],
                [top_right.x, top_right.y],
                [bottom_right.x, bottom_right.y],
                [bottom_left.x, bottom_left.y]
            ], dtype=np.float32)
            self._H_from_basis = cv2.getPerspectiveTransform(src_pts, dst_pts)
            self._H_to_basis = np.linalg.inv(self._H_from_basis)
        else:
            target_origin = Point(0, 0)
            target_bl = Point(0, 1)
            target_br = Point(1, 1)
            target_matrix = np.array([[target_origin.x], [target_bl.x], [target_br.x],
                                    [target_origin.y], [target_bl.y], [target_br.y]],
                                    float)

            from_matrix = np.array([[origin.x, origin.y, 1, 0, 0, 0],
                                    [bottom_left.x, bottom_left.y, 1, 0, 0, 0],
                                    [bottom_right.x, bottom_right.y, 1, 0, 0, 0],
                                    [0, 0, 0, origin.x, origin.y, 1],
                                    [0, 0, 0, bottom_left.x, bottom_left.y, 1],
                                    [0, 0, 0, bottom_right.x, bottom_right.y, 1]],
                                float)

            result = np.matmul(np.linalg.inv(from_matrix), target_matrix)
            self._transformation_matrix = np.array([[result[0][0], result[1][0]],
                                            [result[3][0], result[4][0]]])
            self._transformation_matrix_inv = np.linalg.inv(self._transformation_matrix)
            self._rotation_matrix = np.array([[result[2][0]], [result[5][0]]])

    def to_basis(self, point: Point) -> Point:
        if self._is_perspective:
            pt = np.array([[[point.x, point.y]]], dtype=np.float32)
            res = cv2.perspectiveTransform(pt, self._H_to_basis)
            return Point(res[0][0][0], res[0][0][1])
        point_vector = np.array([[point.x], [point.y]], float)
        result = np.matmul(self._transformation_matrix,
                           point_vector) + self._rotation_matrix
        return Point(result[0][0], result[1][0])

    def from_basis(self, point: Point) -> Point:
        if self._is_perspective:
            pt = np.array([[[point.x, point.y]]], dtype=np.float32)
            res = cv2.perspectiveTransform(pt, self._H_from_basis)
            return Point(res[0][0][0], res[0][0][1])
        point_vector = np.array([[point.x], [point.y]], float)
        result = np.matmul(self._transformation_matrix_inv,
                           (point_vector - self._rotation_matrix))
        return Point(result[0][0], result[1][0])

    def poly_from_basis(self, polygon: Polygon) -> Polygon:
        return [self.from_basis(point) for point in polygon]


def guess_centroid(quadrilateral: Polygon) -> Point:
    """Guesses an approximate centroid. Works well for squares."""
    xs = [p.x for p in quadrilateral]
    ys = [p.y for p in quadrilateral]
    return Point(math_utils.mean([max(xs), min(xs)]),
                 math_utils.mean([max(ys), min(ys)]))


class Orientation(enum.Enum):
    VERTICAL = enum.auto()
    HORIZONTAL = enum.auto()
