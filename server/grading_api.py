"""The grading service, deployed on Modal.

Nothing is kept. Every request unpacks its uploads into a temporary directory,
runs the same pipeline the command line runs, reads the results back into
memory, and deletes the directory before replying. There is no database, no
volume, no object store and no job queue, so there is nothing to leak and
nothing to clean up later.

That is also why there is no async job API: a result that outlives its request
has to be stored somewhere. Each request does one batch, synchronously. Large
jobs are split by the operator at the scanner, which the website explains.

Deploy with::

    modal secret create grading-passphrase GRADING_PASSPHRASE=<something long>
    modal deploy server/grading_api.py
"""

import base64
import json
import os
import pathlib
import shutil
import sys
import tempfile
import typing as tp

try:
    import modal
except ImportError:  # local testing without the Modal SDK installed
    modal = None

# --- limits ---------------------------------------------------------------

#: Biggest upload accepted, in bytes. Modal's own request ceiling is higher,
#: but a batch this size already takes long enough that splitting it is the
#: better answer, and the message says so.
MAX_UPLOAD_BYTES = 40 * 1024 * 1024

#: Sheets per batch we suggest. 25 sheets is 50 pages, which lands around
#: 20-30 seconds - comfortably inside the timeout with room for a slow scan.
SUGGESTED_SHEETS_PER_BATCH = 25

#: How long one request may run before Modal gives up on it.
REQUEST_TIMEOUT_SECONDS = 600

REPO_ROOT = pathlib.Path(__file__).parent.parent

if modal is not None:
    image = (modal.Image.debian_slim(python_version="3.13")
             .apt_install("libgl1", "libglib2.0-0")
             .pip_install(
                 "numpy>=2.1,<3",
                 "opencv-python-headless>=4.11,<6",
                 "pypdfium2>=4.30,<6",
                 "reportlab>=4.2,<6",
                 "Pillow>=10.4,<14",
                 "fastapi[standard]",
             )
             .add_local_dir(REPO_ROOT / "src", remote_path="/root/src"))

    app = modal.App("uhsjcl-grading")


def _passphrase() -> str:
    return os.environ.get("GRADING_PASSPHRASE", "")


# --- helpers that run inside the container --------------------------------


def _collect_outputs(folder: pathlib.Path) -> tp.List[tp.Dict[str, tp.Any]]:
    """Read every output file into memory, ready to send back.

    Text comes back as text so the browser can show it and keep it; PDFs come
    back base64-encoded.
    """
    files: tp.List[tp.Dict[str, tp.Any]] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        if path.suffix.lower() in (".csv", ".txt"):
            files.append({
                "name": relative,
                "type": "text/csv" if path.suffix.lower() == ".csv"
                        else "text/plain",
                "encoding": "utf-8",
                "data": path.read_text(encoding="utf-8"),
            })
        else:
            files.append({
                "name": relative,
                "type": "application/pdf",
                "encoding": "base64",
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            })
    return files


def _summarise(result, thresholds_note: str) -> tp.Dict[str, tp.Any]:
    from src import answer_key as keys_module

    rows = result.rows
    scored = [row for row in rows if row.points is not None]
    statuses: tp.Dict[str, int] = {}
    for row in rows:
        if row.status:
            statuses[row.status] = statuses.get(row.status, 0) + 1
    return {
        "sheets": len({(row.source_file, row.student_id) for row in rows}),
        "rows": len(rows),
        "scored": len(scored),
        "unclear": len(result.unclear),
        "missing": len(result.missing),
        "statuses": statuses,
        "students": sorted({row.student_id for row in rows}),
        "thresholds": [{
            "file": name,
            "spec": value.to_spec(),
            "answer_select": value.answer_select,
            "answer_review": value.answer_review,
            "metadata_select": value.metadata_select,
            "metadata_review": value.metadata_review,
        } for name, value in result.thresholds],
        "thresholds_note": thresholds_note,
        "test_not_found": statuses.get(keys_module.TEST_NOT_FOUND, 0),
        "test_not_allowed": statuses.get(keys_module.TEST_NOT_ALLOWED, 0),
        "level_needed": statuses.get(keys_module.LEVEL_NEEDED, 0),
    }


def _write_upload(folder: pathlib.Path, name: str, data: bytes
                  ) -> pathlib.Path:
    safe = pathlib.Path(name).name or "upload"
    path = folder / safe
    path.write_bytes(data)
    return path


def build_app():
    """Construct the FastAPI app.

    A plain function, not buried inside the Modal decorator, so the whole
    service can be exercised locally with fastapi.testclient - see
    test/test_server.py - without deploying anything.
    """
    from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, Response

    sys.path.insert(0, "/root")

    from src import answer_key as keys_module
    from src import console as console_module
    from src import pipeline
    from src import review as review_module
    from src import sheet_generation
    from src import sheet_layout
    from src import thresholds as th

    web = FastAPI(title="UHS JCL grading", docs_url=None, redoc_url=None)
    web.add_middleware(
        CORSMiddleware,
        # The site is static and sends no cookies, so the passphrase header is
        # the only thing standing between the endpoint and the open internet.
        allow_origins=["*"],
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["*"],
        max_age=86400,
    )

    def check(request: Request):
        expected = _passphrase()
        if not expected:
            raise HTTPException(
                500, "The server has no passphrase configured. Run: modal "
                     "secret create grading-passphrase GRADING_PASSPHRASE=...")
        given = request.headers.get("x-grading-key", "")
        if given != expected:
            raise HTTPException(401, "Wrong passphrase.")

    def read_text_config(raw: tp.Optional[str]) -> sheet_layout.SheetText:
        if not raw:
            return sheet_layout.SheetText()
        try:
            return sheet_layout.SheetText.from_dict(json.loads(raw))
        except json.JSONDecodeError:
            raise HTTPException(400, "The layout field is not valid JSON.")
        except sheet_layout.SheetTextError as error:
            raise HTTPException(400, str(error))

    def guard_size(uploads: tp.Sequence[bytes]):
        total = sum(len(item) for item in uploads)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"That upload is {total / 1048576:.0f} MB, and the limit is "
                f"{MAX_UPLOAD_BYTES // 1048576} MB. Scan in smaller batches - "
                f"about {SUGGESTED_SHEETS_PER_BATCH} sheets each - and grade "
                "them one at a time. Every batch gets its own results, and "
                "the site will keep them side by side.")

    # --- what the site needs to know before it starts ---

    @web.get("/health")
    def health(request: Request):
        check(request)
        defaults = sheet_layout.SheetText()
        return {
            "ok": True,
            "questions_per_test": sheet_layout.QUESTIONS_PER_TEST,
            "tests_per_sheet": sheet_layout.TESTS_PER_SHEET,
            "pages_per_sheet": sheet_layout.PAGES_PER_SHEET,
            "student_id_digits": sheet_layout.STUDENT_ID_DIGITS,
            "test_id_digits": sheet_layout.TEST_ID_DIGITS,
            "options": sheet_layout.OPTIONS,
            "latin_level_count": sheet_layout.LATIN_LEVEL_COUNT,
            "defaults": defaults.to_dict(),
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "suggested_sheets_per_batch": SUGGESTED_SHEETS_PER_BATCH,
            "key_rows": list(keys_module.REQUIRED_ROWS),
        }

    # --- the printable sheet ---

    @web.post("/sheet")
    async def sheet(request: Request):
        check(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            text = sheet_layout.SheetText.from_dict(body.get("layout", body))
        except sheet_layout.SheetTextError as error:
            raise HTTPException(400, str(error))

        folder = pathlib.Path(tempfile.mkdtemp(prefix="sheet-"))
        try:
            path = sheet_generation.render(folder / "Answer Sheet.pdf", text)
            data = path.read_bytes()
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition":
                'attachment; filename="Answer Sheet.pdf"'
            })

    # --- a blank answer key ---

    @web.get("/key-template")
    def key_template(request: Request):
        check(request)
        folder = pathlib.Path(tempfile.mkdtemp(prefix="key-"))
        try:
            path = keys_module.write_template(folder / "Keys.csv")
            body = path.read_text(encoding="utf-8")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return Response(content=body, media_type="text/csv")

    # --- grading one batch ---

    @web.post("/grade")
    async def grade(request: Request,
                    scans: tp.List[UploadFile],
                    key: tp.Optional[UploadFile] = None,
                    overrides: tp.Optional[tp.List[UploadFile]] = None,
                    batch: str = Form(""),
                    threshold: str = Form(""),
                    annotate: str = Form("false"),
                    tests: str = Form(""),
                    layout: str = Form("")):
        check(request)
        text = read_text_config(layout)

        payloads = [await upload.read() for upload in scans]
        guard_size(payloads)

        work = pathlib.Path(tempfile.mkdtemp(prefix="grade-"))
        try:
            scans_root = work / "Scans"
            batch_folder = pipeline.resolve_batch_folder(
                scans_root, batch or None)
            batch_folder.mkdir(parents=True, exist_ok=True)
            for upload, payload in zip(scans, payloads):
                _write_upload(batch_folder, upload.filename or "scan.pdf",
                              payload)

            key_path = None
            if key is not None:
                key_path = _write_upload(work, "Keys.csv", await key.read())

            override_paths = []
            for upload in (overrides or []):
                override_paths.append(
                    _write_upload(work, upload.filename or "Review.csv",
                                  await upload.read()))

            options = pipeline.RunOptions(
                input_folder=scans_root,
                output_folder=work / "Results",
                batch=batch or None,
                key_file=key_path,
                override_files=tuple(override_paths),
                threshold_spec=threshold or None,
                annotate=annotate.lower() in ("1", "true", "yes", "on"),
                tests=pipeline.parse_tests(
                    tests, sheet_layout.TESTS_PER_SHEET),
                sheet_text=text)

            transcript = console_module.Console(enabled=False)
            try:
                result = pipeline.run(options, transcript)
            except (pipeline.BreakingError, keys_module.AnswerKeyError,
                    review_module.OverrideError,
                    review_module.NotFinishedError,
                    th.ThresholdError) as error:
                return JSONResponse(status_code=422,
                                    content={"error": str(error)})

            output = pipeline.resolve_batch_folder(work / "Results",
                                                   batch or None)
            note = ("supplied" if result.supplied_thresholds
                    else "calibrated")
            return {
                "summary": _summarise(result, note),
                "files": _collect_outputs(output),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # --- re-scoring, with no image processing at all ---

    @web.post("/regrade")
    async def regrade(request: Request,
                      results: UploadFile,
                      key: tp.Optional[UploadFile] = None,
                      overrides: tp.Optional[tp.List[UploadFile]] = None,
                      layout: str = Form("")):
        check(request)
        text = read_text_config(layout)

        work = pathlib.Path(tempfile.mkdtemp(prefix="regrade-"))
        try:
            results_path = _write_upload(work, "Results.csv",
                                         await results.read())
            override_paths = []
            for upload in (overrides or []):
                override_paths.append(
                    _write_upload(work, upload.filename or "Review.csv",
                                  await upload.read()))

            try:
                rows = pipeline.read_results(results_path)
                applied = 0
                if override_paths:
                    corrections = review_module.load(override_paths)
                    applied = pipeline.apply_overrides(rows, corrections)
                keys = None
                if key is not None:
                    key_path = _write_upload(work, "Keys.csv",
                                             await key.read())
                    keys = keys_module.load(
                        key_path, sheet_layout.QUESTIONS_PER_TEST,
                        latin_levels=text.latin_levels)
                pipeline.score_rows(rows, keys)
            except (pipeline.BreakingError, keys_module.AnswerKeyError,
                    review_module.OverrideError,
                    review_module.NotFinishedError) as error:
                return JSONResponse(status_code=422,
                                    content={"error": str(error)})

            output = work / "Results"
            output.mkdir(parents=True, exist_ok=True)
            questions = len(rows[0].marked) if rows else \
                sheet_layout.QUESTIONS_PER_TEST
            pipeline.write_results(output / pipeline.RESULTS_FILENAME, rows,
                                   questions)
            pipeline.write_question_stats(output / pipeline.STATS_FILENAME,
                                          rows, keys, questions)

            statuses: tp.Dict[str, int] = {}
            for row in rows:
                if row.status:
                    statuses[row.status] = statuses.get(row.status, 0) + 1
            return {
                "summary": {
                    "rows": len(rows),
                    "scored": len([r for r in rows if r.points is not None]),
                    "corrections_applied": applied,
                    "statuses": statuses,
                    "unclear": 0,
                    "missing": 0,
                },
                "files": _collect_outputs(output),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return web


if modal is not None:

    @app.function(image=image,
                  timeout=REQUEST_TIMEOUT_SECONDS,
                  cpu=2.0,
                  memory=4096,
                  secrets=[modal.Secret.from_name("grading-passphrase")])
    @modal.asgi_app()
    def api():
        sys.path.insert(0, "/root")
        return build_app()
