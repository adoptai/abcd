/**
 * AdoptAI Extension Test Runner
 * Sends queries sequentially to the copilot and captures responses.
 * Polls for response completion rather than using a fixed wait time.
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
    const timeout = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 30000);
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

async function readContent(ws) {
  const result = await cdpSend(ws, 'Runtime.evaluate', {
    expression: `(function() {
      // Scroll any conversation container to bottom to ensure all content is rendered
      const scrollable = document.querySelector('[class*="conversationLayout"], [class*="scroll"]');
      if (scrollable) scrollable.scrollTop = scrollable.scrollHeight;
      return document.body.innerText;
    })()`,
    returnByValue: true,
  });
  return result.result.value;
}

async function typeText(ws, text) {
  // Focus the chat textarea
  await cdpSend(ws, 'Runtime.evaluate', {
    expression: `(function() {
      const ta = document.querySelector('textarea[placeholder*="want"], textarea[placeholder*="type"], textarea[placeholder*="message"]');
      if (ta) { ta.focus(); ta.click(); return 'FOCUSED'; }
      const fallback = document.querySelector('textarea');
      if (fallback) { fallback.focus(); fallback.click(); return 'FOCUSED_FALLBACK'; }
      return 'NO_TEXTAREA';
    })()`,
    returnByValue: true,
  });

  for (const char of text) {
    await cdpSend(ws, 'Input.dispatchKeyEvent', { type: 'keyDown', text: char });
    await cdpSend(ws, 'Input.dispatchKeyEvent', { type: 'keyUp', text: char });
    await new Promise(r => setTimeout(r, 20));
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

function isStillLoading(text) {
  const loadingIndicators = [
    'Thinking...',
    'Determining the next step',
    'Executing workflow',
    'Calling required APIs',
    'Next step is',
    'Retrieved results from',
    'Processing',
    'Sure, fetching',
  ];
  const lines = text.split('\n');
  // Check last 8 lines for loading indicators
  const tail = lines.slice(-8).join('\n');
  return loadingIndicators.some(ind => tail.includes(ind));
}

async function waitForResponse(ws, beforeText, maxWaitMs = 90000) {
  const startTime = Date.now();
  let lastText = '';
  let stableCount = 0;

  while (Date.now() - startTime < maxWaitMs) {
    await new Promise(r => setTimeout(r, 3000));
    const currentText = await readContent(ws);

    // Response has arrived if content changed from before
    if (currentText !== beforeText && currentText.length > beforeText.length + 20) {
      // If still showing loading indicators, keep waiting
      if (isStillLoading(currentText)) {
        console.log(`  ... still loading (${Math.round((Date.now() - startTime)/1000)}s)`);
        lastText = currentText;
        stableCount = 0;
        continue;
      }

      // Check if response is stable (text stopped changing)
      if (currentText === lastText) {
        stableCount++;
        if (stableCount >= 2) {
          // Text stable for 6+ seconds and no loading indicators — done
          return currentText;
        }
      } else {
        stableCount = 0;
      }
      lastText = currentText;
    }
  }
  return lastText || await readContent(ws);
}

async function sendQuery(ws, query, queryNum) {
  const before = await readContent(ws);
  console.log(`\n[${ queryNum }] Sending: "${query}"`);

  await typeText(ws, query);
  await new Promise(r => setTimeout(r, 300));
  await pressEnter(ws);

  console.log(`[${queryNum}] Waiting for response...`);
  const after = await waitForResponse(ws, before, 120000);

  // Extract the response portion — everything after the query text in the page
  const queryIdx = after.lastIndexOf(query);
  if (queryIdx !== -1) {
    const responseStart = queryIdx + query.length;
    let response = after.slice(responseStart).trim();
    // Remove trailing "POWERED BY ADOPT" marker
    response = response.replace(/\s*POWERED BY ADOPT\s*$/, '').trim();
    return response;
  }
  return after;
}

// ---- DEFAULT QUERIES (used when no --test-file is provided) ----

const DEFAULT_QUERIES = [
  // General Queries
  { id: 1, category: 'General', query: 'Show me all client entities and their tax years' },
  { id: 2, category: 'General', query: 'Show me all Complete Automation years' },
  { id: 3, category: 'General', query: 'What is the total QRE and federal credit for Complete Automation?' },
  { id: 4, category: 'General', query: 'How many employees does Complete Automation have and what are the business components?' },
  { id: 5, category: 'General', query: 'Who are the top 5 highest paid R&D employees at Complete Automation by Section 41 wages?' },
  // Reference Library
  { id: 6, category: 'Reference', query: 'What are the four parts of the test for qualifying research activities under IRC Section 41?' },
  { id: 7, category: 'Reference', query: 'What does Treasury Regulation 1.174-2 define as research and experimental expenditures?' },
  { id: 8, category: 'Reference', query: 'What types of activities are excluded from qualifying research under IRC Section 41? What are the exclusions?' },
  { id: 9, category: 'Reference', query: 'What does the IRS audit techniques guide say about examining qualified wage expenditures and employee time allocations for R&D?' },
  { id: 10, category: 'Reference', query: 'What records and documentation does the IRS require to substantiate an R&D tax credit claim?' },
  // Steve's Technical
  { id: 11, category: 'Technical', query: 'What risk management flags exist for Complete Automation? Show me flagged employees and data quality issues.' },
  { id: 12, category: 'Technical', query: 'What software tools, CAD systems, or technology platforms are used in R&D activities?' },
  { id: 13, category: 'Technical', query: 'What subcontractors or vendors are mentioned in the Complete Automation study? Are there any contract research expenses?' },
];

// ---- CLI ARGUMENT PARSING ----

const cliArgs = process.argv.slice(2);
const testFileArg = cliArgs.find(a => a.startsWith('--test-file='));
const resultsFileArg = cliArgs.find(a => a.startsWith('--results-file='));
const targetIdArg = cliArgs.find(a => !a.startsWith('--'));

const queries = testFileArg
  ? JSON.parse(fs.readFileSync(testFileArg.split('=')[1], 'utf8'))
  : DEFAULT_QUERIES;

const RESULTS_FILE = resultsFileArg
  ? resultsFileArg.split('=')[1]
  : (process.env.RESULTS_FILE || './test-results.json');

const targetId = targetIdArg ? parseInt(targetIdArg) : null;

// ---- MAIN ----

(async () => {
  const targets = await httpGet('/json');
  let ext = targets.find(t => t.url?.includes('chrome-extension://') && t.url.includes('index.html') && t.type === 'iframe');
  if (!ext) ext = targets.find(t => t.url?.includes('chrome-extension://') && t.url.includes('index.html') && t.type === 'page');
  if (!ext) { console.error('Extension not found! Is the popup open?'); process.exit(1); }

  const ws = new WebSocket(ext.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.on('open', resolve); ws.on('error', reject); });
  await cdpSend(ws, 'Runtime.enable');

  const results = [];
  const toRun = targetId ? queries.filter(q => q.id === targetId) : queries;

  for (const q of toRun) {
    const startTime = Date.now();
    try {
      const response = await sendQuery(ws, q.query, q.id);
      const elapsed = (Date.now() - startTime) / 1000;
      results.push({ ...q, response, response_time_s: elapsed, error: null });
      console.log(`[${q.id}] Response captured (${response.length} chars, ${elapsed.toFixed(1)}s)`);
    } catch (e) {
      const elapsed = (Date.now() - startTime) / 1000;
      console.error(`[${q.id}] ERROR: ${e.message}`);
      results.push({ ...q, response: null, response_time_s: elapsed, error: e.message });
    }
  }

  // Save results
  fs.writeFileSync(RESULTS_FILE, JSON.stringify(results, null, 2));
  console.log(`\nDone! ${results.length} results saved to ${RESULTS_FILE}`);

  ws.close();
})().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
