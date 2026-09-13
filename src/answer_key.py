"""The answer keys, read from a CSV the convention staff maintain by hand.

The file runs *down* the page: the first column names the field, and each
further column is one test. With eighty questions that is far easier to edit
than eighty columns across.

===========  ===================  =========================  ==============
Name         Latin Literature     Reading Comp (lower)       Reading Comp (upper)
Test ID      1001                 1002                       1002
Allowed                           MS-1, MS-2, MS-3           HS-1, HS-2, HS-3
1            A                    D                          B
2            C                    A                          B
...
80           E                    B                          C
===========  ===================  =========================  ==============

``Name`` is free text, for the reports. ``Test ID`` is the 4-digit number
students take the test with. ``Allowed`` lists the Latin levels that may take
it, comma-separated; blank means every level may.

Two columns may share a Test ID when the levels that may take them do not
overlap, as ``1002`` does above. That is how one printed test can be marked
against different keys for different levels. Which key a student is scored
against is then decided by the Latin level they bubbled.

An answer cell says what a *correct sheet* looks like:

* ``B`` - the student must have filled B and nothing else.
* ``ABD`` - the student must have filled all three of A, B and D.
* ``A|BD`` - either of those is accepted: A alone, or B and D together.
* ``X`` - the question is not scored at all, which is how to retire a faulty
  question without renumbering anything. A blank cell means the same, but ``X``
  says it on purpose, where a blank could just as easily be unfinished work.
"""

import csv
import pathlib
import typing as tp

from . import sheet_layout as layout

NAME_ROW = "Name"
TEST_ID_ROW = "Test ID"
ALLOWED_ROW = "Allowed"
REQUIRED_ROWS = (NAME_ROW, TEST_ID_ROW, ALLOWED_ROW)

#: The row this replaced. Meeting one means reading a file written for the old
#: rules, where the levels listed were the ones that could *not* take the test.
#: Loading it as though it said the opposite would silently score the wrong
#: students, so it is refused instead.
RETIRED_EXCLUDED_ROW = "Excluded"

#: Separates alternative acceptable answers within one cell.
ALTERNATIVE_SEPARATOR = "|"

#: An answer cell holding this retires the question: nobody is scored on it.
#: A blank cell does the same, but this says so deliberately. It is safe to
#: reserve because the letter is not one of the bubbles on the sheet.
VOID_ANSWER = "X"

#: Written next to a score when the student's Test ID matches no key.
TEST_NOT_FOUND = "TEST NOT FOUND"
#: Written next to a score when no key for that Test ID takes the student's
#: Latin level.
TEST_NOT_ALLOWED = "TEST NOT ALLOWED"
#: Written next to a score when the Test ID has several keys, one per level,
#: and the student's Latin level could not be read - so there is no way to say
#: which of them applies. Filling the level in on the Missing sheet and
#: re-scoring resolves it.
LEVEL_NEEDED = "LATIN LEVEL NEEDED"


class AnswerKeyError(ValueError):
    """A problem with the key file that must be fixed before grading."""


class Key(tp.NamedTuple):
    """One test's key."""

    name: str
    test_id: str
    allowed_levels: tp.FrozenSet[str]
    """The Latin levels that may take this test, upper-cased. Never empty: a
    blank cell in the file is expanded to every level in use, so that overlap
    between two keys sharing a Test ID is a plain set intersection."""

    answers: tp.Tuple[tp.Tuple[tp.FrozenSet[str], ...], ...]
    """Per question, the set of acceptable *complete* answers. An empty tuple
    means the question is not scored."""

    def allows(self, latin_level: str) -> bool:
        return latin_level.strip().upper() in self.allowed_levels

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
    if not text or text.upper() == VOID_ANSWER:
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


def _parse_allowed(cell: str, where: str,
                   levels_in_use: tp.Sequence[str]) -> tp.FrozenSet[str]:
    known = {level.upper() for level in levels_in_use}
    levels = {
        part.strip().upper()
        for part in cell.replace(";", ",").split(",") if part.strip()
    }
    if not levels:
        return frozenset(known)
    unknown = levels - known
    if unknown:
        raise AnswerKeyError(
            f"{where}: '{', '.join(sorted(unknown))}' is not a Latin level. "
            f"Use one of {', '.join(levels_in_use)}.")
    return frozenset(levels)


class KeySet:
    """Every key in the file, looked up by Test ID and Latin level.

    A Test ID usually has exactly one key. It may have several when they take
    disjoint sets of Latin levels, which is how the same printed test can be
    marked differently for, say, the middle school and high school entries.
    """

    def __init__(self, keys: tp.Sequence[Key]):
        self._by_id: tp.Dict[str, tp.List[Key]] = {}
        for key in keys:
            self._by_id.setdefault(key.test_id, []).append(key)

    def variants(self, test_id: str) -> tp.Tuple[Key, ...]:
        """Every key sharing this Test ID, in file order."""
        return tuple(self._by_id.get(test_id, ()))

    def lookup(self, test_id: str,
               latin_level: str) -> tp.Optional[Key]:
        """The key that applies, or None if the level may not take the test.

        With one key for the ID and no readable level, that key is returned:
        there is nothing to choose between.
        """
        variants = self.variants(test_id)
        if not variants:
            return None
        if not latin_level.strip():
            return variants[0] if len(variants) == 1 else None
        for key in variants:
            if key.allows(latin_level):
                return key
        return None

    def is_ambiguous(self, test_id: str, latin_level: str) -> bool:
        """True when only the missing Latin level stands in the way."""
        return not latin_level.strip() and len(self.variants(test_id)) > 1

    def __getitem__(self, test_id: str) -> Key:
        """The only key for this Test ID.

        Raises if there are several, because choosing between them needs a
        Latin level - use ``lookup`` there.
        """
        variants = self.variants(test_id)
        if not variants:
            raise KeyError(test_id)
        if len(variants) > 1:
            raise KeyError(
                f"Test ID {test_id} has {len(variants)} keys, one per Latin "
                "level; look it up with a level rather than on its own.")
        return variants[0]

    def __contains__(self, test_id: object) -> bool:
        return test_id in self._by_id

    def __len__(self) -> int:
        return sum(len(group) for group in self._by_id.values())

    def __iter__(self) -> tp.Iterator[Key]:
        for test_id in sorted(self._by_id):
            yield from self._by_id[test_id]


def load(path: pathlib.Path,
         questions: int = layout.QUESTIONS_PER_TEST,
         latin_levels: tp.Optional[tp.Sequence[str]] = None
         ) -> KeySet:
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

    if RETIRED_EXCLUDED_ROW in rows and ALLOWED_ROW not in rows:
        raise AnswerKeyError(
            f"The key file has an '{RETIRED_EXCLUDED_ROW}' row. That row was "
            f"replaced by '{ALLOWED_ROW}', which lists the levels that *may* "
            "take each test rather than the ones that may not. Rewrite the "
            "row with the opposite meaning and rename it, so that a file is "
            "never read as saying the reverse of what it says.")

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

    keys: tp.List[Key] = []
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
        allowed = _parse_allowed(cell(ALLOWED_ROW, column), where,
                                 levels_in_use)

        # Sharing a Test ID is allowed, but only while the keys cannot both
        # apply to one student.
        for earlier in keys:
            if earlier.test_id != test_id:
                continue
            clash = earlier.allowed_levels & allowed
            if clash:
                raise AnswerKeyError(
                    f"{where}: Test ID {test_id} is used twice in the key file "
                    f"('{earlier.name}' and '{name or test_id}'), and both "
                    f"allow {', '.join(sorted(clash))}. Two tests may share an "
                    "ID only when the levels allowed to take them do not "
                    "overlap, so that every student matches exactly one.")

        keys.append(Key(
            name=name or f"Test {test_id}",
            test_id=test_id,
            allowed_levels=allowed,
            answers=tuple(
                _parse_answer_cell(cell(str(number), column),
                                   f"{where} question {number}")
                for number in range(1, questions + 1)),
        ))

    if not keys:
        raise AnswerKeyError(
            f"The key file '{path.name}' has row labels but no tests.")
    return KeySet(keys)


def write_template(path: pathlib.Path,
                   questions: int = layout.QUESTIONS_PER_TEST) -> pathlib.Path:
    """Write a blank key file for staff to fill in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    examples = [
        (NAME_ROW, ["Latin Literature", "Reading Comprehension 1",
                    "Mythology"]),
        (TEST_ID_ROW, ["1001", "1002", "1003"]),
        (ALLOWED_ROW, ["", "MS-1, MS-2, MS-3", ""]),
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for label, values in examples:
            writer.writerow([label] + values)
        for number in range(1, questions + 1):
            writer.writerow([str(number), "", "", ""])
    return path
