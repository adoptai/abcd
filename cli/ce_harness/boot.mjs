/**
 * AdoptAI Extension Boot Sequencer
 *
 * Runs after Chrome starts. Handles the startup sequence:
 *   1. Waits for Chrome CDP to be ready
 *   2. Waits for app.adopt.ai to fully load (auth session)
 *   3. Activates the target tab
 *   4. Triggers the Adopt extension via the service worker CDP target
 *
 * Usage: node boot.mjs <target-url>
 */
import WebSocket from 'ws';
import http from 'http';

const TARGET_URL = process.argv[2];
const APP_HOST = 'app.adopt.ai';
const CDP_PORT = 9222;

const MAX_WAIT_CHROME_MS = 15000;
const MAX_WAIT_APP_MS = 60000;
const MAX_WAIT_EXT_MS = 15000;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function httpGet(path) {
  return new Promise((resolve, reject) => {
    http.get(`http://localhost:${CDP_PORT}${path}`, res => {
      let data = '';
      res.on('data', c => (data += c));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve([]); }
      });
    }).on('error', reject);
  });
}

let msgId = 1;
function cdpSend(ws, method, params = {}) {
  const id = msgId++;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 15000);
    const handler = data => {
      const msg = JSON.parse(data.toString());
      if (msg.id === id) {
        clearTimeout(timeout);
        ws.removeListener('message', handler);
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      }
    };
    ws.on('message', handler);
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ---------------------------------------------------------------------------
// Step 1: Wait for Chrome CDP to be ready
// ---------------------------------------------------------------------------

async function waitForChrome() {
  const start = Date.now();
  while (Date.now() - start < MAX_WAIT_CHROME_MS) {
    try {
      await httpGet('/json');
      return true;
    } catch {
      await sleep(500);
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Step 2: Wait for app.adopt.ai tab to fully load
// ---------------------------------------------------------------------------

async function waitForAppReady() {
  const start = Date.now();
  while (Date.now() - start < MAX_WAIT_APP_MS) {
    let targets;
    try { targets = await httpGet('/json'); }
    catch { await sleep(1000); continue; }

    const appTab = targets.find(t => t.url?.includes(APP_HOST) && t.type === 'page');
    if (appTab) {
      try {
        const ws = new WebSocket(appTab.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => { ws.on('open', resolve); ws.on('error', reject); });
        await cdpSend(ws, 'Runtime.enable');
        const result = await cdpSend(ws, 'Runtime.evaluate', {
          expression: `document.readyState`,
          returnByValue: true,
        });
        ws.close();
        if (result?.result?.value === 'complete') {
          return appTab;
        }
      } catch { /* tab may not be connectable yet */ }
    }
    await sleep(1000);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Step 3: Activate the target tab
// ---------------------------------------------------------------------------

async function activateTab(targetUrl) {
  const targets = await httpGet('/json');
  const tab = targets.find(t => t.url?.includes(new URL(targetUrl).hostname) && t.type === 'page');
  if (!tab) return null;

  // Use Target.activateTarget via any existing page connection
  const anyTab = targets.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
  if (anyTab) {
    try {
      const ws = new WebSocket(anyTab.webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.on('open', resolve); ws.on('error', reject); });
      await cdpSend(ws, 'Target.activateTarget', { targetId: tab.id });
      ws.close();
    } catch { /* ignore */ }
  }
  return tab;
}

// ---------------------------------------------------------------------------
// Step 4: Open the extension on the target tab via service worker CDP
// ---------------------------------------------------------------------------

async function openExtension(targetUrl) {
  const start = Date.now();
  const hostname = new URL(targetUrl).hostname;

  // Connect to service worker once and keep retrying sendMessage until content script is ready
  let sw = null;
  while (Date.now() - start < MAX_WAIT_EXT_MS) {
    let targets;
    try { targets = await httpGet('/json'); }
    catch { await sleep(1000); continue; }

    sw = targets.find(t =>
      t.url?.startsWith('chrome-extension://') &&
      (t.type === 'service_worker' || t.type === 'background_page')
    );
    if (sw) break;
    await sleep(1000);
  }

  if (!sw) {
    console.log('[boot] Extension service worker not found.');
    return false;
  }

  let ws;
  try {
    ws = new WebSocket(sw.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.on('open', resolve); ws.on('error', reject); });
    await cdpSend(ws, 'Runtime.enable');
  } catch (e) {
    console.log(`[boot] Could not connect to service worker: ${e.message}`);
    return false;
  }

  // Inject the extension iframe directly — chrome.scripting.executeScript with
  // a self-contained function that creates the iframe using the URL passed as arg.
  // This bypasses injectBottomPopup (minified in dist) entirely.
  const retryEnd = Date.now() + MAX_WAIT_EXT_MS;
  while (Date.now() < retryEnd) {
    try {
      const result = await cdpSend(ws, 'Runtime.evaluate', {
        expression: `
          new Promise((resolve) => {
            chrome.tabs.query({}, (tabs) => {
              const tab = tabs.find(t => t.url && t.url.includes('${hostname}'));
              if (!tab) { resolve('tab_not_found'); return; }
              const iframeSrc = chrome.runtime.getURL('index.html');
              chrome.scripting.executeScript({
                target: { tabId: tab.id },
                func: (src) => {
                  if (document.getElementById('adopt-copilot-popup')) return 'already_exists';
                  const iframe = document.createElement('iframe');
                  iframe.id = 'adopt-copilot-popup';
                  iframe.src = src + '?t=' + Date.now();
                  iframe.style.cssText = [
                    'position:fixed', 'right:0px', 'bottom:0px',
                    'width:450px', 'height:calc(100vh - 80px)',
                    'z-index:2147483647', 'border:none',
                    'box-shadow:-2px 0 8px rgba(0,0,0,0.15)'
                  ].join(';');
                  document.body.appendChild(iframe);
                  return 'injected';
                },
                args: [iframeSrc]
              }, (results) => {
                const err = chrome.runtime.lastError;
                resolve(err ? ('error: ' + err.message) : (results?.[0]?.result || 'done'));
              });
            });
          })
        `,
        awaitPromise: true,
        returnByValue: true,
      });

      const status = result?.result?.value ?? '';
      if (status === 'tab_not_found') {
        console.log('[boot] Target tab not found yet, retrying...');
        await sleep(2000);
        continue;
      }
      if (status.startsWith('error')) {
        console.log(`[boot] Inject failed: ${status}`);
        await sleep(2000);
        continue;
      }
      ws.close();
      return true;
    } catch (e) {
      await sleep(1000);
    }
  }

  ws.close();
  return false;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

(async () => {
  if (!TARGET_URL) {
    console.error('[boot] Usage: node boot.mjs <target-url>');
    process.exit(1);
  }

  console.log('[boot] Waiting for Chrome...');
  const chromeReady = await waitForChrome();
  if (!chromeReady) {
    console.log('[boot] Chrome did not start in time.');
    process.exit(1);
  }

  console.log('[boot] Waiting for app.adopt.ai to load...');
  const appTab = await waitForAppReady();
  if (!appTab) {
    console.log('[boot] app.adopt.ai did not load in time. Please check your connection.');
  } else {
    console.log('[boot] app.adopt.ai ready.');
  }

  // Small delay to let the extension initialize its session state
  await sleep(1500);

  console.log(`[boot] Activating target tab: ${TARGET_URL}`);
  await activateTab(TARGET_URL);
  await sleep(2000); // wait for content script to inject after tab is focused

  console.log('[boot] Opening Adopt extension...');
  const opened = await openExtension(TARGET_URL);
  if (opened) {
    console.log('[boot] Extension opened. Ready to test!');
  } else {
    console.log('[boot] Could not auto-open extension. Please click the Adopt icon manually.');
  }
})().catch(e => {
  console.error('[boot] Fatal:', e.message);
  process.exit(1);
});
