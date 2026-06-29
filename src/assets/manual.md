<!-- NOTE: This file is used to generate the manual.pdf, which must be
done as part of the build process (see /build_instructions.md) -->

![OpenMCR](wordmark.png)

# User Manual

## Basic Usage

### Installation

For download and installation instructions, see the project homepage:
https://github.com/iansan5653/open-mcr

### Printing Sheets

In order to use the OpenMCR software, students must use the provided multiple-
choice sheets. Three options are available (click the names to print PDFs):

- `multiple_choice_sheet_75q` (https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_75q.pdf): Has 75 questions as well as space for students' full names, course ID, student ID, and test form code.
- `multiple_choice_sheet_150q` (https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_150q.pdf):
  Doubles the number of questions to 150, but removes the bubbles for student name. Instead, a non-processed write-in name field is provided.
- `multiple_choice_sheet_240q_twosided` (https://github.com/iansan5653/open-mcr/raw/master/src/assets/multiple_choice_sheet_240q_twosided.pdf):
  A double-sided sheet that combines three 75-question tests (225 questions
  total) onto a single sheet, so you can grade three exams at once.

  **Page 1** has three columns:
  - Student ID (10 digits) + a write-in Name blank
  - Test 1 ID (4 digits) + 38 MCQs (Q1-Q38, the "left half" of Test 1)
  - 37 MCQs (Q39-Q75, the "right half" of Test 1)

  **Page 2** has four columns:
  - Test 2 ID (4 digits) + 38 MCQs (Q1-Q38, the "left half" of Test 2)
  - 37 MCQs (Q39-Q75, the "right half" of Test 2)
  - Test 3 ID (4 digits) + 38 MCQs (Q1-Q38, the "left half" of Test 3)
  - 37 MCQs (Q39-Q75, the "right half" of Test 3)

  Each MCQ "test" is split into two side-by-side halves on the page; the
  scanner reads them as a single 75-question column (Q1..Q75). Each MCQ
  column carries its own Test ID, and the Student ID from the front page
  is propagated to the back page so all three rows in the output share
  the same Student ID.

  **Scanning**: the sheet must be scanned as a 2-page document (PDF or
  multi-page TIFF) so the scanner sees both sides. Each scanned student
  produces three output rows (one per test) in the results CSV. To
  create an answer key, fill in Student ID `9999999999`, set each Test
  ID, and bubble in the correct answers for all three tests - the
  software will produce three key rows that are matched to students
  by Test ID.

The program is robust and should work with most printers and scanners, however
best results will be obtained by using a laser printer in black & white mode
with the 'save toner' option turned off.

### Filling Sheets

Students should be instructed to fill bubbles throughly and erase completely
if necessary. It is not necessary to require any specific type of pen or pencil
to be used.

### Creating Answer Keys

If you would like to take advantage of the automatic grading feature of the
software, you must provide it with one or more answer keys. To create an answer
key, simply print a normal sheet and put `9999999999` in the **Student ID**
field. Also, add a **Test Form Code** which will be used to match students' exams
with the correct answer key, and finally fill in the exam with the correct
answers.

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
   stored the images in.
   - If you select the _convert multiple answers in a
     question to 'F'_ option, then if a student selects, for example, `A`
     _and_ `B` for a question, the output file will save that as `F` instead of
     `[A|B]`.
   - If you select the _save empty in questions as 'G'_ option, if a student
     skips a question by leaving it blank, the output file will save that as
     `G` instead of as a blank cell.
4. Under **Select Output Folder**, click <kbd>Browse</kbd> and select the folder where
   you would like to save the resulting CSV files.
   - If you select the _sort results by name_ option, results will be sorted
     by the students' last, first, and middle names (in that order). Otherwise,
     results will be saved in the order processed.
5. Click <kbd>Continue</kbd>.

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
