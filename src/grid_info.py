import enum
import typing as tp

from . import alphabet
from . import geometry_utils
Orientation = geometry_utils.Orientation

KEY_STUDENT_ID = "99999"

# Default grid dimensions used by the legacy single-page variants (75q and 150q).
GRID_HORIZONTAL_CELLS = 36
GRID_VERTICAL_CELLS = 48


class Field(enum.Enum):
    """Fields that exist on the bubble sheet."""
    LAST_NAME = enum.auto()
    FIRST_NAME = enum.auto()
    MIDDLE_NAME = enum.auto()
    TEST_FORM_CODE = enum.auto()
    STUDENT_ID = enum.auto()
    COURSE_ID = enum.auto()
    IMAGE_FILE = enum.auto()


class VirtualField(enum.Enum):
    """Data points that don't exist on the bubble sheet, but could be added to the output."""
    SCORE = enum.auto()
    POINTS = enum.auto()


RealOrVirtualField = tp.Union[Field, VirtualField]


class FieldType(enum.Enum):
    LETTER = enum.auto()
    NUMBER = enum.auto()


class GridGroupInfo():
    """Metadata about a set of grid cells (not tied to a specific grid or image).
    
    `field_orientation` controls how successive fields are laid out spatially
    (HORIZONTAL = fields go left→right, VERTICAL = fields go top→bottom).
    `cell_orientation` controls how the bubbles *within* each field are read
    (HORIZONTAL = bubbles go left→right, VERTICAL = bubbles go top→bottom).
    When `cell_orientation` is not specified it defaults to `field_orientation`,
    which matches the behaviour of all existing single-axis fields.
    """

    horizontal_start: int
    vertical_start: int
    num_fields: int
    field_length: int
    fields_type: FieldType
    field_orientation: Orientation
    cell_orientation: Orientation

    def __init__(
            self,
            horizontal_start: int,
            vertical_start: int,
            num_fields: int = 1,
            fields_type: FieldType = FieldType.NUMBER,
            field_length: tp.Optional[int] = None,
            field_orientation: Orientation = Orientation.VERTICAL,
            cell_orientation: tp.Optional[Orientation] = None):
        self.horizontal_start = horizontal_start
        self.vertical_start = vertical_start
        self.num_fields = num_fields
        if field_length is not None:
            self.field_length = field_length
        elif fields_type is FieldType.LETTER:
            self.field_length = alphabet.LENGTH
        else:
            self.field_length = 10
        self.fields_type = fields_type
        self.field_orientation = field_orientation
        # cell_orientation defaults to field_orientation for backward compatibility
        self.cell_orientation = cell_orientation if cell_orientation is not None else field_orientation


# A Field may map to either a single GridGroupInfo or to a list of them.
FieldValue = tp.Union[GridGroupInfo, tp.List[GridGroupInfo], None]


def _is_field_list(value: FieldValue) -> bool:
    return isinstance(value, list)


class FormVariant():
    """Description of a bubble-sheet form."""

    fields: tp.Dict[Field, FieldValue]
    question_columns: tp.List[tp.List[GridGroupInfo]]
    horizontal_cells: int
    vertical_cells: int
    basis_width: float
    basis_height: float
    y_shift: float

    def __init__(
            self,
            fields: tp.Dict[Field, FieldValue],
            questions: tp.Union[GridGroupInfo,
                               tp.List[GridGroupInfo],
                               tp.List[tp.List[GridGroupInfo]]],
            horizontal_cells: int = GRID_HORIZONTAL_CELLS,
            vertical_cells: int = GRID_VERTICAL_CELLS,
            basis_width: float = 49.5,
            basis_height: float = 31.75,
            y_shift: float = 0.0):
        self.fields = dict(fields)
        if isinstance(questions, GridGroupInfo):
            self.question_columns = [[questions]]
        elif len(questions) > 0 and isinstance(questions[0], GridGroupInfo):
            self.question_columns = [list(questions)]
        else:
            self.question_columns = [list(col) for col in questions]
        self.horizontal_cells = horizontal_cells
        self.vertical_cells = vertical_cells
        self.basis_width = basis_width
        self.basis_height = basis_height
        self.y_shift = y_shift

    @property
    def questions(self) -> tp.List[GridGroupInfo]:
        return [q for col in self.question_columns for q in col]

    @property
    def num_questions(self) -> int:
        return sum(len(col) for col in self.question_columns)

    @property
    def questions_per_column(self) -> int:
        if not self.question_columns:
            return 0
        return max(len(col) for col in self.question_columns)


# Legacy Variants
form_75q = FormVariant(
    {
        Field.LAST_NAME: GridGroupInfo(1, 3, 12, fields_type=FieldType.LETTER),
        Field.FIRST_NAME: GridGroupInfo(14, 3, 6, fields_type=FieldType.LETTER),
        Field.MIDDLE_NAME: GridGroupInfo(21, 3, 2, fields_type=FieldType.LETTER),
        # Student ID: 5 columns of digits, each column has 10 bubbles (0-9)
        # Fields go left→right (HORIZONTAL), but each column's bubbles go top→bottom (VERTICAL).
        Field.STUDENT_ID: GridGroupInfo(
            25,                          # starting column for first digit
            3,                           # starting row for bubbles
            num_fields=5,                # five digit fields (columns)
            fields_type=FieldType.NUMBER,
            field_length=10,             # ten bubbles per digit (0-9)
            field_orientation=Orientation.HORIZONTAL,
            cell_orientation=Orientation.VERTICAL,
        ),
        Field.COURSE_ID: GridGroupInfo(25, 16, 10),
        Field.TEST_FORM_CODE: GridGroupInfo(27, 28, fields_type=FieldType.LETTER, field_length=6, field_orientation=Orientation.HORIZONTAL)
    }, [
        GridGroupInfo(2 + (7 * (i // 15)), 32 + i - (15 * (i // 15)), fields_type=FieldType.LETTER, field_length=5, field_orientation=Orientation.HORIZONTAL)
        for i in range(75)
    ])

form_150q = FormVariant(
    {
        # Student ID: 5 columns of digits, each column has 10 bubbles (0-9)
        # Fields go left→right (HORIZONTAL), but each column's bubbles go top→bottom (VERTICAL).
        Field.STUDENT_ID: GridGroupInfo(
            25,
            3,
            num_fields=5,
            fields_type=FieldType.NUMBER,
            field_length=10,
            field_orientation=Orientation.HORIZONTAL,
            cell_orientation=Orientation.VERTICAL,
        ),
        Field.COURSE_ID: GridGroupInfo(14, 3, 10),
        Field.TEST_FORM_CODE: GridGroupInfo(4, 12, fields_type=FieldType.LETTER, field_length=6, field_orientation=Orientation.HORIZONTAL)
    }, [
        GridGroupInfo(2 + (7 * (i // 30)), 17 + i - (30 * (i // 30)), fields_type=FieldType.LETTER, field_length=5, field_orientation=Orientation.HORIZONTAL)
        for i in range(150)
    ])

# ---------------------------------------------------------------------------
# Two-sided 240-question variant (3 MCQ columns x 80 questions).
# ---------------------------------------------------------------------------

TWOSIDED_HORIZONTAL_CELLS = 24
TWOSIDED_VERTICAL_CELLS = 60
TWOSIDED_BASIS_WIDTH = 49.5
TWOSIDED_BASIS_HEIGHT = (TWOSIDED_VERTICAL_CELLS / GRID_VERTICAL_CELLS) * 31.75


def _make_mcq_column_split(vertical_start: int,
                           *half_specs: tp.Tuple[int, int]
                           ) -> tp.List[GridGroupInfo]:
    questions: tp.List[GridGroupInfo] = []
    for h_start, count in half_specs:
        for i in range(count):
            questions.append(
                GridGroupInfo(h_start,
                              vertical_start + i,
                              fields_type=FieldType.LETTER,
                              field_length=5,
                              field_orientation=Orientation.HORIZONTAL))
    return questions


def _make_two_sided_variant(
        fields: tp.Dict[Field, FieldValue],
        question_columns: tp.List[tp.List[GridGroupInfo]],
        y_shift: float = 0.0) -> FormVariant:
    return FormVariant(fields,
                       question_columns,
                       horizontal_cells=TWOSIDED_HORIZONTAL_CELLS,
                       vertical_cells=TWOSIDED_VERTICAL_CELLS,
                       basis_width=TWOSIDED_BASIS_WIDTH,
                       basis_height=TWOSIDED_BASIS_HEIGHT,
                       y_shift=y_shift)

# Page 1:
# Col 1: Student ID (5 digits, column 2)
# Col 2: Test 1 MCQ (sub1: col 13, sub2: col 19)
form_240q_page1 = _make_two_sided_variant(
    {
        # Student ID: 5 columns of digits, each column has 10 bubbles (0-9).
        # Fields go left→right (HORIZONTAL), bubbles within each column go top→bottom (VERTICAL).
        Field.STUDENT_ID: GridGroupInfo(
            2,                           # column where the first digit starts
            4,                           # row where bubbles start (row 4 = first bubble row)
            num_fields=5,                # five digit columns
            fields_type=FieldType.NUMBER,
            field_length=10,             # ten bubbles per digit (0-9)
            field_orientation=Orientation.HORIZONTAL,
            cell_orientation=Orientation.VERTICAL,
        ),
        Field.TEST_FORM_CODE: GridGroupInfo(13, 2, 4),
    },
    [
        _make_mcq_column_split(16, (13, 40), (19, 40)),
    ],
)

# Page 2:
# Col 1: Test 2 (sub1: col 1, sub2: col 7)
# Col 2: Test 3 (sub1: col 13, sub2: col 19)
form_240q_page2 = _make_two_sided_variant(
    {
        Field.TEST_FORM_CODE: [
            GridGroupInfo(1, 1, 4),
            GridGroupInfo(13, 1, 4),
        ],
    },
    [
        _make_mcq_column_split(32, (1, 40), (7, 40)),
        _make_mcq_column_split(48, (13, 40), (19, 40)),
    ],
)


class TwoSidedFormVariant():
    """Variant with two pages. Holds separate per-page FormVariant configurations."""

    page_variants: tp.List[FormVariant]

    def __init__(self, page_variants: tp.List[FormVariant]):
        assert isinstance(page_variants, list)
        assert len(page_variants) == 2
        self.page_variants = page_variants

    def variant_for_page(self, page_index: int) -> FormVariant:
        return self.page_variants[page_index]


# The two-sided 240q form combines two page variants.
form_240q = TwoSidedFormVariant([form_240q_page1, form_240q_page2])

# Alias for backward compatibility / main script expectation
form_two_sided_240q = form_240q


def _make_mcq_column_split_v2(specs: tp.List[tp.Tuple[int, int, int]]) -> tp.List[GridGroupInfo]:
    questions: tp.List[GridGroupInfo] = []
    for h_start, v_start, count in specs:
        for i in range(count):
            questions.append(
                GridGroupInfo(h_start,
                              v_start + i,
                              fields_type=FieldType.LETTER,
                              field_length=5,
                              field_orientation=Orientation.HORIZONTAL))
    return questions


form_225q_page1 = _make_two_sided_variant(
    {
        Field.STUDENT_ID: GridGroupInfo(
            2,
            6,   # PDF row 6 = digit-0 bubble (SID_ROW=5, field_digits draws at row_start+1+digit)
            num_fields=5,
            fields_type=FieldType.NUMBER,
            field_length=10,
            field_orientation=Orientation.HORIZONTAL,
            cell_orientation=Orientation.VERTICAL,
        ),
        Field.TEST_FORM_CODE: GridGroupInfo(
            13,
            6,   # same as SID
            num_fields=4,
            fields_type=FieldType.NUMBER,
            field_length=10,
            field_orientation=Orientation.HORIZONTAL,
            cell_orientation=Orientation.VERTICAL,
        ),
    },
    [
        _make_mcq_column_split_v2([(13, 19, 38), (19, 19, 37)]),
    ],
    y_shift=0.006,
)

form_225q_page2 = _make_two_sided_variant(
    {
        Field.TEST_FORM_CODE: [
            GridGroupInfo(
                0,
                6,   # PDF row 6 = digit-0 bubble
                num_fields=4,
                fields_type=FieldType.NUMBER,
                field_length=10,
                field_orientation=Orientation.HORIZONTAL,
                cell_orientation=Orientation.VERTICAL,
            ),
            GridGroupInfo(
                13,
                6,
                num_fields=4,
                fields_type=FieldType.NUMBER,
                field_length=10,
                field_orientation=Orientation.HORIZONTAL,
                cell_orientation=Orientation.VERTICAL,
            ),
        ],
    },
    [
        _make_mcq_column_split_v2([(0, 19, 38), (6, 19, 37)]),
        _make_mcq_column_split_v2([(13, 19, 38), (19, 19, 37)]),
    ],
    y_shift=0.006,
)

form_two_sided_225q = TwoSidedFormVariant([form_225q_page1, form_225q_page2])

