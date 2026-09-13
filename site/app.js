/*
 * The grading wizard.
 *
 * Everything typed here lives in localStorage under one key, so the page can
 * be closed and come back to. Scans are never stored: they go straight from
 * the file input to the server and the results come back in the same response.
 * Results.csv is small enough to keep (about 33 KB for a hundred students) and
 * is kept, because step 7 needs it to re-score without the scans. Marked-up
 * PDFs are offered as a download and then dropped.
 *
 * An answer is stored per position, as an array, never as a joined string: a
 * key with a gap at question 40 has to keep questions 41 onwards where they
 * are, and joining loses that.
 */
'use strict';

const STORE_KEY = 'jcl-grading-v1';
const GAP = '-';               // a question not filled in yet

const state = load();

function blank() {
  return {
    endpoint: '', passphrase: '',
    sheet: null,                 // filled from /health defaults on connect
    tests: [newTest()],
    keyCsv: '',
    keySource: null,             // {file, count} once a file has been loaded
    undo: null,                  // {tests, keySource} as they were before an upload
    thresholdMode: 'auto',
    threshold: { as: '', ar: '', ms: '', mr: '' },
    annotate: false,
    batches: [],
    nextBatch: 1,
    limits: null,
  };
}

function newTest() {
  return {
    name: '', id: '', excluded: [], answers: [],
    dirty: false,    // answers changed by hand since the last file was loaded
    edits: [],       // question indices typed into the grid, so they can be
                     // shown in bold again after a reload
  };
}

function load() {
  let saved;
  try {
    const raw = localStorage.getItem(STORE_KEY);
    saved = raw ? JSON.parse(raw) : null;
  } catch (error) {
    console.warn('Could not read saved work:', error);
  }
  const result = Object.assign(blank(), saved || {});
  // Answers used to be stored as one string per test.
  result.tests = (result.tests || []).map((test) => Object.assign(newTest(), test, {
    answers: Array.isArray(test.answers) ? test.answers
           : textToAnswers(test.answers || ''),
  }));
  if (!result.tests.length) result.tests = [newTest()];
  // Batches used to carry an editable label instead of a fixed number.
  result.batches = (result.batches || []).map((batch, index) => Object.assign(
    {}, batch, { n: batch.n || Number(batch.label) || index + 1 }));
  result.nextBatch = Math.max(
    result.nextBatch || 1,
    ...result.batches.map((batch) => batch.n + 1), 1);
  return result;
}

function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch (error) {
    // Quota is the only realistic failure, and only with an improbable number
    // of batches kept. Say so rather than failing silently.
    say('msg-grade', 'bad',
        'This browser will not store any more results. Download what you ' +
        'have, then use Reset at the foot of the page to clear the saved work.');
  }
}

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, kids = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const kid of [].concat(kids)) {
    node.append(kid && kid.nodeType ? kid : document.createTextNode(kid));
  }
  return node;
};

function say(id, kind, text) {
  const box = $(id);
  if (!box) return;
  box.className = 'msg ' + (kind || '');
  box.textContent = text || '';
}

function plural(count, one, many) {
  return count === 1 ? one : many;
}

/* "'1001'", "'1001' and '1002'", "'1001', '1002', and '1003'". */
function quotedList(items) {
  const quoted = items.map((item) => "'" + item + "'");
  if (quoted.length <= 1) return quoted.join('');
  if (quoted.length === 2) return quoted[0] + ' and ' + quoted[1];
  return quoted.slice(0, -1).join(', ') + ', and ' + quoted[quoted.length - 1];
}

function setStep(id, done, hintId, hint) {
  const step = $(id);
  if (step) step.dataset.state = done ? 'done' : '';
  if (hintId && $(hintId)) $(hintId).textContent = hint;
}

/* Open a later step once the one before it is done, but only the first time:
   re-opening something deliberately collapsed is worse than leaving it shut. */
const advanced = new Set();
function advance(id) {
  if (advanced.has(id)) return;
  advanced.add(id);
  const step = $(id);
  if (step && !step.open) step.open = true;
}

function questionCount() {
  return state.limits ? state.limits.questions_per_test : 80;
}

// --- talking to the server -------------------------------------------------

async function call(path, { method = 'GET', body, raw = false } = {}) {
  if (!state.endpoint) throw new Error('Connect to the server first (step 1).');
  const base = state.endpoint.replace(/\/+$/, '');
  let response;
  try {
    response = await fetch(base + path, {
      method,
      headers: { 'X-Grading-Key': state.passphrase },
      body,
    });
  } catch (error) {
    throw new Error(
      'Could not reach the server. Check the address, and check you are ' +
      'online.\n\n' + error.message);
  }
  if (response.status === 401) {
    throw new Error('The server did not accept that passphrase.');
  }
  if (response.status === 413) {
    throw new Error((await response.json()).detail);
  }
  if (raw) {
    if (!response.ok) throw new Error(await describe(response));
    return response.blob();
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      (payload && (payload.error || payload.detail)) ||
      ('The server returned ' + response.status + '.'));
  }
  return payload;
}

async function describe(response) {
  try {
    const payload = await response.json();
    return payload.error || payload.detail || ('HTTP ' + response.status);
  } catch {
    return 'HTTP ' + response.status;
  }
}

function download(name, data, type) {
  const blob = data instanceof Blob ? data : new Blob([data], { type });
  const url = URL.createObjectURL(blob);
  const link = el('a', { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function fileFromServer(item) {
  if (item.encoding === 'base64') {
    const binary = atob(item.data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: item.type });
  }
  return new Blob([item.data], { type: item.type + ';charset=utf-8' });
}

/* A file input dressed as a button. The native control is unstyleable, so it
   is hidden inside the label and the label reports the choice. */
function filePicker(host, { accept, multiple = false, label = 'Choose file' }) {
  host.className = 'filebtn';
  host.textContent = '';
  const input = el('input', { type: 'file', accept: accept, multiple: multiple });
  const pick = el('span', { className: 'pick', textContent: label });
  const chosen = el('span', { className: 'chosen', textContent: 'none chosen' });
  input.onchange = () => {
    const files = Array.from(input.files || []);
    chosen.textContent = !files.length ? 'none chosen'
      : files.length === 1 ? files[0].name
      : files.length + ' files';
    if (host.onpicked) host.onpicked(files);
  };
  host.append(input, pick, chosen);
  return input;
}

// --- 1. connect ------------------------------------------------------------

async function connect() {
  state.endpoint = $('endpoint').value.trim();
  state.passphrase = $('passphrase').value;
  if (!state.endpoint) return say('msg-connect', 'bad', 'Enter the server address.');
  say('msg-connect', 'info', 'Connecting…');
  try {
    const health = await call('/health');
    state.limits = health;
    if (!state.sheet) state.sheet = health.defaults;
    save();
    say('msg-connect', 'ok',
        `Connected. This sheet has ${health.tests_per_sheet} tests of ` +
        `${health.questions_per_test} questions, a ` +
        `${health.student_id_digits}-digit Student ID and ` +
        `${health.latin_level_count} Latin levels.`);
    setStep('step-connect', true, 'hint-connect', 'Connected');
    renderLimits();
    renderSheetForm();
    renderTests();
    ['step-sheet', 'step-tests', 'step-key', 'step-thresholds', 'step-grade',
     'step-review'].forEach(advance);
  } catch (error) {
    say('msg-connect', 'bad', error.message);
    setStep('step-connect', false, 'hint-connect', 'Not connected');
  }
}

function renderLimits() {
  const limits = state.limits;
  $('q-count').textContent = questionCount();
  if (!limits) return;
  $('suggest-sheets').textContent = limits.suggested_sheets_per_batch;
  $('suggest-pages').textContent =
    limits.suggested_sheets_per_batch * limits.pages_per_sheet;
  $('limit-note').textContent =
    'One upload may be at most ' +
    Math.round(limits.max_upload_bytes / 1048576) +
    ' MB. If a batch is larger than that, split it at the scanner and grade ' +
    'the pieces as separate batches — each gets its own results, and this ' +
    'page keeps them side by side.';
}

// --- 2. the sheet ----------------------------------------------------------

function renderSheetForm() {
  const sheet = state.sheet;
  // The wording comes from the server, so until step 1 is done there is
  // nothing to put under these headings. Take them down rather than leave
  // them standing over empty space.
  $('h-levels').hidden = !sheet;
  $('h-writeins').hidden = !sheet;
  if (!sheet) {
    $('levels').textContent = '';
    $('levels').append(el('p', { className: 'note', textContent:
      'Connect in step 1 and the sheet’s wording appears here.' }));
    $('writeins').textContent = '';
    return;
  }
  $('sheet-title').value = sheet.title;
  $('directions').value = sheet.directions.join('\n');

  const levels = $('levels');
  levels.textContent = '';
  sheet.latin_levels.forEach((level, index) => {
    const input = el('input', { type: 'text', value: level, maxLength: 10 });
    input.oninput = () => {
      sheet.latin_levels[index] = input.value; save(); renderTests();
    };
    levels.append(el('label', {}, [
      el('span', { className: 'lab', textContent: 'Level ' + (index + 1) }),
      input,
    ]));
  });

  const writeins = $('writeins');
  writeins.textContent = '';
  sheet.write_in_labels.forEach((label, index) => {
    const input = el('input', { type: 'text', value: label, maxLength: 24 });
    input.oninput = () => { sheet.write_in_labels[index] = input.value; save(); };
    writeins.append(el('label', {}, [
      el('span', { className: 'lab', textContent: 'Line ' + (index + 1) }),
      input,
    ]));
  });

  const custom = JSON.stringify(sheet) !== JSON.stringify(state.limits?.defaults);
  setStep('step-sheet', true, 'hint-sheet',
          custom ? 'Custom wording' : 'Default wording');
}

function readSheetForm() {
  state.sheet.title = $('sheet-title').value.trim();
  state.sheet.directions = $('directions').value
    .split('\n').map((line) => line.replace(/\s+$/, ''))
    .filter((line, index, all) => line !== '' || index < all.length - 1);
  save();
  return state.sheet;
}

async function makeSheet() {
  say('msg-sheet', 'info', 'Building the PDF…');
  try {
    const blob = await call('/sheet', {
      method: 'POST',
      body: JSON.stringify({ layout: readSheetForm() }),
      raw: true,
    });
    download('Answer Sheet.pdf', blob);
    say('msg-sheet', 'ok',
        'Downloaded. Print it double-sided, flipping on the long edge, at ' +
        '100% scale.');
    renderSheetForm();
    advance('step-tests');
  } catch (error) {
    say('msg-sheet', 'bad', error.message);
  }
}

// --- 3. the tests ----------------------------------------------------------

function renderTests() {
  const body = document.querySelector('#tests tbody');
  body.textContent = '';
  const levels = state.sheet ? state.sheet.latin_levels : [];
  const digits = state.limits ? state.limits.test_id_digits : 4;

  state.tests.forEach((test, index) => {
    const name = el('input', { type: 'text', value: test.name,
                               placeholder: 'Latin Literature' });
    name.oninput = () => { test.name = name.value; save(); refreshKey(); };

    const id = el('input', { type: 'text', value: test.id, maxLength: digits,
                             placeholder: '1001', inputMode: 'numeric' });
    id.oninput = () => {
      test.id = id.value.replace(/\D/g, '');
      id.value = test.id; save(); refreshKey();
    };
    // A Test ID is fixed-width, so 31 means 0031. Pad once focus leaves,
    // rather than while it is being typed.
    id.onblur = () => {
      if (test.id && test.id.length < digits) {
        test.id = test.id.padStart(digits, '0');
        id.value = test.id; save(); refreshKey();
      }
    };

    const chips = el('div', { className: 'chips' });
    levels.forEach((level) => {
      const box = el('input', { type: 'checkbox',
                                checked: test.excluded.includes(level) });
      box.onchange = () => {
        test.excluded = box.checked
          ? test.excluded.concat([level])
          : test.excluded.filter((item) => item !== level);
        save(); refreshKey();
      };
      chips.append(el('label', {}, [box, level]));
    });

    const remove = el('button', { textContent: '×', title: 'Remove this test' });
    remove.onclick = () => {
      state.tests.splice(index, 1);
      if (!state.tests.length) state.tests.push(newTest());
      save(); renderTests();
    };

    body.append(el('tr', {}, [
      el('td', {}, [name]), el('td', {}, [id]),
      el('td', {}, [chips]), el('td', {}, [remove]),
    ]));
  });
  refreshKey();
}

// --- 4. the answers --------------------------------------------------------

/* Text form of one test's answers. Gaps show as a dash so that position is
   visible; trailing gaps are dropped, since they only mean "not yet". */
function answersToText(answers) {
  const list = answers.slice();
  while (list.length && !list[list.length - 1]) list.pop();
  return list.map((item) => item || GAP).join(' ');
}

/* Accept "A B C", "A,B,C", one per line, or one unbroken "ABC" string. */
function textToAnswers(raw) {
  const text = (raw || '').trim();
  if (!text) return [];
  let parts;
  if (/[\s,]/.test(text)) {
    parts = text.split(/[\s,]+/).filter((item) => item !== '');
  } else if (text.includes('|')) {
    // Alternatives need a separator to be unambiguous, so a bare "A|BD" is
    // one answer, not several.
    parts = [text];
  } else {
    parts = text.split('');
  }
  return parts.map((item) => (item === GAP ? '' : item.toUpperCase()));
}

function answered(test) {
  return test.answers.filter((item) => item).length;
}

/* Tests real enough to have a column in the key file. */
function namedTests() {
  return state.tests.filter((test) => test.id || test.name);
}

function testLabel(test) {
  return (test.name || 'Untitled test') + (test.id ? ' (' + test.id + ')' : '');
}

function renderAnswers() {
  const host = $('answers');
  host.textContent = '';
  const tests = namedTests();
  if (!tests.length) {
    host.append(el('p', { className: 'note',
      textContent: 'Add a test in step 3 first.' }));
    return;
  }
  tests.forEach((test) => {
    const box = el('textarea', { rows: 3, value: answersToText(test.answers),
                                 placeholder: 'A B C D E …' });
    const count = el('span', { className: 'note' });
    const refresh = () => {
      const have = answered(test);
      const need = questionCount();
      count.textContent = have + ' of ' + need + ' answers';
      count.style.color = have === need ? 'var(--good)'
                        : have ? 'var(--warn)' : 'var(--ink-faint)';
    };
    box.oninput = () => {
      test.answers = textToAnswers(box.value).slice(0, questionCount());
      test.dirty = true;
      save(); refresh(); renderGrid(); updateKeyMessage();
    };
    refresh();
    host.append(el('label', {}, [
      el('span', { className: 'lab', textContent: testLabel(test) }), box, count,
    ]));
  });
}

/* One row per question, one column per test. Rebuilt only when the shape
   changes — a keystroke writes into the array and leaves the DOM alone. */
function renderGrid() {
  const host = $('answer-grid');
  const need = questionCount();
  const tests = namedTests();
  host.textContent = '';
  if (!tests.length) {
    host.append(el('p', { className: 'note', style: 'padding:.8rem',
      textContent: 'Add a test in step 3 and its answers will appear here.' }));
    return;
  }

  const head = el('tr', {}, [el('th', { textContent: '#' })].concat(
    tests.map((test) => el('th', {
      textContent: test.name || ('Test ' + test.id),
    }))));
  const body = el('tbody');
  for (let number = 1; number <= need; number++) {
    const index = number - 1;
    const cells = [el('td', { textContent: String(number) })];
    tests.forEach((test) => {
      const input = el('input', { type: 'text', maxLength: 11,
                                  value: test.answers[index] || '' });
      if (test.edits.includes(index)) input.classList.add('edited');
      input.oninput = () => {
        const text = input.value.toUpperCase().replace(/[^A-Z|]/g, '');
        input.value = text;
        while (test.answers.length < need) test.answers.push('');
        test.answers[index] = text;
        test.dirty = true;
        if (!test.edits.includes(index)) test.edits.push(index);
        input.classList.add('edited');
        save(); renderAnswers(); updateKeyMessage();
      };
      cells.push(el('td', {}, [input]));
    });
    body.append(el('tr', {}, cells));
  }
  host.append(el('table', {}, [el('thead', {}, [head]), body]));
}

function csvCell(value) {
  const text = String(value == null ? '' : value);
  return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

/* The transposed layout the server expects: field names down the first column,
   one further column per test. */
function buildKeyCsv(withAnswers = true) {
  const need = questionCount();
  const tests = state.tests.filter((test) => test.id || test.name);
  const rows = [
    ['Name'].concat(tests.map((test) => test.name)),
    ['Test ID'].concat(tests.map((test) => test.id)),
    ['Excluded'].concat(tests.map((test) => test.excluded.join(', '))),
  ];
  for (let number = 1; number <= need; number++) {
    rows.push([String(number)].concat(tests.map(
      (test) => (withAnswers ? test.answers[number - 1] || '' : ''))));
  }
  return rows.map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

/* Recompute everything downstream of the tests table. */
function refreshKey() {
  renderAnswers();
  renderGrid();
  updateKeyMessage();
}

function updateKeyMessage() {
  const withId = state.tests.filter((test) => test.id);
  const need = questionCount();
  const complete = withId.filter((test) => answered(test) === need);
  state.keyCsv = withId.length ? buildKeyCsv() : '';

  const ids = withId.map((test) => test.id);
  const duplicate = ids.find((id, index) => ids.indexOf(id) !== index);
  if (duplicate) {
    say('msg-tests', 'bad',
        'Test ID ' + duplicate + ' is used twice. Each test needs its own.');
  } else if (withId.length) {
    say('msg-tests', '', '');
  } else if (state.tests.some((test) => test.name)) {
    say('msg-tests', 'warn', 'Every test needs a Test ID.');
  } else {
    say('msg-tests', '', '');
  }
  setStep('step-tests', withId.length > 0 && !duplicate, 'hint-tests',
          withId.length ? withId.length + ' ' + plural(withId.length, 'test', 'tests')
                        : 'No tests yet');

  // Provenance, then readiness.
  const lines = [];
  const clauses = [];
  if (state.keySource) {
    clauses.push('Loaded ' + state.keySource.count + ' ' +
                 plural(state.keySource.count, 'test', 'tests') + ' from ' +
                 state.keySource.file);
  }
  const overwritten = namedTests().filter((test) => test.dirty)
    .map((test) => test.id || test.name);
  if (overwritten.length) {
    const clause = plural(overwritten.length, 'test', 'tests') + ' ' +
                   quotedList(overwritten) + ' ' +
                   plural(overwritten.length, 'was', 'were') +
                   ' manually overwritten';
    clauses.push(clauses.length ? 'and ' + clause
                                : clause[0].toUpperCase() + clause.slice(1));
  }
  if (clauses.length) lines.push(clauses.join(', ') + '.');

  let kind = 'ok';
  if (!withId.length) {
    kind = '';
  } else if (complete.length < withId.length) {
    kind = 'warn';
    lines.push(complete.length + ' of ' + withId.length + ' ' +
               plural(withId.length, 'test has', 'tests have') +
               ' a full set of ' + need + ' answers.');
  } else {
    lines.push(complete.length + ' ' + plural(complete.length, 'test', 'tests') +
               ' ready, ' + need + ' answers each.');
  }
  say('msg-key', duplicate ? 'bad' : kind, lines.join('\n'));

  // The file is worth offering once it holds work that is not already on disk:
  // either the answers were typed here, or a loaded file has been changed
  // since. Otherwise the operator already has the file and does not need a
  // second copy of it.
  const answersExist = state.tests.some((test) => answered(test) > 0);
  $('download-key').hidden =
    !answersExist || (!!state.keySource && !overwritten.length);
  $('undo-key').hidden = !state.undo;

  setStep('step-key', withId.length > 0 && complete.length === withId.length,
          'hint-key',
          withId.length ? complete.length + ' of ' + withId.length + ' complete'
                        : 'No answers yet');
  save();
}

// --- reading a key file ----------------------------------------------------

/* A real CSV parse: a quoted cell may contain commas and newlines, which a
   split on "\n" would tear in half. Also drops a leading byte-order mark,
   which Excel writes and which would otherwise hide the first row label. */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;
  const body = text.replace(/^﻿/, '');
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (quoted) {
      if (ch === '"' && body[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ',') {
      row.push(cell); cell = '';
    } else if (ch === '\r') {
      // handled by the \n that follows, or ends the row on its own
      if (body[i + 1] !== '\n') { row.push(cell); rows.push(row); row = []; cell = ''; }
    } else if (ch === '\n') {
      row.push(cell); rows.push(row); row = []; cell = '';
    } else {
      cell += ch;
    }
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return rows.map((cells) => cells.map((item) => item.trim()));
}

/* "Test ID", "test id", "TestID" and "Test_ID" all mean the same row. */
function normalizeLabel(label) {
  return String(label || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function readKeyCsv(text, filename) {
  const rows = parseCsv(text).filter((row) => row.some((cell) => cell !== ''));
  const rowFor = (label) => {
    const want = normalizeLabel(label);
    const found = rows.find((row) => normalizeLabel(row[0]) === want);
    return found ? found.slice(1) : null;
  };

  const ids = rowFor('Test ID');
  if (!ids) {
    const labels = rows.slice(0, 6).map((row) => row[0])
      .filter((item) => item !== '');
    throw new Error(
      'There is no "Test ID" row in ' + filename + '. The field names run ' +
      'down the first column: Name, Test ID, Excluded, then 1 to ' +
      questionCount() + '.' +
      (labels.length ? '\n\nThe first column of that file starts: ' +
                       labels.join(', ') + '.' : ''));
  }
  if (!ids.some((id) => id !== '')) {
    throw new Error(
      'The "Test ID" row in ' + filename + ' is empty, so there is no test to ' +
      'load. Put each test\'s ID in that row, one per column.');
  }

  const names = rowFor('Name') || [];
  const excluded = rowFor('Excluded') || [];
  const digits = state.limits ? state.limits.test_id_digits : 4;
  const need = questionCount();
  const numbered = new Map();
  rows.forEach((row) => {
    if (/^\d+$/.test(row[0])) numbered.set(Number(row[0]), row.slice(1));
  });

  const loaded = [];
  ids.forEach((id, column) => {
    if (!id && !names[column]) return;
    const answers = [];
    for (let number = 1; number <= need; number++) {
      const row = numbered.get(number);
      answers.push(((row && row[column]) || '').toUpperCase());
    }
    loaded.push(Object.assign(newTest(), {
      name: names[column] || '',
      id: /^\d+$/.test(id) ? id.padStart(digits, '0') : id,
      excluded: (excluded[column] || '').split(',')
        .map((item) => item.trim()).filter(Boolean),
      answers: answers,
    }));
  });
  return loaded;
}

/* Merge by Test ID rather than replacing: a file with one corrected test
   should not delete the other two. */
function uploadKey(file) {
  const reader = new FileReader();
  reader.onload = () => {
    let loaded;
    try {
      loaded = readKeyCsv(String(reader.result), file.name);
    } catch (error) {
      return say('msg-key', 'bad', error.message);
    }
    state.undo = {
      tests: JSON.parse(JSON.stringify(state.tests)),
      keySource: state.keySource,
    };
    const merged = namedTests();
    const had = merged.length;
    let updated = 0;
    loaded.forEach((incoming) => {
      const match = merged.find((test) => test.id && test.id === incoming.id);
      // The file is now the record of what this test says, so its answers stop
      // counting as hand-edited.
      if (match) { Object.assign(match, incoming); updated++; }
      else merged.push(incoming);
    });
    const kept = had - updated;
    state.tests = merged.length ? merged : [newTest()];
    state.keySource = { file: file.name, count: loaded.length };
    save();
    renderTests();
    if (kept > 0) {
      const box = $('msg-key');
      box.textContent += '\n' + kept + ' ' + plural(kept, 'test', 'tests') +
        ' already here ' + plural(kept, 'was', 'were') + ' not in that file, ' +
        'and ' + plural(kept, 'was', 'were') + ' left alone.';
    }
  };
  reader.readAsText(file);
}

function undoUpload() {
  if (!state.undo) return;
  state.tests = state.undo.tests;
  state.keySource = state.undo.keySource;
  state.undo = null;
  save();
  renderTests();
  const box = $('msg-key');
  box.textContent = 'Upload undone.\n' + box.textContent;
}

// --- 5. thresholds ---------------------------------------------------------

function thresholdSpec() {
  if (state.thresholdMode !== 'manual') return '';
  const parts = ['as', 'ar', 'ms', 'mr'].map((key) => state.threshold[key] || '');
  return parts.every((part) => part === '') ? '' : parts.join(',');
}

function renderThresholds() {
  const manual = state.thresholdMode === 'manual';
  $('thr-override').checked = manual;
  $('thr-manual').hidden = !manual;
  ['as', 'ar', 'ms', 'mr'].forEach((key) => {
    $('thr-' + key).value = state.threshold[key] || '';
  });
  const spec = thresholdSpec();
  setStep('step-thresholds', true, 'hint-thresholds',
          spec ? 'Fixed: ' + spec : 'Automatic');
}

// --- 6. grading ------------------------------------------------------------

function addBatch() {
  state.batches.push({ n: state.nextBatch++, summary: null, results: '',
                       files: [], seconds: 0, review: {} });
  save(); renderBatches();
}

function renderBatches() {
  const host = $('batches');
  host.textContent = '';
  if (!state.batches.length) {
    host.append(el('p', { className: 'note',
      textContent: 'No batches yet. Add one for each PDF from the scanner.' }));
  }
  state.batches.forEach((batch, index) => host.append(batchCard(batch, index)));

  const graded = state.batches.filter((batch) => batch.summary);
  setStep('step-grade', graded.length > 0, 'hint-grade',
          graded.length ? graded.length + ' of ' + state.batches.length + ' graded'
                        : 'Nothing graded');
  renderReview();
}

function batchCard(batch, index) {
  const card = el('div', { className: 'batch' });
  const title = el('h4', {}, ['Batch ' + batch.n]);
  const message = el('div', { className: 'msg' });
  const results = el('div');

  // Once a batch is graded it is a record of a run that happened, with its own
  // key and thresholds. Re-running it with today's settings would quietly
  // disagree with the files already downloaded, so it is closed to editing.
  if (batch.summary) {
    const drop = el('button', { className: 'danger', textContent: 'Remove' });
    drop.onclick = () => {
      if (!confirm('Remove batch ' + batch.n + ' and its saved results? ' +
                   'Anything not downloaded is lost.')) return;
      state.batches.splice(index, 1); save(); renderBatches();
    };
    card.append(title, el('div', { className: 'row' }, [drop]), results);
    results.append(summaryView(batch));
    return card;
  }

  const host = el('label');
  const picker = filePicker(host, {
    accept: '.pdf,.png,.jpg,.jpeg,.tif,.tiff', multiple: true,
    label: 'Choose scan',
  });
  const go = el('button', { className: 'primary', textContent: 'Grade this batch' });
  const drop = el('button', { textContent: 'Remove' });
  drop.onclick = () => { state.batches.splice(index, 1); save(); renderBatches(); };

  const bar = el('div', { className: 'bar' });
  const fill = el('i');
  bar.append(fill);
  bar.hidden = true;

  go.onclick = async () => {
    const files = Array.from(picker.files || []);
    if (!files.length) {
      message.className = 'msg bad';
      message.textContent = 'Choose the scanned PDF for this batch first.';
      return;
    }
    const limit = state.limits ? state.limits.max_upload_bytes : Infinity;
    const total = files.reduce((sum, file) => sum + file.size, 0);
    if (total > limit) {
      message.className = 'msg bad';
      message.textContent =
        'That is ' + (total / 1048576).toFixed(0) + ' MB, over the ' +
        Math.round(limit / 1048576) + ' MB limit. Split it at the scanner ' +
        'into smaller batches and add each one here.';
      return;
    }

    go.disabled = true; drop.disabled = true;
    bar.hidden = false; fill.style.width = '15%';
    message.className = 'msg info';
    message.textContent = 'Uploading and grading ' +
      (total / 1048576).toFixed(1) + ' MB. This takes a few seconds per page.';

    const form = new FormData();
    files.forEach((file) => form.append('scans', file, file.name));
    if (state.keyCsv) {
      form.append('key', new Blob([state.keyCsv], { type: 'text/csv' }),
                  'Keys.csv');
    }
    form.append('batch', String(batch.n));
    form.append('threshold', thresholdSpec());
    form.append('annotate', state.annotate ? 'true' : 'false');
    if (state.sheet) form.append('layout', JSON.stringify(state.sheet));

    const started = performance.now();
    try {
      fill.style.width = '55%';
      const body = await call('/grade', { method: 'POST', body: form });
      fill.style.width = '100%';
      batch.seconds = (performance.now() - started) / 1000;
      batch.summary = body.summary;
      const resultsFile = body.files.find(
        (item) => item.name.endsWith('Results.csv'));
      batch.results = resultsFile ? resultsFile.data : '';
      // Keep only the small text files; PDFs are offered now and dropped.
      batch.files = body.files
        .filter((item) => item.encoding === 'utf-8')
        .map((item) => ({ name: item.name, type: item.type, data: item.data }));
      save();
      body.files.filter((item) => item.encoding === 'base64')
        .forEach((item) => download(item.name.split('/').pop(),
                                    fileFromServer(item)));
      renderBatches();
    } catch (error) {
      message.className = 'msg bad';
      message.textContent = error.message;
      bar.hidden = true;
    } finally {
      go.disabled = false; drop.disabled = false;
    }
  };

  card.append(title, el('div', { className: 'row' }, [host, go, drop]),
              bar, message, results);
  return card;
}

function summaryView(batch) {
  const summary = batch.summary;
  const box = el('div');
  const stat = (value, label) => el('span', { className: 'stat' },
    [el('b', { textContent: String(value) }), label]);

  box.append(el('div', {}, [
    stat(summary.sheets, 'sheets'),
    stat(summary.rows, 'result rows'),
    stat(summary.unclear, 'unclear'),
    stat(summary.missing, 'missing'),
    stat(batch.seconds ? batch.seconds.toFixed(0) + ' s' : '—', 'to grade'),
  ]));

  if (summary.test_not_found) {
    box.append(el('div', { className: 'msg warn' },
      [summary.test_not_found + ' ' +
       plural(summary.test_not_found, 'row has', 'rows have') +
       ' a Test ID that is not in the key. Check the key, or correct the ' +
       'Test ID in step 7.']));
  }
  if (summary.test_not_allowed) {
    box.append(el('div', { className: 'msg warn' },
      [summary.test_not_allowed + ' ' +
       plural(summary.test_not_allowed, 'row is', 'rows are') +
       ' for a test that student’s Latin level may not sit.']));
  }

  box.append(fileList(batch.files));
  return box;
}

function fileList(files) {
  const list = el('ul', { className: 'files' });
  files.forEach((item) => {
    const get = el('button', { textContent: 'Download' });
    get.onclick = () => download(item.name.split('/').pop(), item.data, item.type);
    list.append(el('li', {}, [
      el('span', { className: 'name', textContent: item.name }), get]));
  });
  return list;
}

// --- 7. review -------------------------------------------------------------

function renderReview() {
  const host = $('review-batches');
  host.textContent = '';
  const pending = state.batches.filter(
    (batch) => batch.summary &&
               (batch.summary.unclear || batch.summary.missing));

  if (!state.batches.some((batch) => batch.summary)) {
    host.append(el('p', { className: 'note',
                          textContent: 'Grade a batch first.' }));
    setStep('step-review', false, 'hint-review', 'Nothing to review');
    return;
  }
  if (!pending.length) {
    host.append(el('p', { className: 'msg ok', textContent:
      'Every mark was read confidently. There is nothing to fix.' }));
    setStep('step-review', true, 'hint-review', 'All clear');
    return;
  }

  pending.forEach((batch) => host.append(reviewCard(batch)));
  setStep('step-review', false, 'hint-review',
          pending.length + ' ' + plural(pending.length, 'batch', 'batches') +
          ' to fix');
}

function reviewCard(batch) {
  const card = el('div', { className: 'batch' });
  const reviewFiles = batch.files.filter(
    (item) => /Unclear|Missing/.test(item.name));

  const host = el('label');
  const picker = filePicker(host, { accept: '.csv', multiple: true,
                                    label: 'Choose corrections' });
  const go = el('button', { className: 'primary',
                            textContent: 'Apply and re-score' });
  const message = el('div', { className: 'msg' });

  go.onclick = async () => {
    const files = Array.from(picker.files || []);
    if (!files.length) {
      message.className = 'msg bad';
      message.textContent = 'Choose the corrected CSV files first.';
      return;
    }
    if (!batch.results) {
      message.className = 'msg bad';
      message.textContent = 'This batch has no saved results to re-score.';
      return;
    }
    go.disabled = true;
    message.className = 'msg info';
    message.textContent = 'Re-scoring. The scans are not needed for this.';

    const form = new FormData();
    form.append('results', new Blob([batch.results], { type: 'text/csv' }),
                'Results.csv');
    files.forEach((file) => form.append('overrides', file, file.name));
    if (state.keyCsv) {
      form.append('key', new Blob([state.keyCsv], { type: 'text/csv' }),
                  'Keys.csv');
    }
    if (state.sheet) form.append('layout', JSON.stringify(state.sheet));

    try {
      const body = await call('/regrade', { method: 'POST', body: form });
      const resultsFile = body.files.find(
        (item) => item.name.endsWith('Results.csv'));
      if (resultsFile) batch.results = resultsFile.data;
      batch.files = body.files.filter((item) => item.encoding === 'utf-8')
        .map((item) => ({ name: item.name, type: item.type, data: item.data }));
      batch.summary = Object.assign({}, batch.summary, {
        unclear: 0, missing: 0,
        scored: body.summary.scored,
        statuses: body.summary.statuses,
      });
      save();
      const applied = body.summary.corrections_applied;
      message.className = 'msg ok';
      message.textContent =
        applied + ' ' + plural(applied, 'correction', 'corrections') +
        ' applied, ' + body.summary.scored + ' rows scored. Download the ' +
        'updated Results.csv above.';
      renderBatches();
    } catch (error) {
      message.className = 'msg bad';
      message.textContent = error.message;
    } finally {
      go.disabled = false;
    }
  };

  card.append(
    el('h4', {}, ['Batch ' + batch.n]),
    el('p', { className: 'note', textContent:
      batch.summary.unclear + ' unclear, ' + batch.summary.missing +
      ' missing. Download these, correct them, then bring them back.' }),
    fileList(reviewFiles),
    el('div', { className: 'row', style: 'margin-top:.6rem' }, [host, go]),
    message);
  return card;
}

// --- wiring ----------------------------------------------------------------

function start() {
  $('endpoint').value = state.endpoint;
  $('passphrase').value = state.passphrase;
  $('annotate').checked = state.annotate;

  $('connect').onclick = connect;

  $('make-sheet').onclick = makeSheet;
  $('reset-sheet').onclick = () => {
    if (!state.limits) return say('msg-sheet', 'bad', 'Connect first.');
    state.sheet = JSON.parse(JSON.stringify(state.limits.defaults));
    save(); renderSheetForm(); renderTests();
    say('msg-sheet', 'ok', 'Back to the standard wording.');
  };
  $('sheet-title').oninput = () => save();
  $('directions').oninput = () => save();

  $('add-test').onclick = () => {
    state.tests.push(newTest());
    save(); renderTests();
  };

  $('download-template').onclick = () => {
    if (!state.tests.some((test) => test.id || test.name)) {
      return say('msg-key', 'bad',
                 'Add at least one test in step 3 first, so the template has ' +
                 'a column to fill in.');
    }
    download('Keys.csv', buildKeyCsv(false), 'text/csv');
    say('msg-key', 'ok',
        'Template downloaded. Fill in the numbered rows, save it as CSV, then ' +
        'bring it back here.');
  };
  $('download-key').onclick = () => {
    if (!state.keyCsv) {
      return say('msg-key', 'bad', 'Add at least one test with a Test ID.');
    }
    download('Keys.csv', state.keyCsv, 'text/csv');
  };
  const keyHost = $('pick-key');
  filePicker(keyHost, { accept: '.csv', label: 'Upload Keys.csv' });
  keyHost.onpicked = (files) => { if (files[0]) uploadKey(files[0]); };
  $('undo-key').onclick = undoUpload;

  $('thr-override').onchange = () => {
    state.thresholdMode = $('thr-override').checked ? 'manual' : 'auto';
    save(); renderThresholds();
  };
  ['as', 'ar', 'ms', 'mr'].forEach((key) => {
    $('thr-' + key).oninput = () => {
      state.threshold[key] = $('thr-' + key).value; save(); renderThresholds();
    };
  });

  $('add-batch').onclick = addBatch;
  $('annotate').onchange = () => {
    state.annotate = $('annotate').checked; save();
  };

  $('reset').onclick = () => {
    if (!confirm('Clear the tests, answers, thresholds and every graded ' +
                 'batch saved in this browser? This cannot be undone.')) return;
    try {
      localStorage.removeItem(STORE_KEY);
    } catch (error) {
      console.warn('Could not clear saved work:', error);
    }
    location.reload();
  };

  renderSheetForm();
  renderTests();
  renderThresholds();
  renderBatches();
  renderLimits();
  if (state.endpoint && state.passphrase) connect();
}

document.addEventListener('DOMContentLoaded', start);
