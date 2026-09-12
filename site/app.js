/*
 * The grading wizard.
 *
 * Everything the operator types lives in localStorage under one key, so the
 * page can be closed and come back to mid-convention. Scans are never stored:
 * they go straight from the file input to the server and the results come
 * back in the same response. Results.csv is small enough to keep (about 33 KB
 * for a hundred students) and is kept, because step 7 needs it to re-score
 * without the scans. Marked-up PDFs are offered as a download and then
 * dropped.
 */
'use strict';

const STORE_KEY = 'jcl-grading-v1';
const DEFAULT_LEVEL_COUNT = 7;

const state = load();

function blank() {
  return {
    endpoint: '', passphrase: '',
    sheet: null,                 // filled from /health defaults on connect
    tests: [{ name: '', id: '', excluded: [], answers: '' }],
    keyCsv: '',
    thresholdMode: 'auto',
    threshold: { as: '', ar: '', ms: '', mr: '' },
    annotate: false,
    batches: [],                 // {id, label, summary, results, review:{}}
    limits: null,
  };
}

function load() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? Object.assign(blank(), JSON.parse(raw)) : blank();
  } catch (error) {
    console.warn('Could not read saved work:', error);
    return blank();
  }
}

function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch (error) {
    // Quota is the only realistic failure, and only if someone has kept an
    // improbable number of batches. Say so rather than failing silently.
    say('msg-grade', 'bad',
        'This browser will not store any more results. Download what you ' +
        'have, then use "Start a new convention" to clear the saved work.');
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

function setStep(id, done, hintId, hint) {
  const step = $(id);
  if (step) step.dataset.state = done ? 'done' : '';
  if (hintId && $(hintId)) $(hintId).textContent = hint;
}

/* Open a later step once the one before it is done, but only the first time:
   re-opening something the operator has deliberately collapsed is worse than
   leaving it shut. */
const advanced = new Set();
function advance(id) {
  if (advanced.has(id)) return;
  advanced.add(id);
  const step = $(id);
  if (step && !step.open) step.open = true;
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
    ['step-sheet', 'step-key', 'step-thresholds', 'step-scan', 'step-grade',
     'step-review'].forEach(advance);
  } catch (error) {
    say('msg-connect', 'bad', error.message);
    setStep('step-connect', false, 'hint-connect', 'Not connected');
  }
}

function renderLimits() {
  const limits = state.limits;
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
  if (!sheet) return;
  $('sheet-title').value = sheet.title;
  $('directions').value = sheet.directions.join('\n');

  const levels = $('levels');
  levels.textContent = '';
  sheet.latin_levels.forEach((level, index) => {
    const input = el('input', { type: 'text', value: level, maxLength: 10 });
    input.oninput = () => { sheet.latin_levels[index] = input.value; save(); renderTests(); };
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
    advance('step-key');
  } catch (error) {
    say('msg-sheet', 'bad', error.message);
  }
}

// --- 3. the key ------------------------------------------------------------

function renderTests() {
  const body = document.querySelector('#tests tbody');
  body.textContent = '';
  const levels = state.sheet ? state.sheet.latin_levels : [];

  state.tests.forEach((test, index) => {
    const name = el('input', { type: 'text', value: test.name,
                               placeholder: 'Latin Literature' });
    name.oninput = () => { test.name = name.value; save(); renderAnswers(); updateKeyHint(); };

    const id = el('input', { type: 'text', value: test.id, maxLength: 4,
                             placeholder: '1001', inputMode: 'numeric' });
    id.oninput = () => { test.id = id.value.replace(/\D/g, ''); id.value = test.id;
                         save(); renderAnswers(); updateKeyHint(); };

    const chips = el('div', { className: 'chips' });
    levels.forEach((level) => {
      const box = el('input', { type: 'checkbox',
                                checked: test.excluded.includes(level) });
      box.onchange = () => {
        test.excluded = box.checked
          ? test.excluded.concat([level])
          : test.excluded.filter((item) => item !== level);
        save();
      };
      chips.append(el('label', {}, [box, level]));
    });

    const remove = el('button', { textContent: '×', title: 'Remove this test' });
    remove.onclick = () => {
      state.tests.splice(index, 1);
      if (!state.tests.length) state.tests.push({ name: '', id: '', excluded: [], answers: '' });
      save(); renderTests(); renderAnswers(); updateKeyHint();
    };

    body.append(el('tr', {}, [
      el('td', {}, [name]), el('td', {}, [id]),
      el('td', {}, [chips]), el('td', {}, [remove]),
    ]));
  });
  renderAnswers();
  updateKeyHint();
}

function renderAnswers() {
  const host = $('answers');
  host.textContent = '';
  state.tests.forEach((test) => {
    const label = (test.name || 'Untitled test') +
                  (test.id ? ' (' + test.id + ')' : '');
    const box = el('textarea', { rows: 3, value: test.answers || '',
                                 placeholder: 'A B C D E …' });
    const count = el('span', { className: 'note' });
    const refresh = () => {
      const parsed = parseAnswers(box.value);
      const need = state.limits ? state.limits.questions_per_test : 80;
      count.textContent = parsed.length + ' of ' + need + ' answers';
      count.style.color = parsed.length === need ? 'var(--good)'
                        : parsed.length ? 'var(--warn)' : 'var(--ink-faint)';
    };
    box.oninput = () => { test.answers = box.value; save(); refresh(); updateKeyHint(); };
    refresh();
    host.append(el('label', {}, [
      el('span', { className: 'lab', textContent: label }), box, count,
    ]));
  });
}

/* Accept "A B C", "A,B,C", one per line, or one unbroken "ABC" string. */
function parseAnswers(raw) {
  const text = (raw || '').trim();
  if (!text) return [];
  if (/[\s,]/.test(text)) {
    return text.split(/[\s,]+/).filter((item) => item !== '');
  }
  // No separators: a run of letters, one answer each. Alternatives need a
  // separator to be unambiguous, so a bare "A|BD" is treated as one answer.
  if (text.includes('|')) return [text];
  return text.split('');
}

function csvCell(value) {
  const text = String(value == null ? '' : value);
  return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

function buildKeyCsv() {
  const need = state.limits ? state.limits.questions_per_test : 80;
  const tests = state.tests.filter((test) => test.id || test.name);
  const rows = [
    ['Name'].concat(tests.map((test) => test.name)),
    ['Test ID'].concat(tests.map((test) => test.id)),
    ['Excluded'].concat(tests.map((test) => test.excluded.join(', '))),
  ];
  const answers = tests.map((test) => parseAnswers(test.answers));
  for (let number = 1; number <= need; number++) {
    rows.push([String(number)].concat(
      answers.map((list) => list[number - 1] || '')));
  }
  return rows.map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

function updateKeyHint() {
  const tests = state.tests.filter((test) => test.id);
  const need = state.limits ? state.limits.questions_per_test : 80;
  const complete = tests.filter(
    (test) => parseAnswers(test.answers).length === need);
  state.keyCsv = tests.length ? buildKeyCsv() : '';
  save();

  const ids = tests.map((test) => test.id);
  const duplicate = ids.find((id, index) => ids.indexOf(id) !== index);
  if (duplicate) {
    say('msg-key', 'bad',
        'Test ID ' + duplicate + ' is used twice. Each test needs its own.');
  } else if (tests.length && complete.length < tests.length) {
    say('msg-key', 'warn',
        complete.length + ' of ' + tests.length + ' tests have a full set of ' +
        need + ' answers. You can still download the file and finish it in a ' +
        'spreadsheet.');
  } else if (tests.length) {
    say('msg-key', 'ok', tests.length + ' test' +
        (tests.length === 1 ? '' : 's') + ' ready.');
  } else {
    say('msg-key', '', '');
  }

  setStep('step-key', tests.length > 0 && !duplicate, 'hint-key',
          tests.length ? tests.length + ' test' + (tests.length === 1 ? '' : 's')
                       : 'No tests yet');
}

function uploadKey(file) {
  const reader = new FileReader();
  reader.onload = () => {
    state.keyCsv = String(reader.result);
    // Read the three header rows back so the table reflects the file.
    const rows = state.keyCsv.split(/\r?\n/).map(splitCsvLine);
    const find = (label) => (rows.find((row) => row[0] === label) || []).slice(1);
    const names = find('Name'), ids = find('Test ID'), excluded = find('Excluded');
    if (!ids.length) {
      return say('msg-key', 'bad',
                 'That file has no "Test ID" row. It should run down the ' +
                 'page: Name, Test ID, Excluded, then 1 to 80.');
    }
    const answerRows = rows.filter((row) => /^\d+$/.test(row[0]));
    state.tests = ids.map((id, column) => ({
      name: names[column] || '', id: id,
      excluded: (excluded[column] || '').split(',')
        .map((item) => item.trim()).filter(Boolean),
      answers: answerRows.map((row) => row[column + 1] || '').join(' ').trim(),
    }));
    save(); renderTests();
    say('msg-key', 'ok', 'Loaded ' + state.tests.length + ' tests from the file.');
  };
  reader.readAsText(file);
}

function splitCsvLine(line) {
  const out = []; let cell = ''; let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { out.push(cell); cell = ''; }
    else cell += ch;
  }
  out.push(cell);
  return out.map((item) => item.trim());
}

// --- 4. thresholds ---------------------------------------------------------

function thresholdSpec() {
  if (state.thresholdMode !== 'manual') return '';
  const parts = ['as', 'ar', 'ms', 'mr'].map((key) => state.threshold[key] || '');
  return parts.every((part) => part === '') ? '' : parts.join(',');
}

function renderThresholds() {
  const manual = state.thresholdMode === 'manual';
  document.querySelector('input[name=thr][value=' +
    (manual ? 'manual' : 'auto') + ']').checked = true;
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
  const next = String(state.batches.length + 1);
  state.batches.push({ id: next, label: next, summary: null, results: '',
                       files: [], review: {} });
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
  const label = el('input', { type: 'text', value: batch.label,
                              style: 'width:5rem; display:inline-block' });
  label.oninput = () => { batch.label = label.value; save(); };

  const picker = el('input', { type: 'file', accept: '.pdf,.png,.jpg,.jpeg,.tif,.tiff',
                               multiple: true });
  const go = el('button', { className: 'primary', textContent: 'Grade this batch' });
  const drop = el('button', { textContent: 'Remove' });
  drop.onclick = () => { state.batches.splice(index, 1); save(); renderBatches(); };

  const bar = el('div', { className: 'bar' });
  const fill = el('i');
  bar.append(fill);
  bar.hidden = true;
  const message = el('div', { className: 'msg' });
  const results = el('div');

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
    form.append('batch', batch.label || '');
    form.append('threshold', thresholdSpec());
    form.append('annotate', state.annotate ? 'true' : 'false');
    if (state.sheet) form.append('layout', JSON.stringify(state.sheet));

    try {
      fill.style.width = '55%';
      const body = await call('/grade', { method: 'POST', body: form });
      fill.style.width = '100%';
      batch.summary = body.summary;
      const resultsFile = body.files.find((item) => item.name.endsWith('Results.csv'));
      batch.results = resultsFile ? resultsFile.data : '';
      // Keep only the small text files; PDFs are offered now and dropped.
      batch.files = body.files
        .filter((item) => item.encoding === 'utf-8')
        .map((item) => ({ name: item.name, type: item.type, data: item.data }));
      save();
      body.files.filter((item) => item.encoding === 'base64')
        .forEach((item) => download(item.name.split('/').pop(),
                                    fileFromServer(item)));
      message.className = 'msg ok';
      message.textContent = 'Graded.';
      renderBatches();
    } catch (error) {
      message.className = 'msg bad';
      message.textContent = error.message;
      bar.hidden = true;
    } finally {
      go.disabled = false; drop.disabled = false;
    }
  };

  card.append(
    el('h4', {}, ['Batch ', label]),
    el('div', { className: 'row' }, [picker, go, drop]),
    bar, message, results);

  if (batch.summary) results.append(summaryView(batch));
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
  ]));

  if (summary.test_not_found) {
    box.append(el('div', { className: 'msg warn' },
      [summary.test_not_found + ' row(s) have a Test ID that is not in the ' +
       'key. Check the key, or correct the Test ID in step 7.']));
  }
  if (summary.test_not_allowed) {
    box.append(el('div', { className: 'msg warn' },
      [summary.test_not_allowed + ' row(s) are for a test that student’s ' +
       'Latin level may not sit.']));
  }
  if (summary.thresholds && summary.thresholds.length) {
    box.append(el('p', { className: 'note' },
      ['Thresholds ' + summary.thresholds_note + ': ',
       el('code', { textContent: summary.thresholds[0].spec })]));
  }

  const list = el('ul', { className: 'files' });
  batch.files.forEach((item) => {
    const get = el('button', { textContent: 'Download' });
    get.onclick = () => download(item.name.split('/').pop(),
                                 item.data, item.type);
    list.append(el('li', {}, [
      el('span', { className: 'name', textContent: item.name }), get]));
  });
  box.append(list);
  return box;
}

// --- 7. review -------------------------------------------------------------

function renderReview() {
  const host = $('review-batches');
  host.textContent = '';
  const pending = state.batches.filter(
    (batch) => batch.summary && (batch.summary.unclear || batch.summary.missing));

  if (!state.batches.some((batch) => batch.summary)) {
    host.append(el('p', { className: 'note', textContent: 'Grade a batch first.' }));
    setStep('step-review', false, 'hint-review', 'Nothing to review');
    return;
  }
  if (!pending.length) {
    host.append(el('p', { className: 'msg ok',
      textContent: 'Every mark was read confidently. There is nothing to settle.' }));
    setStep('step-review', true, 'hint-review', 'All clear');
    return;
  }

  pending.forEach((batch) => host.append(reviewCard(batch)));
  setStep('step-review', false, 'hint-review',
          pending.length + ' batch' + (pending.length === 1 ? '' : 'es') +
          ' to settle');
}

function reviewCard(batch) {
  const card = el('div', { className: 'batch' });
  const reviewFiles = batch.files.filter(
    (item) => /Unclear|Missing/.test(item.name));

  const list = el('ul', { className: 'files' });
  reviewFiles.forEach((item) => {
    const get = el('button', { textContent: 'Download' });
    get.onclick = () => download(item.name.split('/').pop(), item.data, item.type);
    list.append(el('li', {}, [
      el('span', { className: 'name', textContent: item.name }), get]));
  });

  const picker = el('input', { type: 'file', accept: '.csv', multiple: true });
  const go = el('button', { className: 'primary', textContent: 'Apply and re-score' });
  const message = el('div', { className: 'msg' });

  go.onclick = async () => {
    const files = Array.from(picker.files || []);
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
      const resultsFile = body.files.find((item) => item.name.endsWith('Results.csv'));
      if (resultsFile) batch.results = resultsFile.data;
      batch.files = body.files.filter((item) => item.encoding === 'utf-8')
        .map((item) => ({ name: item.name, type: item.type, data: item.data }));
      batch.summary = Object.assign({}, batch.summary, {
        unclear: 0, missing: 0,
        scored: body.summary.scored,
        statuses: body.summary.statuses,
      });
      save();
      message.className = 'msg ok';
      message.textContent =
        body.summary.corrections_applied + ' correction' +
        (body.summary.corrections_applied === 1 ? '' : 's') +
        ' applied, ' + body.summary.scored + ' rows scored. ' +
        'Download the updated Results.csv below.';
      renderBatches();
    } catch (error) {
      message.className = 'msg bad';
      message.textContent = error.message;
    } finally {
      go.disabled = false;
    }
  };

  card.append(
    el('h4', {}, ['Batch ' + batch.label]),
    el('p', { className: 'note', textContent:
      batch.summary.unclear + ' unclear, ' + batch.summary.missing +
      ' missing. Download these, correct them in Sheets, then bring them back.' }),
    list,
    el('div', { className: 'row', style: 'margin-top:.7rem' }, [picker, go]),
    message);
  return card;
}

// --- wiring ----------------------------------------------------------------

function start() {
  $('endpoint').value = state.endpoint;
  $('passphrase').value = state.passphrase;
  $('annotate').checked = state.annotate;

  $('connect').onclick = connect;
  $('forget').onclick = () => {
    if (!confirm('Clear everything saved in this browser — server details, ' +
                 'sheet wording, the key and all graded results?')) return;
    localStorage.removeItem(STORE_KEY);
    location.reload();
  };

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
    state.tests.push({ name: '', id: '', excluded: [], answers: '' });
    save(); renderTests();
  };
  $('download-key').onclick = () => {
    if (!state.keyCsv) {
      return say('msg-key', 'bad', 'Add at least one test with a Test ID.');
    }
    download('Keys.csv', state.keyCsv, 'text/csv');
  };
  $('upload-key').onchange = (event) => {
    if (event.target.files[0]) uploadKey(event.target.files[0]);
  };

  document.querySelectorAll('input[name=thr]').forEach((radio) => {
    radio.onchange = () => {
      state.thresholdMode = radio.value; save(); renderThresholds();
    };
  });
  ['as', 'ar', 'ms', 'mr'].forEach((key) => {
    $('thr-' + key).oninput = () => {
      state.threshold[key] = $('thr-' + key).value; save(); renderThresholds();
    };
  });

  $('reset').onclick = () => {
    if (!confirm('Clear the tests, keys, thresholds and every graded batch ' +
                 'saved in this browser? This cannot be undone.')) return;
    try {
      localStorage.removeItem(STORE_KEY);
    } catch (error) {
      console.warn('Could not clear saved work:', error);
    }
    location.reload();
  };

  $('add-batch').onclick = addBatch;
  $('annotate').onchange = () => { state.annotate = $('annotate').checked; save(); };

  if (state.sheet) renderSheetForm();
  renderTests();
  renderThresholds();
  renderBatches();
  renderLimits();
  if (state.endpoint && state.passphrase) connect();
}

document.addEventListener('DOMContentLoaded', start);
