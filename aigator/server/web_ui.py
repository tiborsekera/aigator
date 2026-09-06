"""Embedded single-page search and viewer Web UI for Aigator."""

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aigator — Your conversations, together</title>
  <style>
    :root {
      --bg: #111516;
      --card-bg: #191e20;
      --card-border: #2c3436;
      --text: #edf1f0;
      --text-muted: #9aa9a8;
      --accent: #b2e7ca;
      --accent-hover: #96d8b4;
      --code-bg: #131819;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; }
    header { border-bottom: 1px solid var(--card-border); padding: 1.1rem 2rem; display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
    .brand { font-size: 1.25rem; font-weight: 650; letter-spacing: -0.04em; display: flex; align-items: center; gap: 0.7rem; }
    .brand-icon { background: var(--accent); color: var(--bg); border-radius: 10px; width: 32px; height: 32px; display: grid; place-items: center; font-size: 1rem; letter-spacing: -0.1em; }
    .tagline { font-size: 0.8rem; color: var(--text-muted); font-weight: 400; letter-spacing: 0; padding-left: 1rem; border-left: 1px solid var(--card-border); margin-left: 0.4rem; }
    .stats-badge { font-size: 0.78rem; color: var(--text-muted); }
    .local-badge { font-size: 0.75rem; color: var(--accent); white-space: nowrap; }
    .header-status { display: flex; align-items: center; gap: 1.5rem; }
    main { flex: 1; max-width: 1500px; width: 100%; margin: 0 auto; padding: 1.8rem 2rem; display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 2rem; }
    
    .sidebar { display: flex; flex-direction: column; gap: 1rem; height: calc(100vh - 133px); min-height: 440px; }
    .section-label { font-size: 0.7rem; color: var(--text-muted); letter-spacing: 0.12em; text-transform: uppercase; font-weight: 600; }
    .search-box { position: relative; display: flex; gap: 0.5rem; }
    .search-input { flex: 1; min-width: 0; padding: 0.75rem 0.9rem; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 7px; color: var(--text); font-size: 0.85rem; outline: none; }
    .search-input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2); }
    .search-btn { background: var(--accent); color: #0f172a; border: none; border-radius: 8px; padding: 0 1.2rem; font-weight: 600; cursor: pointer; font-size: 0.9rem; transition: background 0.15s; }
    .search-btn:hover { background: var(--accent-hover); }
    .search-options { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; font-size: 0.72rem; color: var(--text-muted); margin-top: -0.3rem; }
    .fuzzy-toggle { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; white-space: nowrap; }
    .fuzzy-toggle input { accent-color: var(--accent); cursor: pointer; }
    
    .filter-tabs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.35rem; padding-bottom: 0.2rem; }
    .tab-btn { background: transparent; border: 1px solid transparent; color: var(--text-muted); padding: 0.35rem 0.6rem; border-radius: 5px; cursor: pointer; font-size: 0.75rem; white-space: nowrap; transition: all 0.15s; }
    .tab-btn:hover { border-color: var(--accent); color: var(--text); }
    .tab-btn.active { background: #293c33; color: var(--accent); font-weight: 600; }

    .session-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 0.25rem; padding-right: 0.3rem; }
    .session-item { display: block; color: inherit; text-decoration: none; border: 1px solid transparent; border-radius: 7px; padding: 1rem 0.9rem; cursor: pointer; transition: background 0.15s; }
    .session-item:hover { background: var(--card-bg); }
    .session-item.selected { border-color: #3a5146; background: #202d27; }
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
    .session-item-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; }
    .source-tag { font-size: 0.65rem; font-weight: 600; letter-spacing: 0.025em; padding: 0.15rem 0.4rem; border-radius: 4px; }
    .tag-chatgpt { background: rgba(16, 163, 127, 0.2); color: #34d399; }
    .tag-claude_web { background: rgba(217, 119, 6, 0.2); color: #fbbf24; }
    .tag-gemini_web { background: rgba(59, 130, 246, 0.2); color: #60a5fa; }
    .tag-perplexity { background: rgba(139, 92, 246, 0.2); color: #c084fc; }
    .tag-vscode_copilot { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
    .tag-claude_code { background: #3b2c24; color: #efb28c; }
    .tag-codex { background: #243b31; color: var(--accent); }
    .tag-copilot_cli { background: #302c42; color: #c5b4ef; }
    .tag-other { background: rgba(148, 163, 184, 0.2); color: #94a3b8; }
    
    .session-title { font-size: 0.88rem; font-weight: 500; margin: 0.45rem 0; line-height: 1.45; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .session-meta { font-size: 0.75rem; color: var(--text-muted); display: flex; justify-content: space-between; }
    .search-snippet { font-size: 0.8rem; color: #cbd5e1; margin-top: 0.4rem; line-height: 1.35; background: rgba(0,0,0,0.25); padding: 0.4rem 0.6rem; border-radius: 4px; }
    .search-snippet mark { background: #fef08a; color: #000; border-radius: 2px; padding: 0 2px; font-weight: 600; }

    .viewer { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; display: flex; flex-direction: column; height: calc(100vh - 133px); min-height: 440px; overflow: hidden; }
    .viewer-header { padding: 1.5rem 1.8rem; border-bottom: 1px solid var(--card-border); display: flex; flex-direction: column; align-items: flex-start; gap: 1rem; }
    .viewer-title { font-size: 1.35rem; font-weight: 550; letter-spacing: -0.025em; color: var(--text); line-height: 1.4; }
    .viewer-meta { color: var(--text-muted); font-size: 0.75rem; display: flex; align-items: center; flex-wrap: wrap; gap: 0.65rem; margin-top: 0.6rem; }
    .viewer-actions { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
    
    .copy-session-btn { background: transparent; color: var(--text); border: 1px solid #3a4446; border-radius: 6px; padding: 0.5rem 0.8rem; font-size: 0.75rem; font-weight: 500; cursor: pointer; text-decoration: none; white-space: nowrap; }
    .copy-session-btn:hover { background: #293334; border-color: var(--text-muted); }
    .original-link { color: var(--accent); border-color: #455e4f; }

    .thread-container { flex: 1; overflow-y: auto; padding: 1.5rem 1.8rem; display: flex; flex-direction: column; gap: 1.3rem; }
    .message-card { display: flex; flex-direction: column; gap: 0.6rem; padding: 1.1rem 1.2rem; border-radius: 7px; line-height: 1.75; font-size: 0.88rem; }
    .msg-user { background: #232b2c; color: var(--text); width: 100%; }
    .msg-assistant { background: transparent; color: #d2dcda; width: 100%; padding-top: 0.2rem; }
    
    .msg-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.2rem; }
    .msg-role { font-size: 0.65rem; font-weight: 600; letter-spacing: 0.05em; color: var(--text-muted); }
    .msg-user .msg-role { color: var(--accent); }
    .copy-msg-btn { background: transparent; border: 1px solid transparent; color: var(--text-muted); cursor: pointer; font-size: 0.75rem; padding: 0.15rem 0.45rem; border-radius: 4px; transition: all 0.15s; }
    .copy-msg-btn:hover { background: rgba(255,255,255,0.12); color: #38bdf8; border-color: rgba(56,189,248,0.3); }
    
    .msg-content { white-space: pre-wrap; word-break: break-word; }
    .empty-state { display: flex; flex-direction: column; gap: 0.8rem; height: 100%; justify-content: center; align-items: center; color: var(--text-muted); font-size: 0.9rem; text-align: center; padding: 2rem; }
    .empty-state strong { color: var(--text); font-size: 1.4rem; font-weight: 500; letter-spacing: -0.03em; }
    .hint { color: var(--text-muted); font-size: 0.7rem; line-height: 1.6; }
    @media (max-width: 900px) { main { grid-template-columns: 290px minmax(0, 1fr); gap: 1rem; padding: 1rem; } .tagline { display: none; } }
    @media (max-width: 680px) { header { padding: 1rem; } main { grid-template-columns: minmax(0, 1fr); } .sidebar { height: 400px; min-height: 0; } .viewer { height: 650px; } .header-status { gap: 0.5rem; } .local-badge { display: none; } .viewer-header, .thread-container { padding: 1.2rem; } }
  </style>
</head>
<body>
  <header>
    <div class="brand"><span class="brand-icon" aria-hidden="true">ai</span>Aigator<span class="tagline">Your conversations, together.</span></div>
    <div class="header-status"><span class="local-badge">● Local storage</span><span class="stats-badge" id="stats">Connecting…</span></div>
  </header>
  <main>
    <div class="sidebar">
      <div class="section-label">Conversations</div>
      <div class="search-box">
        <input type="search" id="searchInput" class="search-input" placeholder="Search your conversations…" aria-label="Search conversations" autofocus>
        <button class="search-btn" id="searchBtn">Search</button>
      </div>
      <div class="search-options">
        <label class="fuzzy-toggle" title="Match abbreviated words, with the best matches first. Turn off for keyword and quoted-phrase search."><input type="checkbox" id="fuzzyToggle" checked>Fuzzy search</label>
        <span id="searchStatus" role="status" aria-live="polite">Type to find · best matches first</span>
      </div>
      <div class="filter-tabs">
        <button class="tab-btn active" data-src="">All</button>
        <button class="tab-btn" data-src="claude_code">Claude Code</button>
        <button class="tab-btn" data-src="codex">Codex</button>
        <button class="tab-btn" data-src="copilot_cli">Copilot CLI</button>
        <button class="tab-btn" data-src="vscode_copilot">VS Code Copilot</button>
        <button class="tab-btn" data-src="chatgpt">ChatGPT</button>
        <button class="tab-btn" data-src="claude_web">Claude Web</button>
        <button class="tab-btn" data-src="gemini_web">Gemini</button>
        <button class="tab-btn" data-src="perplexity">Perplexity</button>
      </div>
      <div class="session-list" id="sessionList">
        <div style="color:var(--text-muted);padding:1rem;">Loading sessions...</div>
      </div>
      <div class="hint">↓ to results · Enter to read · Ctrl+click to open original<br>Ctrl+C on a focused web conversation opens its website.</div>
    </div>
    <div class="viewer" id="viewer">
      <div class="empty-state"><strong>Pick up where you left off.</strong><span>Search across your AI conversations.<br>Select a thread to read, copy, or open the original.</span></div>
    </div>
  </main>

  <script>
    const AUTH_TOKEN = "__AIGATOR_AUTH_TOKEN__";
    let activeSource = '';
    let selectedSessionId = null;
    let currentSession = null;
    let listRequest = 0;
    let detailRequest = 0;
    let searchTimer;
    let searchController;
    const searchInput = document.getElementById('searchInput');
    const fuzzyToggle = document.getElementById('fuzzyToggle');
    const searchStatus = document.getElementById('searchStatus');
    try { fuzzyToggle.checked = localStorage.getItem('aigator.fuzzy') !== 'false'; } catch {}
    function searchHint() {
      searchStatus.textContent = fuzzyToggle.checked ? 'Type to find · best matches first' : 'Keywords · Enter to search';
    }
    searchHint();

    function sourceName(source) {
      const names = {claude_code: 'Claude Code', codex: 'Codex', copilot_cli: 'Copilot CLI', vscode_copilot: 'VS Code Copilot', chatgpt: 'ChatGPT', claude_web: 'Claude Web', gemini_web: 'Gemini', perplexity: 'Perplexity'};
      return Object.hasOwn(names, source) ? names[source] : source || 'Other';
    }

    function originalUrl(session) {
      const homes = {chatgpt: 'https://chatgpt.com/', claude_web: 'https://claude.ai/',
        gemini_web: 'https://gemini.google.com/app', perplexity: 'https://www.perplexity.ai/'};
      const source = session.source;
      if (!Object.hasOwn(homes, source)) return null;
      const home = homes[source];
      if (!home) return null;
      let native = session.native_id;
      if (!native && typeof session.session_id === 'string' && session.session_id.startsWith(source + '_')) {
        try { native = decodeURIComponent(session.session_id.slice(source.length + 1)); } catch { return home; }
      }
      if (typeof native !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(native) || /^(act_|chatgpt_|claude_|gemini_|pplx_)/.test(native)) return home;
      const prefixes = {chatgpt: 'https://chatgpt.com/c/', claude_web: 'https://claude.ai/chat/', gemini_web: 'https://gemini.google.com/app/'};
      return prefixes[source] ? prefixes[source] + encodeURIComponent(native) : home;
    }

    function displayDate(value) {
      const text = String(value || '');
      const date = new Date(text.slice(0, 10) + 'T00:00:00Z');
      return Number.isNaN(date.getTime()) ? text : new Intl.DateTimeFormat('en', {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'}).format(date);
    }

    function apiFetch(url, options = {}) {
      options.headers = options.headers || {};
      if (AUTH_TOKEN) {
        options.headers['Authorization'] = 'Bearer ' + AUTH_TOKEN;
      }
      return fetch(url, options);
    }

    async function loadStats() {
      try {
        const res = await apiFetch('/api/stats');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        document.getElementById('stats').innerText = `${data.total_sessions.toLocaleString()} conversations · ${data.total_messages.toLocaleString()} messages`;
      } catch (err) {
        document.getElementById('stats').innerText = 'Connection unavailable';
      }
    }

    async function loadSessions() {
      clearTimeout(searchTimer);
      if (searchController) searchController.abort();
      searchHint();
      const request = ++listRequest;
      const listEl = document.getElementById('sessionList');
      listEl.innerHTML = '<div style="color:var(--text-muted);padding:1rem;">Loading sessions...</div>';
      try {
        let url = '/api/sessions?limit=50';
        if (activeSource) url += `&source=${encodeURIComponent(activeSource)}`;
        const res = await apiFetch(url);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const sessions = await res.json();
        if (request !== listRequest) return;
        
        if (!sessions.length) {
          listEl.innerHTML = '<div style="color:var(--text-muted);padding:1rem;">No sessions found for this filter.</div>';
          return;
        }

        listEl.innerHTML = sessions.map(s => {
          const src = s.source || 'other';
          const tagClass = 'tag-' + (/^[a-z_]+$/.test(src) ? src : 'other');
          const isSel = s.session_id === selectedSessionId ? 'selected' : '';
          const lastDate = s.updated_at || s.created_at || '';
          return `
            <a class="session-item ${isSel}" data-session-id="${escapeHtml(s.session_id)}" href="${escapeHtml(originalUrl(s) || '#')}" target="_blank" rel="noopener noreferrer" aria-current="${isSel ? 'true' : 'false'}">
              <div class="session-item-header">
                <span class="source-tag ${tagClass}">${escapeHtml(sourceName(src))}</span>
                <span style="font-size:0.75rem;color:var(--text-muted);">${Number(s.message_count) || 0} msgs</span>
              </div>
              <div class="session-title">${escapeHtml(s.title)}</div>
              <div class="session-meta">
                <span>${escapeHtml(displayDate(lastDate))}</span>
              </div>
            </a>
          `;
        }).join('');
      } catch (err) {
        if (request !== listRequest) return;
        listEl.innerHTML = `<div style="color:#ef4444;padding:1rem;">Error loading sessions: ${escapeHtml(err.message)}</div>`;
      }
    }

    async function handleSearch() {
      clearTimeout(searchTimer);
      if (searchController) searchController.abort();
      const query = document.getElementById('searchInput').value.trim();
      if (!query) {
        return loadSessions();
      }

      const request = ++listRequest;

      const listEl = document.getElementById('sessionList');
      const terms = query.split(/\\s+/);
      if (fuzzyToggle.checked && (query.length > 256 || terms.length > 8 || terms.some(term => term.length > 64))) {
        searchStatus.textContent = 'Query too long';
        listEl.innerHTML = '<div style="color:var(--text-muted);padding:1rem;">Use up to 8 short terms for fuzzy search, or turn it off to search a longer phrase.</div>';
        return;
      }
      searchStatus.textContent = 'Searching…';
      searchController = new AbortController();

      try {
        let url = `/api/search?q=${encodeURIComponent(query)}&fuzzy=${fuzzyToggle.checked ? '1' : '0'}`;
        if (activeSource) url += `&source=${encodeURIComponent(activeSource)}`;
        const res = await apiFetch(url, {signal: searchController.signal});
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const results = await res.json();
        if (request !== listRequest) return;
        searchStatus.textContent = `${results.length} matches · ${fuzzyToggle.checked ? 'best first' : 'newest first'}`;

        if (!results.length) {
          listEl.innerHTML = `<div style="color:var(--text-muted);padding:1rem;">No results matching "${escapeHtml(query)}"</div>`;
          return;
        }

        listEl.innerHTML = results.map(r => {
          const src = r.source || 'other';
          const tagClass = 'tag-' + (/^[a-z_]+$/.test(src) ? src : 'other');
          const isSel = r.session_id === selectedSessionId ? 'selected' : '';
          const lastDate = r.session_updated_at || r.timestamp || '';
          return `
            <a class="session-item ${isSel}" data-session-id="${escapeHtml(r.session_id)}" href="${escapeHtml(originalUrl(r) || '#')}" target="_blank" rel="noopener noreferrer" aria-current="${isSel ? 'true' : 'false'}">
              <div class="session-item-header">
                <span class="source-tag ${tagClass}">${escapeHtml(sourceName(src))}</span>
                <span style="font-size:0.75rem;color:var(--text-muted);">${escapeHtml(displayDate(lastDate))}</span>
              </div>
              <div class="session-title">${escapeHtml(r.title)}</div>
              <div class="search-snippet">${formatSnippet(r.snippet)}</div>
            </a>
          `;
        }).join('');
      } catch (err) {
        if (request !== listRequest) return;
        if (err.name === 'AbortError') return;
        searchStatus.textContent = 'Search unavailable';
        listEl.innerHTML = `<div style="color:#ef4444;padding:1rem;">Search error: ${escapeHtml(err.message)}</div>`;
      }
    }

    async function openSession(sessionId) {
      const request = ++detailRequest;
      selectedSessionId = sessionId;
      currentSession = null;
      document.querySelectorAll('.session-item').forEach(el => {
        const selected = el.dataset.sessionId === sessionId;
        el.classList.toggle('selected', selected);
        el.setAttribute('aria-current', String(selected));
      });
      
      const viewer = document.getElementById('viewer');
      viewer.innerHTML = '<div class="empty-state">Loading conversation thread...</div>';

      try {
        const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const session = await res.json();
        if (request !== detailRequest) return;
        currentSession = session;

        const src = session.source || 'other';
        const tagClass = 'tag-' + (/^[a-z_]+$/.test(src) ? src : 'other');
        const messages = session.messages || [];

        viewer.innerHTML = `
          <div class="viewer-header">
            <div>
              <div class="viewer-title">${escapeHtml(session.title)}</div>
              <div class="viewer-meta">
                <span class="source-tag ${tagClass}">${escapeHtml(sourceName(src))}</span>
                <span>Updated ${escapeHtml(displayDate(session.updated_at))}</span><span>·</span><span>${messages.length} messages</span>
              </div>
            </div>
            <div class="viewer-actions">
              ${originalUrl(session) ? `<a class="copy-session-btn original-link" id="openOriginalBtn" href="${escapeHtml(originalUrl(session))}" target="_blank" rel="noopener noreferrer">Open in ${escapeHtml(sourceName(src))} ↗</a>` : ''}
              <button class="copy-session-btn" id="copySessionBtn" title="Copy complete conversation as Markdown">Copy Markdown</button>
            </div>
          </div>
          <div class="thread-container">
            ${messages.map((m, idx) => {
              const isUser = m.role === 'user';
              const cardClass = isUser ? 'msg-user' : 'msg-assistant';
              const roleTitle = isUser ? 'USER' : (m.role ? m.role.toUpperCase() : 'ASSISTANT');
              const modelInfo = m.model ? ` (${escapeHtml(m.model)})` : '';
              return `
                <div class="message-card ${cardClass}">
                  <div class="msg-header">
                    <div class="msg-role">${escapeHtml(roleTitle)}${modelInfo}</div>
                    <button class="copy-msg-btn" data-message-index="${idx}" title="Copy message text">
                      Copy
                    </button>
                  </div>
                  <div class="msg-content">${escapeHtml(m.content)}</div>
                </div>
              `;
            }).join('')}
          </div>
        `;
      } catch (err) {
        if (request !== detailRequest) return;
        currentSession = null;
        viewer.innerHTML = `<div class="empty-state" style="color:#ef4444;">Failed to load session: ${escapeHtml(err.message)}</div>`;
      }
    }

    function copyFullSession() {
      if (!currentSession) return;
      const btn = document.getElementById('copySessionBtn');
      
      let md = `# Conversation: ${currentSession.title}\\n\\n`;
      md += `- **Source:** ${currentSession.source}\\n`;
      md += `- **Session ID:** \\`${currentSession.session_id}\\`\\n`;
      md += `- **Created:** ${currentSession.created_at}\\n`;
      md += `- **Last Message:** ${currentSession.updated_at}\\n`;
      md += `- **Messages:** ${(currentSession.messages || []).length}\\n\\n`;
      md += `---\\n\\n`;

      for (const m of (currentSession.messages || [])) {
        const role = (m.role || 'user').toUpperCase();
        const modelStr = m.model ? ` (${m.model})` : '';
        const timeStr = m.timestamp ? ` • ${m.timestamp}` : '';
        md += `### ${role}${modelStr}${timeStr}\\n\\n`;
        md += `${m.content}\\n\\n`;
        md += `---\\n\\n`;
      }

      navigator.clipboard.writeText(md.trim()).then(() => {
        if (btn) {
          const original = btn.innerHTML;
          btn.innerHTML = 'Copied ✓';
          setTimeout(() => { btn.innerHTML = original; }, 2000);
        }
      }).catch(err => {
        alert('Failed to copy: ' + err.message);
      });
    }

    function copyMessage(index, btnEl) {
      if (!currentSession || !currentSession.messages || !currentSession.messages[index]) return;
      const msg = currentSession.messages[index];
      
      navigator.clipboard.writeText(msg.content).then(() => {
        if (btnEl) {
          const orig = btnEl.innerHTML;
          btnEl.innerHTML = '✓ Copied';
          btnEl.style.color = '#38bdf8';
          setTimeout(() => {
            btnEl.innerHTML = orig;
            btnEl.style.color = '';
          }, 2000);
        }
      }).catch(err => {
        alert('Failed to copy message: ' + err.message);
      });
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function formatSnippet(snippet) {
      if (!snippet) return '';
      return escapeHtml(snippet).replace(/«/g, '<mark>').replace(/»/g, '</mark>');
    }

    searchInput.addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      ++listRequest; // Invalidate previous responses immediately, even during debounce.
      if (searchController) searchController.abort();
      if (!searchInput.value.trim()) { loadSessions(); return; }
      searchHint();
      if (fuzzyToggle.checked && !e.isComposing) searchTimer = setTimeout(handleSearch, 250);
    });
    searchInput.addEventListener('compositionend', () => {
      if (fuzzyToggle.checked) { clearTimeout(searchTimer); searchTimer = setTimeout(handleSearch, 250); }
    });
    fuzzyToggle.addEventListener('change', () => {
      try { localStorage.setItem('aigator.fuzzy', String(fuzzyToggle.checked)); } catch {}
      handleSearch();
    });
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        handleSearch();
      } else if (e.key === 'ArrowDown') {
        const first = document.querySelector('.session-item');
        if (first) { e.preventDefault(); first.focus(); }
      }
    });

    document.getElementById('searchBtn').addEventListener('click', () => {
      handleSearch();
    });

    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeSource = btn.getAttribute('data-src');
        if (document.getElementById('searchInput').value.trim()) {
          handleSearch();
        } else {
          loadSessions();
        }
      });
    });

    document.getElementById('sessionList').addEventListener('click', event => {
      const item = event.target.closest('[data-session-id]');
      if (!item) return;
      if ((event.ctrlKey || event.metaKey) && item.getAttribute('href') !== '#') return;
      event.preventDefault();
      item.focus({preventScroll: true});
      openSession(item.dataset.sessionId);
    });
    document.getElementById('sessionList').addEventListener('keydown', event => {
      const item = event.target.closest('[data-session-id]');
      if (item && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        event.preventDefault();
        const next = event.key === 'ArrowDown' ? item.nextElementSibling : item.previousElementSibling;
        if (next) next.focus();
        else if (event.key === 'ArrowUp') searchInput.focus();
      }
      if (item && event.ctrlKey && event.key.toLowerCase() === 'c' && !event.altKey && !event.shiftKey && !window.getSelection().toString() && item.getAttribute('href') !== '#') {
        event.preventDefault();
        window.open(item.href, '_blank', 'noopener,noreferrer');
      }
    });
    document.getElementById('viewer').addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button) return;
      if (button.id === 'copySessionBtn') copyFullSession();
      if (button.dataset.messageIndex !== undefined) copyMessage(Number(button.dataset.messageIndex), button);
    });

    // Initial load
    loadStats();
    loadSessions();
  </script>
</body>
</html>
"""
