"""Command-line entry point.

Run with ``python -m src.main`` (not ``python src/main.py`` - the package uses
relative imports). Exit status:

===  =========================================================================
0    finished; any marks needing review are listed in the review sheets
1    a breaking problem - nothing was written, and the message says why
===  =========================================================================
"""

import argparse
import pathlib
import sys

from . import answer_key
from . import console as console_module
from . import pipeline
from . import review as review_module
from . import sheet_layout as layout
from . import thresholds as th
from .file_handling import parse_path_arg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="OpenMCR, CAJCL State Convention edition.\n"
                    "Reads scanned answer sheets and writes the results.",
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("input_folder", nargs="?", type=parse_path_arg,
                        help="Folder holding the scans. With --batch, the\n"
                             "scans are read from '<folder>/Batch N'.")
    parser.add_argument("output_folder", nargs="?", type=parse_path_arg,
                        help="Folder to write the results to. With --batch,\n"
                             "they are written to '<folder>/Batch N'.")
    parser.add_argument("--batch", metavar="N",
                        help="Batch number. Scans are read from\n"
                             "'<input>/Batch N' and results written to\n"
                             "'<output>/Batch N', and the review sheets are\n"
                             "named after it so several batches can be told\n"
                             "apart once downloaded.")
    parser.add_argument("--key", type=parse_path_arg, metavar="FILE.csv",
                        help="The answer key CSV: field names down the first\n"
                             "column - Name, Test ID, Excluded (or Allowed),\n"
                             "then 1 to 80 - and one column per test.\n"
                             "Without it the sheets are read but not scored.")
    parser.add_argument("--overrides", type=parse_path_arg, nargs="+",
                        default=(), metavar="FILE.csv",
                        help="Corrected review sheets (Unclear.csv and/or\n"
                             "Missing.csv) to fold in before scoring.\n"
                             "A folder may be given instead, and the review\n"
                             "sheets in it are found automatically.")
    parser.add_argument("--regrade", type=parse_path_arg, metavar="Results.csv",
                        help="Re-score an existing Results.csv without\n"
                             "touching the scans. Use after correcting the\n"
                             "key or the review sheets.")
    parser.add_argument("--threshold", metavar="SPEC",
                        help="Bubble cutoffs, instead of calibrating from the\n"
                             "scans. Four numbers - answer cutoff, answer\n"
                             "review band, metadata cutoff, metadata review\n"
                             "band - or just the two cutoffs, or one number\n"
                             "for everything. Calibration.txt prints the\n"
                             "line to copy.")
    parser.add_argument("--annotate", action="store_true",
                        help="Also write a marked-up copy of every scan.")
    parser.add_argument("--annotate-students", metavar="WHO",
                        help="Narrow --annotate to the papers worth looking\n"
                             "at: 'unknown' for the ones whose Student ID\n"
                             "could not be read in full, or a list of\n"
                             "Student IDs. Defaults to all of them.")
    parser.add_argument("--annotate-pages", metavar="N[,N...]",
                        help="Narrow --annotate to these page numbers,\n"
                             "counted within their own file.")
    parser.add_argument("--annotate-grid", action="store_true",
                        help="Draw the cell grid the page was read\n"
                             "against, on the marked-up copies. The\n"
                             "answer to 'where did it think the bubbles\n"
                             "were'.")
    parser.add_argument("--sides", metavar="SIDE[,SIDE]", default="",
                        help="Which sides of the sheet this scan contains:\n"
                             "'front', 'back', or both. A one-sided scan is\n"
                             "read a page at a time instead of in pairs, and\n"
                             "only the tests printed on that side are\n"
                             "graded. Defaults to both.")
    parser.add_argument("--skip-blanks", action="store_true",
                        help="Ignore pages with nothing on them, which is\n"
                             "what a duplex scanner produces for the empty\n"
                             "reverse of a one-sided original. Blank pages\n"
                             "are listed in Pages.csv either way.")
    parser.add_argument("--ignore-done", action="store_true",
                        help="Accept a corrected review sheet whose Done\n"
                             "column was never ticked. Only for the case\n"
                             "where every row really was looked at.")
    parser.add_argument("--tests", metavar="N[,N...]",
                        help="Grade only these tests, numbered from 1 across\n"
                             "the sheet: '--tests 2' grades the second test\n"
                             "and ignores the others. Defaults to all of\n"
                             "them.")
    parser.add_argument("--key-template", type=parse_path_arg,
                        metavar="FILE.csv",
                        help="Write a blank answer key CSV to this path and\n"
                             "exit.")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Re-raise unexpected errors with a traceback.")
    return parser


def _parse_sides(raw: str) -> tuple:
    """Read --sides into the page positions a scan contains."""
    text = (raw or "").strip().lower()
    if not text:
        return (0, 1)
    wanted = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if part in ("0", "front"):
            wanted.add(0)
        elif part in ("1", "back"):
            wanted.add(1)
        elif part:
            raise pipeline.BreakingError(
                f"'{part}' is not a side of the sheet. Use 'front', 'back', "
                "or both.")
    if not wanted:
        raise pipeline.BreakingError(
            "At least one side of the sheet has to be included in the scan.")
    return tuple(sorted(wanted))


def _parse_numbers(raw):
    """Read a "1,3,5" list into page numbers."""
    text = (raw or "").strip()
    if not text:
        return None
    wanted = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise pipeline.BreakingError(f"'{part}' is not a page number.")
        wanted.append(int(part))
    return tuple(wanted) or None


def _parse_students(raw):
    """Read --annotate-students into a filter for the marked-up copies."""
    from . import annotation

    text = (raw or "").strip()
    if not text:
        return None
    if text.lower() in ("unknown", "unreadable"):
        return annotation.UNKNOWN_IDS
    listed = tuple(part.strip() for part in text.replace(";", ",").split(",")
                   if part.strip())
    return listed or None


def _make_output_encoding_safe():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _regrade(args, console: console_module.Console) -> int:
    """Re-score without image processing."""
    rows = pipeline.read_results(args.regrade)
    console.line(f"Re-scoring {console_module.plural(len(rows), 'row')} from "
                 f"'{args.regrade.name}'.")

    if args.overrides:
        overrides = review_module.load(list(args.overrides),
                                       require_done=not args.ignore_done)
        applied = pipeline.apply_overrides(rows, overrides)
        console.line(
            f"Applied "
            f"{console_module.plural(applied.changed, 'correction')} from "
            f"{console_module.quoted_list([p.name for p in args.overrides])}.",
            indent=1)
        _report_override_problems(applied.problems, console)

    keys = answer_key.load(args.key) if args.key else None
    pipeline.score_rows(rows, keys)

    destination = args.output_folder or args.regrade.parent
    destination = pipeline.resolve_batch_folder(destination, args.batch) \
        if args.output_folder else destination
    destination.mkdir(parents=True, exist_ok=True)
    results = pipeline.write_results(destination / pipeline.RESULTS_FILENAME,
                                     rows, len(rows[0].marked) if rows else 80)
    pipeline.write_question_stats(destination / pipeline.STATS_FILENAME, rows,
                                  keys, len(rows[0].marked) if rows else 80)
    console.line(f"All exams re-scored and saved to {destination}.")
    if keys is None:
        console.line("No exam keys were provided, so no scoring was "
                     "performed.", indent=1)
    console.line(f"{results.name} and {pipeline.STATS_FILENAME} updated.",
                 indent=1)
    return 0


def _report_override_problems(problems, console: console_module.Console):
    """Corrections that named no test, or more than one.

    Worth saying out loud: the row was typed by somebody who meant it, and
    quietly dropping it would lose a decision a person had already made.
    """
    if not problems:
        return
    console.line(
        f"{console_module.plural(len(problems), 'correction')} could not be "
        "matched to a test and "
        f"{console_module.verb(len(problems), 'was', 'were')} not applied:")
    for problem in problems[:10]:
        console.line(problem, indent=1)
    if len(problems) > 10:
        console.line(f"... and {len(problems) - 10} more", indent=1)


def _report_pages(result: pipeline.RunResult,
                  console: console_module.Console, output: pathlib.Path):
    """What became of the pages that were not graded."""
    from . import batching

    set_aside = [report for report in result.reports
                 if report.status in batching.SET_ASIDE]
    if not set_aside:
        return
    console.line(
        f"{console_module.plural(len(set_aside), 'page')} "
        f"{console_module.verb(len(set_aside), 'was', 'were')} not graded. "
        f"Every page is listed in '{pipeline.PAGES_FILENAME}'.")
    for report in set_aside[:10]:
        console.line(f"{report.label}: {report.status.lower()} - "
                     f"{report.note}", indent=1)
    if len(set_aside) > 10:
        console.line(f"... and {len(set_aside) - 10} more", indent=1)


def _report(result: pipeline.RunResult, options: pipeline.RunOptions,
            console: console_module.Console, output: pathlib.Path,
            scored: bool):
    if result.supplied_thresholds:
        spec = result.thresholds[0][1].to_spec() if result.thresholds else ""
        console.line(f"Thresholds supplied: {spec}.")
    else:
        console.line("Automatic thresholding used.")
        console.line(
            f"Calibrated thresholds saved to "
            f"{output / th.CALIBRATION_FILENAME}.", indent=1)

    console.line(f"All exams processed and saved to {output}.")
    _report_pages(result, console, output)
    _report_override_problems(result.override_problems, console)
    if result.annotated:
        console.line(
            f"{console_module.plural(len(result.annotated), 'marked-up PDF')} "
            f"saved to '{pipeline.ANNOTATED_DIRNAME}'.", indent=1)
    if not scored:
        console.line("No exam keys were provided, so no scoring was "
                     "performed.", indent=1)

    pending = len(result.unclear) + len(result.missing)
    if pending:
        prefix = (f"Batch {options.batch}{review_module.BATCH_SEPARATOR}" if options.batch else "")
        names = []
        if result.unclear:
            names.append(f"'{prefix}{review_module.UNCLEAR_BASENAME}.csv'")
        if result.missing:
            names.append(f"'{prefix}{review_module.MISSING_BASENAME}.csv'")
        console.line(
            f"{console_module.plural(pending, 'mark')} "
            f"{console_module.verb(pending, 'needs', 'need')} manual review. "
            f"See {' and '.join(names)}.")
        console.line(
            "Regrade with overrides via: "
            f"{_regrade_command(result, options, output, prefix)}",
            indent=1)


def _regrade_command(result: pipeline.RunResult,
                     options: pipeline.RunOptions, output: pathlib.Path,
                     prefix: str) -> str:
    parts = ["python -m src.main", f'--regrade "{result.results_path}"']
    # Point --overrides at the folder rather than the two files: the sheets
    # are named with an em dash, which a legacy console cannot print, so a
    # copied command line would otherwise carry a broken path.
    parts.append(f'--overrides "{output}"')
    if options.key_file:
        parts.append(f'--key "{options.key_file}"')
    return " ".join(parts)


def main(argv: list) -> int:
    _make_output_encoding_safe()
    parser = build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        return 1
    args = parser.parse_args(argv)
    console = console_module.Console()

    try:
        if args.key_template:
            path = answer_key.write_template(args.key_template)
            console.line(f"Blank answer key written to {path}.")
            console.line("Fill in one row per test, then pass it with --key.",
                         indent=1)
            return 0

        if args.regrade:
            return _regrade(args, console)

        if not args.input_folder or not args.output_folder:
            parser.print_help(sys.stderr)
            return 1

        options = pipeline.RunOptions(input_folder=args.input_folder,
                                      output_folder=args.output_folder,
                                      batch=args.batch,
                                      key_file=args.key,
                                      override_files=tuple(args.overrides),
                                      threshold_spec=args.threshold,
                                      annotate=args.annotate,
                                      annotate_students=_parse_students(
                                          args.annotate_students),
                                      annotate_pages=_parse_numbers(
                                          args.annotate_pages),
                                      annotate_grid=args.annotate_grid,
                                      tests=pipeline.parse_tests(
                                          args.tests,
                                          layout.TESTS_PER_SHEET),
                                      sides=_parse_sides(args.sides),
                                      skip_blanks=args.skip_blanks,
                                      require_done=not args.ignore_done,
                                      debug=args.debug)
        result = pipeline.run(options, console)
        output = pipeline.resolve_batch_folder(args.output_folder, args.batch)
        _report(result, options, console, output, scored=args.key is not None)
        return 0

    except (pipeline.BreakingError, answer_key.AnswerKeyError,
            review_module.OverrideError, review_module.NotFinishedError,
            th.ThresholdError) as error:
        console.error("")
        console.error(f"Stopped: {error}")
        console.error("")
        console.error("Nothing was written. Fix the problem above and run "
                      "again.")
        if args.debug:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
