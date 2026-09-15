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
const MAX_DIRECTIONS = 12;     // what fits above the bubbles
const VOID = 'X';              // a question deliberately not scored

const state = load();

function blank() {
  return {
    endpoint: '', passphrase: '',
    sheet: null,                 // filled from /health defaults on connect
    tests: [newTest()],
    keyCsv: '',
    keySource: null,             // {file, count} once a file has been loaded
    undo: null,                  // {tests, keySource} as they were before an upload
    tidyUndo: null,              // paste boxes as they were before a tidy
    thresholdMode: 'auto',
    threshold: { as: '', ar: '', ms: '', mr: '' },
    annotate: false,
    annotateWho: '',             // '', 'unknown', or 'list'
    annotateIds: '',
    onlyTests: null,             // null grades every test on the sheet
    sides: null,                 // null means both sides were scanned
    skipBlanks: false,
    batches: [],
    nextBatch: 1,
    limits: null,
  };
}

function newTest() {
  return {
    // Which levels may take this test, held as positions in the sheet's list
    // rather than names: renaming level 7 in step 2 must not strand a test
    // that still says "HS-Adv". null means every level.
    name: '', id: '', allowed: null, answers: [],
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
  // Older saved work named the levels, and older still named the excluded
  // ones. Both become positions.
  const levels = (result.sheet && result.sheet.latin_levels) || [];
  const positionOf = (name) => levels.findIndex(
    (level) => level.toUpperCase() === String(name).toUpperCase());
  result.tests.forEach((test) => {
    if (Array.isArray(test.excluded) && levels.length && test.allowed == null) {
      test.allowed = levels
        .map((level, index) => (test.excluded.includes(level) ? -1 : index))
        .filter((index) => index >= 0);
    } else if (Array.isArray(test.allowed) &&
               test.allowed.some((item) => typeof item === 'string')) {
      test.allowed = test.allowed.map(positionOf).filter((index) => index >= 0);
    }
    delete test.excluded;
  });
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

/* "a", "a and b", "a, b, and c" - without the quotes quotedList adds. */
/* "a", "a and b", "a, b, and c". Separate from quotedPlain, which is only
   for the kinds of review a batch is waiting on and has its own fallback. */
function joinList(items, conjunction) {
  const list = (items || []).filter(Boolean);
  const word = conjunction || 'and';
  if (!list.length) return '';
  if (list.length === 1) return list[0];
  if (list.length === 2) return list[0] + ' ' + word + ' ' + list[1];
  return list.slice(0, -1).join(', ') + ', ' + word + ' ' +
         list[list.length - 1];
}

function quotedPlain(items) {
  const list = (items || []).slice();
  if (!list.length) return 'unclear or missing';
  if (list.length === 1) return list[0];
  if (list.length === 2) return list[0] + ' or ' + list[1];
  return list.slice(0, -1).join(', ') + ', or ' + list[list.length - 1];
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

/* Sheet wording from an invite link, applied once /health has supplied the
   defaults it was expressed against. */
let invited = null;

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

function levelNames() {
  return state.sheet ? state.sheet.latin_levels.slice() : [];
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

/* A file input dressed as a button, with what was chosen sitting beside it
   rather than inside it — in the button it read as part of the label. */
function filePicker(host, { accept, multiple = false, label = 'Choose file' }) {
  host.className = 'picker';
  host.textContent = '';
  const input = el('input', { type: 'file', accept: accept, multiple: multiple });
  const button = el('label', { className: 'filebtn' },
    [input, el('span', { className: 'pick', textContent: label })]);
  const chosen = el('span', { className: 'chosen',
                              textContent: 'no file uploaded' });
  input.onchange = () => {
    const files = Array.from(input.files || []);
    chosen.textContent = !files.length ? 'no file uploaded'
      : files.length === 1 ? files[0].name
      : files.length + ' files';
    if (host.onpicked) host.onpicked(files);
  };
  host.append(button, chosen);
  return input;
}

/* An invite carries the server details in the URL *fragment*, which browsers
   never send to a web server. It is read once and then wiped from the address
   bar, so it does not sit in the history of the machine that opened it. The
   passphrase is still in whatever the link was sent through, which is why the
   page says so in as many words. */
/* Only what differs from the server's own defaults, so a link stays short
   when nothing has been customised. */
function customSheet() {
  const sheet = state.sheet;
  const defaults = state.limits && state.limits.defaults;
  if (!sheet || !defaults) return null;
  const changed = {};
  ['title', 'latin_levels', 'write_in_labels', 'directions'].forEach((field) => {
    if (JSON.stringify(sheet[field]) !== JSON.stringify(defaults[field])) {
      changed[field] = sheet[field];
    }
  });
  return Object.keys(changed).length ? changed : null;
}

/* `withDesign` adds the sheet wording and the advanced settings. The tests,
   answers and results never travel: they would make the link unwieldy, and
   Keys.csv is already a file made to be passed around. */
/* Rewrite the script's level list to match this sheet. The copy on the
   server carries the stock names; substituting them here is what keeps the
   spreadsheet's dropdown from disagreeing with the paper.

   The \r?\n is not decoration: the file is served exactly as it sits in the
   repository, which on a Windows checkout means CRLF. */
function scriptWithLevels(body) {
  const levels = levelNames();
  if (!levels.length) return body;
  const listed = levels.map(
    (name) => "'" + String(name).replace(/\\/g, '\\\\')
                                .replace(/'/g, "\\'") + "'").join(', ');
  return body.replace(
    /\/\* LATIN_LEVELS \*\/\r?\nvar LATIN_LEVELS = \[[^\]]*\];/,
    '/* LATIN_LEVELS */\nvar LATIN_LEVELS = [' + listed + '];');
}

/* Hand over Sheet.gs with this sheet's Latin levels written into it. */
async function downloadScript() {
  let body;
  try {
    const response = await fetch('Sheet.gs');
    if (!response.ok) throw new Error('HTTP ' + response.status);
    body = await response.text();
  } catch (error) {
    return say('msg-review', 'bad',
               'Could not fetch Sheet.gs (' + error.message +
               '). Reload the page and try again.');
  }
  download('Sheet.gs', scriptWithLevels(body), 'text/plain');
  say('msg-review', 'ok',
      'Sheet.gs downloaded, carrying this sheet\u2019s Latin levels.');
}

function inviteLink(withDesign) {
  const payload = JSON.stringify({
    e: $('endpoint').value.trim(),
    p: $('passphrase').value,
    s: withDesign ? (customSheet() || undefined) : undefined,
    t: withDesign && state.thresholdMode === 'manual'
      ? state.threshold : undefined,
    o: withDesign && Array.isArray(state.onlyTests)
      ? state.onlyTests : undefined,
  });
  const bytes = new TextEncoder().encode(payload);
  const base64 = btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return location.origin + location.pathname + '#k=' + base64;
}

function readInvite() {
  const match = /[#&]k=([A-Za-z0-9\-_]+)/.exec(location.hash || '');
  if (!match) return false;
  // Wipe it whatever happens next, so a bad link is not left lying around.
  history.replaceState(null, '', location.pathname + location.search);
  try {
    const base64 = match[1].replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    if (!payload.e) return false;
    state.endpoint = payload.e;
    state.passphrase = payload.p || '';
    $('endpoint').value = state.endpoint;
    $('passphrase').value = state.passphrase;
    if (payload.t) {
      state.thresholdMode = 'manual';
      state.threshold = Object.assign(blank().threshold, payload.t);
    }
    if (Array.isArray(payload.o)) state.onlyTests = payload.o;
    // The sheet wording has to wait for the defaults it is a diff against.
    invited = payload.s || null;
    save();
    return true;
  } catch (error) {
    console.warn('Could not read that invite link:', error);
    return false;
  }
}

async function copyInvite(withDesign) {
  const box = withDesign ? 'msg-sheet' : 'msg-connect';
  if (!$('endpoint').value.trim()) {
    return say(box, 'bad',
               'Connect in step 1 first, then copy the link.');
  }
  const link = inviteLink(withDesign);
  const what = withDesign
    ? 'Link copied, carrying the server details and this sheet design.'
    : 'Invite link copied.';
  try {
    await navigator.clipboard.writeText(link);
    say(box, 'ok', what + ' It contains the passphrase, so send it privately.');
  } catch (error) {
    // Clipboard access can be refused; show the link so it can be copied out.
    say(box, 'warn',
        'This browser would not let the page use the clipboard. Copy the ' +
        'link by hand:\n\n' + link);
  }
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
    // A copy, not the defaults themselves: the sheet is compared against them
    // to tell custom wording from stock, and to reset back to it. Sharing one
    // object would quietly edit the baseline along with the sheet.
    const defaults = () => JSON.parse(JSON.stringify(health.defaults));
    if (!state.sheet) state.sheet = defaults();
    if (invited) {
      state.sheet = Object.assign(defaults(), invited);
      invited = null;
    }
    save();
    say('msg-connect', 'ok',
        `Connected. This sheet has ${health.tests_per_sheet} tests of ` +
        `${health.questions_per_test} questions, a ` +
        `${health.student_id_digits}-digit Student ID and ` +
        `${health.latin_level_count} Latin levels.`);
    setStep('step-connect', true, 'hint-connect', 'Connected');
    renderLimits();
    renderWhichTests();
    renderSheetForm();
    renderTests();
    ['step-sheet', 'step-tests', 'step-key', 'step-grade',
     'step-review'].forEach(advance);
    renderAdvancedHint();
  } catch (error) {
    say('msg-connect', 'bad', error.message);
    setStep('step-connect', false, 'hint-connect', 'Not connected');
  }
}

function renderLimits() {
  const limits = state.limits;
  $('q-count').textContent = questionCount();
  document.querySelectorAll('.q-count').forEach(
    (node) => { node.textContent = questionCount(); });
  if (!limits) return;
  $('suggest-sheets').textContent = limits.suggested_sheets_per_batch;
  $('suggest-pages').textContent =
    limits.suggested_sheets_per_batch * limits.pages_per_sheet;
  $('limit-mb').textContent = Math.round(limits.max_upload_bytes / 1048576);
}

// --- 2. the sheet ----------------------------------------------------------

function renderSheetForm() {
  const sheet = state.sheet;
  // The wording comes from the server, so until step 1 is done there is
  // nothing to put under these headings. Take them down rather than leave
  // them standing over empty space.
  ['h-levels', 'level-note', 'h-writeins', 'writein-note'].forEach((id) => {
    if ($(id)) $(id).hidden = !sheet;
  });
  if (!sheet) {
    $('levels').textContent = '';
    $('levels').append(el('p', { className: 'note', textContent:
      'Connect in step 1 and the sheet’s wording appears here.' }));
    $('writeins').textContent = '';
    return;
  }
  $('sheet-title').value = sheet.title;
  $('directions').value = sheet.directions.join('\n');
  renderDirectionCount();

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

  renderLevelButtons();
  const custom = JSON.stringify(sheet) !== JSON.stringify(state.limits?.defaults);
  setStep('step-sheet', true, 'hint-sheet',
          custom ? 'Custom wording' : 'Default wording');
}

function levelLimits() {
  const limits = state.limits || {};
  return { min: limits.latin_level_min || 2, max: limits.latin_level_max || 10 };
}

function renderLevelButtons() {
  const row = $('level-buttons');
  if (!row) return;
  const sheet = state.sheet;
  row.hidden = !sheet;
  if (!sheet) return;
  const { min, max } = levelLimits();
  const count = sheet.latin_levels.length;
  $('add-level').disabled = count >= max;
  $('drop-level').disabled = count <= min;
  $('level-count').textContent =
    count + ' of ' + max + ' levels. Removing one also removes it from any ' +
    'test that was restricted to it.';
}

function setLevelCount(next) {
  const sheet = state.sheet;
  const { min, max } = levelLimits();
  if (!sheet || next < min || next > max) return;
  const levels = sheet.latin_levels.slice();
  while (levels.length < next) levels.push('Level ' + (levels.length + 1));
  levels.length = next;
  sheet.latin_levels = levels;
  // A test allowed only on a level that no longer exists would be a test
  // nobody can take, so those positions are dropped.
  state.tests.forEach((test) => {
    if (!Array.isArray(test.allowed)) return;
    const kept = test.allowed.filter((index) => index < next);
    test.allowed = kept.length ? kept : null;
  });
  save();
  renderSheetForm();
  renderTests();
}

function renderDirectionCount() {
  const box = $('directions-count');
  if (!box) return;
  const lines = $('directions').value.split('\n');
  const over = lines.length - MAX_DIRECTIONS;
  box.textContent = over > 0
    ? (lines.length + ' lines. Only the first ' + MAX_DIRECTIONS +
       ' will be printed.')
    : lines.filter((line) => line.trim()).length + ' of ' + MAX_DIRECTIONS +
      ' lines';
  box.style.color = over > 0 ? 'var(--warn)' : 'var(--ink-faint)';
}

function directionsFromBox() {
  return $('directions').value
    .split('\n').map((line) => line.replace(/\s+$/, ''))
    .filter((line, index, all) => line !== '' || index < all.length - 1)
    .slice(0, MAX_DIRECTIONS);
}

function readSheetForm() {
  state.sheet.title = $('sheet-title').value.trim();
  state.sheet.directions = directionsFromBox();
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

    const allowed = allowedPositions(test);
    const chips = el('div', { className: 'chips' });
    levels.forEach((level, position) => {
      const box = el('input', { type: 'checkbox',
                                checked: allowed.includes(position) });
      box.onchange = () => {
        const next = box.checked
          ? allowed.concat([position]).sort((a, b) => a - b)
          : allowed.filter((item) => item !== position);
        if (!next.length) {
          // A test nobody may take cannot be graded at all.
          box.checked = true;
          return say('msg-tests', 'bad',
                     'At least one level has to be able to take each test.');
        }
        test.allowed = next;
        save(); renderTests();
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

/* Answers copied out of a PDF or a printed key usually arrive numbered, and
   often in column order:

     1. B   5. B   9. C  13. C
     2. D   6. C  10. A  14. B

   so each answer has to go where its number says, not where it appears. Any
   punctuation between the number and the letters is ignored. Returns the
   answers plus a complaint about anything that could not be read, which the
   box below shows rather than swallowing. */
function parseNumbered(raw, need) {
  const answers = [];
  const seen = new Map();
  const problems = [];
  let matched = 0;
  // number, any punctuation or space, then the answer letters.
  const pattern = /(\d+)[^A-Za-z0-9]*([A-Za-z|]+)/g;
  let match;
  while ((match = pattern.exec(raw)) !== null) {
    matched += match[0].length;
    const number = Number(match[1]);
    const letters = match[2].toUpperCase();
    if (number < 1 || number > need) {
      problems.push('question ' + number + ' is outside 1\u2013' + need);
      continue;
    }
    if (seen.has(number) && seen.get(number) !== letters) {
      problems.push('question ' + number + ' is given twice, as "' +
                    seen.get(number) + "' and '" + letters + "'");
      continue;
    }
    seen.set(number, letters);
    while (answers.length < number) answers.push('');
    answers[number - 1] = letters === GAP ? '' : letters;
  }

  // Whatever the pattern did not consume should be nothing but separators.
  const leftover = raw.replace(pattern, ' ').replace(/[\s,;.:()|-]+/g, '');
  if (leftover) {
    problems.push("could not read '" + leftover.slice(0, 24) + "'");
  }
  if (!matched) problems.push('no numbered answers found');
  return { answers: answers, problems: problems };
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

/* Numbered when the text holds digits, positional when it does not. */
function readPaste(raw) {
  const text = (raw || '').trim();
  if (/[0-9]/.test(text)) return parseNumbered(text, questionCount());
  return { answers: textToAnswers(text), problems: [] };
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
  renderTidyButtons();
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
    const refresh = (problems) => {
      const have = answered(test);
      const need = questionCount();
      if (problems && problems.length) {
        count.textContent = have + ' of ' + need + ' answers \u2014 ' +
                            problems.slice(0, 2).join('; ') + '.';
        count.style.color = 'var(--bad)';
        return;
      }
      count.textContent = have + ' of ' + need + ' answers';
      count.style.color = have === need ? 'var(--good)'
                        : have ? 'var(--warn)' : 'var(--ink-faint)';
    };
    box.oninput = () => {
      const read = readPaste(box.value);
      test.answers = read.answers.slice(0, questionCount());
      test.dirty = true;
      save(); refresh(read.problems); renderGrid(); updateKeyMessage();
    };
    refresh();
    host.append(el('label', {}, [
      el('span', { className: 'lab', textContent: testLabel(test) }), box, count,
    ]));
  });
}

/* One row per question, one column per test. Rebuilt only when the shape
   changes — a keystroke writes into the array and leaves the DOM alone. */
/* Rewrites every paste box in the canonical form: one line, space
   separated, with a dash wherever an answer is still missing. The answers
   themselves are already parsed, so this only changes what is shown - which
   is the point, since it shows what the parser made of a messy paste. */
function tidyAnswers() {
  const boxes = Array.from(document.querySelectorAll('#answers textarea'));
  state.tidyUndo = boxes.map((box) => box.value);
  save();
  renderAnswers();
  renderTidyButtons();
  say('msg-key', 'ok',
      'Rewritten as the page reads them. A dash marks a question with no ' +
      'answer yet.');
}

function undoTidy() {
  const boxes = Array.from(document.querySelectorAll('#answers textarea'));
  (state.tidyUndo || []).forEach((text, index) => {
    if (!boxes[index]) return;
    boxes[index].value = text;
    boxes[index].dispatchEvent(new Event('input'));
  });
  state.tidyUndo = null;
  save();
  renderTidyButtons();
  say('msg-key', '', '');
}

function renderTidyButtons() {
  const tidy = $('tidy-answers');
  if (!tidy) return;
  const any = state.tests.some((test) => answered(test) > 0);
  tidy.disabled = !any;
  $('undo-tidy').hidden = !state.tidyUndo;
}

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
      const permitted = new RegExp(
        '[^' + (state.limits ? state.limits.options : 'ABCDE') + VOID + '|]',
        'g');
      input.oninput = () => {
        const text = input.value.toUpperCase().replace(permitted, '');
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

/* The same rules the server enforces, checked here so a typo surfaces while
   the key is being written rather than after a batch of scans has been
   uploaded and refused. Returns a sentence, or '' if the cell is fine. */
function cellProblem(cell) {
  if (!cell || cell === VOID) return '';
  const letters = state.limits ? state.limits.options : 'ABCDE';
  const alternatives = cell.split('|');
  if (alternatives.some((item) => item === '')) {
    return "has a stray '|' — write alternatives as A|BD, with a letter on " +
           'each side of it';
  }
  for (const alternative of alternatives) {
    for (const letter of alternative) {
      if (!letters.includes(letter)) {
        return "contains '" + letter + "', which is not one of " +
               letters.split('').join('/');
      }
    }
    if (new Set(alternative).size !== alternative.length) {
      return "repeats a letter in '" + alternative + "'";
    }
  }
  const seen = alternatives.map((item) => item.split('').sort().join(''));
  if (new Set(seen).size !== seen.length) {
    return 'lists the same answer twice';
  }
  return '';
}

function firstKeyProblem() {
  for (const test of namedTests()) {
    for (let index = 0; index < test.answers.length; index++) {
      const problem = cellProblem(test.answers[index]);
      if (problem) {
        return 'Test ' + (test.id ? "'" + test.id + "'" : test.name) +
               ', question ' + (index + 1) + ' ' + problem + '.';
      }
    }
  }
  return '';
}

/* The positions this test allows, defaulting to all of them. */
function allowedPositions(test) {
  const levels = levelNames();
  if (!Array.isArray(test.allowed)) return levels.map((_, index) => index);
  return test.allowed.filter((index) => index < levels.length);
}

function allowsEveryLevel(test) {
  const levels = levelNames();
  return !levels.length || allowedPositions(test).length === levels.length;
}

function allowedNames(test) {
  const levels = levelNames();
  return allowedPositions(test).map((index) => levels[index]);
}

/* The other way round, which is what the file is written with. */
function excludedNames(test) {
  const allowed = allowedPositions(test);
  return levelNames().filter((_, index) => !allowed.includes(index));
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
    /* Excluded rather than Allowed, so the common case is a blank cell.
       A test nobody is barred from then stays correct when a Latin level is
       added or removed later, where a written-out Allowed list would quietly
       start excluding the new one. The server reads either row. */
    ['Excluded'].concat(tests.map(
      (test) => (allowsEveryLevel(test) ? '' : excludedNames(test).join(', ')))),
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

/* Sharing a Test ID is allowed, so long as no student could match both. */
function firstIdClash(tests) {
  const levels = levelNames();
  for (let i = 0; i < tests.length; i++) {
    for (let j = i + 1; j < tests.length; j++) {
      if (tests[i].id !== tests[j].id) continue;
      const other = allowedPositions(tests[j]);
      const both = allowedPositions(tests[i])
        .filter((position) => other.includes(position))
        .map((position) => levels[position]);
      // With no levels known yet, a shared ID is still a clash.
      if (both.length || !levels.length) {
        return {
          id: tests[i].id,
          first: tests[i].name || tests[i].id,
          second: tests[j].name || tests[j].id,
          levels: both.length ? both : levels,
        };
      }
    }
  }
  return null;
}

function updateKeyMessage() {
  const withId = state.tests.filter((test) => test.id);
  const need = questionCount();
  const complete = withId.filter((test) => answered(test) === need);
  state.keyCsv = withId.length ? buildKeyCsv() : '';

  const clash = firstIdClash(withId);
  const duplicate = clash ? clash.id : '';
  if (clash) {
    say('msg-tests', 'bad',
        'Test ID ' + clash.id + " is used by both '" + clash.first + "' and '" +
        clash.second + '", and both accept ' + clash.levels.join(', ') +
        '. Two tests may share an ID only when the levels that may take them ' +
        'do not overlap.');
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

  const problem = firstKeyProblem();
  let kind = 'ok';
  if (problem) {
    kind = 'bad';
    lines.push(problem);
  }
  if (!withId.length) {
    kind = problem ? 'bad' : '';
  } else if (complete.length < withId.length) {
    kind = problem ? 'bad' : 'warn';
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
  $('download-key-note').hidden = $('download-key').hidden;
  $('download-key-row').classList.toggle('empty', $('download-key').hidden);
  $('undo-key').hidden = !state.undo;
  renderTidyButtons();

  setStep('step-key',
          withId.length > 0 && complete.length === withId.length && !problem,
          'hint-key',
          problem ? 'Check the answers'
                  : withId.length
                    ? complete.length + ' of ' + withId.length + ' complete'
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
      "There is no 'Test ID' row in " + filename + '. ' +
      'The first column has headers Name, Test ID, Excluded, then 1 to ' +
      questionCount() + '.' +
      (labels.length ? '\n\nThe first column of that file starts: ' +
                       labels.join(', ') + '.' : ''));
  }
  if (!ids.some((id) => id !== '')) {
    throw new Error(
      "The 'Test ID' row in " + filename + ' is empty, so there is no test to ' +
      'load. Put each test\'s ID in that row, one per column.');
  }

  const names = rowFor('Name') || [];
  /* A test's levels can be written either way round. Both rows at once can
     contradict each other, so the file is refused rather than guessed at -
     the same rule the server applies. */
  if (rowFor('Allowed') && rowFor('Excluded')) {
    throw new Error(
      "That file has both an 'Allowed' row and an 'Excluded' row. They say " +
      'the same thing from opposite sides and can contradict each other, so ' +
      'keep whichever one you meant and delete the other.');
  }
  const barring = !rowFor('Allowed');
  const levelRow = rowFor('Allowed') || rowFor('Excluded') || [];
  const levelLabel = barring ? 'Excluded' : 'Allowed';
  const digits = state.limits ? state.limits.test_id_digits : 4;
  const levels = levelNames();
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
    // Commas or semicolons, matching what the server accepts - a file it
    // would read must not be refused here.
    const listed = (levelRow[column] || '').split(/[,;]/)
      .map((item) => item.trim()).filter(Boolean);
    const positions = listed.map((name) => levels.findIndex(
      (level) => level.toUpperCase() === name.toUpperCase()));
    const stray = listed.filter((_, index) => positions[index] < 0);
    if (stray.length) {
      throw new Error(
        "'" + stray[0] + "' in the " + levelLabel + ' row is not one of this ' +
        'sheet\u2019s Latin levels (' + levels.join(', ') + '). Check the ' +
        'spelling, or rename the level in step 2 first.');
    }
    // Stored as the levels that may sit the test, however the file said it.
    let allowed = null;
    if (listed.length) {
      allowed = barring
        ? levels.map((_, index) => index).filter(
            (index) => !positions.includes(index))
        : positions;
      if (!allowed.length) {
        throw new Error(
          'The Excluded row bars every Latin level from "' +
          (names[column] || id) + '", so no student could sit it. Leave the ' +
          'cell blank to bar nobody, or name only the levels that may not ' +
          'sit it.');
      }
    }
    loaded.push(Object.assign(newTest(), {
      name: names[column] || '',
      id: /^\d+$/.test(id) ? id.padStart(digits, '0') : id,
      allowed: allowed,
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
      // Matched on ID *and* levels, because an ID may now name two tests.
      const match = merged.find(
        (test) => test.id && test.id === incoming.id &&
                  String(allowedPositions(test)) ===
                  String(allowedPositions(incoming)));
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

/* One line for whatever has been changed away from the defaults. */
/* What gets sent as annotate_students: '' for every paper, 'unknown' for the
   ones whose Student ID could not be read, or the IDs themselves. */
function annotateSpec() {
  if (!state.annotate) return '';
  if (state.annotateWho === 'unknown') return 'unknown';
  if (state.annotateWho === 'list') return readAnnotateIds().good.join(',');
  return '';
}

/* The Student IDs typed into the mark-up filter, and anything typed there
   that cannot be one. Digits only: students bubble digits, so a word in this
   box would silently match nobody. */
function readAnnotateIds() {
  const digits = state.limits ? state.limits.student_id_digits : 5;
  const listed = (state.annotateIds || '').split(/[,;]/)
    .map((item) => item.trim()).filter(Boolean);
  const good = [];
  const bad = [];
  listed.forEach((item) => {
    if (!/^[0-9?]+$/.test(item)) bad.push(item);
    else if (item.length > digits) bad.push(item);
    else good.push(item.padStart(digits, '0'));
  });
  return { good: good, bad: bad, digits: digits };
}

function renderAnnotateWho() {
  const box = $('annotate-who');
  if (!box) return;
  box.hidden = !state.annotate;
  $('annotate-students').value = state.annotateWho || '';
  $('annotate-list').hidden = state.annotateWho !== 'list';
  $('annotate-ids').value = state.annotateIds || '';

  const read = readAnnotateIds();
  const showing = state.annotate && state.annotateWho === 'list';
  if (!showing || !read.bad.length) {
    say('msg-annotate-ids', '', '');
  } else {
    say('msg-annotate-ids', 'bad',
        quotedList(read.bad) + ' ' + plural(read.bad.length, 'is', 'are') +
        ' not a Student ID. They are ' + read.digits +
        ' digits, so write them as numbers separated by commas.');
  }
}

function renderAdvancedHint() {
  const notes = [];
  const spec = thresholdSpec();
  if (sidesSpec()) notes.push(sidesSpec() + ' only');
  if (testsSpec()) {
    notes.push(plural(gradedTests().length, 'test ', 'tests ') +
               gradedTests().join(' and ') + ' only');
  }
  if (state.annotate) {
    notes.push(state.annotateWho ? 'mark-up, some papers' : 'mark-up on');
  }
  if (spec) notes.push('thresholds fixed');
  setStep('step-thresholds', true, 'hint-thresholds',
          notes.length ? notes.join(', ') : 'Defaults');
}

function renderThresholds() {
  const manual = state.thresholdMode === 'manual';
  $('thr-override').checked = manual;
  $('thr-manual').hidden = !manual;
  ['as', 'ar', 'ms', 'mr'].forEach((key) => {
    $('thr-' + key).value = state.threshold[key] || '';
  });
  renderAdvancedHint();
}

/* --- keeping the marked-up PDFs ------------------------------------------

   localStorage holds a few megabytes and the rest of the saved state has to
   fit in it too, so the annotated scans live in IndexedDB instead. Every
   call resolves rather than rejects: a browser with storage turned off, or a
   private window, must still grade - it just cannot offer the file twice. */

const PDF_DB = 'jcl-grading-pdfs';
const PDF_STORE = 'pdfs';
let pdfDatabase = null;

function openPdfStore() {
  if (pdfDatabase) return Promise.resolve(pdfDatabase);
  return new Promise((resolve) => {
    let request;
    try {
      request = indexedDB.open(PDF_DB, 1);
    } catch (error) {
      return resolve(null);
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(PDF_STORE)) {
        db.createObjectStore(PDF_STORE);
      }
    };
    request.onsuccess = () => { pdfDatabase = request.result; resolve(pdfDatabase); };
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
}

function pdfKey(batchNumber, name) {
  return batchNumber + '/' + name;
}

async function keepPdf(batchNumber, name, blob) {
  const db = await openPdfStore();
  if (!db) return false;
  return new Promise((resolve) => {
    let transaction;
    try {
      transaction = db.transaction(PDF_STORE, 'readwrite');
    } catch (error) {
      return resolve(false);
    }
    transaction.objectStore(PDF_STORE).put(blob, pdfKey(batchNumber, name));
    transaction.oncomplete = () => resolve(true);
    transaction.onerror = () => resolve(false);
    transaction.onabort = () => resolve(false);
  });
}

async function readPdf(batchNumber, name) {
  const db = await openPdfStore();
  if (!db) return null;
  return new Promise((resolve) => {
    let request;
    try {
      request = db.transaction(PDF_STORE, 'readonly')
        .objectStore(PDF_STORE).get(pdfKey(batchNumber, name));
    } catch (error) {
      return resolve(null);
    }
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => resolve(null);
  });
}

async function dropPdfs(batchNumber) {
  const db = await openPdfStore();
  if (!db) return;
  const batch = state.batches.find((item) => item.n === batchNumber);
  const names = (batch && batch.pdfs) || [];
  try {
    const store = db.transaction(PDF_STORE, 'readwrite').objectStore(PDF_STORE);
    names.forEach((name) => store.delete(pdfKey(batchNumber, name)));
  } catch (error) {
    console.warn('Could not clear stored PDFs', error);
  }
}

async function dropEveryPdf() {
  const db = await openPdfStore();
  if (!db) return;
  try {
    db.transaction(PDF_STORE, 'readwrite').objectStore(PDF_STORE).clear();
  } catch (error) {
    console.warn('Could not clear stored PDFs', error);
  }
}

// --- 6. grading ------------------------------------------------------------

function testsPerSheet() {
  return state.limits ? state.limits.tests_per_sheet : 3;
}

/* Which test numbers this run will grade. */
function gradedTests() {
  const total = testsPerSheet();
  const all = Array.from({ length: total }, (_, index) => index + 1);
  if (!Array.isArray(state.onlyTests)) return all;
  const kept = state.onlyTests.filter((number) => number <= total);
  return kept.length ? kept : all;
}

/* '' when every test is graded, so the common case sends nothing. */
function testsSpec() {
  const kept = gradedTests();
  return kept.length === testsPerSheet() ? '' : kept.join(',');
}

/* Which side of the sheet a test number is printed on. Spelled out beside
   the checkboxes so they are not mistaken for the tests listed in step 3. */
function sideOfTest(number) {
  const perPage = (state.limits && state.limits.tests_on_page) || [1, 2];
  let seen = 0;
  for (let page = 0; page < perPage.length; page++) {
    seen += perPage[page];
    if (number <= seen) return page === 0 ? 'front' : 'back';
  }
  return 'back';
}

/* Which sides of the paper the scans hold. null means both, which is what
   an ordinary duplex scan is and what almost everybody wants. */
function gradedSides() {
  if (!Array.isArray(state.sides) || !state.sides.length) return [0, 1];
  return state.sides.slice().sort((a, b) => a - b);
}

function sidesSpec() {
  const kept = gradedSides();
  return kept.length === 2 ? '' : (kept[0] === 0 ? 'front' : 'back');
}

/* The test numbers printed on one side of the sheet. */
function testsOnSide(side) {
  const perPage = (state.limits && state.limits.tests_on_page) || [1, 2];
  const numbers = [];
  let seen = 0;
  perPage.forEach((count, page) => {
    for (let index = 0; index < count; index++) {
      seen += 1;
      if (page === side) numbers.push(seen);
    }
  });
  return numbers;
}

function renderWhichSides() {
  const host = $('which-sides');
  if (!host) return;
  host.textContent = '';
  const kept = gradedSides();
  ['front', 'back'].forEach((name, side) => {
    const box = el('input', { type: 'checkbox',
                              checked: kept.includes(side) });
    box.onchange = () => setSide(side, box.checked);
    host.append(el('label', {}, [
      box, name + ' (' + joinList(testsOnSide(side).map(
        (number) => 'Test ' + number)) + ')']));
  });

  const note = $('sides-note');
  const blanks = $('blanks-box');
  const oneSided = kept.length === 1;
  if (note) {
    note.hidden = !oneSided;
    if (oneSided) {
      note.textContent =
        'Only the ' + (kept[0] === 0 ? 'front' : 'back') + ' is being read, ' +
        'so each page is graded on its own.' +
        (kept[0] === 1
          ? ' The Latin level is printed on the front, so a test with one key '
            + 'per level cannot be scored from the back alone.'
          : '');
    }
  }
  if (blanks) {
    blanks.hidden = !oneSided;
    $('skip-blanks').checked = !!state.skipBlanks;
  }
  renderAdvancedHint();
}

/* Ticking a side brings its tests with it; unticking one takes them away.
   The two settings cannot be allowed to contradict each other - grading a
   test whose side was never scanned produces nothing and explains nothing. */
function setSide(side, on) {
  const kept = gradedSides();
  const next = on ? kept.concat([side]) : kept.filter((item) => item !== side);
  if (!next.length) {
    say('msg-thresholds', 'bad',
        'At least one side of the sheet has to be scanned.');
    return renderWhichSides();
  }
  state.sides = next.length === 2 ? null : next.sort((a, b) => a - b);

  const allowed = next.reduce(
    (all, item) => all.concat(testsOnSide(item)), []);
  const tests = gradedTests().filter((number) => allowed.includes(number));
  state.onlyTests = tests.length === testsPerSheet() ? null : tests;
  if (!tests.length) state.onlyTests = null;
  if (!on) state.skipBlanks = state.skipBlanks && next.length === 1;

  say('msg-thresholds', '', '');
  save();
  renderWhichSides();
  renderWhichTests();
}

function renderWhichTests() {
  const host = $('which-tests');
  if (!host) return;
  host.textContent = '';
  const kept = gradedTests();
  for (let number = 1; number <= testsPerSheet(); number++) {
    const box = el('input', { type: 'checkbox',
                              checked: kept.includes(number) });
    const side = sideOfTest(number) === 'front' ? 0 : 1;
    box.disabled = !gradedSides().includes(side);
    box.onchange = () => {
      const next = box.checked
        ? kept.concat([number]).sort((a, b) => a - b)
        : kept.filter((item) => item !== number);
      if (!next.length) {
        box.checked = true;
        return say('msg-thresholds', 'bad',
                   'At least one test has to be graded.');
      }
      say('msg-thresholds', '', '');
      state.onlyTests = next.length === testsPerSheet() ? null : next;
      save(); renderWhichTests(); renderAdvancedHint();
    };
    const label = el('label', {}, [box, 'Test ' + number + ' (' +
                                        sideOfTest(number) + ')']);
    if (box.disabled) {
      label.title = 'The ' + sideOfTest(number) + ' of the sheet is not in ' +
                    'this scan.';
      label.style.opacity = '.5';
    }
    host.append(label);
  }
}

function addBatch() {
  state.batches.push({ n: state.nextBatch++, summary: null, results: '',
                       files: [], seconds: 0, review: {} });
  save(); renderBatches();
  // Newest first, so the card you just asked for is the one at the top. With
  // a dozen batches above it, appending looked like nothing had happened.
  const first = document.querySelector('#batches .batch');
  if (first) first.scrollIntoView({ block: 'nearest' });
}

/* Newest first. The numbers still count upwards, so "batch 3" means the same
   thing whichever end of the list it is at. */
function batchOrder() {
  return state.batches.map((batch, index) => ({ batch: batch, index: index }))
    .reverse();
}

function renderBatches() {
  const host = $('batches');
  host.textContent = '';
  if (!state.batches.length) {
    host.append(el('p', { className: 'note',
                          textContent: 'No batches yet.' }));
  }

  const hidden = state.batches.filter((batch) => batch.hidden);
  if (hidden.length) {
    const show = el('button', { textContent:
      'Show ' + hidden.length + ' hidden ' +
      plural(hidden.length, 'batch', 'batches') });
    show.onclick = () => {
      state.batches.forEach((batch) => { batch.hidden = false; });
      save(); renderBatches();
    };
    host.append(el('div', { className: 'row', style: 'margin-bottom:.8rem' },
                   [show]));
  }

  batchOrder().forEach(({ batch, index }) => {
    if (batch.hidden) return;
    host.append(batchCard(batch, index));
  });

  const graded = state.batches.filter((batch) => batch.summary);
  setStep('step-grade', graded.length > 0, 'hint-grade',
          graded.length ? graded.length + ' of ' + state.batches.length + ' graded'
                        : 'Nothing graded');
  renderReview();
}

/* "Scan 1234.pdf and 1 more at 12:34 PM" - enough to tell one graded batch
   from the next without opening anything. */
function batchStamp(batch) {
  const parts = [];
  const names = batch.uploaded || [];
  if (names.length) {
    parts.push(names[0] + (names.length > 1
      ? ' and ' + (names.length - 1) + ' more' : ''));
  }
  if (batch.at) {
    const when = new Date(batch.at);
    if (!isNaN(when)) {
      parts.push('at ' + when.toLocaleTimeString([],
        { hour: 'numeric', minute: '2-digit' }));
    }
  }
  return parts.join(' ');
}

function batchCard(batch, index) {
  const card = el('div', { className: 'batch' });
  const message = el('div', { className: 'msg' });
  const results = el('div');
  const header = (extra) => {
    const title = el('div', { className: 'batch-title' },
                     [el('h4', {}, ['Batch ' + batch.n])]);
    const stamp = batchStamp(batch);
    if (stamp) {
      title.append(el('span', { className: 'stamp', textContent: stamp }));
    }
    return el('div', { className: 'batch-head' },
              [title].concat(extra || []));
  };

  // Once a batch is graded it is a record of a run that happened, with its own
  // key and thresholds. Re-running it with today's settings would quietly
  // disagree with the files already downloaded, so it is closed to editing.
  if (batch.summary) {
    // Hiding keeps everything and only folds the card away; removing throws
    // the results out. They are deliberately not the same button.
    const hide = el('button', { textContent: 'Hide' });
    hide.onclick = () => { batch.hidden = true; save(); renderBatches(); };
    const drop = el('button', { className: 'danger', textContent: 'Remove' });
    drop.onclick = () => {
      if (!confirm('Remove batch ' + batch.n + ' and its saved results? ' +
                   'Anything not downloaded is lost.')) return;
      dropPdfs(batch.n);
      state.batches.splice(index, 1); save(); renderBatches();
    };
    card.append(header(el('div', { className: 'row' }, [hide, drop])), results);
    results.append(summaryView(batch));
    return card;
  }

  const host = el('span');
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

    batch.uploaded = files.map((file) => file.name);
    batch.at = Date.now();
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
    form.append('tests', testsSpec());
    form.append('sides', sidesSpec());
    form.append('skip_blanks', state.skipBlanks ? 'true' : 'false');
    form.append('annotate_students', annotateSpec());
    if (state.sheet) form.append('layout', JSON.stringify(state.sheet));

    const started = performance.now();
    try {
      fill.style.width = '55%';
      const body = await call('/grade', { method: 'POST', body: form });
      fill.style.width = '100%';
      batch.seconds = (performance.now() - started) / 1000;
      batch.tests = testsSpec();
      batch.summary = body.summary;
      const resultsFile = body.files.find(
        (item) => item.name.endsWith('Results.csv'));
      batch.results = resultsFile ? resultsFile.data : '';
      // Keep only the small text files; PDFs are offered now and dropped.
      batch.files = body.files
        .filter((item) => item.encoding === 'utf-8')
        .map((item) => ({ name: item.name, type: item.type, data: item.data }));
      // PDFs go to IndexedDB rather than localStorage, which could not hold
      // them, so they can be downloaded again later - after a re-score, or
      // after somebody loses the first copy.
      const pdfs = body.files.filter((item) => item.encoding === 'base64');
      batch.pdfs = pdfs.map((item) => item.name.split('/').pop());
      batch.pdfsKept = true;
      for (const item of pdfs) {
        const name = item.name.split('/').pop();
        const blob = fileFromServer(item);
        if (!await keepPdf(batch.n, name, blob)) batch.pdfsKept = false;
        download(name, blob);
      }
      save();
      renderBatches();
    } catch (error) {
      message.className = 'msg bad';
      message.textContent = error.message;
      bar.hidden = true;
    } finally {
      go.disabled = false; drop.disabled = false;
    }
  };

  card.append(header(), el('div', { className: 'row' }, [host, go, drop]),
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
    stat(summary.rows, 'tests'),
    stat(summary.unclear, 'unclear'),
    stat(summary.missing, 'missing'),
    stat(batch.seconds ? batch.seconds.toFixed(0) + ' s' : '—',
         'processing time'),
  ]));

  if (summary.pages_set_aside) {
    const counted = summary.pages || {};
    const kinds = Object.keys(counted)
      .filter((name) => name !== 'Graded' && name !== 'Blank')
      .map((name) => counted[name] + ' ' + name.toLowerCase());
    box.append(el('div', { className: 'msg warn' }, [
      summary.pages_set_aside + ' ' +
      plural(summary.pages_set_aside, 'page', 'pages') +
      ' could not be graded (' + joinList(kinds) + '). Every page ' +
      'of the batch and what became of it is listed in ',
      el('code', { textContent: 'Pages.csv' }),
      ' below. The rest of the batch was graded normally.',
    ]));
  }
  if (summary.override_problems && summary.override_problems.length) {
    box.append(el('div', { className: 'msg warn' }, [
      plural(summary.override_problems.length, 'One correction', 'Some ' +
             'corrections') + ' could not be matched to a test and ' +
      plural(summary.override_problems.length, 'was', 'were') +
      ' not applied:\n' + summary.override_problems.join('\n'),
    ]));
  }
  if (summary.test_not_found) {
    box.append(el('div', { className: 'msg warn' },
      [summary.test_not_found + ' ' +
       plural(summary.test_not_found, 'row has', 'rows have') +
       ' a Test ID that is not in the key. Check the key, or correct the ' +
       'Test ID in step 7.']));
  }
  if (summary.level_needed) {
    box.append(el('div', { className: 'msg warn' },
      [summary.level_needed + ' ' +
       plural(summary.level_needed, 'row has', 'rows have') +
       ' a Test ID with one key per Latin level, and the level itself could ' +
       'not be read. Fill it in on the Missing sheet in step 7 and re-score.']));
  }
  if (summary.test_not_allowed) {
    box.append(el('div', { className: 'msg warn' },
      [summary.test_not_allowed + ' ' +
       plural(summary.test_not_allowed, 'row is', 'rows are') +
       ' for a test that student’s Latin level may not take.']));
  }

  if (batch.tests) {
    const numbers = batch.tests.split(',');
    box.append(el('p', { className: 'note', textContent:
      'Only ' + plural(numbers.length, 'test ', 'tests ') +
      numbers.join(' and ') + ' ' +
      plural(numbers.length, 'was', 'were') + ' graded in this batch.' }));
  }

  box.append(fileList(batch.files));

  if (batch.pdfs && batch.pdfs.length) {
    const many = batch.pdfs.length !== 1;
    box.append(el('p', { className: 'note', textContent:
      plural(batch.pdfs.length, 'A marked-up scan was', 'Marked-up scans were') +
      ' sent to this browser\u2019s downloads folder. ' +
      (batch.pdfsKept
        ? (many ? 'Copies are' : 'A copy is') +
          ' kept here too, so you can take ' +
          (many ? 'them' : 'it') + ' again.'
        : 'This browser would not store ' + (many ? 'copies' : 'a copy') +
          ', so that download is the only one.') }));
    if (batch.pdfsKept) {
      box.append(fileList(batch.pdfs.map((name) => ({
        name: name, pdf: true, batch: batch.n,
      }))));
    }
  }
  return box;
}

/* Results first, then anything needing attention, then the reference
   material - Calibration.txt last, since it is only read when a run looks
   wrong. */
function byUsefulness(files) {
  const rank = (item) => (/Results\.csv/.test(item.name) ? 0
                        : /Unclear|Missing/.test(item.name) ? 1
                        : /Pages\.csv/.test(item.name) ? 2
                        : /Question Stats/.test(item.name) ? 3 : 4);
  return files.slice().sort((a, b) => rank(a) - rank(b));
}

function fileList(files) {
  const list = el('ul', { className: 'files' });
  byUsefulness(files).forEach((item) => {
    const get = el('button', { textContent: 'Download' });
    get.onclick = async () => {
      if (item.pdf) {
        const blob = await readPdf(item.batch, item.name);
        if (!blob) {
          return say('msg-grade', 'bad',
                     'This browser no longer has a copy of ' + item.name +
                     '. Grade the batch again to produce a new one.');
        }
        return download(item.name, blob);
      }
      download(item.name.split('/').pop(), item.data, item.type);
    };
    const name = el('span', { className: 'name', textContent: item.name });
    if (item.updated) {
      name.append(el('span', { className: 'tag', textContent: '(updated)' }));
    }
    list.append(el('li', {}, [name, get]));
  });
  return list;
}

// --- 7. review -------------------------------------------------------------

function renderReview() {
  const host = $('review-batches');
  host.textContent = '';
  const graded = state.batches.filter((batch) => batch.summary);

  if (!graded.length) {
    host.append(el('p', { className: 'note',
                          textContent: 'Grade a batch first.' }));
    setStep('step-review', false, 'hint-review', 'Nothing to review');
    return;
  }

  graded.forEach((batch) => host.append(reviewCard(batch)));
  const pending = graded.filter(outstanding);
  setStep('step-review', !pending.length, 'hint-review',
          pending.length
            ? pending.length + ' ' +
              plural(pending.length, 'batch', 'batches') + ' to fix'
            : 'All clear');
}

/* What this batch still needs a person for. A kind stops counting once a
   corrected file of that kind has been applied. */
function outstanding(batch) {
  if (batch.updated && batch.updated.leftOver) {
    // Rows came back unticked, so there is still work on this batch.
    return ['unclear'];
  }
  const done = (batch.updated && batch.updated.kinds) || [];
  const kinds = [];
  if (batch.summary.unclear && !done.includes('unclear')) kinds.push('unclear');
  if (batch.summary.missing && !done.includes('missing')) kinds.push('missing');
  return kinds.length ? kinds : null;
}

/* The originals, with anything the re-score replaced marked as updated and
   put in its place. Files the re-score did not produce - Calibration.txt -
   stay exactly as they were rather than disappearing. */
function currentFiles(batch) {
  const fresh = new Map(
    ((batch.updated && batch.updated.files) || []).map(
      (item) => [item.name.split('/').pop(), item]));
  const merged = batch.files.map((item) => {
    const name = item.name.split('/').pop();
    return fresh.has(name)
      ? Object.assign({}, fresh.get(name), { updated: true })
      : item;
  });
  const known = new Set(batch.files.map((item) => item.name.split('/').pop()));
  fresh.forEach((item, name) => {
    if (!known.has(name)) merged.push(Object.assign({}, item, { updated: true }));
  });
  return merged;
}

/* A file whose first column starts Name / Test ID is an answer key, not a
   sheet of corrections, so it is sent as one. */
async function sortUploads(files) {
  const overrides = [];
  let key = null;
  for (const file of files) {
    let head = '';
    try {
      head = (await file.text()).slice(0, 400);
    } catch (error) {
      console.warn('Could not read', file.name, error);
    }
    if (/(^|\n)\s*"?Test\s*ID"?\s*,/i.test(head)) key = file;
    else overrides.push(file);
  }
  return { overrides: overrides, key: key };
}

function reviewCard(batch) {
  const card = el('div', { className: 'batch' });
  const kinds = outstanding(batch);
  const reviewFiles = batch.files.filter(
    (item) => /Unclear|Missing/.test(item.name));

  const host = el('span');
  const picker = filePicker(host, { accept: '.csv', multiple: true,
                                    label: 'Choose corrections' });
  const go = el('button', { className: 'primary',
                            textContent: 'Apply and re-score' });
  const ignore = el('input', { type: 'checkbox' });
  const ignoreLabel = el('label', { className: 'inline' },
                         [ignore, 'Ignore the Done column']);
  ignoreLabel.title = 'Apply every row whether or not it is ticked. For ' +
                      'when the work was done but the column was not.';
  const message = el('div', { className: 'msg' });
  const results = el('div');

  const showResults = () => {
    results.textContent = '';
    if (!batch.updated) return;
    const applied = batch.updated.applied;
    const left = batch.updated.leftOver || 0;
    const done = applied + ' ' +
      plural(applied, 'correction', 'corrections') + ' applied, ' +
      batch.updated.scored + ' rows re-scored.';
    results.append(left
      ? el('p', { className: 'msg warn', textContent:
          done + ' ' + left + ' ' + plural(left, 'row', 'rows') +
          ' had nothing ticked in the Done column, so ' +
          plural(left, 'it was', 'they were') + ' left alone. ' +
          'The shortened file below has just those rows — finish them and ' +
          'upload it again.' })
      : el('p', { className: 'msg ok', textContent:
          'Success! No more marks are ' +
          quotedPlain(batch.updated.kinds) + '. ' + done }));
    if (batch.updated.problems && batch.updated.problems.length) {
      results.append(el('p', { className: 'msg warn', textContent:
        plural(batch.updated.problems.length, 'One correction',
               'Some corrections') + ' could not be matched to a test and ' +
        plural(batch.updated.problems.length, 'was', 'were') +
        ' not applied:\n' + batch.updated.problems.join('\n') }));
    }
    results.append(
      el('p', { className: 'note', textContent:
        'These are the current results for batch ' + batch.n +
        '. The copies in step 6 are left as they were first graded.' }),
      fileList(currentFiles(batch)));
  };

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

    const sorted = await sortUploads(files);
    const form = new FormData();
    form.append('results', new Blob([batch.results], { type: 'text/csv' }),
                'Results.csv');
    sorted.overrides.forEach((file) => form.append('overrides', file, file.name));
    if (sorted.key) {
      form.append('key', sorted.key, 'Keys.csv');
    } else if (state.keyCsv) {
      form.append('key', new Blob([state.keyCsv], { type: 'text/csv' }),
                  'Keys.csv');
    }
    if (state.sheet) form.append('layout', JSON.stringify(state.sheet));
    form.append('require_done', ignore.checked ? 'false' : 'true');

    try {
      const body = await call('/regrade', { method: 'POST', body: form });
      const resultsFile = body.files.find(
        (item) => item.name.endsWith('Results.csv'));
      if (resultsFile) batch.results = resultsFile.data;
      // The batch as graded is left alone; the re-scored files live here.
      const settled = (batch.updated && batch.updated.kinds || []).slice();
      sorted.overrides.forEach((file) => {
        if (/Unclear/i.test(file.name) && !settled.includes('unclear')) {
          settled.push('unclear');
        }
        if (/Missing/i.test(file.name) && !settled.includes('missing')) {
          settled.push('missing');
        }
      });
      batch.updated = {
        files: body.files.filter((item) => item.encoding === 'utf-8')
          .map((item) => ({ name: item.name, type: item.type,
                            data: item.data })),
        applied: body.summary.corrections_applied,
        scored: body.summary.scored,
        leftOver: body.summary.rows_left_over || 0,
        problems: body.summary.override_problems || [],
        kinds: settled.length ? settled : ['unclear', 'missing'],
      };
      save();
      message.className = 'msg';
      message.textContent = '';
      showResults();
      renderBatches();
    } catch (error) {
      message.className = 'msg bad';
      message.textContent = error.message;
    } finally {
      go.disabled = false;
    }
  };

  const heading = kinds
    ? batch.summary.unclear + ' unclear, ' + batch.summary.missing +
      ' missing. Download these, correct them, then bring them back.'
    : batch.updated
      ? 'Already settled. The corrected files are below; upload more to ' +
        're-score again.'
      : 'Nothing was left unclear or missing in this batch.';

  card.append(
    el('div', { className: 'batch-head' }, [el('h4', {}, ['Batch ' + batch.n])]),
    el('p', { className: 'note', textContent: heading }));
  if (reviewFiles.length) card.append(fileList(reviewFiles));
  if (kinds || batch.updated) {
    card.append(
      el('div', { className: 'row', style: 'margin-top:.6rem' },
         [host, go, ignoreLabel]),
      message, results);
  }
  showResults();
  return card;
}

// --- wiring ----------------------------------------------------------------

function start() {
  $('endpoint').value = state.endpoint;
  $('passphrase').value = state.passphrase;
  $('annotate').checked = state.annotate;

  $('connect').onclick = connect;
  $('invite').onclick = () => copyInvite(false);
  $('invite-sheet').onclick = () => copyInvite(true);

  $('make-sheet').onclick = makeSheet;
  $('reset-sheet').onclick = () => {
    if (!state.limits) return say('msg-sheet', 'bad', 'Connect first.');
    state.sheet = JSON.parse(JSON.stringify(state.limits.defaults));
    save(); renderSheetForm(); renderTests();
    say('msg-sheet', 'ok', 'Back to the standard wording.');
  };
  // Typing has to reach the state, not just re-save the old value:
  // anything that re-renders the form would put the old text back.
  $('sheet-title').oninput = () => {
    if (state.sheet) state.sheet.title = $('sheet-title').value;
    save();
  };
  $('directions').oninput = () => {
    if (state.sheet) state.sheet.directions = directionsFromBox();
    save(); renderDirectionCount();
  };
  $('add-level').onclick = () =>
    setLevelCount(state.sheet ? state.sheet.latin_levels.length + 1 : 0);
  $('drop-level').onclick = () =>
    setLevelCount(state.sheet ? state.sheet.latin_levels.length - 1 : 0);

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
  const keyInput = filePicker(keyHost, { accept: '.csv',
                                         label: 'Upload Keys.csv' });
  keyHost.onpicked = (files) => { if (files[0]) uploadKey(files[0]); };
  $('undo-key').onclick = () => {
    undoUpload();
    // Leaving the filename showing next to the box would suggest the file is
    // still in effect, and re-picking the same one would fire no change event.
    keyInput.value = '';
    const chosen = keyHost.querySelector('.chosen');
    if (chosen) chosen.textContent = 'no file uploaded';
  };
  $('tidy-answers').onclick = tidyAnswers;
  $('undo-tidy').onclick = undoTidy;

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
  renderWhichSides();
  renderWhichTests();
  renderAnnotateWho();
  $('annotate').onchange = () => {
    state.annotate = $('annotate').checked;
    save(); renderAnnotateWho(); renderAdvancedHint();
  };
  $('annotate-students').onchange = () => {
    state.annotateWho = $('annotate-students').value;
    save(); renderAnnotateWho(); renderAdvancedHint();
  };
  $('annotate-ids').oninput = () => {
    state.annotateIds = $('annotate-ids').value;
    save();
    renderAnnotateWho();
  };
  $('skip-blanks').onchange = () => {
    state.skipBlanks = $('skip-blanks').checked;
    save(); renderAdvancedHint();
  };

  $('get-script').onclick = (event) => {
    event.preventDefault();
    downloadScript();
  };

  $('reset').onclick = () => {
    if (!confirm('Clear the tests, answers, thresholds and every graded ' +
                 'batch saved in this browser? This cannot be undone.')) return;
    try {
      localStorage.removeItem(STORE_KEY);
    } catch (error) {
      console.warn('Could not clear saved work:', error);
    }
    dropEveryPdf();
    location.reload();
  };

  renderSheetForm();
  renderTests();
  renderThresholds();
  renderBatches();
  renderLimits();
  // An invite link overrides whatever this browser had saved.
  readInvite();
  if (state.endpoint && state.passphrase) connect();
}

document.addEventListener('DOMContentLoaded', start);
