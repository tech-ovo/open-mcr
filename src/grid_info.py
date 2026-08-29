import enum
import typing as tp

from . import alphabet
from . import geometry_utils
from . import sheet_layout
Orientation = geometry_utils.Orientation

#: A sheet whose Student ID is all nines is an answer key rather than a
#: student's paper. The CAJCL sheet uses a 4-digit ID, so that is "9999".
KEY_STUDENT_ID = sheet_layout.KEY_STUDENT_ID

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
    LATIN_LEVEL = enum.auto()
    PAGE_CODE = enum.auto()
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


class FormVariant():
    """Description of a bubble-sheet form."""

    fields: tp.Dict[Field, FieldValue]
    question_columns: tp.List[tp.List[GridGroupInfo]]
    horizontal_cells: int
    vertical_cells: int
    basis_width: float
    basis_height: float
    y_shift: float
    output_fields: tp.List[Field]
    key_student_id: str
    l_mark_offset: tp.Tuple[float, float]
    review_marks_by_default: bool

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
            y_shift: float = 0.0,
            output_fields: tp.Optional[tp.List[Field]] = None,
            l_mark_offset: tp.Tuple[float, float] = (0.15625 / 7.5,
                                                     0.15625 / 10.0),
            review_marks_by_default: bool = False):
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
        # Where this sheet's L-mark sits relative to the grid corner; see
        # `corner_finding.DEFAULT_L_MARK_OFFSET`.
        self.l_mark_offset = l_mark_offset
        # Whether to hold every mark to the legibility bar in `mark_quality`
        # unless the operator says otherwise. On for the CAJCL sheet, whose
        # workflow is "when in doubt, grade it by hand"; off for the legacy
        # sheets, whose scans predate the check and whose behaviour should not
        # change under it.
        self.review_marks_by_default = review_marks_by_default
        # Columns to write to the results CSV. Defaults to every field that
        # exists on the page; variants override it to hide bookkeeping fields
        # (such as the page code) or to fix the column order.
        self.output_fields = (list(output_fields) if output_fields is not None
                              else list(self.fields.keys()))
        # A sheet whose Student ID is all nines is an answer key. How many
        # nines depends on how many digit columns the variant prints.
        student_id = self.fields.get(Field.STUDENT_ID)
        if isinstance(student_id, list):
            student_id = student_id[0] if student_id else None
        self.key_student_id = ("9" * student_id.num_fields
                               if student_id is not None else KEY_STUDENT_ID)

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
# CAJCL State Convention variant: two pages, three 80-question tests.
#
# Every coordinate below is read out of `sheet_layout`, which is also what
# `sheet_generation` draws from, so the grid the reader looks for and the
# sheet that gets printed cannot drift apart.
# ---------------------------------------------------------------------------


def _digit_field(first_column: int) -> GridGroupInfo:
    """A block of digit columns: fields run left to right, bubbles top to
    bottom within each column."""
    return GridGroupInfo(
        first_column,
        sheet_layout.ID_FIRST_BUBBLE_ROW,
        num_fields=sheet_layout.ID_DIGITS,
        fields_type=FieldType.NUMBER,
        field_length=sheet_layout.BUBBLES_PER_DIGIT,
        field_orientation=Orientation.HORIZONTAL,
        cell_orientation=Orientation.VERTICAL,
    )


def _single_choice_field(column: int, row: int, options: int,
                         orientation: Orientation) -> GridGroupInfo:
    """One field whose `options` bubbles run along `orientation`."""
    return GridGroupInfo(
        column,
        row,
        num_fields=1,
        fields_type=FieldType.NUMBER,
        field_length=options,
        field_orientation=orientation,
        cell_orientation=orientation,
    )


def _answer_column(page_index: int, test_on_page: int
                   ) -> tp.List[GridGroupInfo]:
    """The 80 questions of one test, read in printed order: the 40 rows of the
    left block followed by the 40 rows of the right block."""
    questions: tp.List[GridGroupInfo] = []
    for column in sheet_layout.PAGE_SUBCOLUMNS[page_index][test_on_page]:
        for offset in range(sheet_layout.ROWS_PER_SUBCOLUMN):
            questions.append(
                GridGroupInfo(column,
                              sheet_layout.MCQ_FIRST_ROW + offset,
                              fields_type=FieldType.LETTER,
                              field_length=len(sheet_layout.OPTIONS),
                              field_orientation=Orientation.HORIZONTAL))
    return questions


def _cajcl_page(page_index: int, fields: tp.Dict[Field, FieldValue],
                output_fields: tp.List[Field]) -> FormVariant:
    return FormVariant(
        fields,
        [
            _answer_column(page_index, test_on_page) for test_on_page in
            range(len(sheet_layout.PAGE_SUBCOLUMNS[page_index]))
        ],
        horizontal_cells=sheet_layout.GRID_COLUMNS,
        vertical_cells=sheet_layout.GRID_ROWS,
        basis_width=sheet_layout.BASIS_WIDTH,
        basis_height=sheet_layout.BASIS_HEIGHT,
        output_fields=output_fields,
        l_mark_offset=sheet_layout.L_MARK_OFFSET_FRACTION,
        review_marks_by_default=True,
    )


#: Column order of the results CSV. `Field.PAGE_CODE` is deliberately absent:
#: it is bookkeeping used to verify page order, not exam data.
CAJCL_OUTPUT_FIELDS: tp.List[Field] = [
    Field.STUDENT_ID,
    Field.LATIN_LEVEL,
    Field.TEST_FORM_CODE,
    Field.IMAGE_FILE,
]

_PAGE_CODE_FIELD = _single_choice_field(sheet_layout.PAGE_CODE_FIRST_COLUMN,
                                        sheet_layout.PAGE_CODE_ROW,
                                        sheet_layout.PAGE_CODE_OPTIONS,
                                        Orientation.HORIZONTAL)

form_cajcl_page1 = _cajcl_page(
    0,
    {
        Field.PAGE_CODE: _PAGE_CODE_FIELD,
        Field.STUDENT_ID: _digit_field(sheet_layout.STUDENT_ID_COLUMN),
        Field.LATIN_LEVEL: _single_choice_field(
            sheet_layout.LATIN_LEVEL_COLUMN,
            sheet_layout.LATIN_LEVEL_FIRST_ROW,
            len(sheet_layout.LATIN_LEVELS), Orientation.VERTICAL),
        Field.TEST_FORM_CODE: _digit_field(
            sheet_layout.PAGE_TEST_ID_COLUMNS[0][0]),
    },
    CAJCL_OUTPUT_FIELDS,
)

form_cajcl_page2 = _cajcl_page(
    1,
    {
        Field.PAGE_CODE: _PAGE_CODE_FIELD,
        # Repeated on the back so a page that gets separated from its front
        # can still be identified (and so mis-collated batches are caught).
        Field.STUDENT_ID: _digit_field(sheet_layout.STUDENT_ID_COLUMN),
        # One Test ID per answer column on this page.
        Field.TEST_FORM_CODE: [
            _digit_field(column)
            for column in sheet_layout.PAGE_TEST_ID_COLUMNS[1]
        ],
    },
    CAJCL_OUTPUT_FIELDS,
)


class TwoSidedFormVariant():
    """A variant printed on two pages, with a separate layout for each side."""

    page_variants: tp.List[FormVariant]

    def __init__(self, page_variants: tp.List[FormVariant]):
        assert isinstance(page_variants, list)
        assert len(page_variants) == sheet_layout.PAGES_PER_SHEET
        self.page_variants = page_variants

    def variant_for_page(self, page_index: int) -> FormVariant:
        return self.page_variants[page_index % len(self.page_variants)]

    @property
    def pages_per_sheet(self) -> int:
        return len(self.page_variants)

    @property
    def questions_per_column(self) -> int:
        return max(v.questions_per_column for v in self.page_variants)

    @property
    def output_fields(self) -> tp.List[Field]:
        return list(self.page_variants[0].output_fields)

    @property
    def key_student_id(self) -> str:
        return self.page_variants[0].key_student_id

    @property
    def l_mark_offset(self) -> tp.Tuple[float, float]:
        return self.page_variants[0].l_mark_offset

    @property
    def review_marks_by_default(self) -> bool:
        return self.page_variants[0].review_marks_by_default

    @property
    def tests_per_sheet(self) -> int:
        return sum(
            len(v.question_columns) for v in self.page_variants)


form_cajcl = TwoSidedFormVariant([form_cajcl_page1, form_cajcl_page2])

#: Fields whose value is read on the front page and belongs to the whole
#: sheet, so it is carried forward onto the rows produced by the back page.
CARRY_OVER_FIELDS: tp.Tuple[Field, ...] = (Field.STUDENT_ID,
                                           Field.LATIN_LEVEL)


def latin_level_name(raw_value: str) -> str:
    """Map the bubbled Latin level index onto its printed name."""
    digits = "".join(ch for ch in raw_value if ch.isdigit())
    if not digits:
        return ""
    index = int(digits)
    if 0 <= index < len(sheet_layout.LATIN_LEVELS):
        return sheet_layout.LATIN_LEVELS[index]
    return ""
