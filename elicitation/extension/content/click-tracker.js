/**
 * Content script: interaction tracker.
 *
 * Injected into the active tab during recording sessions. Captures click,
 * input, change, and submit events with element metadata and forwards
 * them to the service worker.
 */

(function () {
  // Prevent double-injection
  if (window.__adoptClickTracker) return;

  const handlers = [];

  function cssSelector(el) {
    if (el.id) return `${el.tagName.toLowerCase()}#${el.id}`;
    const parts = [el.tagName.toLowerCase()];
    if (el.className && typeof el.className === "string") {
      const classes = el.className.trim().split(/\s+/).slice(0, 2).join(".");
      if (classes) parts.push("." + classes);
    }
    return parts.join("");
  }

  function sendEvent(type, data) {
    try {
      chrome.runtime.sendMessage({ type, data });
    } catch (err) {
      // Extension context invalidated — clean up all listeners
      cleanup();
    }
  }

  // ── Click handler ─────────────────────────────────────────────────────────

  function handleClick(e) {
    const target =
      e.target.closest("[id], [class], a, button, input, select, textarea, [role]") ||
      e.target;

    sendEvent("CLICK_EVENT", {
      event_type: "click",
      tag_name: target.tagName || "",
      element_id: target.id || null,
      class_name: (typeof target.className === "string" ? target.className : "") || null,
      text_content: (target.textContent || "").trim().slice(0, 100) || null,
      href: target.href || null,
      selector: cssSelector(target),
      url: window.location.href,
      x: Math.round(e.clientX),
      y: Math.round(e.clientY),
      timestamp: new Date().toISOString(),
    });
  }

  // ── Input handler (debounced per element) ─────────────────────────────────

  const inputTimers = new WeakMap();

  function handleInput(e) {
    const target = e.target;
    if (!target || !target.tagName) return;
    const tag = target.tagName.toLowerCase();
    if (tag !== "input" && tag !== "textarea") return;

    // Debounce: wait 500ms after last keystroke
    const existing = inputTimers.get(target);
    if (existing) clearTimeout(existing);

    inputTimers.set(target, setTimeout(() => {
      inputTimers.delete(target);
      sendEvent("INPUT_EVENT", {
        event_type: "input",
        tag_name: target.tagName || "",
        element_id: target.id || null,
        class_name: (typeof target.className === "string" ? target.className : "") || null,
        selector: cssSelector(target),
        url: window.location.href,
        input_type: target.type || "text",
        value: (target.value || "").slice(0, 500),
        field_name: target.name || target.id || null,
        timestamp: new Date().toISOString(),
      });
    }, 500));
  }

  // ── Change handler (select, checkbox, radio) ──────────────────────────────

  function handleChange(e) {
    const target = e.target;
    if (!target || !target.tagName) return;
    const tag = target.tagName.toLowerCase();
    // Only handle selects, checkboxes, radios (text inputs are handled by input handler)
    if (tag === "input" && !["checkbox", "radio"].includes(target.type)) return;
    if (tag !== "select" && tag !== "input") return;

    let val;
    if (target.type === "checkbox" || target.type === "radio") {
      val = target.checked ? "checked" : "unchecked";
    } else {
      val = (target.value || "").slice(0, 500);
    }

    sendEvent("INPUT_EVENT", {
      event_type: "change",
      tag_name: target.tagName || "",
      element_id: target.id || null,
      class_name: (typeof target.className === "string" ? target.className : "") || null,
      selector: cssSelector(target),
      url: window.location.href,
      input_type: target.type || tag,
      value: val,
      field_name: target.name || target.id || null,
      timestamp: new Date().toISOString(),
    });
  }

  // ── Submit handler ────────────────────────────────────────────────────────

  function handleSubmit(e) {
    const form = e.target;
    if (!form || form.tagName?.toLowerCase() !== "form") return;

    sendEvent("INPUT_EVENT", {
      event_type: "submit",
      tag_name: "FORM",
      element_id: form.id || null,
      class_name: (typeof form.className === "string" ? form.className : "") || null,
      selector: cssSelector(form),
      url: window.location.href,
      input_type: "form",
      value: form.action || null,
      field_name: form.name || form.id || null,
      timestamp: new Date().toISOString(),
    });
  }

  // ── Register all listeners ────────────────────────────────────────────────

  document.addEventListener("click", handleClick, true);
  document.addEventListener("input", handleInput, true);
  document.addEventListener("change", handleChange, true);
  document.addEventListener("submit", handleSubmit, true);

  handlers.push(
    { event: "click", fn: handleClick },
    { event: "input", fn: handleInput },
    { event: "change", fn: handleChange },
    { event: "submit", fn: handleSubmit },
  );

  function cleanup() {
    for (const h of handlers) {
      document.removeEventListener(h.event, h.fn, true);
    }
    handlers.length = 0;
    delete window.__adoptClickTracker;
  }

  // Expose for cleanup by service worker's REMOVE_CLICK_TRACKER
  window.__adoptClickTracker = cleanup;

  console.log("[Adopt.ai] Interaction tracker injected (click, input, change, submit)");
})();
