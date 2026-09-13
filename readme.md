## ![OpenMCR](src/assets/wordmark.png)

### _Free and Open-Source Multiple Choice Exam Reader_

### CAJCL State Convention edition

> **Warning**
> As per the license of this software, no warranty is implied. Given that
> students' grades are at stake, please audit the results — particularly with
> low-quality scans. The `--annotate` option exists to make that audit quick.

This is a fork of [OpenMCR](https://github.com/iansan5653/open-mcr), customized
for the **California Junior Classical League State Convention**, where each
student sits three eighty-question tests recorded on a single double-sided
answer sheet.

---

## Contents

- [What this fork adds](#what-this-fork-adds)
- [The CAJCL answer sheet](#the-cajcl-answer-sheet)
- [Answer keys](#answer-keys)
- [Installing](#installing)
- [**Grading from the command line, step by step**](#grading-from-the-command-line-step-by-step)
- [Thresholds](#thresholds)
- [Command reference](#command-reference)
- [**Grading in a browser**](#grading-in-a-browser)
- [How the pieces fit together](#how-the-pieces-fit-together)
- [Background and license](#background-and-license)

---

## What this fork adds

| | |
|---|---|
| **A two-page CAJCL sheet** | Three 80-question tests (240 questions) on one double-sided sheet, set in Times, with the CAJCL emblem in grayscale at the foot of the front page. |
| **Five-digit Student ID** | Bubbled in the same five columns on **both** sides, so a back page that gets separated can still be matched to its front. |
| **Latin level as bubbles** | MS-1, MS-2, MS-3, HS-1, HS-2, HS-3, HS-Adv. Renaming them is free; the count is a code change (see below). |
| **Directions on the sheet** | Printed on the front page. |
| **Front/back markers** | A machine-readable page-code bubble and a solid collation bar along the bottom - left on the front, right on the back. |
| **Batch folders** | `--batch 3` reads `<input>/Batch 3` and writes `<output>/Batch 3`, and names the review sheets after the batch. |
| **Batch splitting** | Scan ten students as one twenty-page PDF; it is split into ten sheets automatically, and mis-collated pages stop the run. |
| **Calibrated thresholds** | The filled/blank cutoff is measured from the scans themselves, once per PDF, so a different scanner needs no retuning. Printed to `Calibration.txt` and overridable with `--threshold`. |
| **A plain cutoff, so `AB` is readable** | A bubble is filled when it is dark enough - never "the darkest of the five" - so a student who means A *and* B is read as `AB`. |
| **Answer keys as a CSV** | One row per field, one column per test: a Test ID, the Latin levels allowed to take it, and 80 answers. Two tests may share an ID when their levels do not overlap. |
| **Review as a spreadsheet** | Anything too faint to call goes to `Unclear.csv` with a checkbox per option; anything required but blank goes to `Missing.csv`. Correct them in Google Sheets and feed them back. |
| **Regrade without rescanning** | `--regrade` re-scores an existing `Results.csv` against a corrected key or corrected review sheets. |
| **Question statistics** | `Question Stats.csv` gives per-question counts and percent correct. |
| **Marked-up PDFs** | `--annotate` rings every bubble the reader acted on, answers and metadata alike. |
| **Grade some tests, not all** | `--tests 2` reads the second test on the sheet and ignores the rest - no results, no key needed, nothing on the review sheets. |
| **A website, for everyone else** | The same pipeline behind a hosted endpoint, driven by a one-page site that walks an operator through all seven steps. Nothing is stored on the server. |

---

## The CAJCL answer sheet

Print it from **[`src/assets/cajcl_answer_sheet.pdf`](src/assets/cajcl_answer_sheet.pdf)**,
double-sided (*duplex*), at **100% scale**. Long-edge binding is the usual
choice; short-edge works too, because the reader corrects a page that arrives
upside down. "Fit to page" is acceptable if a printer insists on it — it reads
correctly, it just makes the bubbles smaller. Do not print two pages to a
sheet.

**Front page (page 1 of 2)**

```
        CALIFORNIA JUNIOR CLASSICAL LEAGUE
                  PAGE ( 1 ) ( 2 )      <- page-code bubbles, "1" printed solid

  LATIN LEVEL         STUDENT ID             TEST 1 ID
   o MS-1            0 0 0 0 0               0 0 0 0
   o MS-2            1 1 1 1 1               1 1 1 1
   o MS-3              ...                     ...
   o HS-1
   o HS-2                                    1  A B C D E   41  A B C D E
   o HS-3                                    2  A B C D E   42  A B C D E
   o HS-Adv                                     ...             ...
                                            40  A B C D E   80  A B C D E
  First Name  ______________
  Last Name   ______________
  School      ______________

  DIRECTIONS
   1. ...

  [ CAJCL emblem, grayscale ]

  ▪   ████████     FRONT — page 1 of 2                            ▪
  ^   ^                                                           ^
  |   collation bar, LEFT on the front (RIGHT on the back)   corner marks
  corner mark
```

**Back page (page 2 of 2)** carries `TEST 2 ID`, the **same** `STUDENT ID` block in the same place, `TEST 3 ID`, and the answer columns for tests 2 and 3.

To regenerate the PDF after changing the layout:

```sh
python -m src.sheet_generation src/assets/cajcl_answer_sheet.pdf
```

### Answer keys

Keys live in a CSV that staff maintain by hand - there is no longer
a special "all nines" key sheet to scan. Start from the template:

```sh
python -m src.main --key-template "Keys.csv"
```

The file runs **down** the page: the first column names the field, and each
further column is one test. With eighty questions that is far easier to edit
than eighty columns across.

```csv
Name,Latin Literature,Reading Comp (lower),Reading Comp (upper)
Test ID,1001,1002,1002
Allowed,,"MS-1, MS-2, MS-3","HS-1, HS-2, HS-3, HS-Adv"
1,A,D,B
2,C,A,B
...
80,E,B,C
```

| Row | Meaning |
|---|---|
| `Name` | What the test is called, for the reports. Free text. |
| `Test ID` | The 4-digit number students bubble. |
| `Allowed` | Latin levels that may take this test, comma-separated. Blank means every level. |
| `1` ... `80` | The correct answer to each question. |

### One test, two keys

Two columns may share a Test ID when the levels they accept do not overlap, as
`1002` does above. That is how one printed test can be marked against a
different key for, say, the middle school and high school entries: the Latin
level the student bubbled decides which key they are scored against.

The levels have to be disjoint, so that every student matches exactly one key —
two columns that both accept HS-1, or a column with a blank `Allowed` beside
any other column with the same ID, stop the run. If a Test ID has several keys
and a student's Latin level cannot be read, that row is marked `LATIN LEVEL NEEDED` rather than guessed at; filling the level in on the Missing sheet and
re-scoring resolves it.

An answer cell says what a **correct sheet looks like**:

| Cell | Means |
|---|---|
| `B` | The student filled B and nothing else. |
| `ABD` | The student filled **all three** of A, B and D. |
| `A|BD` | **Either** is accepted: A alone, or B and D together. |
| `X` | The question is not scored at all. |
| *(blank)* | Also not scored, but see below. |

So several letters together are one answer requiring all of them, and `|`
separates alternatives.

`X` **excludes a question from scoring** — use it when you find out after the
exam that a question should not count. It is right for nobody and wrong for
nobody, it drops out of every total and out of `Question Stats.csv`, and
nothing has to be renumbered. A blank cell does the same, but `X` says so on
purpose, where a blank could just as easily be a row somebody forgot to fill
in.

Letters are not case sensitive, and order within an answer does not matter:
`abd`, `ABD` and `DBA` are one and the same. More than two alternatives are
fine (`A|B|CD`). A repeated letter (`AA`), a stray `|`, or a letter outside
A–E stops the run.

Two things about a key are *breaking* - they stop the run before anything is
written, because grading would otherwise be meaningless:

- two columns sharing a Test ID;
- an answer letter outside A-E, a stray `|`, or an unrecognized Latin level.

Two things are **not** breaking, and are recorded per row in `Results.csv`
instead:

- `TEST NOT FOUND` - the student bubbled a Test ID that is not in the key;
- `TEST NOT ALLOWED` - no key for that Test ID accepts the student's Latin
  level;
- `LATIN LEVEL NEEDED` - that Test ID has one key per level and the student's
  level could not be read, so there is no way to say which applies.

## Installing

You need **Python 3.10 or newer**. The project is developed and tested against
**3.12 and 3.13**. Check what you have:

```sh
python --version
```

If that errors or prints something older, install Python from
[python.org](https://www.python.org/downloads/). On Windows, tick
**"Add Python to PATH"** during installation.

> On macOS and Linux the command is often `python3` rather than `python`. If
> `python` does not work, use `python3` everywhere below.

### Windows (PowerShell)

```powershell
cd C:\dev\open-mcr
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Confirm it worked:

```powershell
python --version      # expect 3.13.x
python -m pytest -q
```

A few things that sometimes trip people up:

- **`py -3.13` rather than bare `py`.** Bare `py` selects the highest version
  installed, which is usually what you want but silently changes the day you
  install a newer Python. Naming the version keeps the environment predictable.

- **If `Activate.ps1` is refused** with *"running scripts is disabled on this
  system"*, your execution policy is `Restricted`. Allow scripts for the
  current window only:

  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  ```

  That expires when you close the window. The Windows default for a user
  account is `RemoteSigned`, which already permits a locally-created
  `Activate.ps1`, so you may never see this. In `cmd.exe`, use
  `.venv\Scripts\activate.bat` instead.

- **Keep the checkout out of a synced folder.** A `.venv` is several hundred
  megabytes of binaries, and OneDrive, Dropbox or iCloud will happily sync all
  of it and hold files open while pip is writing them. `.gitignore` covers
  `.venv` for Git, but a sync client does not read `.gitignore`. Somewhere
  like `C:\dev\open-mcr` avoids the whole question.

### macOS and Linux

```sh
git clone https://github.com/iansan5653/open-mcr.git
cd open-mcr
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If you do not have Git, download the source ZIP from the repository page,
extract it, and `cd` into the extracted folder instead.

> **Linux**: if you see errors about `opencv` or `tkinter`, run
> `sudo apt-get install python3-tk libgl1 libglib2.0-0` and try again.
>
> **macOS**: nothing extra is needed; the tool is command-line only.

### A note on `python -m`

Always run this project with `python -m` — `python -m src.main`,
`python -m pytest`. The modules under `src/` import each other relatively, so
running a file directly (`python src/main.py`) fails with *"attempted relative
import with no known parent package"*. Using `-m` also puts the project folder
on the import path, which is how `pytest` finds `src`.

---

## Grading from the command line, step by step

This section assumes you have never done this before. Follow it in order.

### Step 1 — Open a terminal in the project folder

- **Windows**: open the `open-mcr` folder in File Explorer, click the address bar, type `cmd`, and press <kbd>Enter</kbd>.
- **macOS**: open Terminal, type `cd ` (with a trailing space), drag the `open-mcr` folder onto the window, and press <kbd>Enter</kbd>.
- **Linux**: right-click the folder and choose "Open in Terminal".

Confirm you are in the right place — this should list `src`, `test`, and `readme.md`:

```sh
ls          # Windows: dir
```

### Step 2 — Lay out the folders

Everything is organized by **batch**. A batch is one stack of sheets you
scanned together; giving each one a number keeps its scans, its results and
its review sheets apart from every other batch's.

```
Grading/
  Scans/
    Batch 1/            <- the PDFs from the scanner
      batch.pdf
    Batch 2/
      morning.pdf
      afternoon.pdf
  Results/              <- created for you
  Keys.csv              <- the answer key; NOT inside a batch folder
```

The key sits outside the batch folders because one key serves every batch.

### Step 3 — Scan the sheets

Scan each sheet as **two consecutive pages: front first, back second**. Do not
scan all the fronts first and all the backs afterwards.

**Required:**

- **Page order.** Every front must be followed by its own back. The run stops
  if the pages do not alternate, rather than grade the wrong back against the
  wrong front.
- **Blank page removal off.** It will silently drop a lightly-marked page and
  throw the whole batch out of order.
- **Do not crop into the corner marks.** The reader recovers the grid from
  them, so an aggressive auto-crop can make a page unreadable.

**Recommended:**

- **Resolution**: 200–300 dpi. Higher is slower with no benefit.
- **Color**: black and white, or grayscale. Color also works; grayscale keeps
  the files small.
- **Output**: one PDF per batch is easiest — ten students becomes one
  twenty-page PDF.
- **Deskew and auto-rotate** may be left on or off. The reader straightens the
  page itself, and reads a page that arrives at any quarter turn, so neither
  setting will break a batch.

Put the resulting file(s) in `Scans/Batch 1`. Several files per batch is fine;
they are read in filename order, and each must hold whole sheets.

### Step 4 — Run the grader

```sh
python -m src.main "Grading/Scans" "Grading/Results" --batch 1 --key "Grading/Keys.csv" --annotate
```

- `python -m src.main` — **not** `python src/main.py`.
- `--batch 1` reads `Grading/Scans/Batch 1` and writes
  `Grading/Results/Batch 1`.
- `--key` is optional. Without it the sheets are read but not scored.
- `--annotate` is optional and roughly doubles the run time.

What you will see:

```
Found 1 file: 'batch.pdf'.
  ████████████████████████ 20/20  Processing 'batch.pdf'.
  ████████████████████████ 20/20  Annotating 'batch.pdf'.
Automatic thresholding used.
  Calibrated thresholds saved to Grading/Results/Batch 1/Calibration.txt.
All exams processed and saved to Grading/Results/Batch 1.
  1 marked-up PDF saved to 'Annotated'.
6 marks need manual review. See 'Batch 1 — Unclear.csv' and 'Batch 1 — Missing.csv'.
  Regrade with overrides via: python -m src.main --regrade "..." --overrides "..." --key "..."
```

It is deliberately quiet. A faint mark or an unknown Test ID is recorded in
the output files, not printed. Only a **breaking** problem — one that means no
output should be produced at all — interrupts, and then nothing is written:

- the key file has two tests with the same Test ID, or a bad answer letter;
- the batch is not a clean run of front/back pairs;
- a page's corner marks could not be found at all;
- `--threshold` could not be understood.

### Step 5 — Read the results

`Grading/Results/Batch 1` now holds:

**`Results.csv`** — three rows per student, one per test:

| Batch | File | Page | Student ID | Latin Level | Test ID | Test Name | Status | Points | Out Of | Score (%) | 1 | 2 | … |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | batch.pdf | 1 | 04275 | HS-2 | 1001 | Latin Literature | | 78 | 80 | 97.50 | A | B | … |
| 1 | batch.pdf | 2 | 04275 | HS-2 | 1002 | Reading Comp 1 | TEST NOT ALLOWED | | | | D | AB | … |

- An answer cell holds the letters the student filled — `AB` if they filled
  two, empty if they filled none.
- A `?` inside a Student ID or Test ID marks a digit column that could not be
  read. It is deliberately not guessed at, because `0427` would look like a
  valid but wrong ID.
- `Status` is blank when all is well, or `NEEDS REVIEW`, `TEST NOT FOUND`, or
  `TEST NOT ALLOWED`.

**`Question Stats.csv`** — per question: how many answered it, how many left
it blank, how many chose each option, and the percent correct.

**`Calibration.txt`** — the cutoffs used, and the `--threshold` line to reuse
them.

**`Batch 1 — Unclear.csv`** and **`Batch 1 — Missing.csv`** — only when
something needs a person. See the next step.

**`Annotated/`** — with `--annotate`, a copy of every scan with every bubble
the reader acted on ringed: green for the key's answer, red for a wrong
choice, blue for a mark read with no key to judge it. Anything sent to review
has its **question number** ringed in amber rather than its five options,
which would say nothing about which bubble is the problem. The ID, Latin level
and Test ID bubbles are ringed too, so the whole reading is visible at a
glance.

### Step 6 — Correct anything the software would not guess at

Two things go to review, and only two:

- **Unclear** — a bubble dark enough to be a real attempt but too light to
  count. Neither selected nor ignored; a person decides.
- **Missing** — a *required* field that could not be read: the Student ID,
  the Latin level, a Test ID. **A blank question is not an error** — that is
  the student's choice, and it is simply recorded as blank.

`Batch 1 — Unclear.csv` has one row per unclear mark and a column per
option, with the machine's reading pre-ticked:

```csv
Batch,File,Page,Student ID,Test ID,Question,A,B,C,D,E,Done
1,batch.pdf,3,00031,1001,7,FALSE,TRUE,FALSE,FALSE,FALSE,FALSE
```

`Batch 1 — Missing.csv` asks for a whole field, not one bubble. Type the
complete value into `Value`. A field that failed on both sides of a sheet is
**one** row, with `Page` reading `1,2`:

```csv
Batch,File,Page,Student ID,Field,Value,Done
1,batch.pdf,"1,2",,Student ID,,FALSE
1,batch.pdf,1,04275,Latin level,,FALSE
1,batch.pdf,2,04275,Test 3 ID,,FALSE
```

**Every row must have `Done` ticked** before it is accepted. An unticked row
means nobody has looked at it, and quietly folding in the machine's own guess
would defeat the point of asking — so the run stops and names the rows.

#### Working on it in Google Sheets

A plain CSV has no checkboxes, so this repository ships an Apps Script that
adds them on import: **[`tools/Sheet.gs`](tools/Sheet.gs)**.

1. Make a Google Sheet you will reuse for every batch.
2. **Extensions → Apps Script**, paste in `tools/Sheet.gs`, Save.
3. Click the **Triggers** (clock) icon → **Add Trigger**:
   function `onSpreadsheetChange`, source *From spreadsheet*, type *On change*.
   Save and accept the authorisation prompt.
4. **File → Import → Insert new sheet(s)** and pick a review CSV. It must
   create a **new sheet** — "Replace current sheet" does not fire the trigger.

The script then turns the option columns into real checkboxes, restores the
leading zeros that a CSV import strips from IDs, fits every column to its
contents, sets the whole sheet in Inconsolata, deletes the thousand empty
padding rows the import leaves behind, freezes the header, offers a dropdown
of Latin levels on the Missing sheet, and grays out each row as `Done` is
ticked. If a sheet is ever missed, use **CAJCL → Tidy this review sheet**.

Several people can work in the sheet at once. When it is finished,
**File → Download → Comma-separated values** for each sheet.

#### Feeding the corrections back

```sh
python -m src.main --regrade "Grading/Results/Batch 1/Results.csv" --overrides "Grading/Results/Batch 1" --key "Grading/Keys.csv"
```

`--overrides` takes a **folder** as well as individual files, and finds the
review sheets in it. That is what the printed command uses, because the sheet
names contain an em dash that a legacy Windows console cannot print — a
copied path would otherwise arrive broken.

This re-scores from the saved results — **no image processing**, so it takes a
moment rather than minutes. The run prints the exact command for you, ready to
paste.

Rows are matched on file, page and question, never on the Student ID, because
a spreadsheet will silently turn `04275` into `4275`.

### Grading only some of the tests

The sheet carries three tests, and sometimes only one of them is yours. `--tests`
takes the numbers to read, counted from 1 across the whole sheet:

```sh
python -m src.main "Scans" "Results" --batch 1 --key "Keys.csv" --tests 2
```

A test left out is not read at all. It produces no rows in `Results.csv`, needs
no column in the key, and nothing about it reaches `Unclear.csv` or
`Missing.csv` - so an unreadable Test ID on a test you are not grading cannot
stop the run. `--tests 1,2,3` is the same as leaving the option off.

### Step 7 — Regrading after a key change

The same command handles a corrected key. If a question turns out to be
faulty, edit `Keys.csv` — widen the cell to `ABD` to accept alternatives, or
blank it to stop scoring that question — and run:

```sh
python -m src.main --regrade "Grading/Results/Batch 1/Results.csv" --key "Grading/Keys.csv"
```

Nothing is re-read from the scans, so this is safe to repeat as often as you
like.

### Thresholds

By default the filled/blank cutoff is measured from the scans themselves, once
per PDF, using Otsu's method applied to the *bubble darknesses* rather than to
the image's pixels. Two adjustments make that work here:

- Only a fraction of bubbles are filled, and textbook Otsu's class-size
  weighting drags the cutoff down into the blank cluster when the split is
  that lopsided. A Fisher-style ratio is maximised instead, which has no such
  weighting. On a test sheet, textbook Otsu put the cutoff at 0.12 with almost
  no headroom above the blanks; this puts it at 0.35, in the middle of the gap.
- Answer bubbles and metadata bubbles are calibrated separately.

`Calibration.txt` prints the numbers and the line to reuse them:

```
--threshold 0.3529,0.1943,0.3611,0.2058
```

The four numbers are *answer cutoff, answer review threshold, metadata cutoff,
metadata review threshold*. A bubble above the cutoff is filled; one between
the review threshold and the cutoff goes to `Unclear.csv`.

Every number is **darkness**, from 0 (untouched white paper) to 1 (solid
black). So a **higher number is stricter**: it demands a darker mark before
the bubble counts. Lowering a cutoff makes the reader more willing to call a
faint mark an answer.

You do not have to give all four. Anything left out is calibrated as usual:

```sh
--threshold 0.42               # both cutoffs; review thresholds calibrated
--threshold 0.42,0.36          # the two cutoffs
--threshold 0.42,0.19,,        # answer thresholds only; metadata calibrated
--threshold answer=0.42        # the same, by name
--threshold metadata-review=0.05,answer=0.42
```

---

## Command reference

```
python -m src.main <input_folder> <output_folder> [options]
python -m src.main --regrade <Results.csv> [--overrides ...] [--key ...]
python -m src.main --key-template <Keys.csv>
```

| Option | Effect |
|---|---|
| `--batch N` | Read `<input>/Batch N`, write `<output>/Batch N`, and name the review sheets after the batch. |
| `--key FILE.csv` | Score against this key. Without it, sheets are read but not scored. |
| `--overrides FILE.csv ...` | Corrected review sheets to fold in before scoring. A folder may be given instead, and the sheets in it are found. |
| `--regrade Results.csv` | Re-score an existing results file without touching the scans. |
| `--threshold SPEC` | Use these cutoffs instead of calibrating. Four numbers, two, one, blanks for "calibrate this one", or names such as `answer=0.42`. |
| `--annotate` | Also write marked-up copies of every scan. |
| `--tests N[,N...]` | Grade only these tests, numbered from 1 across the sheet. Default: all of them. |
| `--key-template FILE.csv` | Write a blank answer key and exit. |
| `-d`, `--debug` | Re-raise unexpected errors with a traceback. |

Exit status is `0` when the run finished (whether or not marks need review)
and `1` for a breaking problem, in which case nothing was written.

---

## Grading in a browser

Everything above needs a terminal and a checkout. For everyone else there is a
website instead: one page, seven steps, no install. It talks to a small service
that runs this same pipeline.

**<https://grade.uhsjcl.org>** is the UHS JCL deployment. It is useless without
a server address and a passphrase, so it is safe to share the link.

### What is stored where

**Nothing is stored on the server.** Each request unpacks its uploads into a
temporary directory, runs the pipeline, reads the results back into memory, and
deletes the directory before it replies. There is no database, no volume, no
object store and no job queue. A scan that has been graded is gone from the
server the moment the response is sent, and a scan that was never graded — a
request that failed halfway — is deleted just the same, because the cleanup runs
in a `finally` block.

That guarantee is also why there is no "submit now, collect later" job API: a
result that outlives its request has to be kept somewhere. Each request grades
one batch and returns it, start to finish.

The browser keeps more than the server does. Your test names, Test IDs, answer
keys, thresholds and the `Results.csv` of every batch you have graded live in
that browser's `localStorage`, so you can close the page and come back to it
later. Scans are never stored, on either side: they go from the file
input to the server and the results come back in the same response. Marked-up
PDFs are offered as a download and then dropped. Use **Reset** at the foot of the
page to clear it all.

### Deploying the service

The service is a FastAPI app in `server/grading_api.py`, deployed on
[Modal](https://modal.com). It is gated by a shared passphrase, which the
website sends as an `X-Grading-Key` header.

**First time:**

```sh
pip install modal
modal setup

# Pick something long. Everyone who grades will need it.
modal secret create grading-passphrase GRADING_PASSPHRASE=<a long passphrase>

modal deploy server/grading_api.py
```

Modal prints the endpoint URL. That URL and the passphrase are the two things an
operator types into step 1.

**Every time after that** — whenever anything under `src/` or `server/` changes,
including the sheet layout, the key rules or the thresholds:

```sh
modal deploy server/grading_api.py
```

That is the whole redeploy. It replaces the running app in place, so the
endpoint URL does not change and nobody has to be told anything. `src/` is
copied into the image at deploy time, so a code change that is not redeployed
is simply not live. The website is a separate deploy: it goes out by itself on
push, and needs nothing from you.

To rotate the passphrase, create the secret again with a new value and
redeploy. Anyone still on the old one gets *"The server did not accept that
passphrase."*

### Deploying the website

The site is `site/` — one HTML file, one JavaScript file, no build step and no
dependencies. Any static host will serve it. `.github/workflows/deploy_site.yml`
publishes it to GitHub Pages on every push that touches `site/`, and copies
`tools/Sheet.gs` in alongside it so step 7 can link to the Apps Script.

For a custom domain such as `grade.uhsjcl.org`, set a repository variable named
`SITE_DOMAIN` to that host and point a `CNAME` record at `<owner>.github.io`.
Without the variable it publishes at the default github.io address.

The service allows requests from any origin, so the site can live anywhere,
including a file opened from disk. The passphrase is what limits access, not the
domain.

### The seven steps

| Step | What it does |
|---|---|
| **1. Connect** | Endpoint and passphrase, or an invite link that carries both (see below). The page asks the server how the sheet is laid out — how many tests, questions, Student ID digits and Latin levels — so the rest of the form matches the real sheet rather than a hardcoded copy of it. |
| **2. Design and print the answer sheet** | The title, the directions, the seven Latin level names and the write-in labels, as text boxes. Generates the printable PDF. Wording only: the grid never moves, so a sheet printed from the site reads exactly like one printed from the command line. |
| **3. List the tests** | A row per test — name, Test ID, and which Latin levels may take it. Test IDs are zero-padded to four digits when you leave the box, and two tests may share an ID when their levels do not overlap. |
| **4. Enter the answers** | Three ways into the same data, and you can mix them: download the template and fill it in a spreadsheet, paste a whole test's answers at once, or type into the grid of every question. `Keys.csv` is offered back whenever it holds work that is not already in a file you have. Uploading merges by Test ID rather than replacing, and there is an Undo. |
| **5. Thresholds** | Automatic per batch. Tick the override to pin the four numbers, individually or together, exactly as `--threshold` does. |
| **6. Scan and grade** | Which tests to grade, the scanner settings, then one card per batch. Batches are independent, so a second one can be added at any time and the first one's results stay put. A graded batch becomes read-only: it is the record of a run that happened, against the key and thresholds of the moment. |
| **7. Fix unclear marks** | `Unclear.csv` and `Missing.csv` per batch, the upload that feeds the corrections back and re-scores without the scans, and the optional Apps Script for people who would rather work in Google Sheets. |

### Batch size

One upload may be at most 40 MB, and a request may run for ten minutes. A batch
of 25 sheets — 50 pages — is the size the page suggests: it lands well inside
both.

**Split large jobs at the scanner, not in the browser.** If the convention is
300 sheets, scan twelve PDFs of 25 rather than one of 300, and add twelve
batches on the page. Each gets its own results, its own review sheets and its
own calibration, and the page keeps them side by side. Splitting a 600-page PDF
in JavaScript would mean holding it all in memory in a browser tab, which is a
worse place for it than the scanner's own document feeder.

### Invite links

**Copy an invite link** in step 1 builds a URL that fills the server address
and passphrase in for whoever opens it. The details ride in the URL *fragment*,
which browsers never send to a web server, and the page wipes it from the
address bar as soon as it has read it - so it does not linger in the guest's
history.

That is the only protection it has. **The passphrase is in the link**: anyone
holding it can grade. Send it however you would send the passphrase itself, and
never anywhere public. To cut off an old link, rotate the passphrase.

### Endpoints

If you would rather script against the service than use the page:

| | |
|---|---|
| `GET /health` | Sheet facts, default wording, and the limits. Also the cheapest way to check a passphrase. |
| `POST /sheet` | Layout JSON in, answer-sheet PDF out. |
| `GET /key-template` | The blank `Keys.csv`. |
| `POST /grade` | Multipart: `scans[]`, `key`, `overrides[]`, `batch`, `threshold`, `annotate`, `layout`. Returns a summary and every output file. |
| `POST /regrade` | An existing `Results.csv` plus a corrected key or corrected review sheets. No image processing. |

Every one of them takes the `X-Grading-Key` header.

---

## How the pieces fit together

If you need to change the sheet, these are the files that matter:

| File | Responsibility |
|---|---|
| `src/sheet_layout.py` | **The single source of truth for the layout** — page size, grid, and the row and column of every block. |
| `src/sheet_generation.py` | Draws the printable PDF from those constants. |
| `src/grid_info.py` | Describes the same layout to the reader, also derived from `sheet_layout`. |
| `src/corner_finding.py` | Locates the four registration marks and recovers the grid. |
| `src/grid_reading.py` | Measures how dark every bubble is. |
| `src/reading.py` | Turns one page into bubble groups, and applies the cutoff. |
| `src/thresholds.py` | Calibrates the cutoffs, and parses `--threshold`. |
| `src/batching.py` | Splits a batch into sheets and enforces page order. |
| `src/answer_key.py` | Loads and validates the key CSV. |
| `src/review.py` | Writes the review sheets and reads corrections back. |
| `src/pipeline.py` | Runs the whole thing and writes the output files. |
| `src/annotation.py` | Draws the marked-up PDFs. |
| `src/console.py` | The progress output. |
| `tools/Sheet.gs` | Google Apps Script that formats an imported review CSV. |
| `server/grading_api.py` | The hosted service — the same pipeline behind an HTTP endpoint. |
| `site/` | The one-page website that drives it. |

Because the generator and the reader both derive from `sheet_layout.py`, moving a block is a one-line change in one file — and `test/test_cajcl.py` fails if the two ever disagree.

**Renaming a Latin level is free; changing how many there are is a code
change.** `sheet_layout.LATIN_LEVELS` is the one list: the generator prints a
bubble per entry and `grid_info` reads a bubble per entry, so editing that
tuple moves both together. The block grows downward from
`LATIN_LEVEL_FIRST_ROW` and the write-in lines start at row 21.6, so up to
about 14 levels fit before anything else has to move. Everything beyond the
count — the names — can be set per run, from `--layout` or from the website,
and the server refuses a list of the wrong length rather than printing a sheet
its own reader cannot parse.

**Keep every bubble at the same outline weight.** The reader measures a disc
slightly smaller than the printed circle, so a heavier ring spills into that
disc and makes an *empty* bubble read darker. At 200 dpi a blank bubble reads
0.037 at `sheet_generation.BUBBLE_LINE_WIDTH` and 0.145 at 1.0pt — so mixing
the two splits the blank cluster in two and drags the calibrated review
threshold up with it. `_bubble` sets the weight explicitly for exactly this
reason; anything else that strokes a line should save and restore the canvas
state rather than leave a new width behind.

**Do not change the four corner marks.** `corner_finding` recovers the top-left grid corner by projecting out from the L-mark, and it needs to know where that mark sits relative to the corner. That relationship is `sheet_layout.L_MARK_OFFSET_FRACTION`, passed through to `corner_finding.find_corner_marks`. If you move the L-mark without updating it, the grid skews and the left-hand side of the sheet reads as noise.

Run the tests with:

```sh
python -m pytest
```

`test/test_cajcl.py` builds real sheets with the real generator, bubbles them in, and reads them back, so it catches layout drift, mis-collation, and mis-scoring. `test/test_server.py` drives the hosted service the same way, through a real HTTP client, and checks that each request leaves nothing behind on disk.

---

## Background and license

Commercially available OMR (optical mark recognition) exam sheets, scanners, and processing software can cost educators and educational institutions thousands of dollars per year. In response to this, OpenMCR was developed as a free and easy-to-use alternative.

The original software and multiple choice sheet were developed as an independent study project by Ian Sanders, a mechanical engineering student at the University of South Florida, under the direction of Dr. Autar Kaw. For a detailed discussion of the algorithm, [read the report](https://github.com/iansan5653/open-mcr-report/releases/tag/1.0.0) submitted for that course.

The command line now grades the CAJCL sheet only. The legacy 75- and
150-question layouts are still described in `grid_info.py` and the pipeline
can be pointed at them from Python, but they are no longer offered as a
`--variant`: the key CSV, the Latin-level exclusions, the batch folders and
the page-code checks are all specific to this sheet. Use
[upstream OpenMCR](https://github.com/iansan5653/open-mcr) for those sheets.

The other printable sheets remain available:

- [75 Question Variant](https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_75q.pdf)
- [150 Question Variant](https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_150q.pdf)

To report a bug or request a feature, [file an issue](https://github.com/iansan5653/open-mcr/issues/new).

### Software License

Copyright (C) 2019 Ian Sanders

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

For the full license text, see [license.txt](./license.txt).

### Multiple Choice Sheet License

The multiple choice sheets distributed with this software are licensed under the
Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International license
(CC BY-NC-SA 4.0). In summary, this means that you are free to distribute and
modify the documents so long as you share them under the same license, provide
attribution, and do not use them for commercial purposes. For the full license,
see [the Creative Commons website](https://creativecommons.org/licenses/by-nc-sa/4.0/).

**Note**: You are explicitly _allowed_ to distribute the multiple choice sheets
without attribution if using them unmodified for educational purposes and not in
any way implying that they are your own work. This is an exception to the
Creative Commons terms.

The CAJCL emblem (`src/assets/cajcl.png`) is the property of the California
Junior Classical League and is not covered by that license.
