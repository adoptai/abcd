/**
 * Extension Iframe CDP Controller
 *
 * Connects directly to the Adopt extension's iframe target via raw CDP WebSocket.
 * This bypasses Chrome's cross-origin isolation that blocks Playwright from
 * accessing chrome-extension:// iframes.
 *
 * Usage:
 *   node ext-cdp.mjs <action> [value] [wait_ms]
 *
 * Actions:
 *   read              — Print all visible text in the extension popup
 *   html              — Print the inner HTML (first 3000 chars)
 *   inputs            — List all input/textarea elements
 *   screenshot <path> — (Not supported on iframe targets — use for reference only)
 *   send <msg> [ms]   — Type a message, submit it, wait for response, print result
 *   wait-and-read <ms>— Just wait and re-read content (useful after send)
 */
import WebSocket from 'ws';
import http from 'http';
import fs from 'fs';

function httpGet(path) {
  return new Promise((resolve, reject) => {
    http.get('http://localhost:9222' + path, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => resolve(JSON.parse(data)));
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

const action = process.argv[2] || 'read';
const value = process.argv[3] || '';
const waitMs = parseInt(process.argv[4]) || 8000;

async function findExtensionTarget() {
  const targets = await httpGet('/json');
  const ext = targets.find(t =>
    t.url?.includes('chrome-extension://') &&
    t.url.includes('index.html') &&
    t.type === 'iframe'
  );
  if (!ext) {
    // Also check for page type (if opened as standalone tab)
    const extPage = targets.find(t =>
      t.url?.includes('chrome-extension://') &&
      t.url.includes('index.html') &&
      t.type === 'page'
    );
    return extPage;
  }
  return ext;
}

async function connectToExtension() {
  const ext = await findExtensionTarget();
  if (!ext) {
    console.error('Extension popup not found. Is it open?');
    console.error('The user needs to click the Adopt extension icon on the target site.');
    process.exit(1);
  }

  const ws = new WebSocket(ext.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });

  await cdpSend(ws, 'Runtime.enable');
  return ws;
}

async function readContent(ws) {
  const result = await cdpSend(ws, 'Runtime.evaluate', {
    expression: 'document.body.innerText',
    returnByValue: true,
  });
  return result.result.value;
}

async function typeText(ws, text) {
  // Focus the chat textarea
  await cdpSend(ws, 'Runtime.evaluate', {
    expression: `(function() {
      const ta = document.querySelector('textarea[placeholder*="want"], textarea[placeholder*="type"], textarea[placeholder*="message"]');
      if (ta) { ta.focus(); ta.click(); return 'FOCUSED: ' + ta.placeholder; }
      // Fallback: first textarea
      const fallback = document.querySelector('textarea');
      if (fallback) { fallback.focus(); fallback.click(); return 'FOCUSED (fallback): ' + fallback.placeholder; }
      return 'NO_TEXTAREA_FOUND';
    })()`,
    returnByValue: true,
  });

  // Type character by character using CDP Input events
  for (const char of text) {
    await cdpSend(ws, 'Input.dispatchKeyEvent', { type: 'keyDown', text: char });
    await cdpSend(ws, 'Input.dispatchKeyEvent', { type: 'keyUp', text: char });
    await new Promise(r => setTimeout(r, 30));
  }
}

async function pressEnter(ws) {
  await cdpSend(ws, 'Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
  });
  await cdpSend(ws, 'Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
  });
}

async function run() {
  const ws = await connectToExtension();

  switch (action) {
    case 'read': {
      console.log(await readContent(ws));
      break;
    }

    case 'html': {
      const result = await cdpSend(ws, 'Runtime.evaluate', {
        expression: 'document.body.innerHTML',
        returnByValue: true,
      });
      console.log(result.result.value.slice(0, 3000));
      break;
    }

    case 'inputs': {
      const result = await cdpSend(ws, 'Runtime.evaluate', {
        expression: `JSON.stringify(Array.from(document.querySelectorAll('input, textarea')).map(el => ({
          tag: el.tagName, type: el.type, placeholder: el.placeholder,
          value: el.value, id: el.id, class: el.className?.slice?.(0, 80)
        })))`,
        returnByValue: true,
      });
      const inputs = JSON.parse(result.result.value);
      console.log(`Found ${inputs.length} input(s):`);
      inputs.forEach((inp, i) => console.log(`  [${i}] <${inp.tag}> placeholder="${inp.placeholder}" id="${inp.id}"`));
      break;
    }

    case 'send': {
      if (!value) {
        console.error('Usage: node ext-cdp.mjs send "your message" [wait_ms]');
        process.exit(1);
      }

      const before = await readContent(ws);
      await typeText(ws, value);
      console.log(`Typed: "${value}"`);

      await new Promise(r => setTimeout(r, 300));
      await pressEnter(ws);
      console.log('Submitted. Waiting for response...');

      // Wait for response
      await new Promise(r => setTimeout(r, waitMs));
      const after = await readContent(ws);
      console.log('\n=== Extension Content ===');
      console.log(after);
      break;
    }

    case 'wait-and-read': {
      const ms = parseInt(value) || waitMs;
      console.log(`Waiting ${ms}ms...`);
      await new Promise(r => setTimeout(r, ms));
      console.log(await readContent(ws));
      break;
    }

    default:
      console.log('Unknown action:', action);
      console.log('Available: read, html, inputs, send, wait-and-read');
  }

  ws.close();
}

run().catch(e => { console.error('Error:', e.message); process.exit(1); });
