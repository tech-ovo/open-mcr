"""The answer keys, read from a CSV the convention staff maintain by hand.

The file runs *down* the page: the first column names the field, and each
further column is one test. With eighty questions that is far easier to edit
than eighty columns across.

===========  ===================  =========================  ==============
Name         Latin Literature     Reading Comprehension 1    Mythology
Test ID      1001                 1002                       1003
Excluded                          HS-Adv                     MS-1, MS-2
1            A                    D                          B
2            C                    A                          B
...
80           E                    B                          C
===========  ===================  =========================  ==============

``Name`` is free text, for the reports. ``Test ID`` is the 4-digit number
students bubble, and must be unique. ``Excluded`` lists Latin levels that may
not sit that test, comma-separated; blank means everyone may.

An answer cell says what a *correct sheet* looks like:

* ``B`` - the student must have filled B and nothing else.
* ``ABD`` - the student must have filled all three of A, B and D.
* ``A|BD`` - either of those is accepted: A alone, or B and D together.
* blank - the question is not scored at all, which is how to retire a faulty
  question without renumbering anything.
"""

import csv
import pathlib
import typing as tp

from . import sheet_layout as layout

NAME_ROW = "Name"
TEST_ID_ROW = "Test ID"
EXCLUDED_ROW = "Excluded"
REQUIRED_ROWS = (NAME_ROW, TEST_ID_ROW, EXCLUDED_ROW)

#: Separates alternative acceptable answers within one cell.
ALTERNATIVE_SEPARATOR = "|"

#: Written next to a score when the student's Test ID matches no key.
TEST_NOT_FOUND = "TEST NOT FOUND"
#: Written next to a score when the student's Latin level may not sit the test.
TEST_NOT_ALLOWED = "TEST NOT ALLOWED"


class AnswerKeyError(ValueError):
    """A problem with the key file that must be fixed before grading."""


class Key(tp.NamedTuple):
    """One test's key."""

    name: str
    test_id: str
    excluded_levels: tp.FrozenSet[str]
    answers: tp.Tuple[tp.Tuple[tp.FrozenSet[str], ...], ...]
    """Per question, the set of acceptable *complete* answers. An empty tuple
    means the question is not scored."""

    def allows(self, latin_level: str) -> bool:
        return latin_level.strip().upper() not in self.excluded_levels

    def accepted_text(self, index: int) -> str:
        """How the key reads, for the reports: ``ABD`` or ``A|BD``."""
        if index >= len(self.answers) or not self.answers[index]:
            return ""
        return ALTERNATIVE_SEPARATOR.join(
            "".join(sorted(option)) for option in self.answers[index])

    def score(self, marked: tp.Sequence[tp.Set[str]]
              ) -> tp.Tuple[int, int, tp.List[str]]:
        """Score one student's answers.

        Returns ``(points, out_of, per_question)``, where each entry of
        ``per_question`` is "1", "0", or "" for a question that is not scored.
        A question is correct when the set of bubbles the student filled is
        exactly one of the accepted answers.
        """
        points = 0
        out_of = 0
        detail: tp.List[str] = []
        for index, accepted in enumerate(self.answers):
            if not accepted:
                detail.append("")
                continue
            out_of += 1
            filled = frozenset(marked[index]) if index < len(marked) \
                else frozenset()
            correct = filled in accepted
            points += int(correct)
            detail.append("1" if correct else "0")
        return points, out_of, detail


def _parse_answer_cell(cell: str, where: str
                       ) -> tp.Tuple[tp.FrozenSet[str], ...]:
    text = "".join(cell.split())
    if not text:
        return ()
    accepted: tp.List[tp.FrozenSet[str]] = []
    for alternative in text.split(ALTERNATIVE_SEPARATOR):
        letters = alternative.upper()
        if not letters:
            raise AnswerKeyError(
                f"{where}: '{cell.strip()}' has an empty alternative. Write "
                f"them as 'A{ALTERNATIVE_SEPARATOR}BD', with no stray "
                f"'{ALTERNATIVE_SEPARATOR}'.")
        for letter in letters:
            if letter not in layout.OPTIONS:
                raise AnswerKeyError(
                    f"{where}: '{cell.strip()}' contains '{letter}', which is "
                    f"not one of {'/'.join(layout.OPTIONS)}.")
        if len(set(letters)) != len(letters):
            raise AnswerKeyError(
                f"{where}: '{alternative}' repeats a letter.")
        accepted.append(frozenset(letters))
    if len(set(accepted)) != len(accepted):
        raise AnswerKeyError(
            f"{where}: '{cell.strip()}' lists the same answer twice.")
    return tuple(accepted)


def _parse_excluded(cell: str, where: str,
                    levels_in_use: tp.Sequence[str]) -> tp.FrozenSet[str]:
    levels = {
        part.strip().upper()
        for part in cell.replace(";", ",").split(",") if part.strip()
    }
    known = {level.upper() for level in levels_in_use}
    unknown = levels - known
    if unknown:
        raise AnswerKeyError(
            f"{where}: '{', '.join(sorted(unknown))}' is not a Latin level. "
            f"Use one of {', '.join(levels_in_use)}.")
    return frozenset(levels)


def load(path: pathlib.Path,
         questions: int = layout.QUESTIONS_PER_TEST,
         latin_levels: tp.Optional[tp.Sequence[str]] = None
         ) -> tp.Dict[str, Key]:
    """Read and validate the key file.

    Raises AnswerKeyError on anything that would make grading meaningless.
    """
    levels_in_use = tuple(latin_levels or layout.LATIN_LEVELS)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise AnswerKeyError(f"Could not read the key file: {error}")

    table = [row for row in csv.reader(text.splitlines())
             if any(cell.strip() for cell in row)]
    if not table:
        raise AnswerKeyError(f"The key file '{path.name}' is empty.")

    rows: tp.Dict[str, tp.List[str]] = {}
    for line_number, row in enumerate(table, start=1):
        label = row[0].strip()
        if label in rows:
            raise AnswerKeyError(
                f"Key file line {line_number}: there are two '{label}' rows. "
                "Each row label must appear once.")
        rows[label] = [cell.strip() for cell in row[1:]]

    missing = [name for name in REQUIRED_ROWS if name not in rows]
    if missing:
        raise AnswerKeyError(
            f"The key file has no "
            f"{', '.join(repr(name) for name in missing)} row. Its first "
            f"column must read: {', '.join(REQUIRED_ROWS)}, 1, 2, ... "
            f"{questions}.")
    for number in range(1, questions + 1):
        if str(number) not in rows:
            raise AnswerKeyError(
                f"The key file has no row labelled '{number}'. It needs one "
                f"row per question, labelled 1 through {questions}.")

    width = max(len(rows[name]) for name in rows)
    if width == 0:
        raise AnswerKeyError(
            f"The key file '{path.name}' has row labels but no tests. Put one "
            "test in each column after the first.")

    def cell(label: str, column: int) -> str:
        values = rows[label]
        return values[column] if column < len(values) else ""

    keys: tp.Dict[str, Key] = {}
    for column in range(width):
        test_id = cell(TEST_ID_ROW, column)
        name = cell(NAME_ROW, column)
        if not test_id and not name:
            continue
        where = f"Key file column {column + 2}"
        if not test_id:
            raise AnswerKeyError(
                f"{where} ('{name}'): the Test ID is blank.")
        if not test_id.isdigit():
            raise AnswerKeyError(
                f"{where}: Test ID '{test_id}' is not a number. Students "
                "bubble digits, so a key's Test ID must be digits too.")
        if len(test_id) > layout.TEST_ID_DIGITS:
            raise AnswerKeyError(
                f"{where}: Test ID '{test_id}' has {len(test_id)} digits, but "
                f"the sheet has room for {layout.TEST_ID_DIGITS}.")
        test_id = test_id.zfill(layout.TEST_ID_DIGITS)
        if test_id in keys:
            raise AnswerKeyError(
                f"{where}: Test ID {test_id} is used twice in the key file "
                f"('{keys[test_id].name}' and '{name}'). Each test needs its "
                "own ID or answers cannot be matched to students.")

        keys[test_id] = Key(
            name=name or f"Test {test_id}",
            test_id=test_id,
            excluded_levels=_parse_excluded(cell(EXCLUDED_ROW, column),
                                            where, levels_in_use),
            answers=tuple(
                _parse_answer_cell(cell(str(number), column),
                                   f"{where} question {number}")
                for number in range(1, questions + 1)),
        )

    if not keys:
        raise AnswerKeyError(
            f"The key file '{path.name}' has row labels but no tests.")
    return keys


def write_template(path: pathlib.Path,
                   questions: int = layout.QUESTIONS_PER_TEST) -> pathlib.Path:
    """Write a blank key file for staff to fill in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    examples = [
        (NAME_ROW, ["Latin Literature", "Reading Comprehension 1",
                    "Mythology"]),
        (TEST_ID_ROW, ["1001", "1002", "1003"]),
        (EXCLUDED_ROW, ["", "HS-Adv", "MS-1, MS-2"]),
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for label, values in examples:
            writer.writerow([label] + values)
        for number in range(1, questions + 1):
            writer.writerow([str(number), "", "", ""])
    return path
