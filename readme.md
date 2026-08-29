## ![OpenMCR](src/assets/wordmark.png)

### _Free and Open-Source Multiple Choice Exam Reader_

### CAJCL State Convention edition

[![Continuous Integration](https://github.com/iansan5653/open-mcr/actions/workflows/continuous_integration.yml/badge.svg)](https://github.com/iansan5653/open-mcr/actions/workflows/continuous_integration.yml)

> **Warning**
> As per the license of this software, no warranty is implied. The software is
> stable but there still may be bugs. Given that students' grades are at stake,
> please be sure to audit the results — particularly when working with
> low-quality scans. The `--annotate` option described below exists to make
> that audit quick.

This is a fork of [OpenMCR](https://github.com/iansan5653/open-mcr), customised
for the **California Junior Classical League State Convention**, where each
student sits three eighty-question tests recorded on a single double-sided
answer sheet.

---

## Contents

- [What this fork adds](#what-this-fork-adds)
- [The CAJCL answer sheet](#the-cajcl-answer-sheet)
- [Installing](#installing)
- [**Grading from the command line, step by step**](#grading-from-the-command-line-step-by-step)
- [Command reference](#command-reference)
- [Using the graphical interface instead](#using-the-graphical-interface-instead)
- [How the pieces fit together](#how-the-pieces-fit-together)
- [Background and license](#background-and-license)

---

## What this fork adds

| | |
|---|---|
| **A two-page CAJCL sheet** | Three 80-question tests (240 questions) on one double-sided sheet, set in Times to suit the classical theme, with the CAJCL emblem printed in grayscale at the foot of the front page. |
| **Four-digit Student ID** | Bubbled in the same four columns on **both** sides, so a back page that gets separated can still be matched to its front. |
| **Latin level as bubbles** | Seven options — MS-1, MS-2, MS-3, HS-1, HS-2, HS-3, HS-ADV — replacing the old write-in blank. |
| **Directions on the sheet** | Printed on the front page, so students do not need a separate instruction sheet. |
| **Front/back markers** | Each page carries a printed **page-code bubble** (machine-readable) and a **solid collation bar** along the bottom — left on the front, right on the back — so a printed stack can be checked by riffling it. |
| **Batch splitting** | Scan ten students as one twenty-page PDF; the reader splits it into ten sheets automatically. |
| **Page-order enforcement** | If the pages are not a clean sequence of front-then-back pairs, the run stops and says exactly which page is wrong. |
| **Unclear-mark detection** | Any mark too faint, too partly erased, or too doubled to call is reported in `review_required.csv` and the run exits non-zero, so those sheets can be graded by hand. |
| **Marked-up PDFs** | `--annotate` writes a copy of every scan with the correct answer ringed in green and any wrong choice in red. |

The legacy 75- and 150-question sheets still work exactly as before.

---

## The CAJCL answer sheet

Print it from **[`src/assets/cajcl_answer_sheet.pdf`](src/assets/cajcl_answer_sheet.pdf)**, double-sided, at **100% scale**.

"Fit to page" also works — the reader locates the four corner marks and builds
its grid from them, so a uniformly scaled page reads correctly (verified down
to 85%). 100% is still preferable, because scaling shrinks the bubbles and
throws away margin you may want on a poor scan. What does matter is that the
scaling be *uniform*: never print two pages up, and leave "auto-rotate" off.

**Front page (page 1 of 2)**

```
        CALIFORNIA JUNIOR CLASSICAL LEAGUE
                  PAGE ( 1 ) ( 2 )      <- page-code bubbles, "1" printed solid

  LATIN LEVEL        STUDENT ID              TEST 1 ID
   o MS-1            0 0 0 0                 0 0 0 0
   o MS-2            1 1 1 1                 1 1 1 1
   o MS-3              ...                     ...
   o HS-1
   o HS-2                                    1  A B C D E   41  A B C D E
   o HS-3                                    2  A B C D E   42  A B C D E
   o HS-ADV                                     ...             ...
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

**Back page (page 2 of 2)** carries `TEST 2 ID`, the **same** `STUDENT ID` block in the same place, `TEST 3 ID`, and the answer columns for tests 2 and 3. Its collation bar is on the **right**.

Nothing on the sheet is printed closer to a paper edge than the corner marks
are (0.42in). Those marks are the one thing that absolutely must come out of
the printer, so they set the sheet's real margin requirement. The footer and
the collation bar sit on the same line as the two bottom marks, their bottoms
flush with them, and the title is well inside the top pair. A printer that can
render the marks can render the whole sheet.

To regenerate the PDF after changing the layout:

```sh
python -m src.sheet_generation src/assets/cajcl_answer_sheet.pdf
```

### Answer keys

An answer key is just a normal sheet with **Student ID `9999`** bubbled in. Fill in each Test ID and the correct answers for all three tests. One key sheet produces three keys, one per Test ID. Leave the Latin level blank on a key.

Students are matched to keys by **Test ID**, so the Test ID a student bubbles must match the one on the key.

---

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
cd $HOME\OneDrive\Desktop\open-mcr
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

- **A virtual environment inside OneDrive gets synced.** `.venv` is several
  hundred megabytes of binaries that OneDrive has no reason to back up, and
  sync can hold a file open while pip is trying to write it. `.gitignore`
  covers `.venv` for Git, but OneDrive does not read `.gitignore`. If you hit
  slow installs or permission errors, exclude the folder in OneDrive's settings
  (*Settings → Sync and backup → Advanced settings → Excluded folders*), or
  keep the checkout somewhere outside OneDrive such as `C:\dev\open-mcr`.

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
> **macOS**: if the graphical interface shows a black screen, reinstall Python
> with Tkinter — the easiest route is Homebrew,
> [as described here](https://apple.stackexchange.com/a/315121).

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

### Step 2 — Make two folders

You need one folder holding the scans and one for the results. They must be *different* folders. For example, on your Desktop:

```sh
mkdir "C:\Users\you\Desktop\convention\scans"
mkdir "C:\Users\you\Desktop\convention\results"
```

On macOS or Linux:

```sh
mkdir -p ~/Desktop/convention/scans ~/Desktop/convention/results
```

### Step 3 — Scan the sheets

Scan **every sheet double-sided**, so each student produces two pages, **front page first**. Almost any scanner will do; these settings work well:

- **Colour**: black and white, or grayscale. Colour also works.
- **Resolution**: 200–300 dpi. Higher is slower with no benefit.
- **Output**: one PDF for the whole batch is easiest — ten students becomes one twenty-page PDF.
- **Turn off** "auto-rotate", "deskew", "auto-crop", and "blank page removal". Blank page removal in particular will silently drop a lightly-marked page and throw the whole batch out of order.

Put the answer key sheet in the same batch as the students; the software recognises it by its `9999` Student ID.

Save the resulting file(s) into your `scans` folder. The software reads `.pdf`, `.png`, `.jpg`, `.jpeg`, `.bmp`, `.tif`, and `.tiff`. You may put several files in the folder — they are read in filename order, and each file must contain whole sheets (an even number of pages).

### Step 4 — Run the grader

The command has this shape:

```
python -m src.main  <scans folder>  <results folder>  --variant cajcl
```

Filled in with the folders from Step 2 (all on one line):

```sh
python -m src.main "C:\Users\you\Desktop\convention\scans" "C:\Users\you\Desktop\convention\results" --variant cajcl
```

Note the details:

- `python -m src.main` — **not** `python src/main.py`. The `-m` form is required; the other one fails with an import error.
- `--variant cajcl` — **required** for the CAJCL sheet. Without it the software assumes the legacy 75-question sheet and reads nothing but nonsense.
- Quote any path containing a space.

While it runs you will see one line per page:

```
Found 10 sheet(s) across 20 page(s).
Processing 'batch.pdf (page 1)'.
Processing 'batch.pdf (page 2)'.
...
OK: all exams processed and saved.
OK: all keys processed and saved.
OK: all scored results processed and saved.
```

A twenty-page batch takes a few seconds.

### Step 5 — Read the results

Your results folder now holds CSV files, each named with the date and time it was produced (`2027-04-11_09-30-00__results.csv`). Open them in Excel, Numbers, or any text editor.

**`results.csv`** — what each student marked. **Three rows per student**, one per test:

| Student ID | Latin Level | Test Form Code | Source File | Q1 | Q2 | … |
|---|---|---|---|---|---|---|
| 0427 | HS-2 | 1001 | batch.pdf (page 1) | A | B | … |
| 0427 | HS-2 | 1002 | batch.pdf (page 2) (column 1) | C | C | … |
| 0427 | HS-2 | 1003 | batch.pdf (page 2) (column 2) | E | A | … |

- **Test Form Code** is the Test ID the student bubbled.
- A blank cell means the question was left unanswered.
- `[A|B]` means two bubbles were filled (use `--multiple` to record that as `F` instead).
- A `?` inside a Student ID or Test ID — like `04?7` — marks a digit column the reader could not make out. It is deliberately *not* silently dropped, because `047` would look like a valid but wrong ID.

**`keys.csv`** — the answer keys that were found, one row per Test ID.

**`scores.csv`** — the same three rows per student, with `Total Score (%)` and `Total Points`, and a `1` or `0` per question. If a student's Test ID does not match any key, the score reads `NO KEY FOUND`.

**`review_required.csv`** — only written when something needs a human. See Step 6.

**`rejected_files.csv`** — only written if a page could not be read at all (corner marks not found: a badly skewed, cropped, or blank scan). Re-scan those pages.

### Step 6 — Deal with anything the software refuses to guess at

The command tells you how it went through its **exit code** as well as its output. To see the exit code:

```sh
echo %ERRORLEVEL%        # Windows cmd
echo $LASTEXITCODE       # Windows PowerShell
echo $?                  # macOS / Linux
```

| Code | Meaning | What to do |
|---|---|---|
| `0` | Everything read and scored. | Nothing. |
| `1` | Bad arguments, or the input folder is empty or missing. | Re-check the command and the folder paths. |
| `2` | Read and scored, **but some marks were too unclear to grade**. | See below. |
| `3` | The batch is not a clean sequence of front/back pairs. **Nothing was graded.** | See below. |

#### Exit code 2 — unclear marks

The run finishes and writes all the normal output, then reports:

```
ATTENTION: 3 mark(s) were too unclear to grade automatically. See 'review_required.csv'.
3 mark(s) need to be checked by hand:
  batch.pdf (page 3) | Q17 (Test ID 1001) | too faint or too partly erased to call filled or blank | B only partly filled; B=46% A=2% C=1% of a normal mark
  ...
Error: 3 mark(s) on 2 page(s) are too unclear to grade automatically and must be checked by hand.
```

`review_required.csv` lists each one with its source page, Student ID, location, problem, and the measurement behind the verdict. The percentages are **relative to a normal mark on the same page** — so `B=46%` means that bubble is about half as dark as that student's other answers. This is what a half-erased answer looks like.

Three kinds are reported:

- **borderline** — too faint or too partly erased to call.
- **multiple** — two or more bubbles filled on one question, or two similarly dark bubbles in an ID column.
- **blank** — a required field (Student ID, Latin level) has nothing filled.

Pull those sheets, read them yourself, and correct the CSV by hand. When you have satisfied yourself that the readings are right, re-run with `--allow-unclear` to get a clean exit while still producing the report:

```sh
python -m src.main <scans> <results> --variant cajcl --allow-unclear
```

If the check is too eager or too lax for your scanner, tune it — `--answer-margin 0.20` reports fewer, `--answer-margin 0.40` reports more.

#### Exit code 3 — pages out of order

```
Error: 'batch.pdf (page 7)' is the back page of a sheet, but it was scanned
where the front page of sheet 4 should be. The pages are out of order: every
front page must be immediately followed by its own back page.
```

or

```
Error: 'batch.pdf (page 8)' has Student ID 0031, but the front page of sheet 4
has Student ID 0427. The pages are out of order or two students' sheets have
been interleaved.
```

or

```
Error: The batch has 19 page(s), which is not a whole number of 2-page sheets.
```

Nothing is graded, because a mis-collated batch would attribute one student's answers to another. Re-order or re-scan the pages named and run again. The collation bar along the bottom — left on fronts, right on backs — makes it quick to spot the offender in a printed stack.

### Step 7 — Check the grading by eye

Add `--annotate`:

```sh
python -m src.main <scans> <results> --variant cajcl --annotate
```

This writes an `annotated/` folder holding a marked-up copy of each input file (`batch_annotated.pdf`). Every page is the original scan with:

- **green** on the bubble the key says is correct,
- **red** on a bubble the student filled that is not the correct one,
- **amber** on anything listed in `review_required.csv`,
- a colour key and the page's Student ID printed along the bottom.

Flipping through it is the fastest way to confirm the software read what you read, and it makes a good demonstration. It roughly doubles the run time and produces a large PDF, so it is off by default.

### A complete worked example

```sh
# grade the batch, writing predictable file names and a marked-up copy
python -m src.main ~/Desktop/convention/scans ~/Desktop/convention/results \
    --variant cajcl \
    --annotate \
    --disable-timestamps \
    --sort

# check how it went
echo $?
```

Afterwards, `~/Desktop/convention/results` contains:

```
results.csv
keys.csv
scores.csv
review_required.csv          (only if something needs a human)
annotated/batch_annotated.pdf
```

---

## Command reference

```
python -m src.main <input_folder> <output_folder> [options]
```

| Option | Effect |
|---|---|
| `--variant {cajcl,75,150}` | Which sheet was used. **`cajcl`** is the two-page State Convention sheet. Defaults to `75` (the legacy sheet), so pass it explicitly. |
| `--annotate` | Also write marked-up copies of every scan into `annotated/`. |
| `--allow-unclear` | Still write `review_required.csv`, but exit `0` instead of `2`. |
| `--check-marks` / `--no-mark-review` | Force the unclear-mark check on or off. It is on by default for `cajcl` and off for the legacy variants, whose archival scans predate it. |
| `--answer-margin FRACTION` | How wide the "cannot call it" band is, as a fraction either side of halfway between a blank bubble and a real mark. Default `0.3`, i.e. anything 20–80% as dark as a normal mark. |
| `--id-contrast FRACTION` | How far the darkest bubble of an ID column must stand clear of the next darkest. Default `0.35`. |
| `--anskeys FILE.csv` | Use a CSV of answer keys instead of reading keys from the scans. |
| `--formmap FILE.csv` | A form arrangement map, for exams that differ only in question order. Only one key may be supplied with it. |
| `-ml`, `--multiple` | Record a double-marked question as `F` rather than `[A\|B]`. |
| `-e`, `--empty` | Record an unanswered question as `G` rather than an empty cell. |
| `-s`, `--sort` | Sort the output by student name (falls back to Test ID on sheets without name bubbles). |
| `--mcta` | Also write files for Multiple Choice Test Analysis. |
| `--disable-timestamps` | Leave the date and time out of output file names. **Existing files are overwritten without warning.** |
| `-d`, `--debug` | Write a `debug/` folder with the intermediate images for every page. Large, but invaluable when a sheet will not read. |

Run `python -m src.main` with no arguments for the built-in help.

---

## Using the graphical interface instead

```sh
python -m src.main_gui
```

Choose **CAJCL State Convention** in the *Form Variant* dropdown. The two extra checkboxes match the command-line options: *Save marked-up copies of the sheets for checking* is `--annotate`, and *Grade anyway when some marks are unclear* is `--allow-unclear`.

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
| `src/batching.py` | Splits a batch into sheets and enforces page order. |
| `src/mark_quality.py` | Decides which marks a human needs to settle. |
| `src/annotation.py` | Draws the marked-up PDFs. |
| `src/process_input.py` | Runs the whole pipeline and writes the CSVs. |
| `open_mcr.py` | Launcher used only when packaging with PyInstaller; see `build_instructions.md`. |

Because the generator and the reader both derive from `sheet_layout.py`, moving a block is a one-line change in one file — and `test/test_cajcl.py` fails if the two ever disagree.

**Do not change the four corner marks.** `corner_finding` recovers the top-left grid corner by projecting out from the L-mark, and it needs to know where that mark sits relative to the corner. That relationship is `sheet_layout.L_MARK_OFFSET_FRACTION`, passed through to `corner_finding.find_corner_marks`. If you move the L-mark without updating it, the grid skews and the left-hand side of the sheet reads as noise.

Run the tests with:

```sh
python -m pytest
```

`test/test_cajcl.py` builds real sheets with the real generator, bubbles them in, and reads them back, so it catches layout drift, mis-collation, and mis-scoring.

---

## Background and license

Commercially available OMR (optical mark recognition) exam sheets, scanners, and processing software can cost educators and educational institutions thousands of dollars per year. In response to this, OpenMCR was developed as a free and easy-to-use alternative.

The original software and multiple choice sheet were developed as an independent study project by Ian Sanders, a mechanical engineering student at the University of South Florida, under the direction of Dr. Autar Kaw. For a detailed discussion of the algorithm, [read the report](https://github.com/iansan5653/open-mcr-report/releases/tag/1.0.0) submitted for that course.

The other printable sheets remain available:

- [75 Question Variant](https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_75q.pdf)
- [150 Question Variant](https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_150q.pdf)

For the original operating instructions, see the [Manual](src/assets/manual.md).

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
