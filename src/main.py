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
                        help="The answer key CSV: one row per test, with\n"
                             "columns Name, Test ID, Excluded, then 1 to 80.\n"
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
    parser.add_argument("--key-template", type=parse_path_arg,
                        metavar="FILE.csv",
                        help="Write a blank answer key CSV to this path and\n"
                             "exit.")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Re-raise unexpected errors with a traceback.")
    return parser


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
        overrides = review_module.load(list(args.overrides))
        applied = pipeline.apply_overrides(rows, overrides)
        console.line(
            f"Applied {console_module.plural(applied, 'correction')} from "
            f"{console_module.quoted_list([p.name for p in args.overrides])}.",
            indent=1)

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
