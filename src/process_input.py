"""Reading a batch of scanned sheets and turning it into CSV output."""

import textwrap
import typing as tp
from datetime import datetime
from pathlib import Path

from . import alphabet
from . import annotation
from . import batching
from . import corner_finding
from . import data_exporting
from . import grid_info as grid_i
from . import grid_reading as grid_r
from . import image_utils
from . import mark_quality
from . import scoring
from . import sheet_layout
from .mcta_processing import transform_and_save_mcta_output
from .user_interface import ProgressTrackerWidget

#: Names used when telling the user which side of a sheet a page is.
PAGE_SIDE_NAMES = ("front page", "back page")


class PageResult(tp.NamedTuple):
    """Everything read off one scanned page."""

    student_rows: tp.List[tp.Dict[grid_i.RealOrVirtualField, str]]
    student_answers: tp.List[tp.List[str]]
    key_rows: tp.List[tp.Dict[grid_i.RealOrVirtualField, str]]
    key_answers: tp.List[tp.List[str]]
    student_id: str
    latin_level: str
    page_side: tp.Optional[int]
    annotation_columns: tp.Tuple[annotation.ColumnAnnotation, ...]


def _values_to_digits(values: tp.Sequence[tp.Sequence[tp.Any]],
                      blank: str = "?") -> str:
    """Join a digit block's per-column readings, keeping column positions.

    A column with nothing filled becomes `blank` rather than disappearing, so
    "student 0123 with an unreadable second digit" comes out as `0?23` instead
    of the silently wrong `023`.
    """
    characters: tp.List[str] = []
    for value in values:
        if len(value) == 1:
            characters.append(str(value[0]))
        elif len(value) == 0:
            characters.append(blank)
        else:
            characters.append(blank)
    return "".join(characters)


def _wrapped_error(error: BaseException) -> str:
    """Format an error for a narrow status box or terminal."""
    return "Error: " + textwrap.fill(str(error), 70)


def _first_choice(values: tp.Sequence[tp.Sequence[tp.Any]]
                  ) -> tp.Optional[int]:
    """The index chosen in a single-field block, or None if there isn't one."""
    if not values or len(values[0]) != 1:
        return None
    try:
        return int(values[0][0])
    except (TypeError, ValueError):
        return None


def _extract_page_results(
        image: tp.Any,
        page_label: str,
        form_variant: grid_i.FormVariant,
        review: mark_quality.ReviewCollector,
        collect_annotations: bool,
        debug_path: tp.Optional[Path] = None,
        multi_answers_as_f: bool = False,
        carry_over_fields: tp.Optional[tp.Dict[grid_i.Field, str]] = None,
) -> PageResult:
    """Read one page and return its rows, its identifying fields, and - if
    asked for - where every answer bubble was found.

    Each MCQ column on the page produces one output row: the front page of the
    CAJCL sheet produces one (Test 1) and the back page two (Tests 2 and 3).

    `carry_over_fields` supplies values read from an earlier page of the same
    sheet (the Latin level, for instance, is only bubbled on the front).
    """

    prepared_image = image_utils.prepare_scan_for_processing(
        image, save_path=debug_path)
    debug_mode = debug_path is not None

    corners, basis_transformer = corner_finding.find_corner_marks(
        prepared_image,
        save_path=debug_path,
        basis_width=form_variant.basis_width,
        basis_height=form_variant.basis_height,
        l_mark_offset=form_variant.l_mark_offset)

    morphed_image = image_utils.dilate(prepared_image, save_path=debug_path)

    grid = grid_r.Grid(corners,
                       form_variant.horizontal_cells,
                       form_variant.vertical_cells,
                       morphed_image,
                       basis_transformer=basis_transformer,
                       save_path=debug_path,
                       y_shift=getattr(form_variant, 'y_shift', 0.0))

    # field_fill_percents[field][instance][field_index][bubble_index]
    field_fill_percents: tp.Dict[grid_i.Field,
                                 tp.List[tp.List[tp.List[float]]]] = {}
    for field_key, value in form_variant.fields.items():
        if value is None:
            continue
        field_fill_percents[field_key] = grid_r.get_field_fill_percents(
            field_key, grid, form_variant)

    # answer_fill_percents[column][question][option]
    answer_fill_percents: tp.List[tp.List[tp.List[float]]] = [
        grid_r.get_answer_fill_percents_for_column(col, grid, form_variant)
        for col in range(len(form_variant.question_columns))
    ]

    threshold = grid_r.calculate_bubble_fill_threshold(
        field_fill_percents,
        answer_fill_percents,
        save_path=debug_path,
        form_variant=form_variant)

    # What a blank bubble and a deliberate mark look like on *this* page, used
    # to judge whether an individual answer is clear enough to trust.
    levels = mark_quality.estimate_levels(
        (fill for column in answer_fill_percents for question in column
         for field in question for fill in field), threshold)

    def read_instance(field_key: grid_i.Field, instance: int):
        info = form_variant.fields[field_key]
        if isinstance(info, list):
            info = info[instance]
        return grid_r.get_group_from_info(info, grid).read_value(
            threshold, field_fill_percents[field_key][instance])

    # --- identifying fields ------------------------------------------------

    student_id = ""
    if grid_i.Field.STUDENT_ID in field_fill_percents:
        student_id = _values_to_digits(
            read_instance(grid_i.Field.STUDENT_ID, 0))
        for digit_index, fills in enumerate(
                field_fill_percents[grid_i.Field.STUDENT_ID][0]):
            review.check_choice(page_label, student_id,
                                f"Student ID digit {digit_index + 1}", fills,
                                [str(digit) for digit in range(len(fills))])

    # An all-nines Student ID marks the sheet as an answer key rather than a
    # student's paper. Keys carry no name or Latin level, so those are not
    # required on them.
    is_key = student_id == form_variant.key_student_id

    latin_level = ""
    if grid_i.Field.LATIN_LEVEL in field_fill_percents:
        index = _first_choice(read_instance(grid_i.Field.LATIN_LEVEL, 0))
        if index is not None:
            latin_level = grid_i.latin_level_name(str(index))
        review.check_choice(page_label, student_id, "Latin level",
                            field_fill_percents[grid_i.Field.LATIN_LEVEL][0][0],
                            list(sheet_layout.LATIN_LEVELS),
                            required=not is_key)

    page_side: tp.Optional[int] = None
    if grid_i.Field.PAGE_CODE in field_fill_percents:
        page_side = _first_choice(read_instance(grid_i.Field.PAGE_CODE, 0))

    if not latin_level and carry_over_fields:
        latin_level = carry_over_fields.get(grid_i.Field.LATIN_LEVEL, "")

    # Any remaining single-instance field (the legacy name / course fields).
    shared_field_strings: tp.Dict[grid_i.Field, str] = {}
    for field_key, value in form_variant.fields.items():
        if value is None or isinstance(value, list):
            continue
        if field_key in (grid_i.Field.STUDENT_ID, grid_i.Field.LATIN_LEVEL,
                         grid_i.Field.PAGE_CODE, grid_i.Field.TEST_FORM_CODE):
            continue
        shared_field_strings[field_key] = grid_r.field_group_to_string(
            read_instance(field_key, 0))
    if debug_mode:
        print(f"DEBUG: {page_label}: threshold={threshold:.4f} "
              f"student_id='{student_id}' latin='{latin_level}' "
              f"page_side={page_side} is_key={is_key}")

    # --- one row per answer column ----------------------------------------

    student_rows: tp.List[tp.Dict[grid_i.RealOrVirtualField, str]] = []
    student_answers: tp.List[tp.List[str]] = []
    key_rows: tp.List[tp.Dict[grid_i.RealOrVirtualField, str]] = []
    key_answers: tp.List[tp.List[str]] = []
    annotation_columns: tp.List[annotation.ColumnAnnotation] = []

    test_form_code_info = form_variant.fields.get(grid_i.Field.TEST_FORM_CODE)

    for column_index, column in enumerate(form_variant.question_columns):
        fields: tp.Dict[grid_i.RealOrVirtualField, str] = dict(
            shared_field_strings)
        fields[grid_i.Field.STUDENT_ID] = student_id
        fields[grid_i.Field.LATIN_LEVEL] = latin_level

        form_code = ""
        if test_form_code_info is not None:
            instance = column_index if isinstance(test_form_code_info,
                                                  list) else 0
            instances = (len(test_form_code_info) if isinstance(
                test_form_code_info, list) else 1)
            if instance < instances:
                info = (test_form_code_info[instance] if isinstance(
                    test_form_code_info, list) else test_form_code_info)
                values = read_instance(grid_i.Field.TEST_FORM_CODE, instance)
                if info.fields_type is grid_i.FieldType.NUMBER:
                    form_code = _values_to_digits(values)
                    for digit_index, fills in enumerate(
                            field_fill_percents[
                                grid_i.Field.TEST_FORM_CODE][instance]):
                        review.check_choice(
                            page_label, student_id,
                            f"Test ID digit {digit_index + 1}", fills,
                            [str(digit) for digit in range(len(fills))])
                else:
                    form_code = grid_r.field_group_to_string(values)
        if not form_code and carry_over_fields:
            form_code = carry_over_fields.get(grid_i.Field.TEST_FORM_CODE, "")
        fields[grid_i.Field.TEST_FORM_CODE] = form_code

        fields[grid_i.Field.IMAGE_FILE] = (
            f"{page_label} (column {column_index + 1})"
            if len(form_variant.question_columns) > 1 else page_label)

        answers: tp.List[str] = []
        annotated_questions: tp.List[annotation.QuestionMarks] = []
        for question_index, question_info in enumerate(column):
            # A question is one field of five bubbles, so its fill percents
            # arrive wrapped in a one-element per-field list.
            option_fills = answer_fill_percents[column_index][
                question_index][0]
            option_labels = alphabet.letters[:len(option_fills)]
            verdict = None
            if review.enabled or collect_annotations:
                verdict = mark_quality.review_answer(
                    option_fills, levels, option_labels, review.answer_margin)
            location = f"Q{question_index + 1}"
            if form_code:
                location += f" (Test ID {form_code})"
            review.add(page_label, student_id, location, verdict)

            value = grid_r.read_answer_column(
                column_index, question_index, grid, threshold, form_variant,
                answer_fill_percents[column_index])
            answer = grid_r.field_group_to_string(value)
            if multi_answers_as_f and "|" in answer:
                answer = "F"
            answers.append(answer)

            if collect_annotations:
                circles = grid_r.get_group_cell_circles(question_info, grid)[0]
                chosen = tuple(
                    index for index, fill in enumerate(option_fills)
                    if fill > threshold)
                annotated_questions.append(
                    annotation.QuestionMarks(number=question_index + 1,
                                             circles=tuple(circles),
                                             chosen=chosen,
                                             unclear=verdict is not None))

        if collect_annotations and column:
            caption_centre = grid.get_cell_center(
                column[0].horizontal_start,
                max(column[0].vertical_start - 2, 0))
            annotation_columns.append(
                annotation.ColumnAnnotation(
                    form_code=form_code,
                    questions=tuple(annotated_questions),
                    caption_at=(caption_centre.x, caption_centre.y),
                    caption=f"Test ID {form_code}" if form_code else ""))

        if is_key:
            key_rows.append({
                grid_i.Field.TEST_FORM_CODE: form_code,
                grid_i.Field.IMAGE_FILE: fields[grid_i.Field.IMAGE_FILE],
            })
            key_answers.append(answers)
        else:
            student_rows.append(fields)
            student_answers.append(answers)

    return PageResult(student_rows=student_rows,
                      student_answers=student_answers,
                      key_rows=key_rows,
                      key_answers=key_answers,
                      student_id=student_id,
                      latin_level=latin_level,
                      page_side=page_side,
                      annotation_columns=tuple(annotation_columns))


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
        form_variant: tp.Union[grid_i.FormVariant, grid_i.TwoSidedFormVariant],
        progress_tracker: tp.Optional[ProgressTrackerWidget],
        files_timestamp: tp.Optional[datetime],
        annotate: bool = False,
        review_marks: tp.Optional[bool] = None,
        allow_unclear_marks: bool = False,
        answer_margin: float = mark_quality.DEFAULT_ANSWER_MARGIN,
        id_contrast: float = mark_quality.DEFAULT_ID_CONTRAST):
    """Read every sheet in `image_paths` and write the results to CSV.

    Parameter `progress_tracker` decides which interface is in use: if it is
    given the function runs in GUI mode, otherwise it prints progress to
    stdout.

    `form_variant` is normally a single-page `FormVariant`. For a two-sided
    variant it is a `TwoSidedFormVariant` holding one layout per side; the
    batch is then split into two-page sheets and the page order is verified.

    With `review_marks` on, every mark is re-checked for legibility and a
    `review_required.csv` lists the ones a human needs to settle; unless
    `allow_unclear_marks` is set, that also raises so the caller can fail the
    run. Left as None it follows the variant's own default, which is on for
    the CAJCL sheet and off for the legacy ones. With `annotate` on, a marked-up copy of every
    scan is written showing the correct answer in green and a wrong choice in
    red.

    Raises:
        batching.PageOrderError: if the batch is not a clean sequence of
            front/back pairs.
        mark_quality.AmbiguousMarkError: after the output has been written, if
            any mark was too unclear to grade and `allow_unclear_marks` is
            False.
    """

    def report(message: str, show_count: bool = True):
        if progress_tracker:
            progress_tracker.set_status(message, show_count)
        else:
            print(message)

    is_two_sided = isinstance(form_variant, grid_i.TwoSidedFormVariant)
    pages_per_sheet = form_variant.pages_per_sheet if is_two_sided else 1
    questions_per_row = form_variant.questions_per_column
    output_fields = list(form_variant.output_fields)

    if review_marks is None:
        review_marks = form_variant.review_marks_by_default
    review = mark_quality.ReviewCollector(answer_margin=answer_margin,
                                          id_contrast=id_contrast,
                                          enabled=review_marks)

    answers_results = data_exporting.OutputSheet(output_fields,
                                                 questions_per_row)
    keys_results = data_exporting.OutputSheet(
        [grid_i.Field.TEST_FORM_CODE, grid_i.Field.IMAGE_FILE],
        questions_per_row)
    rejected_files = data_exporting.OutputSheet([grid_i.Field.IMAGE_FILE], 0)

    debug_dir = output_folder / (
        data_exporting.format_timestamp_for_file(files_timestamp) + "debug")
    if debug_mode_on:
        data_exporting.make_dir_if_not_exists(debug_dir)

    # --- plan the batch before decoding anything --------------------------

    sheets = batching.plan_batch(sorted(image_paths), pages_per_sheet)
    if not sheets:
        report("No readable input files were found.", False)
        return
    if pages_per_sheet > 1:
        report(
            f"Found {len(sheets)} sheet(s) across "
            f"{len(sheets) * pages_per_sheet} page(s).", False)
    if progress_tracker:
        progress_tracker.set_maximum(len(sheets))

    annotated_pages: tp.List[annotation.PageAnnotation] = []

    try:
        carry_over: tp.Dict[grid_i.Field, str] = {}
        sheet_front_id = ""

        for page, image in batching.iter_batch_pages(sheets):
            report(f"Processing '{page.label}'.")

            page_variant = (form_variant.variant_for_page(
                page.position_in_sheet) if is_two_sided else form_variant)

            if page.position_in_sheet == 0:
                carry_over = {}
                sheet_front_id = ""

            page_debug_path = None
            if debug_mode_on:
                page_debug_path = debug_dir / (
                    f"sheet_{page.sheet_index + 1:04d}_"
                    f"page_{page.position_in_sheet + 1}")
                data_exporting.make_dir_if_not_exists(page_debug_path)

            try:
                result = _extract_page_results(
                    image=image,
                    page_label=page.label,
                    form_variant=page_variant,
                    review=review,
                    collect_annotations=annotate,
                    debug_path=page_debug_path,
                    multi_answers_as_f=multi_answers_as_f,
                    carry_over_fields=carry_over or None,
                )
            except corner_finding.CornerFindingError as error:
                report(f"Could not find the corner marks on '{page.label}': "
                       f"{error}")
                rejected_files.add({grid_i.Field.IMAGE_FILE: page.label}, [])
                continue

            if pages_per_sheet > 1 and grid_i.Field.PAGE_CODE in (
                    page_variant.fields):
                batching.check_page_side(page, result.page_side,
                                         PAGE_SIDE_NAMES)

            if page.position_in_sheet == 0:
                sheet_front_id = result.student_id
                if result.student_id:
                    carry_over[grid_i.Field.STUDENT_ID] = result.student_id
                if result.latin_level:
                    carry_over[grid_i.Field.LATIN_LEVEL] = result.latin_level
            else:
                batching.check_student_id(page, sheet_front_id,
                                          result.student_id)
                # The front page's reading is authoritative for the sheet, so
                # every row carries the same Student ID even if the back page
                # was bubbled less neatly.
                if sheet_front_id:
                    for row in result.student_rows:
                        row[grid_i.Field.STUDENT_ID] = sheet_front_id

            for row, answers in zip(result.student_rows,
                                    result.student_answers):
                answers_results.add(row, answers)
            for row, answers in zip(result.key_rows, result.key_answers):
                keys_results.add(row, answers)

            if annotate and result.annotation_columns:
                annotated_pages.append(
                    annotation.PageAnnotation(
                        path=page.path,
                        page_index=page.page_index,
                        label=page.label,
                        student_id=result.student_id,
                        columns=result.annotation_columns))

            if page.position_in_sheet == pages_per_sheet - 1 and (
                    progress_tracker):
                progress_tracker.step_progress()

        answers_results.clean_up(
            replace_empty_with="G" if empty_answers_as_g else "")
        answers_results.save(output_folder,
                             "results",
                             sort_results,
                             timestamp=files_timestamp)

        if rejected_files.row_count == 0:
            success_string = "OK: all exams processed and saved.\n"
        else:
            success_string = (
                "NOTE: some pages could not be processed (see rejected_files "
                "output).\nAll other exams were processed and saved.\n")
            rejected_files.save(output_folder,
                                "rejected_files",
                                sort=False,
                                timestamp=files_timestamp)

        if keys_file:
            keys_results.add_file(keys_file)

        # Captured before the arrangement branch below mutates `keys_results`.
        annotation_keys: tp.Dict[str, tp.List[str]] = {}
        if keys_results.row_count and annotate:
            annotation_keys = scoring.establish_key_dict(keys_results)

        if keys_results.row_count == 0:
            success_string += (
                "No exam keys were found, so no scoring was performed.")
        elif arrangement_file and keys_results.row_count == 1:
            answers_results.reorder(arrangement_file)
            keys_results.data[1][keys_results.field_columns.index(
                grid_i.Field.TEST_FORM_CODE)] = ""

            answers_results.save(output_folder,
                                 "rearranged_results",
                                 sort_results,
                                 timestamp=files_timestamp)
            success_string += "OK: results rearranged based on arrangement file.\n"

            keys_results.delete_field_column(grid_i.Field.TEST_FORM_CODE)
            keys_results.save(output_folder,
                              "key",
                              sort_results,
                              timestamp=files_timestamp,
                              transpose=True)
            success_string += "OK: key processed and saved.\n"

            scores = scoring.score_results(answers_results, keys_results,
                                           questions_per_row)
            scores.save(output_folder,
                        "rearranged_scores",
                        sort_results,
                        timestamp=files_timestamp)
            success_string += "OK: scored results processed and saved."
        elif arrangement_file:
            success_string += (
                "SKIPPED: arrangement file and keys were ignored because more than "
                "one key was found.")
        else:
            keys_results.save(output_folder,
                              "keys",
                              sort_results,
                              timestamp=files_timestamp)
            success_string += "OK: all keys processed and saved.\n"
            scores = scoring.score_results(answers_results, keys_results,
                                           questions_per_row)
            scores.save(output_folder,
                        "scores",
                        sort_results,
                        timestamp=files_timestamp)
            success_string += "OK: all scored results processed and saved."

        if output_mcta:
            transform_and_save_mcta_output(answers_results, keys_results,
                                           files_timestamp, output_folder)

        if annotate and annotated_pages:
            annotated_folder = output_folder / (
                data_exporting.format_timestamp_for_file(files_timestamp) +
                "annotated")
            written = annotation.write_annotated_pdfs(
                annotated_pages,
                annotation_keys,
                annotated_folder,
                "".join(alphabet.letters),
                progress=None if progress_tracker else print)
            success_string += (
                f"\nOK: {len(written)} marked-up PDF(s) saved to "
                f"'{annotated_folder.name}'.")

        review_path: tp.Optional[Path] = None
        if review.any_issues:
            review_path = output_folder / (
                data_exporting.format_timestamp_for_file(files_timestamp) +
                "review_required.csv")
            data_exporting.save_csv(review.rows(), review_path)
            success_string += (
                f"\nATTENTION: {len(review.issues)} mark(s) were too unclear to grade "
                f"automatically. See '{review_path.name}'.")

        report(success_string, False)

        if review.any_issues and not allow_unclear_marks:
            if not progress_tracker:
                print(review.summary())
            raise mark_quality.AmbiguousMarkError(
                review.issues,
                str(review_path) if review_path else None)

    except (batching.PageOrderError, mark_quality.AmbiguousMarkError) as error:
        # Both are the operator's problem to fix, not a crash: report them
        # clearly, then let the caller decide the exit status.
        report(_wrapped_error(error), False)
        if progress_tracker:
            progress_tracker.show_exit_button_and_wait()
        raise
    except (RuntimeError, ValueError) as error:
        report(_wrapped_error(error), False)
        if debug_mode_on:
            raise

    if progress_tracker:
        progress_tracker.show_exit_button_and_wait()
