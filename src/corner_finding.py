import typing

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


def find_corner_marks(image: np.ndarray,
                      save_path: typing.Optional[pathlib.PurePath] = None,
                      basis_width: float = 49.5,
                      basis_height: float = 31.75
                      ) -> tuple[geometry_utils.Polygon, geometry_utils.ChangeOfBasisTransformer]:

    all_polygons: typing.List[
        geometry_utils.Polygon] = image_utils.find_polygons(
            image, save_path=save_path)

    # Even though the LMark and SquareMark classes check length, it's faster to
    # filter out the shapes of incorrect length despite the increased time
    # complexity.
    hexagons: typing.List[geometry_utils.Polygon] = []
    quadrilaterals: typing.List[geometry_utils.Polygon] = []
    for poly in all_polygons:
        if len(poly) == 6:
            hexagons.append(poly)
        elif len(poly) == 4:
            quadrilaterals.append(poly)

    if save_path:
        image_utils.draw_polygons(image, hexagons, save_path / "all_hexagons.jpg")
        image_utils.draw_polygons(image, quadrilaterals, save_path / "all_quadrilaterals.jpg")

    for i in range(len(hexagons)):
        hexagon = hexagons[i]

        try:
            l_mark = LMark(hexagon)
        except WrongShapeError:
            continue

        # Establish a preliminary basis transformer to help find the best square marks.
        # This initial transformer uses the raw L-mark vertices.
        preliminary_transformer = geometry_utils.ChangeOfBasisTransformer(
            l_mark.polygon[0], l_mark.polygon[5], l_mark.polygon[4])
        nominal_to_right_side = basis_width
        nominal_to_bottom = basis_height
        # We can afford to allow a decently large error margin here since we are just searching for
        # reference points and the rough coordinate system established based on the L is very
        # sensitive to noise.
        x_tolerance = 0.5 * nominal_to_right_side
        y_tolerance = 0.5 * nominal_to_bottom
        if save_path is not None:
            print(f"DEBUG: tolerance x={x_tolerance:.2f}, y={y_tolerance:.2f}")


        top_right_squares = []
        bottom_left_squares = []
        bottom_right_squares = []

        if save_path:
            # Purely for diagnostic output - save the grid tolerance boxes to a file. This is
            # complicated, but useful for debugging and only is enabled when requested.
            # Nominal polygon of edges
            nominal_poly_new_basis = [
                geometry_utils.Point(0.0, 0.0),
                geometry_utils.Point(nominal_to_right_side, 0.0),
                geometry_utils.Point(nominal_to_right_side, nominal_to_bottom),
                geometry_utils.Point(0.0, nominal_to_bottom)
            ]
            # Boxes within which corner centroids can be found
            corner_tolerance_polys_new_basis = [
                [
                    geometry_utils.Point(x + x_tolerance, y - y_tolerance),
                    geometry_utils.Point(x + x_tolerance, y + y_tolerance),
                    geometry_utils.Point(x - x_tolerance, y + y_tolerance),
                    geometry_utils.Point(x - x_tolerance, y - y_tolerance)
                ] for [x, y] in [
                    [nominal_to_right_side, 0.5],
                    [nominal_to_right_side, nominal_to_bottom],
                    [0.5, nominal_to_bottom]
                ]
            ]
            polys = [preliminary_transformer.poly_from_basis(nominal_poly_new_basis), hexagon] + [
                preliminary_transformer.poly_from_basis(p) for p in corner_tolerance_polys_new_basis
            ]
            image_utils.draw_polygons(
                image,
                polys,
                save_path / f"grid_corner_tolerances_{i}.png",
                thickness=2
            )

        for quadrilateral in quadrilaterals:
            try:
                square = SquareMark(quadrilateral, l_mark.unit_length)
            except WrongShapeError:
                continue
            centroid = geometry_utils.guess_centroid(square.polygon)
            centroid_new_basis = preliminary_transformer.to_basis(centroid)

            if math_utils.is_within_tolerance(
                    centroid_new_basis.x, nominal_to_right_side,
                    x_tolerance) and math_utils.is_within_tolerance(
                        centroid_new_basis.y, 0.5, y_tolerance):
                top_right_squares.append(square)
            elif math_utils.is_within_tolerance(
                    centroid_new_basis.x, 0.5,
                    x_tolerance) and math_utils.is_within_tolerance(
                        centroid_new_basis.y, nominal_to_bottom, y_tolerance):
                bottom_left_squares.append(square)
            elif math_utils.is_within_tolerance(
                    centroid_new_basis.x, nominal_to_right_side,
                    x_tolerance) and math_utils.is_within_tolerance(
                        centroid_new_basis.y, nominal_to_bottom, y_tolerance):
                bottom_right_squares.append(square)

        if len(top_right_squares) == 0 or len(bottom_left_squares) == 0 or len(
                bottom_right_squares) == 0:
            continue

        # Use the most promising square marks by sorting them by their distance
        # to the nominal target center in the new basis.
        def score_square(square):
            centroid = geometry_utils.guess_centroid(square.polygon)
            centroid_nb = preliminary_transformer.to_basis(centroid)
            # target is (nominal_to_right_side, 0.5) for TR, etc.
            return centroid_nb

        # Re-filter and sort squares based on proximity to targets
        def get_best_square(candidates, target_x, target_y):
            if not candidates: return None
            return min(candidates, key=lambda s: (
                (preliminary_transformer.to_basis(geometry_utils.guess_centroid(s.polygon)).x - target_x)**2 +
                (preliminary_transformer.to_basis(geometry_utils.guess_centroid(s.polygon)).y - target_y)**2
            ))

        top_right_square = get_best_square(top_right_squares, nominal_to_right_side, 0.5)
        bottom_left_square = get_best_square(bottom_left_squares, 0.5, nominal_to_bottom)
        bottom_right_square = get_best_square(bottom_right_squares, nominal_to_right_side, nominal_to_bottom)

        if not (top_right_square and bottom_left_square and bottom_right_square):
            continue

        # Calculate the stable top-left grid corner by projecting the top-left L-mark
        # square center (get_origin) using its known PDF offset from the grid origin.
        # In PDF coordinates:
        #   C_BL (bottom-left square center) = (0.5, 0.5)
        #   C_BR (bottom-right square center) = (8.0, 0.5)
        #   C_TL (L-mark top-left square center) = (0.65625, 10.34375)
        # We solve for u and v such that C_grid_TL (0.5, 10.5) is C_BL + u*(C_BR - C_BL) + v*(C_TL - C_BL).
        # This yields:
        #   u = -4/189 = -0.021164
        #   v = 64/63 = 1.015873
        u = -4.0 / 189.0
        v = 64.0 / 63.0
        
        top_left_centroid = l_mark.get_origin()
        bottom_left_centroid = geometry_utils.guess_centroid(bottom_left_square.polygon)
        bottom_right_centroid = geometry_utils.guess_centroid(bottom_right_square.polygon)
        top_right_grid_pixel = geometry_utils.guess_centroid(top_right_square.polygon)
        
        top_left_grid_pixel = geometry_utils.Point(
            bottom_left_centroid.x + u * (bottom_right_centroid.x - bottom_left_centroid.x) + v * (top_left_centroid.x - bottom_left_centroid.x),
            bottom_left_centroid.y + u * (bottom_right_centroid.y - bottom_left_centroid.y) + v * (top_left_centroid.y - bottom_left_centroid.y)
        )
        bottom_left_grid_pixel = bottom_left_centroid
        bottom_right_grid_pixel = bottom_right_centroid
        
        basis_transformer = geometry_utils.ChangeOfBasisTransformer(
            top_left_grid_pixel, bottom_left_grid_pixel, bottom_right_grid_pixel, top_right_grid_pixel)

        top_left_corner = top_left_grid_pixel
        top_right_corner = top_right_grid_pixel
        bottom_right_corner = bottom_right_grid_pixel
        bottom_left_corner = bottom_left_grid_pixel

        grid_corners = [
            top_left_corner,     top_right_corner,
            bottom_right_corner, bottom_left_corner
        ]   

        if save_path:
            image_utils.draw_polygons(image, [grid_corners], save_path / "grid_limits.jpg")

        return grid_corners, basis_transformer
    raise CornerFindingError("Couldn't find document corners.")
