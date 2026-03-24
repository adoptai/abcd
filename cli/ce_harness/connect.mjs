/**
 * CDP Page Helper — interact with regular web pages in the test browser.
 *
 * Usage:
 *   node connect.mjs <action> <tab-index> [args...]
 *
 * Actions:
 *   list                          — List all open tabs
 *   screenshot <tab> <path>       — Save screenshot of a tab
 *   click <tab> <selector>        — Click an element
 *   fill <tab> <selector> <value> — Set an input's value
 *   type <tab> <selector> <value> — Type character by character
 *   press <tab> <key>             — Press a keyboard key
 *   text <tab> [selector]         — Get text content
 *   eval <tab> <expression>       — Evaluate JS expression
 *   navigate <tab> <url>          — Navigate tab to URL
 */
import { chromium } from 'playwright';

const action = process.argv[2] || 'list';
const args = process.argv.slice(3);

async function run() {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  const context = contexts[0];
  const pages = context.pages();

  console.log(`Connected. ${pages.length} tab(s):`);
  pages.forEach((p, i) => console.log(`  [${i}] ${p.url()}`));

  const page = pages[parseInt(args[0]) || 0];

  switch (action) {
    case 'screenshot': {
      const path = args[1] || './shot.png';
      await page.screenshot({ path, fullPage: false });
      console.log(`Screenshot saved: ${path}`);
      break;
    }
    case 'click': {
      await page.click(args[1], { timeout: 10000 });
      console.log(`Clicked: ${args[1]}`);
      break;
    }
    case 'fill': {
      await page.fill(args[1], args[2], { timeout: 10000 });
      console.log(`Filled: ${args[1]} = ${args[2]}`);
      break;
    }
    case 'type': {
      await page.locator(args[1]).pressSequentially(args[2], { delay: 50 });
      console.log(`Typed into: ${args[1]}`);
      break;
    }
    case 'press': {
      await page.keyboard.press(args[1]);
      console.log(`Pressed: ${args[1]}`);
      break;
    }
    case 'text': {
      const text = await page.locator(args[1] || 'body').innerText({ timeout: 10000 });
      console.log(text.slice(0, 5000));
      break;
    }
    case 'eval': {
      const result = await page.evaluate(args[1]);
      console.log(JSON.stringify(result, null, 2));
      break;
    }
    case 'navigate': {
      await page.goto(args[1], { waitUntil: 'domcontentloaded', timeout: 30000 });
      console.log(`Navigated to: ${args[1]}`);
      break;
    }
    case 'list': {
      break; // tab list already printed above
    }
    default:
      console.log('Unknown action:', action);
  }

  await browser.close();
}

run().catch(e => { console.error(e.message); process.exit(1); });
