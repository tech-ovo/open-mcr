"""Command-line entry point.

Run with ``python -m src.main`` (not ``python src/main.py`` - the package uses
relative imports). Exit status:

===  =========================================================================
0    everything read and, if keys were supplied, scored
1    bad arguments, or an unreadable input folder
2    finished, but some marks were too unclear to grade - see the review report
3    the batch was not a clean sequence of front/back pairs; nothing was graded
===  =========================================================================
"""

import argparse
import sys
from datetime import datetime

from . import batching
from . import file_handling
from . import grid_info as grid_i
from . import mark_quality
from .file_handling import parse_path_arg
from .process_input import process_input

VARIANTS = {
    'cajcl': grid_i.form_cajcl,
    '75': grid_i.form_75q,
    '150': grid_i.form_150q,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m src.main',
        description='OpenMCR: an accurate and simple exam bubble sheet '
                    'reading tool.\n'
                    'Reads sheets from the input folder, processes them, and '
                    'saves the results\n'
                    'to the output folder.',
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('input_folder',
                        help='Path to a folder containing scanned input '
                             'sheets.\n'
                             'A sheet whose Student ID is all nines is '
                             'treated as an answer key.\n'
                             'Subfolders are ignored.',
                        type=parse_path_arg)
    parser.add_argument('output_folder',
                        help='Path to a folder to save the results to.',
                        type=parse_path_arg)
    parser.add_argument('--anskeys',
                        help='Answer keys CSV file path. If given, it is used '
                             'in place of any keys found in the scans.',
                        type=parse_path_arg)
    parser.add_argument('--formmap',
                        help='Form arrangement map CSV file path. If given, '
                             'only one answer key may be provided.',
                        type=parse_path_arg)
    parser.add_argument('--variant',
                        default='75',
                        choices=sorted(VARIANTS),
                        help='Form variant:\n'
                             "  cajcl  CAJCL State Convention sheet: two "
                             'pages, three 80-question tests\n'
                             '  75     legacy 75-question sheet (default)\n'
                             '  150    legacy 150-question sheet')
    parser.add_argument('-ml', '--multiple',
                        action='store_true',
                        help='Convert multiple answers in a question to F, '
                             'instead of [A|B].')
    parser.add_argument('-e', '--empty',
                        action='store_true',
                        help='Save empty answers as G. By default they are '
                             'saved as blank values.')
    parser.add_argument('-s', '--sort',
                        action='store_true',
                        help="Sort output by students' name.")
    parser.add_argument('-d', '--debug',
                        action='store_true',
                        help='Turn debug mode on. An additional directory '
                             'with debug data will be created.')
    parser.add_argument('--mcta',
                        action='store_true',
                        help='Output additional files for Multiple Choice '
                             'Test Analysis.')
    parser.add_argument('--disable-timestamps',
                        action='store_true',
                        help='Disable timestamps in file names. Useful when '
                             'consistent file names are required.\n'
                             'Existing files will be overwritten without '
                             'warning!')
    parser.add_argument('--annotate',
                        action='store_true',
                        help='Also write a marked-up copy of every scanned '
                             'sheet, with the correct\n'
                             'answer ringed in green and any wrong choice in '
                             'red, for checking by eye.')
    marks = parser.add_mutually_exclusive_group()
    marks.add_argument('--check-marks',
                       dest='review_marks',
                       action='store_true',
                       default=None,
                       help='Check every mark for legibility and refuse to '
                            'grade the unclear ones.\n'
                            'On by default for the cajcl variant, off for the '
                            'legacy ones.')
    marks.add_argument('--no-mark-review',
                       dest='review_marks',
                       action='store_false',
                       default=None,
                       help='Skip the legibility check entirely.')
    parser.add_argument('--allow-unclear',
                        action='store_true',
                        help='Still report unclear marks in '
                             'review_required.csv, but exit successfully\n'
                             'instead of with status 2.')
    parser.add_argument('--answer-margin',
                        type=float,
                        default=mark_quality.DEFAULT_ANSWER_MARGIN,
                        metavar='FRACTION',
                        help='How close to the fill threshold an answer '
                             'bubble may be before it is\n'
                             'sent for hand grading. Default '
                             f'{mark_quality.DEFAULT_ANSWER_MARGIN}; raise it '
                             'to catch more, lower it to catch fewer.')
    parser.add_argument('--id-contrast',
                        type=float,
                        default=mark_quality.DEFAULT_ID_CONTRAST,
                        metavar='FRACTION',
                        help='How far the darkest bubble of an ID block must '
                             'stand clear of the next\n'
                             'darkest to be trusted. Default '
                             f'{mark_quality.DEFAULT_ID_CONTRAST}.')
    return parser


def _make_output_encoding_safe():
    """Never let an unencodable character in a filename abort a run.

    Windows consoles default to a legacy code page, and a scan named with an
    accented character would otherwise raise UnicodeEncodeError in the middle
    of a batch.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list) -> int:
    _make_output_encoding_safe()
    parser = build_parser()
    # Print help and exit when called without arguments.
    if not argv:
        parser.print_help(sys.stderr)
        return 1

    args = parser.parse_args(argv)

    if not args.input_folder.is_dir():
        print(f"Error: input folder '{args.input_folder}' does not exist.",
              file=sys.stderr)
        return 1
    args.output_folder.mkdir(parents=True, exist_ok=True)

    image_paths = file_handling.filter_images(
        file_handling.list_file_paths(args.input_folder))
    if not image_paths:
        print(f"Error: no scans found in '{args.input_folder}'.",
              file=sys.stderr)
        return 1

    files_timestamp = (None if args.disable_timestamps else
                       datetime.now().replace(microsecond=0))

    try:
        process_input(image_paths,
                      args.output_folder,
                      args.multiple,
                      args.empty,
                      args.anskeys,
                      args.formmap,
                      args.sort,
                      args.mcta,
                      args.debug,
                      VARIANTS[args.variant],
                      None,
                      files_timestamp,
                      annotate=args.annotate,
                      review_marks=args.review_marks,
                      allow_unclear_marks=args.allow_unclear,
                      answer_margin=args.answer_margin,
                      id_contrast=args.id_contrast)
    except batching.PageOrderError:
        return 3
    except mark_quality.AmbiguousMarkError:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
