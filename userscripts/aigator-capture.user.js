// ==UserScript==
// @name         Aigator AI Session Capture
// @namespace    https://github.com/tiborsekera/aigator
// @version      0.1.0
// @description  Sync conversations from ChatGPT, Claude, Gemini, and Perplexity into local Aigator search engine.
// @author       Aigator
// @match        https://chatgpt.com/*
// @match        https://chat.openai.com/*
// @match        https://claude.ai/*
// @match        https://gemini.google.com/*
// @match        https://www.perplexity.ai/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @grant        GM_setValue
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  'use strict';
  function messageNodes(selector) {
    const nodes = Array.from(document.querySelectorAll(selector));
    return nodes.filter(node => !nodes.some(parent => parent !== node && parent.contains(node)));
  }

  GM_registerMenuCommand('Set Aigator auth token', () => {
    const token = prompt('Paste your local Aigator auth token:', getAuthToken());
    if (token !== null) GM_setValue('aigator_token', token.trim());
  });

  function getDaemonUrl() {
    return (typeof GM_getValue !== "undefined" ? GM_getValue("aigator_daemon_url", null) : null) || "http://127.0.0.1:8765/api/ingest";
  }

  function getAuthToken() {
    return (typeof GM_getValue !== "undefined" ? GM_getValue("aigator_token", null) : null) || "";
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
    messageElements.forEach((el) => {
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

    return { source: "chatgpt", id: nativeId, title, turns, created_at: new Date().toISOString() };
  }

  function extractClaude() {
    const urlMatch = window.location.pathname.match(/\/chat\/([a-zA-Z0-9-]+)/);
    const nativeId = urlMatch ? urlMatch[1] : `claude_${Date.now()}`;
    const title = (document.title || "").replace(/ - Claude$/, "").trim() || "Claude Conversation";

    const turns = [];
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

    return { source: "claude_web", id: nativeId, title, turns, created_at: new Date().toISOString() };
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

    return { source: "gemini_web", id: nativeId, title, turns, created_at: new Date().toISOString() };
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

    return { source: "perplexity", id: nativeId, title, turns, created_at: new Date().toISOString() };
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

  function syncToAigator() {
    const data = extractActiveConversation();
    if (!data || !data.turns || data.turns.length === 0) {
      showToast("❌ Could not extract conversation turns from this page.", true);
      return;
    }

    const daemonUrl = getDaemonUrl();
    data.capture_kind = 'dom';
    const token = getAuthToken();
    if (!token) { showToast('Set your Aigator auth token in the userscript menu first.', true); return; }
    const headers = { "Content-Type": "application/json" };
    if (token) {
      headers["Authorization"] = "Bearer " + token;
    }

    GM_xmlhttpRequest({
      method: "POST",
      url: daemonUrl,
      headers: headers,
      data: JSON.stringify(data),
      onload: function (res) {
        if (res.status === 200) {
          try {
            const result = JSON.parse(res.responseText);
            if ((result.outcomes || []).includes('skipped_stale')) {
              showToast('Capture skipped: stored conversation is newer, more complete, or does not match. Import an export to reconcile it.', true);
            } else if (result.outcomes?.every(outcome => outcome === 'unchanged')) {
              showToast('Already up to date.');
            } else {
              showToast(`Synced to Aigator! (${result.stored_message_count} messages stored)`);
            }
          } catch (error) {
            showToast('Invalid response from Aigator.', true);
          }
        } else {
          showToast(`❌ Aigator error (${res.status})`, true);
        }
      },
      onerror: function () {
        showToast(`⚠️ Could not reach local Aigator server on ${daemonUrl}`, true);
      }
    });
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
    setTimeout(() => { toast.style.opacity = "0"; }, 4000);
  }

  function injectFloatingButton() {
    if (document.getElementById("__aigator_sync_btn__")) return;
    const btn = document.createElement("button");
    btn.id = "__aigator_sync_btn__";
    btn.innerHTML = "🐊 Sync";
    btn.title = "Sync active conversation to local Aigator";
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
    btn.onclick = () => syncToAigator();
    document.body.appendChild(btn);
  }

  setTimeout(injectFloatingButton, 1500);
})();
