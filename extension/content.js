/**
 * Aigator Content Script - Intercepts and extracts active AI conversations
 * Supports: ChatGPT, Claude.ai, Gemini, Perplexity
 */

(function () {
  if (window.__AIGATOR_LOADED__) return;
  window.__AIGATOR_LOADED__ = true;

  let daemonUrl = "http://127.0.0.1:8765/api/ingest";
  let autoSync = true;
  let syncing = false;
  let candidate = null;
  let submitted = null;
  let retryAfter = 0;

  function fingerprint(data) {
    return JSON.stringify([data.source, data.id, data.title, data.turns.map(turn => [turn.author, turn.text])]);
  }

  function captureStatus(message, automatic, isError = false) {
    const button = document.getElementById('__aigator_sync_btn__');
    if (button) button.title = message;
    if (!automatic) showToast(message, isError);
  }

  async function autoSyncTick() {
    if (!autoSync || syncing || document.visibilityState === 'hidden') return;
    // Landing pages lack stable IDs and must never become synthetic conversations.
    const routes = {chatgpt: /^\/c\/[a-zA-Z0-9-]+/, claude_web: /^\/chat\/[a-zA-Z0-9-]+/,
      gemini_web: /^\/app\/[a-zA-Z0-9_-]+/, perplexity: /^\/(?:search|thread)\/[a-zA-Z0-9_-]+/};
    if (!routes[getPlatform()]?.test(location.pathname)) {
      candidate = null;
      return;
    }
    if (document.querySelector('[data-is-streaming="true"], button[data-testid="stop-button"], button[aria-label="Stop generating"], button[aria-label="Stop response"]')) {
      candidate = null;
      return;
    }
    const data = extractActiveConversation();
    if (!data?.turns?.length) { candidate = null; return; }
    const key = fingerprint(data);
    if (candidate !== key) { candidate = key; return; }
    if (submitted === key || Date.now() < retryAfter) return;
    const result = await syncToAigator({automatic: true, data});
    if (result.success || result.result?.outcomes?.includes('skipped_stale')) submitted = key;
    else retryAfter = Date.now() + 30000;
  }
  function messageNodes(selector) {
    const nodes = Array.from(document.querySelectorAll(selector));
    return nodes.filter(node => !nodes.some(parent => parent !== node && parent.contains(node)));
  }
  function getPlatform() {
    const host = window.location.hostname;
    if (host.includes("openai.com") || host.includes("chatgpt.com")) return "chatgpt";
    if (host.includes("claude.ai")) return "claude_web";
    if (host.includes("gemini.google.com")) return "gemini_web";
    if (host.includes("perplexity.ai")) return "perplexity";
    return "other";
  }

  function extractChatGPT() {
    const urlMatch = window.location.pathname.match(/\/c\/([a-zA-Z0-9-]+)/);
    const nativeId = urlMatch ? urlMatch[1] : `chatgpt_${Date.now()}`;
    const title = (document.title || "").replace(/ - ChatGPT$/, "").trim() || "ChatGPT Conversation";

    const turns = [];
    const messageElements = document.querySelectorAll('[data-message-author-role]');
    
    if (messageElements.length > 0) {
      messageElements.forEach((el, idx) => {
        const role = el.getAttribute('data-message-author-role');
        const text = el.innerText.trim();
        if (text) {
          turns.push({
            author: role === 'user' ? 'user' : 'assistant',
            text: text,
            timestamp: new Date().toISOString()
          });
        }
      });
    } else {
      // Fallback selector for articles
      const articles = document.querySelectorAll('article');
      articles.forEach((art) => {
        const isUser = art.querySelector('button[aria-label="Edit message"]') || art.querySelector('div[data-message-author-role="user"]');
        const text = art.innerText.trim();
        if (text) {
          turns.push({
            author: isUser ? 'user' : 'assistant',
            text: text,
            timestamp: new Date().toISOString()
          });
        }
      });
    }

    return {
      source: "chatgpt",
      id: nativeId,
      title: title,
      turns: turns,
      created_at: new Date().toISOString()
    };
  }

  function extractClaude() {
    const urlMatch = window.location.pathname.match(/\/chat\/([a-zA-Z0-9-]+)/);
    const nativeId = urlMatch ? urlMatch[1] : `claude_${Date.now()}`;
    const title = (document.title || "").replace(/ - Claude$/, "").trim() || "Claude Conversation";

    const turns = [];
    // Claude conversation turns
    const userMsgs = document.querySelectorAll('.font-user-message, [data-testid="user-message"]');
    const assistantMsgs = document.querySelectorAll('.font-claude-message, [data-testid="assistant-message"]');

    // General selector for message rows
    const allTurnNodes = document.querySelectorAll('[data-is-streaming], [data-testid*="message"], .grid-cols-1 > div');
    
    if (userMsgs.length > 0 || assistantMsgs.length > 0) {
      const allElements = messageNodes('.font-user-message, .font-claude-message, [data-testid="user-message"], [data-testid="assistant-message"]');
      allElements.forEach((el) => {
        const isUser = el.classList.contains('font-user-message') || el.getAttribute('data-testid') === 'user-message';
        const text = el.innerText.trim();
        if (text) {
          turns.push({
            author: isUser ? 'user' : 'assistant',
            text: text,
            timestamp: new Date().toISOString()
          });
        }
      });
    } else {
      // Fallback to text blocks
      const paragraphs = document.querySelectorAll('main p, main pre');
      if (paragraphs.length > 0) {
        let currentText = [];
        paragraphs.forEach(p => currentText.push(p.innerText));
        turns.push({ author: 'user', text: title });
        turns.push({ author: 'assistant', text: currentText.join('\n\n') });
      }
    }

    return {
      source: "claude_web",
      id: nativeId,
      title: title,
      turns: turns,
      created_at: new Date().toISOString()
    };
  }

  function extractGemini() {
    const urlMatch = window.location.pathname.match(/\/app\/([a-zA-Z0-9-_]+)/);
    const nativeId = urlMatch ? urlMatch[1] : `gemini_${Date.now()}`;
    const title = (document.title || "").replace(/ - Gemini$/, "").trim() || "Gemini Conversation";

    const turns = [];
    const userSelector = 'user-query, .user-query-container, [data-test-id="user-query"]';
    const responseSelector = 'model-response, .response-container, [data-test-id="model-response"]';
    messageNodes(userSelector + ', ' + responseSelector).forEach(node => {
      const text = node.innerText.trim();
      if (text) turns.push({author: node.matches(userSelector) ? 'user' : 'model', text, timestamp: new Date().toISOString()});
    });

    if (turns.length === 0) {
      const textNodes = document.querySelectorAll('main .message-content, main .text-content');
      textNodes.forEach((node, i) => {
        turns.push({
          author: i % 2 === 0 ? 'user' : 'model',
          text: node.innerText.trim(),
          timestamp: new Date().toISOString()
        });
      });
    }

    return {
      source: "gemini_web",
      id: nativeId,
      title: title,
      turns: turns,
      created_at: new Date().toISOString()
    };
  }

  function extractPerplexity() {
    const urlMatch = window.location.pathname.match(/\/(search|thread)\/([a-zA-Z0-9-_]+)/);
    const nativeId = urlMatch ? urlMatch[2] : `pplx_${Date.now()}`;
    const title = (document.title || "").replace(/ - Perplexity$/, "").trim() || "Perplexity Thread";

    const turns = [];
    const queryEl = document.querySelector('h1') || document.querySelector('textarea');
    const mainTextEl = document.querySelector('.prose') || document.querySelector('main');

    if (queryEl) {
      turns.push({ author: 'user', text: queryEl.innerText || queryEl.value || title });
    }
    if (mainTextEl) {
      turns.push({ author: 'assistant', text: mainTextEl.innerText.trim() });
    }

    return {
      source: "perplexity",
      id: nativeId,
      title: title,
      turns: turns,
      created_at: new Date().toISOString()
    };
  }

  function extractActiveConversation() {
    const platform = getPlatform();
    switch (platform) {
      case "chatgpt": return extractChatGPT();
      case "claude_web": return extractClaude();
      case "gemini_web": return extractGemini();
      case "perplexity": return extractPerplexity();
      default: return null;
    }
  }

  async function syncToAigator({automatic = false, data = extractActiveConversation()} = {}) {
    if (syncing) return {success: false, error: 'Sync already in progress'};
    if (!data || !data.turns || data.turns.length === 0) {
      captureStatus("Could not extract conversation turns from this page.", automatic, true);
      return { success: false, error: "No turns found" };
    }

    try {
      syncing = true;
      data.capture_kind = 'dom';
      const result = await chrome.runtime.sendMessage({ action: 'ingest', payload: data, automatic });
      if (!result.error) {
        if ((result.outcomes || []).includes('skipped_stale')) {
          const error = 'Capture skipped: stored conversation is newer, more complete, or does not match. Import an export to reconcile it.';
          captureStatus(error, automatic, true);
          return {success: false, error, result};
        }
        const unchanged = result.outcomes?.every(outcome => outcome === 'unchanged');
        const message = unchanged ? 'Already up to date.' : `Synced to Aigator! (${result.stored_message_count} messages stored)`;
        submitted = fingerprint(data);
        captureStatus(message, automatic);
        return { success: true, message, result };
      } else {
        captureStatus(`Aigator: ${result.error || "Failed"}`, automatic, true);
        return { success: false, error: result.error };
      }
    } catch (err) {
      captureStatus(`Aigator daemon not reachable at ${daemonUrl}. Run 'aigator daemon'.`, automatic, true);
      return { success: false, error: err.message };
    } finally {
      syncing = false;
    }
  }

  function showToast(message, isError = false) {
    let toast = document.getElementById("__aigator_toast__");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "__aigator_toast__";
      Object.assign(toast.style, {
        position: "fixed",
        bottom: "20px",
        right: "20px",
        padding: "10px 16px",
        borderRadius: "8px",
        backgroundColor: isError ? "#ef4444" : "#0f172a",
        color: "#f8fafc",
        border: "1px solid #38bdf8",
        fontSize: "13px",
        fontWeight: "500",
        boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
        zIndex: "999999",
        transition: "opacity 0.3s ease",
        opacity: "0",
        pointerEvents: "none"
      });
      document.body.appendChild(toast);
    }
    toast.style.backgroundColor = isError ? "#ef4444" : "#0f172a";
    toast.innerText = message;
    toast.style.opacity = "1";
    setTimeout(() => {
      toast.style.opacity = "0";
    }, 4000);
  }

  function injectFloatingButton() {
    if (document.getElementById("__aigator_sync_btn__")) return;
    const btn = document.createElement("button");
    btn.id = "__aigator_sync_btn__";
    btn.innerHTML = "🐊 Sync";
    btn.title = "Sync active conversation to Aigator local search";
    Object.assign(btn.style, {
      position: "fixed",
      bottom: "20px",
      right: "20px",
      padding: "8px 14px",
      borderRadius: "20px",
      backgroundColor: "#0f172a",
      color: "#38bdf8",
      border: "1px solid #38bdf8",
      fontSize: "12px",
      fontWeight: "700",
      cursor: "pointer",
      boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
      zIndex: "999998",
      transition: "transform 0.15s, background-color 0.15s"
    });
    btn.onmouseenter = () => { btn.style.transform = "scale(1.05)"; btn.style.backgroundColor = "#1e293b"; };
    btn.onmouseleave = () => { btn.style.transform = "scale(1)"; btn.style.backgroundColor = "#0f172a"; };
    btn.onclick = () => syncToAigator();
    document.body.appendChild(btn);
  }

  // Listen for messages from popup or background
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
      if (req.action === "sync_chat") {
        syncToAigator().then(sendResponse);
        return true;
      } else if (req.action === "extract_chat") {
        sendResponse(extractActiveConversation());
      }
    });
  }

  setTimeout(injectFloatingButton, 1500);
  if (chrome.storage?.local) {
    chrome.storage.local.get(['autoSync']).then(config => { autoSync = config.autoSync !== false; });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes.autoSync) autoSync = changes.autoSync.newValue !== false;
      if (changes.autoSync || changes.authToken || changes.daemonUrl) {
        candidate = null;
        submitted = null;
        retryAfter = 0;
      }
    });
    setInterval(autoSyncTick, 5000);
  }
})();
