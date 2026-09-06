document.addEventListener('DOMContentLoaded', async () => {
  const syncBtn = document.getElementById('syncBtn');
  const openUiBtn = document.getElementById('openUiBtn');
  const daemonState = document.getElementById('daemonState');
  const daemonStatus = document.getElementById('daemonStatus');
  const msgEl = document.getElementById('msg');
  const daemonUrlInput = document.getElementById('daemonUrlInput');
  const authTokenInput = document.getElementById('authTokenInput');
  const saveSettingsBtn = document.getElementById('saveSettingsBtn');
  const autoSyncInput = document.getElementById('autoSyncInput');

  // Load saved settings
  const stored = await chrome.storage.local.get(['daemonUrl', 'authToken', 'autoSync']);
  autoSyncInput.checked = stored.autoSync !== false;
  autoSyncInput.addEventListener('change', async () => {
    await chrome.storage.local.set({autoSync: autoSyncInput.checked});
    msgEl.textContent = autoSyncInput.checked ? 'Automatic sync enabled.' : 'Automatic sync paused.';
  });
  document.getElementById('pairBtn').addEventListener('click', async () => {
    try {
      const result = await chrome.runtime.sendMessage({action: 'pair'});
      if (result.error) throw new Error(result.error);
      msgEl.textContent = 'Connected. Visit a chat to start syncing.';
      daemonState.textContent = '🟢 Connected';
      authTokenInput.value = (await chrome.storage.local.get(['authToken'])).authToken || '';
    } catch (error) { msgEl.textContent = error.message; }
  });
  const daemonUrl = stored.daemonUrl || 'http://127.0.0.1:8765';
  let authToken = stored.authToken || '';

  const baseUrl = daemonUrl.replace(/\/api\/ingest\/?$/, '').replace(/\/$/, '');

  daemonUrlInput.value = daemonUrl;
  authTokenInput.value = authToken;

  // Check local daemon status
  try {
    const headers = {};
    if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
    const res = await fetch(`${baseUrl}/api/stats`, {
      headers,
      signal: AbortSignal.timeout(1500)
    });
    if (res.ok) {
      daemonState.textContent = '🟢 Online';
      daemonState.style.color = '#4ade80';
    } else if (res.status === 401) {
      daemonState.textContent = '🟡 Auth required';
      daemonState.style.color = '#fbbf24';
    } else {
      daemonState.textContent = '🟡 Error (' + res.status + ')';
      daemonState.style.color = '#fbbf24';
    }
  } catch (e) {
    daemonState.textContent = '🔴 Offline';
    daemonState.style.color = '#f87171';
  }

  saveSettingsBtn.addEventListener('click', async () => {
    const newUrl = daemonUrlInput.value.trim() || 'http://127.0.0.1:8765';
    const newToken = authTokenInput.value.trim();
    await chrome.storage.local.set({ daemonUrl: newUrl, authToken: newToken });
    msgEl.textContent = 'Settings saved!';
    setTimeout(() => { msgEl.textContent = ''; }, 2000);
  });

  syncBtn.addEventListener('click', async () => {
    msgEl.textContent = 'Syncing conversation...';
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab) {
        msgEl.textContent = 'No active tab found.';
        return;
      }

      chrome.tabs.sendMessage(tab.id, { action: 'sync_chat' }, (response) => {
        if (chrome.runtime.lastError) {
          msgEl.textContent = 'Error: Refresh the chat page first.';
        } else if (response && response.success) {
          msgEl.textContent = response.message || '✅ Synced successfully!';
        } else {
          msgEl.textContent = `❌ ${response ? response.error : 'Failed'}`;
        }
      });
    } catch (err) {
      msgEl.textContent = `Error: ${err.message}`;
    }
  });

  openUiBtn.addEventListener('click', () => {
    const uiUrl = daemonUrlInput.value.trim().replace(/\/api\/ingest$/, '') || 'http://127.0.0.1:8765';
    chrome.tabs.create({ url: uiUrl });
  });
});
