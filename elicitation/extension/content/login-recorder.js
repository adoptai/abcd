/**
 * Content script: login-recording interaction tracker.
 *
 * Injected instead of click-tracker.js when login recording mode is active.
 * Captures login form interactions with:
 *   - field role labeling (username | password | otp | unknown_sensitive)
 *   - value redaction for sensitive fields
 *   - richer selector metadata (autocomplete, placeholder, aria-label, data-*)
 *
 * Sends LOGIN_CLICK_EVENT / LOGIN_INPUT_EVENT messages to the service worker.
 */

(function () {
  if (window.__adoptLoginRecorder) return;

  const handlers = [];

  // ── Metadata helpers ─────────────────────────────────────────────────────

  function getDataAttrs(el) {
    const attrs = {};
    for (const attr of el.attributes) {
      if (attr.name.startsWith("data-")) {
        attrs[attr.name] = attr.value;
      }
    }
    return Object.keys(attrs).length > 0 ? JSON.stringify(attrs) : null;
  }

  function buildRichSelector(el) {
    // Priority 1: data-testid / data-test
    const testId = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testId) return `[data-testid="${testId}"]`;

    // Priority 2: stable id (no 8+ hex chars)
    if (el.id && !/[0-9a-f]{8,}/i.test(el.id)) return `#${el.id}`;

    const tag = el.tagName.toLowerCase();

    // Priority 3: name attribute
    if (el.name) return `${tag}[name="${el.name}"]`;

    // Priority 4: autocomplete
    const ac = el.getAttribute("autocomplete");
    if (ac && ac !== "off") return `${tag}[autocomplete="${ac}"]`;

    // Priority 5: semantic combination
    const parts = [tag];
    if (el.type && tag === "input") parts.push(`[type="${el.type}"]`);
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel) parts.push(`[aria-label="${ariaLabel}"]`);
    else if (el.placeholder) parts.push(`[placeholder="${el.placeholder}"]`);
    if (parts.length > 1) return parts.join("");

    // Priority 6: class-based fallback
    if (el.className && typeof el.className === "string") {
      const classes = el.className.trim().split(/\s+/).slice(0, 2).join(".");
      if (classes) return `${tag}.${classes}`;
    }
    return tag;
  }

  function detectFieldRole(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || "").toLowerCase();
    const name = (el.name || el.id || "").toLowerCase();
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase();
    const ariaLabel = (el.getAttribute("aria-label") || "").toLowerCase();
    const placeholder = (el.placeholder || "").toLowerCase();

    // Password
    if (type === "password") return "password";

    // OTP / MFA
    const otpPatterns = ["otp", "mfa", "totp", "2fa", "verification", "one-time", "onetime"];
    if (
      ac === "one-time-code" ||
      otpPatterns.some((p) => name.includes(p) || ariaLabel.includes(p) || placeholder.includes(p)) ||
      (el.maxLength >= 4 && el.maxLength <= 8 && (type === "number" || type === "tel"))
    ) {
      return "otp";
    }

    // Username / email
    const usernamePatterns = ["email", "username", "user_name", "userid", "login", "user-name"];
    if (
      type === "email" ||
      ["email", "username"].includes(ac) ||
      usernamePatterns.some((p) => name.includes(p) || ariaLabel.includes(p) || placeholder.includes(p))
    ) {
      return "username";
    }

    return null;
  }

  function shouldRedact(fieldRole) {
    return fieldRole === "password" || fieldRole === "otp";
  }

  function sendEvent(type, data) {
    try {
      chrome.runtime.sendMessage({ type, data });
    } catch (err) {
      cleanup();
    }
  }

  // ── Click handler ─────────────────────────────────────────────────────────

  function handleClick(e) {
    const target =
      e.target.closest("[id], [class], a, button, input, select, textarea, [role]") ||
      e.target;

    sendEvent("LOGIN_CLICK_EVENT", {
      event_type: "click",
      tag_name: target.tagName || "",
      element_id: target.id || null,
      class_name: (typeof target.className === "string" ? target.className : "") || null,
      text_content: (target.textContent || "").trim().slice(0, 100) || null,
      href: target.href || null,
      selector: buildRichSelector(target),
      url: window.location.href,
      x: Math.round(e.clientX),
      y: Math.round(e.clientY),
      input_type: target.type || null,
      autocomplete: target.getAttribute("autocomplete") || null,
      placeholder: target.placeholder || null,
      aria_label: target.getAttribute("aria-label") || null,
      role_attr: target.getAttribute("role") || null,
      data_attrs_json: getDataAttrs(target),
      timestamp: new Date().toISOString(),
    });
  }

  // ── Input handler (debounced) ─────────────────────────────────────────────

  const inputTimers = new WeakMap();

  function handleInput(e) {
    const target = e.target;
    if (!target || !target.tagName) return;
    const tag = target.tagName.toLowerCase();
    if (tag !== "input" && tag !== "textarea") return;

    const existing = inputTimers.get(target);
    if (existing) clearTimeout(existing);

    inputTimers.set(target, setTimeout(() => {
      inputTimers.delete(target);

      const fieldRole = detectFieldRole(target);
      const redact = shouldRedact(fieldRole);
      const rawValue = (target.value || "").slice(0, 500);

      sendEvent("LOGIN_INPUT_EVENT", {
        event_type: "input",
        tag_name: target.tagName || "",
        element_id: target.id || null,
        class_name: (typeof target.className === "string" ? target.className : "") || null,
        selector: buildRichSelector(target),
        url: window.location.href,
        input_type: target.type || "text",
        value: redact ? "[REDACTED]" : rawValue,
        field_name: target.name || target.id || null,
        field_role: fieldRole,
        is_redacted: redact,
        autocomplete: target.getAttribute("autocomplete") || null,
        placeholder: target.placeholder || null,
        aria_label: target.getAttribute("aria-label") || null,
        role_attr: target.getAttribute("role") || null,
        data_attrs_json: getDataAttrs(target),
        timestamp: new Date().toISOString(),
      });
    }, 500));
  }

  // ── Change handler ────────────────────────────────────────────────────────

  function handleChange(e) {
    const target = e.target;
    if (!target || !target.tagName) return;
    const tag = target.tagName.toLowerCase();
    if (tag === "input" && !["checkbox", "radio"].includes(target.type)) return;
    if (tag !== "select" && tag !== "input") return;

    const fieldRole = detectFieldRole(target);
    const redact = shouldRedact(fieldRole);

    let val;
    if (target.type === "checkbox" || target.type === "radio") {
      val = target.checked ? "checked" : "unchecked";
    } else {
      val = redact ? "[REDACTED]" : (target.value || "").slice(0, 500);
    }

    sendEvent("LOGIN_INPUT_EVENT", {
      event_type: "change",
      tag_name: target.tagName || "",
      element_id: target.id || null,
      class_name: (typeof target.className === "string" ? target.className : "") || null,
      selector: buildRichSelector(target),
      url: window.location.href,
      input_type: target.type || tag,
      value: val,
      field_name: target.name || target.id || null,
      field_role: fieldRole,
      is_redacted: redact,
      autocomplete: target.getAttribute("autocomplete") || null,
      placeholder: target.placeholder || null,
      aria_label: target.getAttribute("aria-label") || null,
      role_attr: target.getAttribute("role") || null,
      data_attrs_json: getDataAttrs(target),
      timestamp: new Date().toISOString(),
    });
  }

  // ── Submit handler ────────────────────────────────────────────────────────

  function handleSubmit(e) {
    const form = e.target;
    if (!form || form.tagName?.toLowerCase() !== "form") return;

    sendEvent("LOGIN_INPUT_EVENT", {
      event_type: "submit",
      tag_name: "FORM",
      element_id: form.id || null,
      class_name: (typeof form.className === "string" ? form.className : "") || null,
      selector: buildRichSelector(form),
      url: window.location.href,
      input_type: "form",
      value: form.action || null,
      field_name: form.name || form.id || null,
      field_role: null,
      is_redacted: false,
      autocomplete: null,
      placeholder: null,
      aria_label: null,
      role_attr: null,
      data_attrs_json: null,
      timestamp: new Date().toISOString(),
    });
  }

  // ── Register listeners ────────────────────────────────────────────────────

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
    delete window.__adoptLoginRecorder;
  }

  window.__adoptLoginRecorder = cleanup;

  console.log("[Adopt.ai] Login recorder injected (username/password/OTP detection + redaction)");
})();
