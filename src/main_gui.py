"""Graphical entry point.

Run with ``python -m src.main_gui``. The packaged application starts here too,
by way of the ``open_mcr.py`` launcher in the repository root - see
``build_instructions.md``.
"""

import sys
from datetime import datetime

from . import batching
from . import file_handling
from . import grid_info as grid_i
from . import mark_quality
from . import user_interface
from .process_input import process_input

FORM_VARIANTS = {
    user_interface.FormVariantSelection.VARIANT_75_Q: grid_i.form_75q,
    user_interface.FormVariantSelection.VARIANT_150_Q: grid_i.form_150q,
    user_interface.FormVariantSelection.VARIANT_CAJCL: grid_i.form_cajcl,
}


def main() -> int:
    user_input = user_interface.MainWindow()
    if user_input.cancelled:
        return 0

    image_paths = file_handling.filter_images(
        file_handling.list_file_paths(user_input.input_folder))
    progress_tracker = user_input.create_and_pack_progress(
        maximum=len(image_paths))

    try:
        process_input(image_paths,
                      user_input.output_folder,
                      user_input.multi_answers_as_f,
                      user_input.empty_answers_as_g,
                      user_input.keys_file,
                      user_input.arrangement_map,
                      user_input.sort_results,
                      user_input.output_mcta,
                      user_input.debug_mode,
                      FORM_VARIANTS[user_input.form_variant],
                      progress_tracker,
                      datetime.now().replace(microsecond=0),
                      annotate=user_input.annotate,
                      allow_unclear_marks=user_input.allow_unclear_marks)
    except (batching.PageOrderError, mark_quality.AmbiguousMarkError):
        # Already reported in the progress window, which waits for the user.
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
