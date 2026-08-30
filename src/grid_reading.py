"""Functions for establishing and reading the grid."""

# pyright: reportIncompatibleMethodOverride=false

import abc
import pathlib
import typing as tp

import cv2
import numpy as np
from numpy import ma

from . import alphabet
from . import geometry_utils
from . import grid_info
from . import image_utils

""" This is what determines the circle size of the grid cell mask. If it is 0,
the circle touches all edges of the grid cell. If it is 0.5, the circle is 50%
of the width and height of the cell (centered at the center of the cell.)

Notes:
 * Remember that the grid is not perfect.
 * If this is too large, you risk the circle enclosing part of another bubble
 or extraneous data.
 * If this is too small, it won't include the entire bubble. We want to include
 the entire bubble including the border, because all the bubbles are known to be
 the same size so if we include the entire bubble then we have around the same
 fill % for all of them.
"""
GRID_CELL_CROP_FRACTION = 0.25

# TODO: Import from geometry_utils when pyright#284 is fixed.
Polygon = tp.List[geometry_utils.Point]


class Grid:
    corners: Polygon
    horizontal_cells: int
    vertical_cells: int
    image: np.ndarray
    basis_transformer: geometry_utils.ChangeOfBasisTransformer

    def __init__(self,
                 corners: geometry_utils.Polygon,
                 horizontal_cells: int,
                 vertical_cells: int,
                 image: np.ndarray,
                 basis_transformer: tp.Optional[geometry_utils.ChangeOfBasisTransformer] = None,
                 save_path: tp.Optional[pathlib.PurePath] = None,
                 y_shift: float = 0.0):
        """Initiate a new Grid. Corners should be clockwise starting from the
        top left - if not, the grid will have unexpected behavior.

        If `save_path` is provided, will save the resulting image to this location
        as "grid.jpg". Used for debugging purposes."""
        self.corners = corners
        self.horizontal_cells = horizontal_cells
        self.vertical_cells = vertical_cells
        self.y_shift = y_shift
        
        if basis_transformer is not None:
            self.basis_transformer = basis_transformer
        else:
            self.basis_transformer = geometry_utils.ChangeOfBasisTransformer(
                corners[0], corners[3], corners[2])

        self.horizontal_cell_size = 1 / self.horizontal_cells
        self.vertical_cell_size = 1 / self.vertical_cells

        self.image = image

        if save_path:
            image_utils.save_image(save_path / "grid.jpg", self.draw_grid())

    def _get_cell_shape_in_basis(self, across: int,
                                 down: int) -> geometry_utils.Polygon:
        return [
            geometry_utils.Point(across * self.horizontal_cell_size,
                                 down * self.vertical_cell_size + self.y_shift),
            geometry_utils.Point((across + 1) * self.horizontal_cell_size,
                                 down * self.vertical_cell_size + self.y_shift),
            geometry_utils.Point((across + 1) * self.horizontal_cell_size,
                                 (down + 1) * self.vertical_cell_size + self.y_shift),
            geometry_utils.Point(across * self.horizontal_cell_size,
                                 (down + 1) * self.vertical_cell_size + self.y_shift),
        ]


    def get_cell_range(self, across: int, down: int) -> tp.Tuple[tp.Tuple[float, float], tp.Tuple[float, float]]:
        """Get the range of x and y-dimensions that this cell touches, in the basis that the cell
        is given.
        
        Returns tuple of ((min_x, max_x), (min_y, max_y))"""
        cell = self.get_cell_shape(across, down)
        # Cannot just use the top-left and bottom-right points according to the polygon - what if
        # the cell is rotated 30 degrees? We want to use the absolute max and min coordinates.
        x_coords = [point.x for point in cell]
        y_coords = [point.y for point in cell]
        return (min(x_coords), max(x_coords)), (min(y_coords), max(y_coords))

    def get_cell_shape(self, across: int, down: int) -> geometry_utils.Polygon:
        """Get the shape of a cell using it's 0-based index. Returns the contour
        in CW direction starting with the top left cell."""
        return self.basis_transformer.poly_from_basis(self._get_cell_shape_in_basis(across, down))

    def get_unmasked_cell_matrix(self, across: int, down: int) -> np.ndarray:
        """Get the matrix of pixels in the grid cell area. Note if the grid is rotated, this will
        include pixels outside the cell since the cell will be a diamond and this will be a
        rectangle."""
        ((min_x, max_x), (min_y, max_y)) = self.get_cell_range(across, down)
        # Add 1 for inclusive indexing. Numpy will not accept even rounded floats as indexes.
        return self.image[
            int(round(min_y)):int(round(max_y + 1)),
            int(round(min_x)):int(round(max_x + 1))
]

    def get_cell_center(self, across: int, down: int) -> geometry_utils.Point:
        """Get the center point of the cell."""
        ((min_x, max_x), (min_y, max_y)) = self.get_cell_range(across, down)
        return geometry_utils.Point(min_x + ((max_x - min_x) / 2),
                                    min_y + ((max_y - min_y) / 2))

    def get_cell_circle(self, across: int,
                        down: int) -> tp.Tuple[geometry_utils.Point, float]:
        ((min_x, max_x), (min_y, max_y)) = self.get_cell_range(across, down)
        # If the cell is not perfectly square, base the circle size on the average dimension
        average_dimension = ((max_x - min_x) + (max_y - min_y)) / 2
        diameter = average_dimension * (1 - GRID_CELL_CROP_FRACTION)
        center = self.get_cell_center(across, down)
        return (center, diameter / 2)

    def get_masked_cell_matrix(self, across: int, down: int) -> ma.MaskedArray:
        """Get the matrix of pixels in the cell area, masked down to just the cell circle."""
        # Extract the raw pixel values for the cell
        unmasked = self.get_unmasked_cell_matrix(across, down)
        # Determine cell bounds in image coordinates
        ((min_x, max_x), (min_y, max_y)) = self.get_cell_range(across, down)
        # Compute average dimension and radius for the circular mask
        avg_dim = ((max_x - min_x) + (max_y - min_y)) / 2
        radius = int((avg_dim * (1 - GRID_CELL_CROP_FRACTION)) / 2)
        # Build a boolean mask where points outside the circle are masked (True)
        height, width = unmasked.shape
        cy, cx = height // 2, width // 2
        yy, xx = np.ogrid[:height, :width]
        mask_bool = (xx - cx) ** 2 + (yy - cy) ** 2 > radius ** 2
        masked = ma.masked_array(unmasked, mask_bool)
        return masked

    def draw_grid(self):
        """Draws the grid on the image, returning a copy with red dots at grid
        points."""
        image = image_utils.bw_to_bgr(self.image)
        for x in range(self.horizontal_cells):
            for y in range(self.vertical_cells):
                points = self.get_cell_shape(x, y)
                for point in points:
                    cv2.circle(image,
                               (int(round(point.x)), int(round(point.y))), 2,
                               (0, 0, 255), -1)
                center, radius = self.get_cell_circle(x, y)
                cv2.circle(image, (int(round(center.x)), int(round(center.y))),
                           int(round(radius)), (255, 0, 0), 1)
        return image


class _GridField(abc.ABC):
    """A grid field is one set of grid cells that represents a value, ie a single
    letter or number."""

    horizontal_start_index: float
    vertical_start_index: float
    orientation: geometry_utils.Orientation
    num_cells: int
    grid: Grid

    def __init__(self, grid: Grid, horizontal_start: int, vertical_start: int,
                 orientation: geometry_utils.Orientation, num_cells: int):
        self.vertical_start = vertical_start
        self.horizontal_start = horizontal_start
        self.orientation = orientation
        self.num_cells = num_cells
        self.grid = grid

    @abc.abstractclassmethod
    def read_value(self, threshold: float, fill_percents: tp.List[float]
                   ) -> tp.Union[tp.List[str], tp.List[int]]:
        ...

    def _read_value_indexes(self, threshold: float,
                            fill_percents: tp.List[float]) -> tp.List[int]:
        filled = [
            i for i in range(self.num_cells) if fill_percents[i] > threshold
        ]
        return filled

    def cell_coordinates(self) -> tp.List[tp.Tuple[int, int]]:
        """The `(across, down)` grid coordinate of each bubble in this field."""
        is_vertical = self.orientation is geometry_utils.Orientation.VERTICAL
        return [(self.horizontal_start
                 if is_vertical else self.horizontal_start + i,
                 self.vertical_start + i
                 if is_vertical else self.vertical_start)
                for i in range(self.num_cells)]

    def get_cell_matrixes(self) -> tp.List[ma.MaskedArray]:
        # Have to crop the edges to avoid getting the borders of the write-in
        # squares in the cells
        return [
            self.grid.get_masked_cell_matrix(x, y)
            for x, y in self.cell_coordinates()
        ]

    def get_all_fill_percents(self) -> tp.List[float]:
        results = [
            image_utils.get_fill_percent(square)
            for square in self.get_cell_matrixes()
        ]
        return results


class NumberGridField(_GridField):
    """A number grid field is one set of grid cells that represents a digit.
    
    For numeric fields exactly one bubble per field should be filled.
    We use argmax (highest fill percent) rather than threshold comparison.
    The global threshold is calibrated to MCQ answer bubbles which are
    typically much darker than number-field bubbles; using it here causes
    blank results. Instead we use a low fixed noise-floor cutoff (0.05) —
    any column whose max fill exceeds 5% is considered to have a bubble filled.
    """
    # Minimum fill percent to consider a bubble filled (noise floor)
    _MIN_FILL = 0.05

    def read_value(self, threshold: float,
                   fill_percents: tp.List[float]) -> tp.List[int]:
        if not fill_percents:
            return []
        max_fill = max(fill_percents)
        # Use a low fixed floor rather than the global (MCQ-calibrated) threshold
        if max_fill <= self._MIN_FILL:
            return []
        best_idx = fill_percents.index(max_fill)
        return [best_idx]


class LetterGridField(_GridField):
    """A number grid field is one set of grid cells that represents a letter."""
    def read_value(self, threshold: float,
                   fill_percents: tp.List[float]) -> tp.List[str]:
        return [
            alphabet.letters[i]
            for i in super()._read_value_indexes(threshold, fill_percents)
        ]


class _GridFieldGroup(abc.ABC):
    """A grid field group is a group of grid fields, ie a word. Do not use
    directly."""

    fields: tp.Sequence[_GridField]

    @abc.abstractclassmethod
    def __init__(self, grid: Grid, horizontal_start: int, vertical_start: int,
                 num_fields: int, field_length: int,
                 field_orientation: geometry_utils.Orientation):
        ...

    def read_value(self, threshold: float,
                   fill_percents: tp.List[tp.List[float]]
                   ) -> tp.List[tp.Union[tp.List[str], tp.List[int]]]:
        """Calculate the field value using precalculated fill percents."""
        return [
            field.read_value(threshold, fill_percents[i])
            for i, field in enumerate(self.fields)
        ]

    def get_all_fill_percents(self) -> tp.List[tp.List[float]]:
        return [field.get_all_fill_percents() for field in self.fields]


class NumberGridFieldGroup(_GridFieldGroup):
    """A number grid field group is one group of fields that represents an
    entire number."""
    def __init__(self, grid: Grid, horizontal_start: int, vertical_start: int,
                 num_fields: int, field_length: int,
                 field_orientation: geometry_utils.Orientation,
                 cell_orientation: tp.Optional[geometry_utils.Orientation] = None):
        fields_vertical = field_orientation is geometry_utils.Orientation.VERTICAL
        # cell_orientation controls how bubbles within each field are read;
        # defaults to field_orientation for backward compatibility.
        _cell_orient = cell_orientation if cell_orientation is not None else field_orientation
        self.fields = [
            NumberGridField(
                grid,
                horizontal_start + i if not fields_vertical else horizontal_start,
                vertical_start + i if fields_vertical else vertical_start,
                _cell_orient, field_length) for i in range(num_fields)
        ]

    def read_value(self, threshold: float,
                   fill_percents: tp.List[tp.List[float]]
                   ) -> tp.List[tp.List[int]]:
        return tp.cast(tp.List[tp.List[int]],
                       super().read_value(threshold, fill_percents))


class LetterGridFieldGroup(_GridFieldGroup):
    """A letter grid field group is one group of fields that represents an
    entire string."""
    def __init__(self, grid: Grid, horizontal_start: int, vertical_start: int,
                 num_fields: int, field_length: int,
                 field_orientation: geometry_utils.Orientation,
                 cell_orientation: tp.Optional[geometry_utils.Orientation] = None):
        fields_vertical = field_orientation is geometry_utils.Orientation.VERTICAL
        _cell_orient = cell_orientation if cell_orientation is not None else field_orientation
        self.fields = [
            LetterGridField(
                grid,
                horizontal_start + i if not fields_vertical else horizontal_start,
                vertical_start + i if fields_vertical else vertical_start,
                _cell_orient, field_length) for i in range(num_fields)
        ]

    def read_value(self, threshold: float,
                   fill_percents: tp.List[tp.List[float]]
                   ) -> tp.List[tp.List[str]]:
        return tp.cast(tp.List[tp.List[str]],
                       super().read_value(threshold, fill_percents))


def get_group_from_info(info: grid_info.GridGroupInfo,
                        grid: Grid) -> _GridFieldGroup:
    if info.fields_type is grid_info.FieldType.LETTER:
        return LetterGridFieldGroup(grid, info.horizontal_start,
                                    info.vertical_start, info.num_fields,
                                    info.field_length, info.field_orientation,
                                    info.cell_orientation)
    else:
        return NumberGridFieldGroup(grid, info.horizontal_start,
                                    info.vertical_start, info.num_fields,
                                    info.field_length, info.field_orientation,
                                    info.cell_orientation)


def get_answer_fill_percents_for_column(
        column_index: int, grid: Grid,
        form_variant: grid_info.FormVariant
) -> tp.List[tp.List[float]]:
    """Return the fill-percent list for every question in a specific MCQ
    column."""
    column = form_variant.question_columns[column_index]
    return [get_group_from_info(q, grid).get_all_fill_percents() for q in column]


def get_field_fill_percents(
        field: grid_info.Field, grid: Grid,
        form_variant: grid_info.FormVariant
) -> tp.List[tp.List[tp.List[float]]]:
    """Return the fill-percent matrix for every instance of a field."""
    grid_group_info = form_variant.fields[field]
    if grid_group_info is None:
        return []
    if isinstance(grid_group_info, list):
        infos = grid_group_info
    else:
        infos = [grid_group_info]
    return [get_group_from_info(info, grid).get_all_fill_percents() for info in infos]


def get_group_cell_circles(
        info: grid_info.GridGroupInfo, grid: Grid
) -> tp.List[tp.List[tp.Tuple[float, float, float]]]:
    """Locate every bubble of a group in the source image.

    Returns one list of `(x, y, radius)` circles per field in the group, in the
    same order as the fill percents. Used to draw the marked-up PDF.
    """
    group = get_group_from_info(info, grid)
    circles: tp.List[tp.List[tp.Tuple[float, float, float]]] = []
    for field in group.fields:
        field_circles: tp.List[tp.Tuple[float, float, float]] = []
        for across, down in field.cell_coordinates():
            centre, radius = grid.get_cell_circle(across, down)
            field_circles.append((centre.x, centre.y, radius))
        circles.append(field_circles)
    return circles
