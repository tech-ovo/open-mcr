"""Minimal test - just try to read the synthetic key sheet."""

import os
from pathlib import Path

# Import using absolute package paths since this script is executed as a module within the src package
from src import grid_info as grid_i
from src.process_input import process_input

# Just process the synthetic key with debug mode
out_dir = "/tmp/minimal_test"
if os.path.exists(out_dir):
    import shutil
    shutil.rmtree(out_dir)
os.makedirs(out_dir, exist_ok=True)

print(f"Processing test_key.pdf...")
try:
    process_input(
        image_paths=[Path("/tmp/test_key.pdf")],
        output_folder=Path(out_dir),
        multi_answers_as_f=False,
        empty_answers_as_g=False,
        keys_file=None,
        arrangement_file=None,
        sort_results=False,
        output_mcta=False,
        debug_mode_on=True,
        form_variant=grid_i.form_two_sided_225q,
        progress_tracker=None,
        files_timestamp=None,
    )
    print("Done.")
except Exception as e:
    import traceback
    traceback.print_exc()