import textwrap
import typing as tp
from pathlib import Path
from datetime import datetime

from . import data_exporting
from . import image_utils
from . import corner_finding
from . import scoring
from . import grid_info as grid_i
from . import grid_reading as grid_r
from .user_interface import ProgressTrackerWidget
from .mcta_processing import transform_and_save_mcta_output


def _extract_page_results(
        image: tp.Any,
        image_label: str,
        form_variant: grid_i.FormVariant,
        debug_path: tp.Optional[Path] = None,
        multi_answers_as_f: bool = False,
        carry_over_fields: tp.Optional[
            tp.Dict[grid_i.Field, str]] = None,
) -> tp.Tuple[tp.List[tp.Dict[grid_i.RealOrVirtualField, str]],
              tp.List[tp.List[str]],
              tp.List[tp.Dict[grid_i.RealOrVirtualField, str]],
              tp.List[tp.List[str]],
              str]:
    """Process a single page image and return:

    * A list of student-result field dicts (one per MCQ column on the page)
    * A matching list of answer lists (each has form_variant.questions_per_column
      entries, formatted as strings A/B/C/...)
    * A list of key-result field dicts (one per MCQ column), empty if not a key
    * A matching list of key answer lists

    Each page may produce multiple rows: legacy single-column variants produce
    exactly one row, while the two-sided variant can produce up to two rows
    from a single page (page 2 has two MCQ columns).

    `carry_over_fields` is a dict of fields whose values should be propagated
    from a prior page when this page does not redefine them. Used by the
    two-sided variant so that the Student ID read on page 1 is carried
    forward to the rows produced from page 2 (which has no Student ID field).
    """

    prepared_image = image_utils.prepare_scan_for_processing(
        image, save_path=debug_path)

    debug_mode = debug_path is not None

    try:
        corners, basis_transformer = corner_finding.find_corner_marks(
            prepared_image,
            save_path=debug_path,
            basis_width=form_variant.basis_width,
            basis_height=form_variant.basis_height)
        if debug_mode:
            print(f"DEBUG: corners={corners}")
    except corner_finding.CornerFindingError as e:
        if debug_mode:
            print(f"DEBUG: CornerFindingError: {e}")
        raise

    morphed_image = image_utils.dilate(prepared_image, save_path=debug_path)

    # Establish a grid sized to the variant
    grid = grid_r.Grid(corners,
                       form_variant.horizontal_cells,
                       form_variant.vertical_cells,
                       morphed_image,
                       basis_transformer=basis_transformer,
                       save_path=debug_path,
                       y_shift=getattr(form_variant, 'y_shift', 0.0))

    # Calculate fill percent for every bubble of every field instance
    # field_fill_percents[field] is a list-of-lists-of-lists:
    #   field -> [instance][field_index][bubble_index]
    field_fill_percents: tp.Dict[grid_i.Field,
                                 tp.List[tp.List[tp.List[float]]]] = {}
    for field_key, value in form_variant.fields.items():
        if value is None:
            continue
        field_fill_percents[field_key] = grid_r.get_field_fill_percents(
            field_key, grid, form_variant)

    # Calculate fill percent for every bubble of every question
    # answer_fill_percents[column_index][question_index][option_index]
    answer_fill_percents: tp.List[tp.List[tp.List[float]]] = [
        grid_r.get_answer_fill_percents_for_column(col, grid, form_variant)
        for col in range(len(form_variant.question_columns))
    ]

    threshold = grid_r.calculate_bubble_fill_threshold(
        field_fill_percents,
        answer_fill_percents,
        save_path=debug_path,
        form_variant=form_variant)

    # Read single-instance fields (those used by every row: STUDENT_ID, etc.)
    shared_field_strings: tp.Dict[grid_i.Field, str] = {}
    for field_key, value in form_variant.fields.items():
        if value is None:
            continue
        if isinstance(value, list):
            # Multi-instance fields are handled per-column below
            continue
        result = grid_r.read_field_as_string(
            field_key, grid, threshold, form_variant,
            field_fill_percents[field_key])
        if result is not None:
            shared_field_strings[field_key] = result

    # Apply any carry-over fields from a prior page (e.g. Student ID from
    # page 1 being propagated to page 2 of the two-sided form). These only
    # fill in values for fields that this page did not define.
    if carry_over_fields:
        for field_key, value in carry_over_fields.items():
            if field_key not in shared_field_strings and value:
                shared_field_strings[field_key] = value
    # Debug: show all detected shared fields
    if debug_mode:
        print("DEBUG: shared_field_strings=", shared_field_strings)
        # Extra debug: dump Student ID fill percents vs threshold
        sid_fp = field_fill_percents.get(grid_i.Field.STUDENT_ID)
        if sid_fp is not None:
            print(f"DEBUG: STUDENT_ID fill percents (threshold={threshold:.4f}):")
            for inst_i, instance in enumerate(sid_fp):
                for col_i, col_fills in enumerate(instance):
                    max_f = max(col_fills) if col_fills else 0
                    best = col_fills.index(max_f) if col_fills else -1
                    above = [i for i, f in enumerate(col_fills) if f > threshold]
                    print(f"  inst={inst_i} col={col_i}: max={max_f:.4f} best={best} above_thresh={above} fills={[round(f,4) for f in col_fills]}")
    # Extract raw student ID (may be empty)
    student_id_raw = shared_field_strings.get(grid_i.Field.STUDENT_ID, "")
    student_id = ''.join(ch for ch in student_id_raw if ch.isdigit())
    # Pad to exactly 5 digits (leading zeros) as required by the form
    student_id = student_id.zfill(5)
    # Use any digit string found for student ID (no fixed length requirement)
    if not student_id:
        for val in shared_field_strings.values():
            digits = ''.join(ch for ch in val if ch.isdigit())
            if digits:
                student_id = digits
                break
    # Determine if this is a key sheet
    is_key = student_id == grid_i.KEY_STUDENT_ID
    if debug_mode:
        print(f"DEBUG: raw student_id='{student_id_raw}'")
        print(f"DEBUG: digits only student_id='{student_id}'")
        print(f"DEBUG: KEY='{grid_i.KEY_STUDENT_ID}' -> is_key={is_key}")

    student_rows: tp.List[tp.Dict[grid_i.RealOrVirtualField, str]] = []
    student_answers: tp.List[tp.List[str]] = []
    key_rows: tp.List[tp.Dict[grid_i.RealOrVirtualField, str]] = []
    key_answers: tp.List[tp.List[str]] = []

    for column_index, column in enumerate(form_variant.question_columns):
        # For multi-instance fields, read the column's instance
        per_column_fields: tp.Dict[grid_i.RealOrVirtualField, str] = dict(
            shared_field_strings)

        multi_value = form_variant.fields.get(grid_i.Field.TEST_FORM_CODE)
        if isinstance(multi_value, list):
            if column_index < len(multi_value):
                test_code_fill = field_fill_percents[
                    grid_i.Field.TEST_FORM_CODE][column_index]
                test_code_value = grid_r.get_group_from_info(
                    multi_value[column_index],
                    grid).read_value(threshold, test_code_fill)
                per_column_fields[grid_i.Field.TEST_FORM_CODE] = (
                    grid_r.field_group_to_string(test_code_value))
            else:
                per_column_fields[grid_i.Field.TEST_FORM_CODE] = ""
        elif (carry_over_fields
              and grid_i.Field.TEST_FORM_CODE in carry_over_fields
              and (grid_i.Field.TEST_FORM_CODE not in per_column_fields
                   or not per_column_fields[grid_i.Field.TEST_FORM_CODE])):
            # Carry over Test Form Code from a prior page if this page doesn't
            # define one of its own.
            carried = carry_over_fields.get(grid_i.Field.TEST_FORM_CODE, "")
            if carried:
                per_column_fields[grid_i.Field.TEST_FORM_CODE] = carried

        per_column_fields[grid_i.Field.IMAGE_FILE] = image_label
        # Tag the column index in a virtual field so consumers can tell which
        # MCQ column produced the row (1-based for human-friendly output).
        per_column_fields[grid_i.VirtualField.POINTS] = ""  # placeholder
        # Encode the column index as part of IMAGE_FILE suffix for clarity
        if len(form_variant.question_columns) > 1:
            per_column_fields[grid_i.Field.IMAGE_FILE] = (
                f"{image_label} (column {column_index + 1})")

        answers = [
            grid_r.field_group_to_string(
                grid_r.read_answer_column(
                    column_index, q, grid, threshold, form_variant,
                    answer_fill_percents[column_index]))
            for q in range(len(column))
        ]
        # Apply the multi-answer -> F transform consistently
        answers = [
            ("F" if multi_answers_as_f and "|" in a else a) for a in answers
        ]

        if is_key:
            # Per-column key: clear out identifying fields and store the key
            key_data: tp.Dict[grid_i.RealOrVirtualField, str] = {
                grid_i.Field.TEST_FORM_CODE:
                per_column_fields.get(grid_i.Field.TEST_FORM_CODE, ""),
                grid_i.Field.IMAGE_FILE: per_column_fields[grid_i.Field.IMAGE_FILE],
            }
            key_rows.append(key_data)
            key_answers.append(answers)
        else:
            student_rows.append(per_column_fields)
            student_answers.append(answers)

    return student_rows, student_answers, key_rows, key_answers, student_id_raw


def process_input(
        image_paths: tp.List[Path],
        output_folder: Path,
        multi_answers_as_f: bool,
        empty_answers_as_g: bool,
        keys_file: tp.Optional[Path],
        arrangement_file: tp.Optional[Path],
        sort_results: bool,
        output_mcta: bool,
        debug_mode_on: bool,
        form_variant: tp.Union[grid_i.FormVariant,
                               grid_i.TwoSidedFormVariant],
        progress_tracker: tp.Optional[ProgressTrackerWidget],
        files_timestamp: tp.Optional[datetime]):
    """Takes input as parameters and process it for either gui or cli.

    Parameter progress_tracker determines whith interface in use.
    If progress_tracker is given, function runs in gui mode.
    If progress_tracker parameter is None, prints all progress statuses to stdout.

    `form_variant` is normally a single-page `FormVariant`. For the
    two-sided variant it is a `TwoSidedFormVariant` that holds two
    page-specific variants - `process_input` reads page 1 with the page-1
    variant and page 2 with the page-2 variant.
    """

    if isinstance(form_variant, grid_i.TwoSidedFormVariant):
        questions_per_row = form_variant.variant_for_page(0).questions_per_column
    else:
        questions_per_row = form_variant.questions_per_column

    if isinstance(form_variant, grid_i.TwoSidedFormVariant):
        fields_list = list(form_variant.variant_for_page(0).fields.keys())
    else:
        fields_list = list(form_variant.fields.keys())
    print(f"DEBUG_PI: form_variant type={type(form_variant).__name__}, is_two_sided={isinstance(form_variant, grid_i.TwoSidedFormVariant)}, questions_per_row={questions_per_row}")
    answers_results = data_exporting.OutputSheet(fields_list, questions_per_row)
    keys_results = data_exporting.OutputSheet([grid_i.Field.TEST_FORM_CODE, grid_i.Field.IMAGE_FILE],
                                              questions_per_row)

    rejected_files = data_exporting.OutputSheet([grid_i.Field.IMAGE_FILE], 0)

    debug_dir = output_folder / (
            data_exporting.format_timestamp_for_file(files_timestamp) + "debug")
    if debug_mode_on:
        data_exporting.make_dir_if_not_exists(debug_dir)

    try:
        for image_path in image_paths:
            if debug_mode_on:
                debug_path = debug_dir / image_path.stem
                data_exporting.make_dir_if_not_exists(debug_path)
            else:
                debug_path = None

            if progress_tracker:
                progress_tracker.set_status(f"Processing '{image_path.name}'.")
            else:
                print(f"Processing '{image_path.name}'.")

            try:
                pages = image_utils.load_image_pages(image_path)
            except image_utils.UnsupportedImageError as exc:
                if progress_tracker:
                    progress_tracker.set_status(
                        f"Skipping '{image_path.name}': {exc}")
                else:
                    print(f"Skipping '{image_path.name}': {exc}")
                rejected_files.add({grid_i.Field.IMAGE_FILE: image_path.name}, [])
                continue

            any_page_failed = False
            # For the two-sided variant, carry shared fields (Student ID, etc.)
            # forward from page 1 to page 2 so that all rows produced from
            # this scan share the same Student ID.
            carry_over: tp.Dict[grid_i.Field, str] = {}
            for page_index, image in enumerate(pages):
                page_label = (image_path.name
                              if len(pages) == 1 else
                              f"{image_path.name} (page {page_index + 1})")
                page_debug_path = None
                if debug_path is not None:
                    page_debug_path = debug_path / f"page_{page_index + 1}"
                    data_exporting.make_dir_if_not_exists(page_debug_path)

                # For a TwoSidedFormVariant, dispatch to the per-page variant.
                # For a plain FormVariant, use it for every page.
                if isinstance(form_variant, grid_i.TwoSidedFormVariant):
                    page_variant = form_variant.variant_for_page(page_index)
                else:
                    page_variant = form_variant

                # For the two-sided form, refresh the carry-over on page 1
                # (so Student ID can be picked up from page 1 if the user
                # wrote it there) and propagate it on subsequent pages.
                page_carry_over = carry_over if page_index > 0 else None

                try:
                    (student_rows, student_answers,
                     key_rows, key_answers, student_id_raw) = _extract_page_results(
                        image=image,
                        image_label=page_label,
                        form_variant=page_variant,
                        debug_path=page_debug_path,
                        multi_answers_as_f=multi_answers_as_f,
                        carry_over_fields=page_carry_over,
                    )
                except corner_finding.CornerFindingError:
                    rejected_files.add(
                        {grid_i.Field.IMAGE_FILE: page_label}, [])
                    any_page_failed = True
                    continue

                for row, answers in zip(student_rows, student_answers):
                    answers_results.add(row, answers)
                for row, answers in zip(key_rows, key_answers):
                    keys_results.add(row, answers)

                # After the first successful page, capture fields that we
                # want to carry forward to subsequent pages. For the
                # two-sided form this is the Student ID.
                if isinstance(form_variant, grid_i.TwoSidedFormVariant
                              ) and page_index == 0:
                    if student_id_raw:
                        carry_over[grid_i.Field.STUDENT_ID] = student_id_raw

            if progress_tracker:
                progress_tracker.step_progress()

        answers_results.clean_up(
            replace_empty_with="G" if empty_answers_as_g else "")
        answers_results.save(output_folder,
                             "results",
                             sort_results,
                             timestamp=files_timestamp)

        if rejected_files.row_count == 0:
            success_string = "✔️ All exams processed and saved.\n"
        else:
            success_string = "❗ Some files could not be processed (see rejected_files output).\nAll other exams were processed and saved.\n"
            rejected_files.save(output_folder, "rejected_files", sort=False, timestamp=files_timestamp)

        if keys_file:
            keys_results.add_file(keys_file)

        if (keys_results.row_count == 0):
            success_string += "No exam keys were found, so no scoring was performed."
        elif (arrangement_file and keys_results.row_count == 1):
            answers_results.reorder(arrangement_file)
            keys_results.data[1][keys_results.field_columns.index(
                grid_i.Field.TEST_FORM_CODE)] = ""

            answers_results.save(output_folder,
                                 "rearranged_results",
                                 sort_results,
                                 timestamp=files_timestamp)
            success_string += "✔️ Results rearranged based on arrangement file.\n"

            keys_results.delete_field_column(grid_i.Field.TEST_FORM_CODE)
            keys_results.save(output_folder,
                              "key",
                              sort_results,
                              timestamp=files_timestamp,
                              transpose=True)

            success_string += "✔️ Key processed and saved.\n"

            scores = scoring.score_results(answers_results, keys_results,
                                           questions_per_row)
            scores.save(output_folder,
                        "rearranged_scores",
                        sort_results,
                        timestamp=files_timestamp)
            success_string += "✔️ Scored results processed and saved."
        elif (arrangement_file):
            success_string += "❌ Arrangement file and keys were ignored because more than one key was found."
        else:
            keys_results.save(output_folder,
                              "keys",
                              sort_results,
                              timestamp=files_timestamp)
            success_string += "✔️ All keys processed and saved.\n"
            scores = scoring.score_results(answers_results, keys_results,
                                           questions_per_row)
            scores.save(output_folder,
                        "scores",
                        sort_results,
                        timestamp=files_timestamp)
            success_string += "✔️ All scored results processed and saved."

        if (output_mcta):
            transform_and_save_mcta_output(answers_results, keys_results, files_timestamp, output_folder)

        if progress_tracker:
            progress_tracker.set_status(success_string, False)
        else:
            print(success_string)
    except (RuntimeError, ValueError) as e:
        wrapped_err = "\n".join(textwrap.wrap(str(e), 70))
        if progress_tracker:
            progress_tracker.set_status(f"Error: {wrapped_err}", False)
        else:
            print(f'Error: {wrapped_err}')
        if debug_mode_on:
            raise
    if progress_tracker:
        progress_tracker.show_exit_button_and_wait()
