"""
Chrome Extension browser control — pure Python replacement for the ce_harness/ Node.js scripts.

Provides:
  - find_chrome_executable()   cross-platform Chrome detection
  - launch_chrome()            launch Chrome with extension via subprocess (replaces start.sh)
  - wait_for_chrome()          poll CDP endpoint until Chrome is ready
  - boot_extension()           inject the extension iframe into the target tab (replaces boot.mjs)
  - run_python_runner()        run test queries via the extension iframe (replaces run-tests.mjs)
"""

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CDP_PORT = 9222


class ExecutionLog:
    """Collects timestamped events during a CE test query."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def log(self, event: str, **details: Any) -> None:
        self.events.append(
            {
                "elapsed_s": round(time.monotonic() - self._start, 1),
                "event": event,
                **details,
            }
        )

    def reset(self) -> None:
        self._start = time.monotonic()
        self.events = []


LOADING_INDICATORS = [
    "Thinking...",
    "Determining the next step",
    "Executing workflow",
    "Calling required APIs",
    "Next step is",
    "Retrieved results from",
    "Processing",
    "Sure, fetching",
]


# ---------------------------------------------------------------------------
# Chrome detection
# ---------------------------------------------------------------------------


def find_chrome_executable() -> str | None:
    """Return path to the system Chrome/Chromium executable, or None if not found."""
    system = platform.system()
    if system == "Linux":
        return (
            shutil.which("google-chrome")
            or shutil.which("chromium-browser")
            or shutil.which("chromium")
        )
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        for c in candidates:
            if Path(c).exists():
                return c
        return shutil.which("google-chrome") or shutil.which("chromium")
    if system == "Windows":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            rf"{local}\Google\Chrome\Application\chrome.exe",
        ]
        for c in candidates:
            if Path(c).exists():
                return c
    return None


# ---------------------------------------------------------------------------
# Chrome launch + boot sequence
# ---------------------------------------------------------------------------


def launch_chrome(
    target_url: str,
    extension_path: str,
    profile_dir: Path,
    cdp_port: int = CDP_PORT,
) -> "subprocess.Popen[bytes]":
    """
    Launch Chrome with the Adopt extension via subprocess.
    Uses the same flags as the original start.sh so extensions load correctly.
    Returns the Popen process (caller is responsible for terminating it).
    """
    chrome = find_chrome_executable()
    if not chrome:
        raise RuntimeError(
            "Google Chrome not found. Install Chrome or set the path to the executable."
        )

    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"Extension: {extension_path}")
    print(f"Profile:   {profile_dir}  (isolated from your daily Chrome)")
    print(f"Debug:     http://localhost:{cdp_port}")
    print(f"Target:    {target_url}")

    cmd = [
        chrome,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-extensions",
        f"--load-extension={extension_path}",
        f"--remote-debugging-port={cdp_port}",
        "--new-window",
        "https://app.adopt.ai",
        target_url,
    ]
    return subprocess.Popen(cmd)


def wait_for_chrome(cdp_port: int = CDP_PORT, timeout_s: int = 15) -> bool:
    """Poll the CDP HTTP endpoint until Chrome is ready. Returns True when ready."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def _get_extension_url(cdp_port: int, timeout_s: int = 30) -> str | None:
    """
    Query the CDP /json endpoint to find the Adopt extension ID, then return its
    index.html URL. Uses any chrome-extension:// target (e.g. service worker) to
    extract the ID — the popup doesn't need to be open yet.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://localhost:{cdp_port}/json/list", timeout=3
            ) as resp:
                targets = json.loads(resp.read())
            ext = next(
                (t for t in targets if t.get("url", "").startswith("chrome-extension://")),
                None,
            )
            if ext:
                # chrome-extension://{id}/... → extract id
                parts = ext["url"].split("/")
                if len(parts) >= 3:
                    ext_id = parts[2]
                    return f"chrome-extension://{ext_id}/index.html"
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    return None


def boot_extension(cdp_port: int = CDP_PORT, target_url: str = "", timeout_s: int = 30) -> bool:
    """
    Inject the Adopt extension iframe into the target tab.
    Finds the extension URL via CDP /json, then injects the iframe directly
    into the target page via page.evaluate() — no service worker needed.
    Returns True if injected, False otherwise.
    """
    from urllib.parse import urlparse

    from playwright.sync_api import sync_playwright

    hostname = urlparse(target_url).hostname or target_url

    # Wait for the extension to appear in CDP targets
    print("[boot] Waiting for extension to load...")
    ext_url = _get_extension_url(cdp_port, timeout_s=timeout_s)
    if not ext_url:
        print("[boot] Extension not found in Chrome — is it loaded? Click the Adopt icon manually.")
        return False

    print(f"[boot] Extension found: {ext_url}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
        except Exception as e:
            print(f"[boot] Could not connect to Chrome: {e}")
            return False

        context = browser.contexts[0]

        # Find and activate the target page
        target_page = next(
            (pg for pg in context.pages if hostname and hostname in pg.url),
            None,
        )
        if target_page is None:
            print(f"[boot] Target page ({hostname}) not found — inject manually.")
            browser.close()
            return False

        target_page.bring_to_front()
        time.sleep(1.5)  # let content script settle after tab focus

        try:
            status = target_page.evaluate(
                """(src) => {
                    if (document.getElementById('adopt-copilot-popup')) return 'exists';
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
                }""",
                ext_url,
            )
            browser.close()
            if status in ("injected", "exists"):
                print("[boot] Extension iframe injected. Ready to test!")
                return True
            print(f"[boot] Unexpected status: {status}")
            return False
        except Exception as e:
            browser.close()
            print(f"[boot] Could not inject extension iframe: {e}")
            return False


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


EXTENSION_IFRAME_SELECTOR = "#adopt-copilot-popup"


def _is_still_loading(text: str) -> bool:
    tail = "\n".join(text.split("\n")[-8:])
    return any(indicator in tail for indicator in LOADING_INDICATORS)


def _read_frame_text(ext_frame: Any) -> str:
    """Read current visible text from the extension FrameLocator."""
    try:
        return ext_frame.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def _wait_for_response(
    ext_frame: Any,
    before_text: str,
    max_wait_s: int = 120,
    exec_log: ExecutionLog | None = None,
) -> str:
    """Poll extension frame content until text is stable and no loading indicators shown."""
    start = time.monotonic()
    last_text = ""
    stable_count = 0
    loading_seen: set[str] = set()

    if exec_log:
        exec_log.log("waiting_for_response")

    while time.monotonic() - start < max_wait_s:
        time.sleep(3)
        current_text = _read_frame_text(ext_frame)
        if not current_text:
            continue

        if current_text != before_text and len(current_text) > len(before_text) + 20:
            if _is_still_loading(current_text):
                elapsed = int(time.monotonic() - start)
                print(f"  ... still loading ({elapsed}s)")
                # Log each unique loading indicator once
                if exec_log:
                    tail = "\n".join(current_text.split("\n")[-8:])
                    for indicator in LOADING_INDICATORS:
                        if indicator in tail and indicator not in loading_seen:
                            loading_seen.add(indicator)
                            exec_log.log("loading_indicator", indicator=indicator)
                last_text = current_text
                stable_count = 0
                continue

            if current_text == last_text:
                stable_count += 1
                if stable_count >= 2:
                    if exec_log:
                        exec_log.log("text_stabilized", chars=len(current_text))
                    return current_text
            else:
                if exec_log and current_text != last_text and last_text:
                    exec_log.log("text_changed", chars=len(current_text))
                stable_count = 0
            last_text = current_text

    if exec_log:
        exec_log.log("timeout", max_wait_s=max_wait_s)
    return last_text or _read_frame_text(ext_frame)


def run_python_runner(
    queries: list[dict[str, Any]],
    results_path: Path,
    cdp_port: int = CDP_PORT,
) -> list[dict[str, Any]]:
    """
    Connect to the running Chrome instance and execute each test query via the
    extension iframe. Returns raw result dicts (no evaluation yet).
    Replaces run-tests.mjs.
    """
    if not importlib.util.find_spec("playwright"):
        print("Error: playwright is not installed. Run: poetry install")
        return []

    from playwright.sync_api import sync_playwright

    results: list[dict[str, Any]] = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
        except Exception as e:
            print(f"Error: Could not connect to Chrome on port {cdp_port}: {e}")
            return []

        context = browser.contexts[0]

        # Prevent Playwright from overriding the browser's native color scheme
        for page in context.pages:
            page.emulate_media(color_scheme="no-preference")

        # Find target page (not app.adopt.ai)
        target_page = next(
            (
                pg
                for pg in context.pages
                if "app.adopt.ai" not in pg.url and pg.url.startswith("http")
            ),
            None,
        )
        if target_page is None:
            print("Error: Could not find target page in Chrome. Is Chrome running?")
            browser.close()
            return []

        # Access the extension popup via frame_locator (cross-origin iframe by CSS id)
        ext_frame = target_page.frame_locator(EXTENSION_IFRAME_SELECTOR)

        # Verify the popup is reachable before running tests
        try:
            ext_frame.locator("body").inner_text(timeout=5000)
        except Exception:
            print(
                f"Error: Extension popup not found (selector: {EXTENSION_IFRAME_SELECTOR}). "
                "Is the extension visible on the target tab?"
            )
            browser.close()
            return []

        # Profile is persisted in the Chrome user-data-dir from the configure step;
        # no runtime switching needed here.

        # Run each query
        for q in queries:
            qid = q.get("id", "?")
            query_text = q.get("query", "")
            start = time.monotonic()
            exec_log = ExecutionLog()

            try:
                before_text = _read_frame_text(ext_frame)
                print(f'\n[{qid}] Sending: "{query_text}"')

                textarea = ext_frame.locator(
                    'textarea[placeholder*="want"], textarea[placeholder*="type"], '
                    'textarea[placeholder*="message"], textarea'
                ).first
                exec_log.log("textarea_found")
                textarea.fill(query_text)
                time.sleep(0.3)
                exec_log.log("sent_message", query=query_text[:80])
                textarea.press("Enter")
                exec_log.log("enter_pressed")

                print(f"[{qid}] Waiting for response...")
                after_text = _wait_for_response(
                    ext_frame, before_text, max_wait_s=120, exec_log=exec_log
                )

                # Extract response: everything after the query in the page text
                query_idx = after_text.rfind(query_text)
                if query_idx != -1:
                    response = after_text[query_idx + len(query_text) :].strip()
                    # Remove trailing "POWERED BY ADOPT" marker
                    response = response.replace("POWERED BY ADOPT", "").strip()
                else:
                    response = after_text

                elapsed = time.monotonic() - start
                exec_log.log("response_captured", chars=len(response))
                print(f"[{qid}] Response captured ({len(response)} chars, {elapsed:.1f}s)")
                results.append(
                    {
                        **q,
                        "response": response,
                        "response_time_s": elapsed,
                        "error": None,
                        "execution_log": exec_log.events,
                    }
                )

            except Exception as e:
                elapsed = time.monotonic() - start
                exec_log.log("error", message=str(e))
                print(f"[{qid}] ERROR: {e}")
                results.append(
                    {
                        **q,
                        "response": None,
                        "response_time_s": elapsed,
                        "error": str(e),
                        "execution_log": exec_log.events,
                    }
                )

        browser.close()

    # Save raw results
    results_path.write_text(json.dumps(results, indent=2))
    print(f"\nDone! {len(results)} results saved to {results_path}")
    return results
