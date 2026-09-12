"""Geometry of the CAJCL State Convention answer sheet.

This module is the single source of truth for the sheet layout. Both the PDF
generator (:mod:`sheet_generation`) and the reader's description of the grid
(:mod:`grid_info`) are derived from the constants below, so the printed sheet
and the grid the software looks for can never drift apart.

Coordinate conventions
----------------------
* ``column`` counts grid cells from the left edge of the grid (0-based).
* ``row`` counts grid cells down from the top edge of the grid (0-based).
* The grid spans exactly the area bounded by the four corner marks, so cell
  ``(0, 0)`` starts at the centre of the top-left L-mark's corner square and
  the grid ends at the centre of the bottom-right square.
"""

import dataclasses
import typing as tp

# --- Page and grid -------------------------------------------------------

PAGE_WIDTH_IN = 8.5
PAGE_HEIGHT_IN = 11.0

GRID_COLUMNS = 24
GRID_ROWS = 60

#: Distance from each page edge to the centres of the corner marks.
CORNER_INSET_IN = 0.5

GRID_WIDTH_IN = PAGE_WIDTH_IN - (2 * CORNER_INSET_IN)   # 7.5
GRID_HEIGHT_IN = PAGE_HEIGHT_IN - (2 * CORNER_INSET_IN)  # 10.0

CELL_WIDTH_IN = GRID_WIDTH_IN / GRID_COLUMNS    # 0.3125
CELL_HEIGHT_IN = GRID_HEIGHT_IN / GRID_ROWS     # 0.1666...

#: Bubble radius as a fraction of the smaller cell dimension.
BUBBLE_FRACTION = 0.40

# --- Registration marks --------------------------------------------------

#: Side length of the (square) arms of the top-left L-mark.
L_MARK_SIZE_IN = 0.3125
#: Side length of the three plain corner squares.
CORNER_SQUARE_SIZE_IN = 0.15625

# The three plain squares are centred on their grid corners, but the L-mark is
# not: it hangs inwards from the top-left corner, so the point corner finding
# recovers from it (the centre of its bounding box) sits this far inside the
# grid on both axes. `corner_finding` needs it to project the top-left grid
# corner back out, and expresses it as a fraction of the grid's size.
L_MARK_ORIGIN_OFFSET_IN = L_MARK_SIZE_IN / 2
L_MARK_OFFSET_FRACTION = (L_MARK_ORIGIN_OFFSET_IN / GRID_WIDTH_IN,
                          L_MARK_ORIGIN_OFFSET_IN / GRID_HEIGHT_IN)

# How close to a page edge the outermost *essential* ink gets: the outer edge
# of a corner square. Printers cannot print to the paper's edge - a laser
# typically loses the outer 0.16-0.20in and an inkjet 0.25in or more - so this
# is the sheet's real requirement of a printer, and nothing decorative should
# sit any closer to an edge than this. Anything that did would be clipped on
# printers that still manage to render the marks the sheet depends on.
MARK_EDGE_CLEARANCE_IN = CORNER_INSET_IN - (CORNER_SQUARE_SIZE_IN / 2)

# ``corner_finding`` works in a basis derived from the L-mark, where one
# horizontal unit is half the L-mark's long side and one vertical unit is the
# whole long side. These are the nominal distances from the L-mark to the
# opposite corner marks in that basis; they seed the corner search.
BASIS_WIDTH = GRID_WIDTH_IN / (L_MARK_SIZE_IN / 2)   # 48.0
BASIS_HEIGHT = GRID_HEIGHT_IN / L_MARK_SIZE_IN       # 32.0

# --- Identification blocks (identical rows on both pages) ----------------

#: Row that carries the "STUDENT ID" / "TEST ID n" captions.
ID_LABEL_ROW = 5
#: Row holding the ``0`` bubble; digit ``d`` sits at ``ID_FIRST_BUBBLE_ROW + d``.
ID_FIRST_BUBBLE_ROW = 6
#: Digits in the Student ID.
STUDENT_ID_DIGITS = 5
#: Digits in each Test ID.
TEST_ID_DIGITS = 4
BUBBLES_PER_DIGIT = 10

#: The Student ID block occupies the same columns and rows on *both* pages so
#: that a loose back page can still be matched to its front page.
STUDENT_ID_COLUMN = 6

# --- Latin level (front page only) ---------------------------------------

LATIN_LEVEL_COLUMN = 1
LATIN_LEVEL_FIRST_ROW = ID_FIRST_BUBBLE_ROW
LATIN_LEVELS: tp.Tuple[str, ...] = ("MS-1", "MS-2", "MS-3", "HS-1", "HS-2",
                                    "HS-3", "HS-Adv")
#: How many Latin level bubbles the sheet prints. Renaming them is free;
#: changing how many there are would move the grid, so it is fixed.
LATIN_LEVEL_COUNT = len(LATIN_LEVELS)

# --- Page code (front/back marker) ---------------------------------------

# Two bubbles, one of which is printed solid. The reader uses this to confirm
# that every front page in a batch is immediately followed by its back page.
PAGE_CODE_ROW = 2
PAGE_CODE_FIRST_COLUMN = 11
PAGE_CODE_OPTIONS = 2
PAGE_CODE_LABELS = ("FRONT", "BACK")

# --- Answer columns ------------------------------------------------------

QUESTIONS_PER_TEST = 80
#: Each test's questions are printed as two side-by-side blocks of 40.
ROWS_PER_SUBCOLUMN = 40
MCQ_FIRST_ROW = 19
OPTIONS = "ABCDE"

#: Leftmost bubble column of each 40-question block, per test, per page. The
#: question numbers are printed in the cell immediately to the left.
PAGE_SUBCOLUMNS: tp.Tuple[tp.Tuple[tp.Tuple[int, int], ...], ...] = (
    ((13, 19), ),          # page 1: Test 1
    ((1, 7), (13, 19)),    # page 2: Test 2, Test 3
)

#: Leftmost digit column of the Test ID block for each test, per page.
PAGE_TEST_ID_COLUMNS: tp.Tuple[tp.Tuple[int, ...], ...] = (
    (13, ),
    (1, 13),
)

PAGES_PER_SHEET = len(PAGE_SUBCOLUMNS)


def cell_to_inches(column: float, row: float) -> tp.Tuple[float, float]:
    """Convert a (possibly fractional) grid cell coordinate to PDF inches.

    The returned point is in PDF user space, i.e. measured from the
    bottom-left corner of the page.
    """
    x = CORNER_INSET_IN + (column * CELL_WIDTH_IN)
    y = PAGE_HEIGHT_IN - CORNER_INSET_IN - (row * CELL_HEIGHT_IN)
    return x, y


def bubble_radius_in() -> float:
    return min(CELL_WIDTH_IN, CELL_HEIGHT_IN) * BUBBLE_FRACTION


def question_number(subcolumn_index: int, row_index: int) -> int:
    """1-based question number for a row of a test's sub-column."""
    return (subcolumn_index * ROWS_PER_SUBCOLUMN) + row_index + 1


# --- wording -------------------------------------------------------------
# Everything above is geometry and is fixed: move it and the reader stops
# finding the bubbles. Everything below is only words printed on the page, so
# it can be changed per convention without touching the grid at all.


DEFAULT_TITLE = "CALIFORNIA JUNIOR CLASSICAL LEAGUE"

DEFAULT_DIRECTIONS: tp.Tuple[str, ...] = (
    "1.  Use a No. 2 pencil or a black or blue pen.",
    "2.  Fill each bubble completely and darkly, inside the circle.",
    "3.  To change an answer, erase it completely.",
    "4.  Print your name and school on the lines above, then bubble your Latin level.",
    "5.  Bubble your Student ID on both sides of this sheet.",
    "6.  Copy the 4-digit Test ID printed on each booklet for each test.",
    "7.  Do not fold or crease this sheet.",
)

DEFAULT_WRITE_IN_LABELS: tp.Tuple[str, ...] = ("First Name", "Last Name",
                                               "School")

#: Room on the page, in characters, before a line starts colliding with the
#: answer columns at this font size.
MAX_DIRECTION_LENGTH = 86
MAX_TITLE_LENGTH = 46


class SheetTextError(ValueError):
    """The wording supplied for a sheet cannot be printed as given."""


@dataclasses.dataclass(frozen=True)
class SheetText:
    """The words printed on a sheet.

    Kept apart from the geometry so it can travel with a request: the grading
    endpoint needs the Latin level names to validate a key file and to write
    them into the results, and the sheet generator needs all of it to print.
    """

    title: str = DEFAULT_TITLE
    directions: tp.Tuple[str, ...] = DEFAULT_DIRECTIONS
    latin_levels: tp.Tuple[str, ...] = LATIN_LEVELS
    write_in_labels: tp.Tuple[str, ...] = DEFAULT_WRITE_IN_LABELS

    def __post_init__(self):
        if not self.title.strip():
            raise SheetTextError("The sheet needs a title.")
        if len(self.title) > MAX_TITLE_LENGTH:
            raise SheetTextError(
                f"The title is {len(self.title)} characters; it has to fit "
                f"across the top of the page, so keep it to "
                f"{MAX_TITLE_LENGTH}.")
        if len(self.latin_levels) != LATIN_LEVEL_COUNT:
            raise SheetTextError(
                f"The sheet prints {LATIN_LEVEL_COUNT} Latin level bubbles, "
                f"so it needs exactly {LATIN_LEVEL_COUNT} names; "
                f"{len(self.latin_levels)} were given. The names can be "
                "anything, but how many there are is fixed by the grid.")
        cleaned = [level.strip() for level in self.latin_levels]
        if any(not level for level in cleaned):
            raise SheetTextError("A Latin level name is blank.")
        if len({level.upper() for level in cleaned}) != len(cleaned):
            raise SheetTextError(
                "Two Latin levels have the same name, so a key could not say "
                "which one it excludes.")
        for level in cleaned:
            if len(level) > 10:
                raise SheetTextError(
                    f"Latin level '{level}' is too long for the space beside "
                    "its bubble; keep names to 10 characters.")
        if len(self.write_in_labels) != len(DEFAULT_WRITE_IN_LABELS):
            raise SheetTextError(
                f"The sheet has {len(DEFAULT_WRITE_IN_LABELS)} write-in "
                "lines, so it needs that many labels.")
        for line in self.directions:
            if len(line) > MAX_DIRECTION_LENGTH:
                raise SheetTextError(
                    f"This direction is {len(line)} characters and would run "
                    f"into the answer columns:{chr(10)}  {line}{chr(10)}"
                    f"Keep each line to {MAX_DIRECTION_LENGTH}, or split it "
                    "across two.")
        if len(self.directions) > 12:
            raise SheetTextError(
                f"{len(self.directions)} lines of directions will not fit "
                "above the emblem; keep it to 12.")

    @classmethod
    def from_dict(cls, data: tp.Optional[tp.Mapping[str, tp.Any]]
                  ) -> "SheetText":
        """Build from the JSON the website sends. Missing keys keep their
        defaults."""
        if not data:
            return cls()

        def strings(key: str, fallback: tp.Tuple[str, ...]
                    ) -> tp.Tuple[str, ...]:
            value = data.get(key)
            if value is None:
                return fallback
            if isinstance(value, str):
                value = [line for line in value.splitlines()]
            if not isinstance(value, (list, tuple)):
                raise SheetTextError(f"'{key}' should be a list of lines.")
            return tuple(str(line).rstrip() for line in value)

        return cls(title=str(data.get("title") or DEFAULT_TITLE).strip(),
                   directions=strings("directions", DEFAULT_DIRECTIONS),
                   latin_levels=tuple(
                       str(level).strip()
                       for level in (data.get("latin_levels")
                                     or LATIN_LEVELS)),
                   write_in_labels=strings("write_in_labels",
                                           DEFAULT_WRITE_IN_LABELS))

    def to_dict(self) -> tp.Dict[str, tp.Any]:
        return {
            "title": self.title,
            "directions": list(self.directions),
            "latin_levels": list(self.latin_levels),
            "write_in_labels": list(self.write_in_labels),
        }
