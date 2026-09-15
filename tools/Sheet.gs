/**
 * Google Apps Script for the CAJCL review spreadsheet.
 *
 * Drop this into a Google Sheet (Extensions > Apps Script), then set up the
 * trigger described at the bottom of this file. After that, importing a
 * "Batch N - Unclear.csv" or "Batch N - Missing.csv" as a NEW SHEET tidies it
 * up automatically: real checkboxes in the option columns, the ID columns
 * kept as text so leading zeros survive, columns fitted to their contents,
 * Inconsolata throughout, the empty padding rows deleted, a frozen header
 * row, and a dropdown of valid Latin levels on the Missing sheet.
 *
 * On the Missing sheet, Value wants the WHOLE field typed in again - the
 * complete Student ID, not a single digit. Page may read "1,2" when the same
 * field could not be read on either side of a sheet.
 *
 * You can also ADD rows, to correct a bubble that was read wrongly but never
 * flagged as doubtful. A row needs enough of File, Page, Test, Test ID and
 * Student ID to pick out exactly one test - normally the Student ID and the
 * Test ID - plus the question number and the answer. CAJCL > Add a correction
 * row sets one up with the checkboxes already in place.
 *
 * Written for the file layout this software produces. If you change the
 * review columns in src/review.py, change OPTION_COLUMNS below to match.
 */

/** Option columns on the Unclear sheet, which become checkboxes. */
var OPTION_COLUMNS = ['A', 'B', 'C', 'D', 'E'];

/**
 * Columns that must stay text, or Sheets eats their leading zeros.
 *
 * 'Test' is deliberately not one of them: it is a plain count of the tests on
 * the sheet (1, 2, 3) and has no leading zero to lose.
 */
var TEXT_COLUMNS = ['Batch', 'Student ID', 'Test ID', 'Page'];

/**
 * The Latin levels, offered as a dropdown when a level is missing.
 *
 * Downloading this file from the grading site fills in the levels your sheet
 * was actually printed with. Editing the line by hand is fine too - it only
 * has to match the names on the answer sheet.
 */
/* LATIN_LEVELS */
var LATIN_LEVELS = ['MS-1', 'MS-2', 'MS-3', 'HS-1', 'HS-2', 'HS-3', 'HS-Adv'];

/** Width in pixels for a checkbox column. */
var CHECKBOX_WIDTH = 42;

/** Everything is set in this, so letters and digits line up. */
var FONT_FAMILY = 'Inconsolata';


/**
 * Fires when the spreadsheet's structure changes. Importing a CSV as a new
 * sheet shows up as INSERT_GRID.
 */
function onSpreadsheetChange(e) {
  if (!e || e.changeType !== 'INSERT_GRID') {
    return;
  }
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  formatReviewSheet(sheet);
}


/** Menu entry, so a sheet can also be tidied by hand. */
function onOpen() {
  SpreadsheetApp.getUi()
      .createMenu('CAJCL')
      .addItem('Tidy this review sheet', 'tidyActiveSheet')
      .addItem('Add a correction row', 'addCorrectionRow')
      .addToUi();
}


function tidyActiveSheet() {
  formatReviewSheet(SpreadsheetApp.getActiveSpreadsheet().getActiveSheet());
}


/**
 * Turn a freshly imported review CSV into something people can work in.
 * Safe to run more than once, and does nothing to a sheet that is not one of
 * ours.
 */
function formatReviewSheet(sheet) {
  var lastRow = sheet.getLastRow();
  var lastColumn = sheet.getLastColumn();
  if (lastRow < 1 || lastColumn < 1) {
    return;
  }

  var header = sheet.getRange(1, 1, 1, lastColumn).getValues()[0];
  var index = {};
  for (var i = 0; i < header.length; i++) {
    index[String(header[i]).trim()] = i + 1;  // 1-based column number
  }

  var isUnclear = OPTION_COLUMNS.every(function (name) {
    return index[name];
  });
  var isMissing = Boolean(index['Field'] && index['Value']);
  if (!isUnclear && !isMissing) {
    return;  // not a review sheet; leave it alone
  }

  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, lastRow, lastColumn).setFontFamily(FONT_FAMILY);
  sheet.getRange(1, 1, 1, lastColumn)
      .setFontWeight('bold')
      .setBackground('#f1f3f4');

  // A CSV import leaves the sheet padded out to a thousand rows. Trim them,
  // or the scroll bar suggests far more work than there is.
  var maxRows = sheet.getMaxRows();
  if (maxRows > lastRow) {
    sheet.deleteRows(lastRow + 1, maxRows - lastRow);
  }
  var maxColumns = sheet.getMaxColumns();
  if (maxColumns > lastColumn) {
    sheet.deleteColumns(lastColumn + 1, maxColumns - lastColumn);
  }

  var dataRows = lastRow - 1;

  // Leading zeros: '04275' must not become 4275, or the corrections will not
  // match the results when they are read back.
  TEXT_COLUMNS.forEach(function (name) {
    var column = index[name];
    if (column && dataRows > 0) {
      var range = sheet.getRange(2, column, dataRows, 1);
      range.setNumberFormat('@');
      var values = range.getValues();
      for (var r = 0; r < values.length; r++) {
        values[r][0] = padIdentifier(name, values[r][0]);
      }
      range.setValues(values);
    }
  });

  if (isUnclear && dataRows > 0) {
    OPTION_COLUMNS.forEach(function (name) {
      var column = index[name];
      var range = sheet.getRange(2, column, dataRows, 1);
      range.insertCheckboxes();
      range.setHorizontalAlignment('center');
      sheet.setColumnWidth(column, CHECKBOX_WIDTH);
    });
  }

  if (isMissing && dataRows > 0 && index['Value']) {
    // Only the Latin level rows get a dropdown; IDs are free text.
    var fieldColumn = index['Field'];
    var valueColumn = index['Value'];
    var fields = sheet.getRange(2, fieldColumn, dataRows, 1).getValues();
    // A warning rather than a refusal: if this list has drifted from the
    // sheet that was printed, the dropdown must not stop somebody typing the
    // level that is actually on the paper.
    var rule = SpreadsheetApp.newDataValidation()
        .requireValueInList(LATIN_LEVELS, true)
        .setAllowInvalid(true)
        .build();
    for (var r = 0; r < fields.length; r++) {
      var cell = sheet.getRange(r + 2, valueColumn);
      if (String(fields[r][0]).toLowerCase().indexOf('latin') === 0) {
        cell.setDataValidation(rule);
      } else {
        cell.setNumberFormat('@');
      }
    }
  }

  if (index['Done'] && dataRows > 0) {
    var doneRange = sheet.getRange(2, index['Done'], dataRows, 1);
    doneRange.insertCheckboxes();
    doneRange.setHorizontalAlignment('center');
    sheet.setColumnWidth(index['Done'], CHECKBOX_WIDTH + 12);

    // Gray out a row once it is ticked, so the remaining work is obvious.
    var doneLetter = columnLetter(index['Done']);
    var whole = sheet.getRange(2, 1, dataRows, lastColumn);
    var existing = sheet.getConditionalFormatRules();
    existing.push(SpreadsheetApp.newConditionalFormatRule()
        .whenFormulaSatisfied('=$' + doneLetter + '2=TRUE')
        .setBackground('#e8f0e8')
        .setFontColor('#8a8a8a')
        .setRanges([whole])
        .build());
    sheet.setConditionalFormatRules(existing);
  }

  // Fit every column to its contents, then put the checkbox columns back to
  // a narrow fixed width - autofit makes them as wide as their header.
  sheet.autoResizeColumns(1, lastColumn);
  var narrow = (isUnclear ? OPTION_COLUMNS : []).concat(['Done']);
  narrow.forEach(function (name) {
    if (index[name]) {
      sheet.setColumnWidth(
          index[name],
          name === 'Done' ? CHECKBOX_WIDTH + 12 : CHECKBOX_WIDTH);
    }
  });
  if (index['Value']) {
    sheet.setColumnWidth(index['Value'], 140);
  }

  sheet.getRange(1, 1, lastRow, lastColumn).setVerticalAlignment('middle');
}


/**
 * Append a blank row to an Unclear sheet, ready to be filled in by hand.
 *
 * Typing into the row below the last one gets you a row with no checkboxes
 * and no text formatting, so the Student ID loses its leading zero and the
 * answer columns cannot be ticked. This sets all of that up, and copies the
 * batch and file down from the row above so only the parts that differ have
 * to be typed.
 */
function addCorrectionRow() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var lastRow = sheet.getLastRow();
  var lastColumn = sheet.getLastColumn();
  var ui = SpreadsheetApp.getUi();
  if (lastRow < 1) {
    ui.alert('Open a review sheet first.');
    return;
  }

  var header = sheet.getRange(1, 1, 1, lastColumn).getValues()[0];
  var index = {};
  for (var i = 0; i < header.length; i++) {
    index[String(header[i]).trim()] = i + 1;
  }
  var isUnclear = OPTION_COLUMNS.every(function (name) {
    return index[name];
  });
  if (!isUnclear) {
    ui.alert('Rows can only be added to an Unclear sheet. The Missing sheet ' +
             'asks for whole fields, which the software already lists in ' +
             'full.');
    return;
  }

  var row = lastRow + 1;
  sheet.insertRowAfter(lastRow);
  sheet.getRange(row, 1, 1, lastColumn).setFontFamily(FONT_FAMILY);

  OPTION_COLUMNS.concat(['Done']).forEach(function (name) {
    if (index[name]) {
      var cell = sheet.getRange(row, index[name]);
      cell.insertCheckboxes();
      cell.setHorizontalAlignment('center');
    }
  });
  TEXT_COLUMNS.forEach(function (name) {
    if (index[name]) {
      sheet.getRange(row, index[name]).setNumberFormat('@');
    }
  });

  // Carry down the parts that are the same for the whole batch, so only what
  // actually differs has to be typed.
  ['Batch', 'File'].forEach(function (name) {
    if (index[name] && lastRow > 1) {
      sheet.getRange(row, index[name])
          .setValue(sheet.getRange(lastRow, index[name]).getValue());
    }
  });

  var first = index['Student ID'] || index['Test ID'] || 1;
  sheet.setActiveRange(sheet.getRange(row, first));
  SpreadsheetApp.getActiveSpreadsheet().toast(
      'Fill in enough of Student ID, Test ID, File, Page and Test to name ' +
      'one test, then the Question number and the answer.',
      'Correction row added', 8);
}


/** Restore the leading zeros a CSV import strips. */
function padIdentifier(name, value) {
  var text = String(value == null ? '' : value).trim();
  if (text === '' || !/^\d+$/.test(text)) {
    return text;
  }
  if (name === 'Student ID') {
    return ('00000' + text).slice(-5);
  }
  if (name === 'Test ID') {
    return ('0000' + text).slice(-4);
  }
  return text;
}


/** 1 -> 'A', 27 -> 'AA'. */
function columnLetter(column) {
  var letter = '';
  while (column > 0) {
    var remainder = (column - 1) % 26;
    letter = String.fromCharCode(65 + remainder) + letter;
    column = Math.floor((column - 1) / 26);
  }
  return letter;
}


/**
 * SETTING UP THE TRIGGER
 * ----------------------
 * onSpreadsheetChange is not a simple trigger, so it has to be installed once
 * per spreadsheet:
 *
 *   1. Extensions > Apps Script, paste this file in, Save.
 *   2. In the Apps Script editor, click Triggers (the clock icon).
 *   3. Add Trigger:
 *        Function to run          onSpreadsheetChange
 *        Event source             From spreadsheet
 *        Event type               On change
 *   4. Save, and accept the authorisation prompt.
 *
 * Then import a review CSV with File > Import > Insert new sheet(s). The
 * import must create a NEW SHEET - "Replace current sheet" does not raise
 * INSERT_GRID and nothing will happen. If a sheet is ever missed, use
 * CAJCL > Tidy this review sheet.
 *
 * When the corrections are done, File > Download > Comma-separated values for
 * each sheet, and pass the downloaded files to --overrides (or upload them in
 * step 7 of the website).
 *
 * Rows you have not ticked are left alone rather than refused, and come back
 * as a shorter sheet to finish next time.
 */
