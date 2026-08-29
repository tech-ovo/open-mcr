"""Regression tests for the legacy sheets, against committed expected output.

Each subdirectory holds an `input/` folder of scans, an optional `args.txt` of
extra command-line arguments, and an `output/` folder of the CSVs the reader
is expected to produce.

Rows are compared as a multiset rather than in order. Sheets that sort equal -
the legacy variants have no name columns to break ties, so most do - come out
in whatever order the filesystem listed the scans, which differs between
machines. The contents still have to match exactly.
"""

import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

current_dir = Path(__file__).parent

#: Cases whose committed fixtures predate the current software and cannot be
#: reproduced. `rearrangement`'s arrangement file only maps three questions,
#: but the 75-question variant it runs against needs all seventy-five, so
#: `data_exporting.validate_order_map` rejects it. The unmodified upstream
#: code fails on it in exactly the same way; the fixture, not the reader,
#: is what needs rebuilding.
KNOWN_BROKEN = {
    "rearrangement":
    "arrangement.csv only maps 3 of the variant's 75 questions"
}


def _case_param(path: Path):
    reason = KNOWN_BROKEN.get(path.name)
    if reason:
        return pytest.param(path, marks=pytest.mark.xfail(reason=reason,
                                                          strict=True))
    return pytest.param(path)


CASES = [
    _case_param(path) for path in sorted(current_dir.iterdir())
    if path.is_dir() and path.name != "__pycache__"
]


def read_rows(path: Path):
    with open(path, newline="") as handle:
        return list(csv.reader(handle))


@pytest.mark.parametrize("case", CASES)
def test_e2e(case: Path, tmp_path: Path):
    input_path = case / "input"
    args_file = case / "args.txt"
    extra = args_file.read_text().split() if args_file.exists() else []
    extra = [
        arg.replace("$$INPUT_DIR$$", f"{input_path}/") for arg in extra
    ]

    actual_dir = tmp_path / "output"
    actual_dir.mkdir()

    subprocess.check_call([
        sys.executable or "python", "-m", "src.main",
        str(input_path),
        str(actual_dir), "--disable-timestamp", "--sort"
    ] + extra,
                          cwd=str(current_dir.parent.parent))

    expected_dir = case / "output"
    expected_files = {path.name for path in expected_dir.iterdir()
                      if path.is_file()}
    actual_files = {path.name for path in actual_dir.iterdir()
                    if path.is_file()}
    assert actual_files == expected_files

    for name in sorted(expected_files):
        expected = read_rows(expected_dir / name)
        actual = read_rows(actual_dir / name)
        assert actual[0] == expected[0], f"{name}: header differs"
        assert Counter(map(tuple, actual[1:])) == Counter(
            map(tuple, expected[1:])), f"{name}: rows differ"
