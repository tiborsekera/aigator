// DOM regressions; synthetic payloads only. No logged-in sites or network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const flush = () => new Promise(resolve => setImmediate(resolve));

async function dashboard() {
  const html = execFileSync(process.env.PYTHON || 'python3', ['-c', 'from aigator.server.web_ui import HTML_PAGE; print(HTML_PAGE)'], {encoding: 'utf8'});
  const hostile = `review"><img src=x onerror=window.pwned=1>'\\`;
  const record = {session_id: hostile, title: hostile, source: hostile, message_count: hostile,
    messages: [{role: hostile, model: hostile, content: hostile, timestamp: hostile}]};
  const calls = [];
  const dom = new JSDOM(html, {url: 'http://127.0.0.1:8765', runScripts: 'dangerously', beforeParse(window) {
    window.fetch = async url => {
      calls.push(url);
      const data = url === '/api/stats' ? {total_sessions: 1, total_messages: 1, size_mb: 1} :
        url.startsWith('/api/sessions/') ? record : url.startsWith('/api/search') ? [{...record, snippet: hostile}] : [record];
      return {ok: true, json: async () => data};
    };
  }});
  await flush();
  const doc = dom.window.document;
  assert.equal(doc.querySelector('.session-item').dataset.sessionId, hostile);
  assert.equal(doc.querySelectorAll('img, [onclick], [onerror]').length, 0);
  doc.querySelector('.session-title').click();
  await flush();
  assert(calls.includes('/api/sessions/' + encodeURIComponent(hostile)));
  assert.equal(doc.querySelectorAll('img, [onclick], [onerror]').length, 0);
  assert(doc.querySelector('.msg-role').textContent.includes(hostile.toUpperCase()));
  doc.getElementById('searchInput').value = 'needle';
  doc.getElementById('searchBtn').click();
  await flush();
  assert.equal(doc.querySelectorAll('img, [onclick], [onerror]').length, 0);
  assert.equal(dom.window.pwned, undefined);
  dom.window.close();
}

async function dashboardInteractions() {
  const html = execFileSync(process.env.PYTHON || 'python3', ['-c', 'from aigator.server.web_ui import HTML_PAGE; print(HTML_PAGE)'], {encoding: 'utf8'});
  const a = {session_id: 'gemini_web_abc123', native_id: 'abc123', source: 'gemini_web', title: 'First conversation', message_count: 1,
    created_at: '2026-09-01', updated_at: '2026-09-02', messages: [{role: 'user', content: 'Synthetic message', timestamp: '2026-09-01'}]};
  const b = {...a, session_id: 'chatgpt_def456', native_id: 'def456', source: 'chatgpt', title: 'Second conversation'};
  const pending = [], opened = [], copied = [];
  const dom = new JSDOM(html, {url: 'http://127.0.0.1:8765', runScripts: 'dangerously', beforeParse(window) {
    window.fetch = url => new Promise(resolve => pending.push({url, resolve}));
    window.open = (...args) => opened.push(args);
    Object.defineProperty(window.navigator, 'clipboard', {value: {writeText: async text => copied.push(text)}});
  }});
  const w = dom.window, doc = w.document;
  async function respond(part, data, ok = true) {
    const index = pending.findIndex(request => request.url.includes(part));
    assert(index >= 0, `Missing pending request: ${part}`);
    pending.splice(index, 1)[0].resolve({ok, status: ok ? 200 : 500, json: async () => data});
    await flush();
  }
  try {
    await respond('/api/stats', {total_sessions: 2, total_messages: 2});
    await respond('/api/sessions?', [a, b]);
    assert.equal(doc.getElementById('fuzzyToggle').checked, true);
    assert.deepEqual(Array.from(doc.querySelectorAll('.tab-btn'), b => b.dataset.src),
      ['', 'claude_code', 'codex', 'copilot_cli', 'vscode_copilot', 'chatgpt', 'claude_web', 'gemini_web', 'perplexity']);
    assert.equal(w.sourceName('claude_code'), 'Claude Code');
    assert.equal(w.sourceName('codex'), 'Codex');
    assert.equal(w.sourceName('copilot_cli'), 'Copilot CLI');
    const first = doc.querySelector('.session-item');
    assert.equal(first.href, 'https://gemini.google.com/app/abc123');
    const modifiedClick = new w.MouseEvent('click', {bubbles: true, cancelable: true, ctrlKey: true});
    first.dispatchEvent(modifiedClick);
    assert.equal(modifiedClick.defaultPrevented, false);
    assert.equal(pending.length, 0);
    const normalClick = new w.MouseEvent('click', {bubbles: true, cancelable: true});
    first.dispatchEvent(normalClick);
    assert.equal(normalClick.defaultPrevented, true);
    assert.equal(doc.activeElement, first);
    assert.equal(first.getAttribute('aria-current'), 'true');
    const copyKey = new w.KeyboardEvent('keydown', {key: 'c', ctrlKey: true, bubbles: true, cancelable: true});
    first.dispatchEvent(copyKey);
    assert.equal(copyKey.defaultPrevented, true);
    assert.deepEqual(opened[0], ['https://gemini.google.com/app/abc123', '_blank', 'noopener,noreferrer']);
    const range = doc.createRange();
    range.selectNodeContents(first.querySelector('.session-title'));
    w.getSelection().removeAllRanges();
    w.getSelection().addRange(range);
    const selectionCopy = new w.KeyboardEvent('keydown', {key: 'c', ctrlKey: true, bubbles: true, cancelable: true});
    first.dispatchEvent(selectionCopy);
    assert.equal(selectionCopy.defaultPrevented, false);
    assert.equal(opened.length, 1);
    w.getSelection().removeAllRanges();
    doc.querySelectorAll('.session-item')[1].click();
    await respond('/api/sessions/chatgpt_', b);
    await respond('/api/sessions/gemini_', a);
    assert.equal(doc.querySelector('.viewer-title').textContent, b.title, 'Late detail response must not replace current selection');
    doc.getElementById('copySessionBtn').click();
    await flush();
    assert(copied[0].startsWith('# Conversation: Second conversation\n\n'));
    assert(copied[0].includes('\nSynthetic message\n'));
    assert(!copied[0].includes('\\n'), 'Markdown uses real line breaks');
    doc.getElementById('searchInput').value = 'first';
    doc.getElementById('searchBtn').click();
    doc.getElementById('searchInput').value = '';
    doc.getElementById('searchBtn').click();
    await respond('/api/sessions?', [b]);
    await respond('/api/search?', [{...a, snippet: 'Old query result'}]);
    assert.equal(doc.querySelector('.session-title').textContent, b.title, 'Late search must not replace current list');
    assert.equal(w.originalUrl({source: 'gemini_web', native_id: 'act_2026'}), 'https://gemini.google.com/app');
    assert.equal(w.originalUrl({source: 'chatgpt', session_id: 'chatgpt_%ZZ'}), 'https://chatgpt.com/');
    assert.equal(w.originalUrl({source: 'chatgpt', native_id: 'https://evil.example/'}), 'https://chatgpt.com/');
    for (const source of ['claude_code', 'codex', 'copilot_cli', 'other', 'constructor', '__proto__', 'toString']) {
      assert.equal(w.originalUrl({source, native_id: 'abc'}), null, `No original site for ${source}`);
    }
    const input = doc.getElementById('searchInput');
    input.value = 'sql srch';
    input.dispatchEvent(new w.Event('input', {bubbles: true}));
    assert.equal(pending.length, 0, 'Typing is debounced');
    await new Promise(resolve => setTimeout(resolve, 300));
    assert(pending[0].url.includes('fuzzy=1'));
    // An old response cannot repaint results while the next query is debouncing.
    input.value = 'new query';
    input.dispatchEvent(new w.Event('input', {bubbles: true}));
    await respond('/api/search?', [{...a, snippet: 'Stale'}]);
    assert.equal(doc.querySelector('.session-title').textContent, b.title);
    doc.getElementById('fuzzyToggle').click();
    assert(pending[0].url.includes('fuzzy=0'));
    assert.equal(w.localStorage.getItem('aigator.fuzzy'), 'false');
    await respond('/api/search?', [{...b, snippet: 'Keyword match'}]);
    input.value = 'exact';
    input.dispatchEvent(new w.Event('input', {bubbles: true}));
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(pending.length, 0, 'Exact mode waits for Enter');
    input.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    assert(pending[0].url.includes('q=exact&fuzzy=0'));
    await respond('/api/search?', [{...b, snippet: 'Exact'}]);
    input.focus();
    input.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));
    assert(doc.activeElement.classList.contains('session-item'));
    doc.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowUp', bubbles: true}));
    assert.equal(doc.activeElement, input);
    input.value = '';
    input.dispatchEvent(new w.Event('input', {bubbles: true}));
    await respond('/api/sessions?', [a, b]);
  } finally {
    dom.window.close();
  }
}

async function popup() {
  const html = fs.readFileSync('extension/popup.html', 'utf8');
  const dom = new JSDOM(html, {runScripts: 'outside-only'});
  const w = dom.window;
  const saved = [], opened = [];
  w.chrome = {storage: {local: {get: async () => ({daemonUrl: 'http://127.0.0.1:8765/api/ingest', authToken: 'paired'}), set: async value => saved.push(value)}},
    tabs: {create: value => opened.push(value), query: async () => [{id: 1}], sendMessage: (id, msg, cb) => cb({success: true})}, runtime: {}};
  w.fetch = async () => ({ok: true});
  w.eval(fs.readFileSync('extension/popup.js', 'utf8'));
  await flush();
  assert.equal(w.document.getElementById('daemonState').textContent, '🟢 Online');
  w.document.getElementById('saveSettingsBtn').click();
  w.document.getElementById('openUiBtn').click();
  w.document.getElementById('syncBtn').click();
  await flush();
  assert.equal(saved[0].authToken, 'paired');
  assert.equal(opened[0].url, 'http://127.0.0.1:8765');
  assert.equal(w.document.getElementById('msg').textContent, '✅ Synced successfully!');
  dom.window.close();
}

async function background() {
  let listener, request, token = 'paired', autoSync = true;
  const context = {URL, AbortSignal, chrome: {runtime: {id: 'own', getURL: path => 'chrome-extension://own/' + path, onMessage: {addListener: fn => listener = fn}},
    storage: {local: {get: async () => ({authToken: token, autoSync}), set: async value => {token = value.authToken;}}}},
    fetch: async (url, options) => {request = {url, options}; return {ok: true, json: async () => url.endsWith('/api/auth/token') ? {token: 'new-pair'} : {stored_message_count: 2}};}};
  vm.runInNewContext(fs.readFileSync('extension/background.js', 'utf8'), context);
  const send = () => new Promise(resolve => listener({action: 'ingest', payload: {source: 'perplexity', turns: []}}, {id: 'own'}, resolve));
  assert.equal((await send()).stored_message_count, 2);
  assert.equal(request.options.headers.Authorization, 'Bearer paired');
  token = '';
  assert.match((await send()).error, /Connect local daemon/);
  const pair = sender => new Promise(resolve => listener({action: 'pair'}, sender, resolve));
  assert.match((await pair({id: 'own', tab: {id: 1}})).error, /popup/);
  assert.equal((await pair({id: 'own', url: 'chrome-extension://own/popup.html', tab: {id: 2}})).status, 'paired');
  assert.equal(token, 'new-pair');
  autoSync = false;
  const paused = await new Promise(resolve => listener({action: 'ingest', automatic: true}, {id: 'own'}, resolve));
  assert.match(paused.error, /paused/);
  assert.equal((await send()).stored_message_count, 2, 'manual capture still works when paused');
}

async function automaticCapture() {
  const dom = new JSDOM('<div data-message-author-role="user">question</div>', {
    url: 'https://chatgpt.com/c/synthetic', runScripts: 'outside-only', pretendToBeVisual: true
  });
  const w = dom.window;
  Object.defineProperty(w.HTMLElement.prototype, 'innerText', {
    get() {return this.textContent;}, set(value) {this.textContent = value;}
  });
  let tick, changed, requests = 0;
  w.setInterval = fn => {tick = fn;};
  w.setTimeout = () => {};
  w.chrome = {storage: {local: {get: async () => ({autoSync: true})},
    onChanged: {addListener: fn => changed = fn}}, runtime: {
      onMessage: {addListener() {}}, sendMessage: async request => {
        requests++;
        assert.equal(request.automatic, true);
        return {outcomes: ['updated'], stored_message_count: request.payload.turns.length};
      }
    }};
  w.eval(fs.readFileSync('extension/content.js', 'utf8'));
  await flush();
  await tick();
  assert.equal(requests, 0, 'wait for stable content');
  await tick();
  await tick();
  assert.equal(requests, 1, 'ignore generated timestamps and unchanged content');
  const message = w.document.querySelector('[data-message-author-role]');
  message.textContent = 'edited question';
  message.dataset.isStreaming = 'true';
  await tick(); await tick();
  assert.equal(requests, 1, 'do not capture known streaming state');
  delete message.dataset.isStreaming;
  await tick(); await tick();
  assert.equal(requests, 2);
  changed({autoSync: {newValue: false}}, 'local');
  message.textContent = 'paused change';
  await tick(); await tick();
  assert.equal(requests, 2);
  changed({autoSync: {newValue: true}}, 'local');
  await tick(); await tick();
  assert.equal(requests, 3);
  w.history.pushState({}, '', '/');
  await tick(); await tick();
  assert.equal(requests, 3, 'ignore landing page');
  w.history.pushState({}, '', '/c/second');
  await tick(); await tick();
  assert.equal(requests, 4, 'capture SPA route change');
  let now = 100000;
  w.Date.now = () => now;
  w.chrome.runtime.sendMessage = async () => {requests++; return {error: 'offline'};};
  message.textContent = 'retry this change';
  await tick(); await tick(); await tick();
  assert.equal(requests, 5, 'failed request backs off');
  now += 30000;
  await tick();
  assert.equal(requests, 6, 'retry after backoff');
  assert.equal(w.document.getElementById('__aigator_toast__'), null, 'automatic sync is quiet');
  dom.window.close();
}

async function captureClients() {
  const cases = [
    ['https://gemini.google.com/app/synthetic', '<user-query><div class="user-query-container">question</div></user-query><model-response><div class="response-container">answer</div></model-response>'],
    ['https://claude.ai/chat/synthetic', '<div data-testid="user-message"><div class="font-user-message">question</div></div><div data-testid="assistant-message"><div class="font-claude-message">answer</div></div>']
  ];
  for (const file of ['extension/content.js', 'userscripts/aigator-capture.user.js']) {
    for (const [url, markup] of cases) {
      const dom = new JSDOM(markup, {url, runScripts: 'outside-only'});
      const w = dom.window;
      Object.defineProperty(w.HTMLElement.prototype, 'innerText', {
        get() {return this.textContent;}, set(value) {this.textContent = value;}
      });
      let listener, payload, outcome = 'skipped_stale';
      const result = () => ({status: 'ok', outcomes: [outcome], stored_message_count: 2});
      // Install the floating button immediately; skip cosmetic toast timers.
      w.setTimeout = (fn, delay) => {if (delay === 1500) fn();};
      w.chrome = {runtime: {onMessage: {addListener: fn => listener = fn}, sendMessage: async req => {
        payload = req.payload; return result();
      }}};
      w.GM_registerMenuCommand = () => {};
      w.GM_getValue = key => key === 'aigator_token' ? 'synthetic-pairing' : null;
      w.GM_xmlhttpRequest = req => {payload = JSON.parse(req.data); req.onload({status: 200, responseText: JSON.stringify(result())});};
      w.eval(fs.readFileSync(file, 'utf8'));
      for (outcome of ['skipped_stale', 'unchanged', 'updated']) {
        if (file.startsWith('extension')) {
          const reply = await new Promise(resolve => listener({action: 'sync_chat'}, {}, resolve));
          assert.equal(reply.success, outcome !== 'skipped_stale');
        } else {
          w.document.getElementById('__aigator_sync_btn__').click();
        }
        assert.equal(payload.turns.length, 2);
        assert.deepEqual(Array.from(payload.turns, turn => turn.text), ['question', 'answer']);
        const message = w.document.getElementById('__aigator_toast__').textContent;
        assert.match(message, outcome === 'skipped_stale' ? /Capture skipped/ : outcome === 'unchanged' ? /Already up to date/ : /Synced/);
      }
      dom.window.close();
    }
  }
}

(async () => {
  for (const file of ['extension/background.js', 'extension/content.js', 'extension/popup.js', 'userscripts/aigator-capture.user.js']) {
    new vm.Script(fs.readFileSync(file, 'utf8'), {filename: file});
  }
  await dashboard();
  await dashboardInteractions();
  await popup();
  await background();
  await captureClients();
  await automaticCapture();
  console.log('Browser DOM, popup, background relay, and JavaScript syntax checks passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
