<!-- NOTE: This file is used to generate the manual.pdf, which must be
done as part of the build process (see /build_instructions.md) -->

![OpenMCR](wordmark.png)

# User Manual

## Basic Usage

### Installation

For download and installation instructions, see the project homepage:
https://github.com/iansan5653/open-mcr

### Printing Sheets

In order to use the OpenMCR software, students must use one of the provided
multiple-choice sheets. Three options are available (click the names to print
PDFs):

- `cajcl_answer_sheet` (src/assets/cajcl_answer_sheet.pdf): The California
  Junior Classical League State Convention sheet. Two pages, printed
  double-sided, carrying three separate 80-question tests (240 questions in
  all), so one sheet covers a student's whole sitting.

  **Front page** carries the directions, write-in blanks for the student's
  name and school, a 4-digit Student ID block, a Latin level block (MS-1,
  MS-2, MS-3, HS-1, HS-2, HS-3, HS-ADV), the Test 1 ID block, and Test 1's
  80 answers in two columns of 40.

  **Back page** repeats the Student ID block in exactly the same place, and
  carries the Test 2 and Test 3 ID blocks with their answer columns.

  The Student ID is bubbled on both sides deliberately: it lets the software
  confirm that each front page is followed by its own back page. Each page
  also carries a printed page-code bubble marking it as the front or the
  back, and a solid collation bar along the bottom - on the left for a front
  page, on the right for a back page - so a printed stack can be checked by
  riffling it.

  **Scanning**: scan every sheet double-sided, front page first. A whole
  batch can be a single PDF: ten students becomes one twenty-page file, and
  the software splits it into ten sheets by itself. Turn off any
  "blank page removal" or "auto-rotate" option on the scanner - dropping a
  page throws the batch out of order. Each student produces three rows in
  the results file, one per test.

- `multiple_choice_sheet_75q` (https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_75q.pdf): Has 75 questions as well as space for students' full names, course ID, student ID, and test form code.
- `multiple_choice_sheet_150q` (https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_150q.pdf):
  Doubles the number of questions to 150, but removes the bubbles for student name. Instead, a non-processed write-in name field is provided.

The program is robust and should work with most printers and scanners, however
best results will be obtained by using a laser printer in black & white mode
with the 'save toner' option turned off.

Print at 100% scale where you can. "Fit to page" does still read correctly -
the software finds the four corner marks and measures everything from them, so
a uniformly scaled page is no problem - but scaling shrinks the bubbles and
leaves less room for error on a poor scan. What must not happen is any
*non-uniform* change: do not print two pages to a sheet, and turn off the
scanner's "auto-rotate" and "auto-crop" options.

Nothing on the sheet is printed closer to the paper's edge than the corner
marks themselves, so any printer that renders the marks renders the whole
sheet.

### Filling Sheets

Students should be instructed to fill bubbles throughly and erase completely
if necessary. It is not necessary to require any specific type of pen or pencil
to be used.

### Creating Answer Keys

If you would like to take advantage of the automatic grading feature of the
software, you must provide it with one or more answer keys. To create an answer
key, simply print a normal sheet and fill the **Student ID** field with all
nines - `9999` on the CAJCL sheet, `99999` on the legacy sheets. Also, add a
**Test Form Code** (the **Test ID** on the CAJCL sheet) which will be used to
match students' exams with the correct answer key, and finally fill in the exam
with the correct answers.

On the CAJCL sheet one key sheet produces three keys, one per Test ID, so fill
in all three tests. Leave the Latin level blank on a key.

This is optional - you can choose to just have the software read the exams and
not score them.

### Reading Sheets

Simply follow the following steps to process any number of filled exam sheets:

1. Scan all sheets using a standard scanner. Convert them into individual
   images and place them into a single folder. This includes answer keys - there
   is no need to scan them seperately.
2. Run the software. If you used the installer, a shortcut will be located in
   your Start menu. The sofware may take a moment to start.
3. Under **Select Input Folder**, click <kbd>Browse</kbd> and select the folder you
   stored the images in, then pick the matching **Form Variant**.
   - If you select the _convert multiple answers in a
     question to 'F'_ option, then if a student selects, for example, `A`
     _and_ `B` for a question, the output file will save that as `F` instead of
     `[A|B]`.
   - If you select the _save empty in questions as 'G'_ option, if a student
     skips a question by leaving it blank, the output file will save that as
     `G` instead of as a blank cell.
   - If you select the _save marked-up copies of the sheets_ option, an
     `annotated` folder is written alongside the CSVs, holding a copy of every
     scan with the correct answer ringed in green, any wrong choice the student
     made in red, and anything the software could not read confidently in
     amber. Flipping through it is the quickest way to check the grading by
     eye. It roughly doubles the processing time.
   - If you select the _grade anyway when some marks are unclear_ option, marks
     that are too faint or too partly erased to call are still listed in
     `review_required.csv`, but they no longer stop the run. See
     **Unclear Marks** below.
4. Under **Select Output Folder**, click <kbd>Browse</kbd> and select the folder where
   you would like to save the resulting CSV files.
   - If you select the _sort results by name_ option, results will be sorted
     by the students' last, first, and middle names (in that order). Otherwise,
     results will be saved in the order processed.
5. Click <kbd>Continue</kbd>.

### Unclear Marks

On the CAJCL sheet, every mark is measured against what a blank bubble and a
properly filled bubble look like on that same page. A mark that lands between
the two - half erased, barely pencilled in, or doubled up with another - cannot
be graded honestly by machine, so the software refuses to guess.

When that happens, all of the normal output is still written, plus a
`review_required.csv` naming every mark that needs a person:

| Column | Meaning |
| --- | --- |
| Source File | The page the mark is on. |
| Student ID | Whose sheet it is. |
| Location | `Q17 (Test ID 1001)`, `Student ID digit 2`, `Latin level`, ... |
| Problem | `borderline`, `multiple`, or `blank`. |
| Measurements | The darkness of the top few bubbles, as a percentage of a normal mark on that page. |

Pull those sheets, read them yourself, and correct the results by hand. From
the command line the run also exits with status `2` so a script can catch it;
add `--allow-unclear` once you have checked them.

A `?` inside a Student ID or Test ID - `04?7` - marks a digit column that could
not be read. It is left in place rather than dropped, because `047` would look
like a perfectly valid but wrong ID.

### Page Order

A two-page sheet is only meaningful as a pair, so the software checks the
pairing before it grades anything. Each page carries a printed page-code
bubble saying whether it is a front or a back, and the Student ID is bubbled
on both sides. If a batch does not hold a whole number of sheets, if a back
page turns up where a front page should be, or if a back page carries a
different Student ID from the front page before it, the run stops and names
the offending page. Nothing is graded, because a mis-collated batch would
credit one student with another's answers.

Re-order or re-scan the pages named and run again.

### Scoring Results

In addition to reading scanned images, the software can also automatically score
the exam results. It does this by comparing the provided keys with the output.
There are three options for this, depending on which way you generate your exams:

#### 1. One Exam Variant

If you give every exam-taker the exact same exam, you can instruct them to leave
the **Test Form Code** field blank on their sheets. In addition, leave that
field blank on the answer key sheet. All exam results will be compared to the
single answer key sheet provided.

#### 2. Shuffled Exam Variants

If you provide the exam-takers with multiple variants of the same exam, and these
variants differ only in question order (in other words, each variant is simply
shuffled), then you can score all of these with the same key file by providing
a file that defines the orders of the shuffled variants.

Each row in this file represents a key, and each
column represents the position that that question should be moved to.

For example, if exam form `A` has questions 1, 2, and 3, exam form `B` might have
them in 3, 1, 2 order and `C` might have them in 3, 2, 1 order. This would result
in the following arrangement file:

```csv
Test Form Code, Q1, Q2, Q3
             A,  1,  2,  3
             B,  3,  1,  2
             C,  3,  2,  1
```

If this were the file you upload, then all of the exams with form `A` would be
left untouched while `B` and `C` would be rearranged to 1, 2, 3 order. Select
the file in the program under the **Select Form Arrangement Map**.

Note that the first row in this file should always be in 1, 2, 3, ... order, and
each row after that should only have one instance of each number.

If you use this option, only one exam key can be provided or an error will be
raised.

#### 3. Distinct Exam Variants

Finally, you can provide the exam-takers with multiple wholly distinct variants
of the same exam. In this case, each exam will be scored by selecting the answer
key with an exactly matching **Test Form Code**. No rearrangement will be
performed.

### Output Files

After the program finishes processing, results will be saved as CSV files in
your selected output folder. These files can be opened in Excel or in any text
editor. Files will be saved with the time of processing to avoid overwriting any
existing files.

If you did not include any answer keys, one raw file will be saved with all of
the students' selected answers and no scoring is performed.

If you did include one or more answer keys, two more files will be saved in
addition to the aforementioned raw file. One of these files will have all of the
keys that were found, and the other will have the scored results. In the scored
file, questions are saved for each student as either `1` (correct) or `0`
(incorrect).

Two more files appear only when they are needed: `review_required.csv`, listing
marks that must be checked by hand, and `rejected_files.csv`, listing pages
whose corner marks could not be found at all (usually a badly skewed, cropped,
or blank scan - re-scan those pages).

On the CAJCL sheet each student produces **three rows** in every output file,
one per test, sharing a Student ID and Latin level and distinguished by their
Test Form Code.

## Advanced Usage

### Providing a Premade Key File

If you do not want to create answer keys using the multiple choice form, or you
want to reuse answer keys across batches, you can select a CSV file under the
**Select Answer Keys File** heading. This file can be a key file previously
generated by OpenMCR, or it can be one you created yourself. If you create it
yourself, it should be in the following form:

```csv
Test Form Code, Source File, Q1, Q2, Q3, ...
             A,            ,  A,  C,  B, ...
             B,            ,  B,  B,  C, ...
             C,            ,  E,  A,  D, ...
```
