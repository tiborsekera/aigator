/** Local ingestion runs in the privileged background worker. Pair in the popup. */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!['ingest', 'pair'].includes(message.action) || sender.id !== chrome.runtime.id) return;
  (async () => {
    const config = await chrome.storage.local.get(['daemonUrl', 'authToken', 'autoSync']);
    const base = (config.daemonUrl || 'http://127.0.0.1:8765').replace(/\/api\/ingest\/?$/, '').replace(/\/$/, '');
    const url = new URL(base);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error('Daemon URL must be loopback HTTP.');
    if (message.action === 'pair') {
      if (sender.url !== chrome.runtime.getURL('popup.html')) throw new Error('Pair from the extension popup.');
      const response = await fetch(base + '/api/auth/token', {signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Local daemon is unavailable.');
      const data = await response.json();
      if (!data.token) throw new Error('Local daemon returned no pairing token.');
      await chrome.storage.local.set({authToken: data.token});
      return {status: 'paired'};
    }
    if (message.automatic && config.autoSync === false) throw new Error('Automatic sync is paused.');
    if (!config.authToken) throw new Error('Open the extension popup and click Connect local daemon.');
    const response = await fetch(base + '/api/ingest', {
      signal: AbortSignal.timeout(15000),
      method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + config.authToken},
      body: JSON.stringify(message.payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Ingestion failed');
    return result;
  })().then(sendResponse).catch(error => sendResponse({error: error.message}));
  return true;
});
