/**
 * Shared Preact components using HTM.
 */
import { html, useState, useRef, useEffect } from "../lib/htm-preact.mjs";
import { startListening, stopListening, isSupported as voiceSupported } from "./voice.js";
import { speak, isSupported as ttsSupported } from "./tts.js";
import * as api from "./api.js";

// ── Settings persistence ────────────────────────────────────────────────────

const SETTINGS_KEY = "elicitation_agent_settings";

export async function loadSettings() {
  try {
    const result = await chrome.storage.local.get(SETTINGS_KEY);
    return result[SETTINGS_KEY] || { textSize: "normal" };
  } catch {
    return { textSize: "normal" };
  }
}

export async function saveSettings(settings) {
  try {
    await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  } catch (e) {
    console.warn("Failed to save settings:", e);
  }
}

export function applyTextSize(size) {
  document.body.classList.remove("text-normal", "text-larger", "text-largest");
  document.body.classList.add(`text-${size}`);
}

// ── OptionsView ─────────────────────────────────────────────────────────────

export function OptionsView({ settings, onUpdate }) {
  const sizes = [
    { value: "normal", label: "Normal", desc: "Default text size (14px base)" },
    { value: "larger", label: "Larger", desc: "Increased text size (16px base)" },
    { value: "largest", label: "Largest", desc: "Maximum text size (18px base)" },
  ];

  return html`
    <div class="content">
      <div class="section-title" style="margin-bottom:12px">Display Settings</div>

      <div style="margin-bottom:16px">
        <label style="display:block;font-size:12px;font-weight:500;color:#374151;margin-bottom:8px">
          Text Size
        </label>
        <div style="display:flex;flex-direction:column;gap:6px">
          ${sizes.map(s => html`
            <label class="card" style="cursor:pointer;display:flex;align-items:center;gap:10px;margin-bottom:0;padding:10px 12px;border-color:${settings.textSize === s.value ? '#2563eb' : '#e5e7eb'}"
              onClick=${() => onUpdate({ ...settings, textSize: s.value })}>
              <input type="radio" name="textSize" value=${s.value}
                checked=${settings.textSize === s.value}
                style="margin:0;accent-color:#2563eb" />
              <div>
                <div style="font-weight:500">${s.label}</div>
                <div style="font-size:11px;color:#6b7280">${s.desc}</div>
              </div>
            </label>
          `)}
        </div>
      </div>

      <div style="padding:12px;background:#f9fafb;border-radius:8px;font-size:12px;color:#6b7280">
        Settings are saved locally and apply to this browser only.
      </div>
    </div>
  `;
}

// ── Markdown renderer (cached sync) ────────────────────────────────────────

let _marked = null;
(async () => {
  try {
    const m = await import("../lib/marked.esm.mjs");
    _marked = m.marked;
  } catch {}
})();

function renderMarkdownSync(md) {
  if (!md) return "";
  if (_marked) return _marked.parse(md);
  // Fallback until marked loads
  return md.replace(/</g, "&lt;").replace(/\n/g, "<br>");
}

// ── MessageList ─────────────────────────────────────────────────────────────

export function MessageList({ messages, streamingContent }) {
  const endRef = useRef(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, streamingContent]);

  if (!messages.length && !streamingContent) {
    return html`<div class="empty-state">No messages yet. Start a conversation!</div>`;
  }

  return html`
    <div class="messages-container">
      ${messages.map(
        (m) => html`
          <div class="message ${m.role}" key=${m.id}>
            ${m.role === "agent"
              ? html`<div class="markdown-content" dangerouslySetInnerHTML=${{ __html: renderMarkdownSync(m.content) }}></div>`
              : html`<div>${m.content}</div>`
            }
            <div class="message-meta">
              ${m.content_type === "voice_transcript" ? "voice " : ""}
              ${new Date(m.timestamp).toLocaleTimeString()}
              ${m.role === "agent"
                ? html` <button
                    class="icon-btn"
                    style="width:20px;height:20px;font-size:11px;display:inline-flex;vertical-align:middle;margin-left:4px"
                    onClick=${() => speak(m.content)}
                    title="Read aloud"
                  >
                    \u{1F50A}
                  </button>`
                : null}
            </div>
          </div>
        `
      )}
      ${streamingContent && html`
        <div class="message agent streaming">
          <div class="markdown-content" dangerouslySetInnerHTML=${{ __html: renderMarkdownSync(streamingContent) }}></div>
          <div class="message-meta">streaming...</div>
        </div>
      `}
      <div ref=${endRef} />
    </div>
  `;
}

// ── MessageInput ────────────────────────────────────────────────────────────

export function MessageInput({ onSend, disabled }) {
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed, "text");
    setText("");
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const toggleVoice = () => {
    if (listening) {
      stopListening();
      setInterim("");
    } else {
      setListening(true);
      setInterim("");
      let hasSent = false;
      startListening({
        onResult: (transcript) => {
          if (hasSent) return;
          hasSent = true;
          onSend(transcript, "voice_transcript");
          stopListening();
          setListening(false);
          setInterim("");
        },
        onInterim: (t) => setInterim(t),
        onEnd: () => {
          setListening(false);
          setInterim("");
        },
      });
    }
  };

  return html`
    ${interim && html`<div class="interim-text">${interim}...</div>`}
    <div class="chat-input-bar">
      <input
        class="input"
        placeholder="Type a message..."
        value=${text}
        onInput=${(e) => setText(e.target.value)}
        onKeyDown=${handleKey}
        disabled=${disabled}
      />
      <button class="btn btn-primary btn-sm" onClick=${handleSend} disabled=${disabled || !text.trim()}>
        Send
      </button>
      ${voiceSupported() &&
      html`
        <button
          class="icon-btn ${listening ? "chat-active" : ""}"
          onClick=${toggleVoice}
          title=${listening ? "Stop listening" : "Speak to Claude"}
        >
          ${listening ? "\u{26AB}" : "\u{1F3A4}"}
        </button>
      `}
    </div>
  `;
}

// ── ChatSessionBar ─────────────────────────────────────────────────────────

export function ChatSessionBar({ sessions, activeSession, onNew, onSwitch, onDelete, onRename }) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");

  const handleDelete = () => {
    if (!activeSession) return;
    if (confirmDelete) {
      onDelete?.(activeSession.id);
      setConfirmDelete(false);
    } else {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 3000);
    }
  };

  const startRename = () => {
    if (!activeSession) return;
    setEditTitle(activeSession.title);
    setEditing(true);
  };

  const commitRename = () => {
    const trimmed = editTitle.trim();
    if (trimmed && activeSession && trimmed !== activeSession.title) {
      onRename?.(activeSession.id, trimmed);
    }
    setEditing(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter") commitRename();
    if (e.key === "Escape") setEditing(false);
  };

  return html`
    <div class="chat-session-bar">
      <button class="btn btn-sm" onClick=${onNew} title="New conversation">+ New</button>
      ${sessions.length > 0 && !editing && html`
        <select
          class="chat-session-select"
          value=${activeSession?.id || ""}
          onChange=${(e) => onSwitch?.(e.target.value)}
        >
          ${sessions.map(
            (s) => html`<option key=${s.id} value=${s.id}>
              ${s.title}${" "}(${new Date(s.created_at).toLocaleDateString()})
            </option>`
          )}
        </select>
      `}
      ${editing && html`
        <input
          class="chat-session-rename-input"
          type="text"
          value=${editTitle}
          onInput=${(e) => setEditTitle(e.target.value)}
          onKeyDown=${handleKeyDown}
          onBlur=${commitRename}
          maxlength="255"
          ref=${(el) => el && setTimeout(() => el.focus(), 0)}
        />
      `}
      ${activeSession && !editing && html`
        <button class="btn btn-sm" onClick=${startRename} title="Rename conversation">
          \u270F
        </button>
      `}
      ${activeSession && html`
        <button
          class="btn btn-sm ${confirmDelete ? "btn-danger-confirm" : ""}"
          onClick=${handleDelete}
          title=${confirmDelete ? "Click again to confirm" : "Delete this conversation"}
        >
          ${confirmDelete ? "Confirm?" : "\u{1F5D1}"}
        </button>
      `}
    </div>
  `;
}

// ── CaptureSessionControls ──────────────────────────────────────────────────

export function CaptureSessionControls({ session, onStart, starting, onStop, onToggleFeature }) {
  if (!session || session.status === "stopped") {
    return html`
      <div class="capture-controls">
        <button class="btn btn-primary btn-sm" onClick=${onStart} disabled=${starting}>
          ${starting ? "Starting..." : "Start Capture"}
        </button>
        ${session?.status === "stopped" && html`
          <span class="capture-status stopped">STOPPED</span>
        `}
      </div>
    `;
  }

  if (session.status === "capturing") {
    return html`
      <div class="capture-controls">
        <button class="btn btn-danger btn-sm" onClick=${() => onStop(session.id)}>Stop</button>
        <span class="capture-status capturing">CAPTURING</span>
        <label class="capture-toggle">
          <input type="checkbox" checked=${session.click_tracking}
            onChange=${(e) => onToggleFeature(session.id, "click_tracking", e.target.checked)} />
          Clicks
        </label>
        <label class="capture-toggle">
          <input type="checkbox" checked=${session.url_monitoring}
            onChange=${(e) => onToggleFeature(session.id, "url_monitoring", e.target.checked)} />
          URLs
        </label>
        <label class="capture-toggle">
          <input type="checkbox" checked=${session.har_capture}
            onChange=${(e) => onToggleFeature(session.id, "har_capture", e.target.checked)} />
          Network
        </label>
        <label class="capture-toggle">
          <input type="checkbox" checked=${session.narration}
            onChange=${(e) => onToggleFeature(session.id, "narration", e.target.checked)} />
          Narrate
        </label>
        ${session.narration && html`<span class="narration-indicator">🔴 Narrating</span>`}
      </div>
    `;
  }

  // idle or paused
  return html`
    <div class="capture-controls">
      <button class="btn btn-primary btn-sm" onClick=${onStart} disabled=${starting}>
        ${starting ? "Starting..." : "Start Capture"}
      </button>
      <span class="capture-status ${session.status}">${session.status.toUpperCase()}</span>
    </div>
  `;
}

// ── ScreenshotControls ──────────────────────────────────────────────────────

export function ScreenshotControls({ onCapture, onRegionCapture, disabled }) {
  return html`
    <div class="screenshot-controls">
      <button
        class="icon-btn"
        onClick=${onCapture}
        disabled=${disabled}
        title="Full page screenshot"
      >
        \u{1F4F7}
      </button>
      <button
        class="icon-btn"
        onClick=${onRegionCapture}
        disabled=${disabled}
        title="Region screenshot"
      >
        \u{2702}
      </button>
    </div>
  `;
}

// ── StatusBar ───────────────────────────────────────────────────────────────

export function StatusBar({ connected, context }) {
  return html`
    <div class="status-bar">
      <span class="status-dot ${connected ? "connected" : ""}"></span>
      <span>${connected ? "Connected" : "Disconnected"}</span>
      ${context && html`<span> | ${context}</span>`}
    </div>
  `;
}

// ── Toast ──────────────────────────────────────────────────────────────────

export function ToastContainer({ toasts, onDismiss }) {
  if (!toasts || !toasts.length) return null;

  return html`
    <div class="toast-container">
      ${toasts.map((t) => html`
        <div key=${t.id} class="toast ${t.type}" onClick=${() => onDismiss(t.id)}>
          ${t.message}
        </div>
      `)}
    </div>
  `;
}

// ── ImageLightbox ─────────────────────────────────────────────────────────

export function ImageLightbox({ url, onClose }) {
  if (!url) return null;

  const openNewTab = (e) => {
    e.stopPropagation();
    window.open(url, "_blank");
  };

  return html`
    <div class="lightbox" onClick=${onClose}>
      <img src=${url} onClick=${(e) => e.stopPropagation()} />
      <button class="lightbox-close" onClick=${onClose}>\u2715</button>
      <div class="lightbox-actions">
        <button class="btn btn-secondary btn-sm" onClick=${openNewTab}>Open in New Tab</button>
      </div>
    </div>
  `;
}

// ── CapturedItems ──────────────────────────────────────────────────────────

const BACKEND_URL = "http://localhost:8000";

export function CapturedItems({ items, onCapture, onImageClick }) {
  if (!items || items.length === 0) {
    return html`
      <div class="content">
        <div class="flex-between" style="margin-bottom:12px">
          <span class="section-title">Captures</span>
          ${onCapture && html`<button class="btn btn-primary btn-sm" onClick=${onCapture}>Capture</button>`}
        </div>
        <div class="empty-state">No captures yet. Use the camera button to take a screenshot.</div>
      </div>
    `;
  }

  const handleClick = (item) => {
    const imgUrl = `${BACKEND_URL}/screenshots/${item.id}/image`;
    if (onImageClick) onImageClick(imgUrl);
    else window.open(imgUrl, "_blank");
  };

  return html`
    <div class="content">
      <div class="flex-between" style="margin-bottom:12px">
        <span class="section-title">Captures (${items.length})</span>
        ${onCapture && html`<button class="btn btn-primary btn-sm" onClick=${onCapture}>Capture</button>`}
      </div>
      <div class="capture-grid">
        ${items.map((item) => html`
          <div key=${item.id} class="capture-card" onClick=${() => handleClick(item)}>
            <img src="${BACKEND_URL}/screenshots/${item.id}/image" alt="screenshot"
              onerror=${(e) => { e.target.style.display = "none"; }} />
            <div class="capture-meta">
              ${item.url ? new URL(item.url).hostname : "unknown"}
              <br />
              ${new Date(item.timestamp).toLocaleTimeString()}
            </div>
          </div>
        `)}
      </div>
    </div>
  `;
}

// ── Timeline ───────────────────────────────────────────────────────────────

const TIMELINE_ICONS = {
  message: "\u{1F4AC}",         // speech bubble
  screenshot: "\u{1F4F7}",      // camera
  click: "\u{1F5B1}",           // mouse
  input: "\u{2328}",            // keyboard
  change: "\u{1F504}",          // toggle/cycle
  submit: "\u{1F4E8}",          // send/envelope
  narration: "\u{1F399}",       // studio mic
  network_request: "\u{2B06}\u{FE0F}",  // up arrow for network
  url_change: "\u{1F517}",      // link
  capture_start: "\u{25B6}",    // play
  capture_stop: "\u{23F9}",     // stop
  voice_start: "\u{1F3A4}",     // microphone
  voice_end: "\u{1F3A4}",
  recording_start: "\u{26AB}",
  recording_stop: "\u{2B1B}",
  har_start: "\u{1F310}",       // globe
  har_stop: "\u{1F310}",
};

const TIMELINE_TYPE_CLASS = {
  message: "message",
  screenshot: "screenshot",
  click: "click",
  input: "click",
  change: "click",
  submit: "click",
  narration: "narration",
  network_request: "network",
  url_change: "url_change",
  capture_start: "capture",
  capture_stop: "capture",
  voice_start: "voice",
  voice_end: "voice",
  recording_start: "recording",
  recording_stop: "recording",
  har_start: "recording",
  har_stop: "recording",
};

const TIMELINE_FILTERS = [
  { key: "all", label: "All", types: null },
  { key: "network", label: "Network", types: ["network_request"] },
  { key: "clicks", label: "Clicks", types: ["click", "input", "change", "submit"] },
  { key: "narration", label: "Narration", types: ["narration"] },
  { key: "navigation", label: "Navigation", types: ["url_change"] },
  { key: "system", label: "System", types: ["capture_start", "capture_stop", "har_start", "har_stop", "voice_start", "voice_end", "recording_start", "recording_stop"] },
];

export function Timeline({ events, onDownload, onClear }) {
  const [filter, setFilter] = useState("all");
  const [copiedId, setCopiedId] = useState(null);
  const [confirmClear, setConfirmClear] = useState(false);

  if (!events || events.length === 0) {
    return html`
      <div class="empty-state">No timeline events yet. Start recording or chatting to see activity here.</div>
    `;
  }

  const activeFilter = TIMELINE_FILTERS.find((f) => f.key === filter);
  const filtered = activeFilter?.types
    ? events.filter((e) => activeFilter.types.includes(e.event_type))
    : events;

  const countLabel = filtered.length !== events.length
    ? `Timeline (${filtered.length} / ${events.length})`
    : `Timeline (${events.length})`;

  const handleCopy = (e) => {
    let text;
    if (e.event_type === "network_request") {
      // Copy full metadata JSON for network events
      try {
        const meta = JSON.parse(e.metadata_json || "{}");
        text = JSON.stringify({ summary: e.summary, timestamp: e.timestamp, ...meta }, null, 2);
      } catch {
        text = e.summary;
      }
    } else {
      text = e.summary;
    }
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(e.id);
      setTimeout(() => setCopiedId(null), 1500);
    });
  };

  const handleClear = () => {
    if (confirmClear) {
      onClear?.();
      setConfirmClear(false);
    } else {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 3000);
    }
  };

  return html`
    <div class="content">
      <div class="flex-between" style="margin-bottom:8px">
        <span class="section-title">${countLabel}</span>
        <div class="flex gap-sm">
          ${onClear && html`
            <button class="btn ${confirmClear ? "btn-danger-confirm" : "btn-danger"} btn-sm" onClick=${handleClear}>
              ${confirmClear ? "Confirm Clear?" : "Clear"}
            </button>
          `}
          ${onDownload && html`
            <button class="btn btn-secondary btn-sm" onClick=${onDownload}>Download</button>
          `}
        </div>
      </div>
      <div class="filter-pills" style="margin-bottom:8px">
        ${TIMELINE_FILTERS.map((f) => html`
          <button class="pill ${filter === f.key ? "active" : ""}" onClick=${() => setFilter(f.key)}>
            ${f.label}
          </button>
        `)}
      </div>
      <div class="timeline">
        ${filtered.map((e) => html`
          <div key=${e.id} class="timeline-item">
            <div class="timeline-icon ${TIMELINE_TYPE_CLASS[e.event_type] || "message"}">
              ${TIMELINE_ICONS[e.event_type] || "\u{2022}"}
            </div>
            <div class="timeline-body">
              <div class="timeline-summary ${e.event_type === "narration" ? "narration-full" : ""}">${e.summary}</div>
              <div class="timeline-time">
                ${new Date(e.timestamp).toLocaleTimeString()}
                <button class="timeline-copy-btn" onClick=${() => handleCopy(e)}
                  title=${e.event_type === "network_request" ? "Copy full JSON payload" : "Copy text"}>
                  ${copiedId === e.id ? "\u2713" : "\u{1F4CB}"}
                </button>
              </div>
            </div>
          </div>
        `)}
      </div>
    </div>
  `;
}

// ── DocumentViewer ─────────────────────────────────────────────────────────

export function DocumentViewer({
  projectId,
  processId,
  canonicalDoc,
  userDocs,
  onRegenerate,
  onSaveDoc,
  onDeleteDoc,
  onCreateDoc,
  showToast,
}) {
  const [editing, setEditing] = useState(null); // doc id being edited
  const [editContent, setEditContent] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");
  const [viewingDoc, setViewingDoc] = useState(null); // user doc being viewed
  const [renderedHtml, setRenderedHtml] = useState("");
  const [viewedHtml, setViewedHtml] = useState("");

  // Render markdown for canonical doc
  useEffect(() => {
    if (canonicalDoc?.content) {
      renderMarkdown(canonicalDoc.content).then(setRenderedHtml);
    } else {
      setRenderedHtml("");
    }
  }, [canonicalDoc?.content, canonicalDoc?.updated_at]);

  // Render markdown for viewed user doc
  useEffect(() => {
    if (viewingDoc?.content) {
      renderMarkdown(viewingDoc.content).then(setViewedHtml);
    } else {
      setViewedHtml("");
    }
  }, [viewingDoc?.content]);

  async function renderMarkdown(md) {
    try {
      const { marked } = await import("../lib/marked.esm.mjs");
      return marked.parse(md);
    } catch {
      return md.replace(/</g, "&lt;").replace(/\n/g, "<br>");
    }
  }

  const startEdit = (doc) => {
    setEditing(doc.id);
    setEditContent(doc.content);
    setEditTitle(doc.title);
  };

  const cancelEdit = () => {
    setEditing(null);
    setEditContent("");
    setEditTitle("");
  };

  const saveEdit = async () => {
    if (!editing) return;
    const update = {};
    if (editTitle) update.title = editTitle;
    update.content = editContent;
    await onSaveDoc(editing, update);
    setEditing(null);
  };

  const openFull = (docId) => {
    const url = chrome.runtime.getURL(`viewer/viewer.html?id=${docId}`);
    chrome.tabs.create({ url });
  };

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    await onCreateDoc({ title: newTitle.trim(), content: newContent, doc_type: "user", process_id: processId || null });
    setCreating(false);
    setNewTitle("");
    setNewContent("");
  };

  return html`
    <div style="padding:0">
      <div class="section-title" style="margin-bottom:8px">Canonical Document</div>
      ${!canonicalDoc
        ? html`
          <div style="margin-bottom:12px">
            <p class="text-muted" style="margin-bottom:6px">No canonical document yet.</p>
            <button class="btn btn-primary btn-sm" onClick=${onRegenerate}>Generate</button>
          </div>
        `
        : editing === canonicalDoc.id
          ? html`
            <div style="margin-bottom:12px">
              <textarea class="input" style="min-height:200px;font-family:monospace;font-size:12px;resize:vertical"
                value=${editContent} onInput=${(e) => setEditContent(e.target.value)}></textarea>
              <div class="flex gap-sm mt-sm">
                <button class="btn btn-primary btn-sm" onClick=${saveEdit}>Save</button>
                <button class="btn btn-secondary btn-sm" onClick=${cancelEdit}>Cancel</button>
              </div>
            </div>
          `
          : html`
            <div style="margin-bottom:12px;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px;max-height:300px;overflow-y:auto">
              <div class="markdown-content" dangerouslySetInnerHTML=${{ __html: renderedHtml }}></div>
            </div>
            <div class="flex gap-sm" style="margin-bottom:12px">
              <button class="btn btn-secondary btn-sm" onClick=${() => startEdit(canonicalDoc)}>Edit</button>
              <button class="btn btn-secondary btn-sm" onClick=${onRegenerate}>Regenerate</button>
              <button class="btn btn-secondary btn-sm" onClick=${() => openFull(canonicalDoc.id)}>Open Full</button>
            </div>
          `
      }

      <div class="flex-between" style="margin-bottom:8px">
        <span class="section-title">User Documents</span>
        <button class="btn btn-primary btn-sm" onClick=${() => setCreating(!creating)}>
          ${creating ? "Cancel" : "+ New"}
        </button>
      </div>

      ${creating && html`
        <div style="margin-bottom:12px">
          <div class="form-group">
            <label>Title</label>
            <input class="input" value=${newTitle} onInput=${(e) => setNewTitle(e.target.value)} placeholder="Document title" />
          </div>
          <div class="form-group">
            <label>Content (markdown)</label>
            <textarea class="input" style="min-height:100px;font-family:monospace;font-size:12px;resize:vertical"
              value=${newContent} onInput=${(e) => setNewContent(e.target.value)} placeholder="# My Document"></textarea>
          </div>
          <button class="btn btn-primary btn-full" onClick=${handleCreate} disabled=${!newTitle.trim()}>Create</button>
        </div>
      `}

      ${viewingDoc
        ? html`
          <div style="margin-bottom:12px">
            <div class="flex-between" style="margin-bottom:6px">
              <strong>${viewingDoc.title}</strong>
              <button class="btn btn-secondary btn-sm" onClick=${() => setViewingDoc(null)}>Back</button>
            </div>
            ${editing === viewingDoc.id
              ? html`
                <div class="form-group">
                  <label>Title</label>
                  <input class="input" value=${editTitle} onInput=${(e) => setEditTitle(e.target.value)} />
                </div>
                <textarea class="input" style="min-height:200px;font-family:monospace;font-size:12px;resize:vertical"
                  value=${editContent} onInput=${(e) => setEditContent(e.target.value)}></textarea>
                <div class="flex gap-sm mt-sm">
                  <button class="btn btn-primary btn-sm" onClick=${saveEdit}>Save</button>
                  <button class="btn btn-secondary btn-sm" onClick=${cancelEdit}>Cancel</button>
                </div>
              `
              : html`
                <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px;max-height:300px;overflow-y:auto">
                  <div class="markdown-content" dangerouslySetInnerHTML=${{ __html: viewedHtml }}></div>
                </div>
                <div class="flex gap-sm mt-sm">
                  <button class="btn btn-secondary btn-sm" onClick=${() => startEdit(viewingDoc)}>Edit</button>
                  <button class="btn btn-secondary btn-sm" onClick=${() => openFull(viewingDoc.id)}>Open Full</button>
                  <button class="btn btn-danger btn-sm" onClick=${() => { onDeleteDoc(viewingDoc.id); setViewingDoc(null); }}>Delete</button>
                </div>
              `
            }
          </div>
        `
        : html`
          ${(!userDocs || userDocs.length === 0)
            ? html`<p class="text-muted">No user documents yet.</p>`
            : userDocs.map((doc) => html`
              <div key=${doc.id} class="card" onClick=${() => setViewingDoc(doc)}>
                <h3>${doc.title}</h3>
                <p>${new Date(doc.updated_at).toLocaleString()}</p>
              </div>
            `)
          }
        `
      }
    </div>
  `;
}

// ── AttachmentsList ───────────────────────────────────────────────────────

export function AttachmentsList({ attachments, onUpload, onDelete, onImageClick }) {
  const fileRef = useRef(null);

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await onUpload(file);
    if (fileRef.current) fileRef.current.value = "";
  };

  const formatSize = (bytes) => {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  };

  const isImage = (ct) => ct && ct.startsWith("image/");

  return html`
    <div style="margin-top:16px">
      <div class="flex-between" style="margin-bottom:8px">
        <span class="section-title">Attachments</span>
        <button class="btn btn-primary btn-sm" onClick=${() => fileRef.current?.click()}>Upload</button>
      </div>
      <input ref=${fileRef} type="file" style="display:none" onChange=${handleFileChange} />
      ${(!attachments || attachments.length === 0)
        ? html`<p class="text-muted">No attachments yet.</p>`
        : attachments.map((att) => {
            const imgUrl = `${BACKEND_URL}/attachments/${att.id}/download`;
            return html`
              <div key=${att.id} class="card" style="cursor:default;display:flex;gap:8px;align-items:center">
                ${isImage(att.content_type) && html`
                  <img src=${imgUrl} alt=${att.filename}
                    style="width:48px;height:48px;object-fit:cover;border-radius:4px;cursor:pointer;flex-shrink:0"
                    onClick=${() => onImageClick?.(imgUrl)}
                    onerror=${(e) => { e.target.style.display = "none"; }}
                  />
                `}
                <div style="flex:1;min-width:0">
                  <h3 style="font-size:13px">
                    <a href=${imgUrl} target="_blank" style="color:#2563eb;text-decoration:none">
                      ${att.filename}
                    </a>
                  </h3>
                  <p>${formatSize(att.file_size)} \u2022 ${att.content_type || "unknown"}</p>
                </div>
                <button class="btn btn-danger btn-sm" style="flex-shrink:0" onClick=${(e) => { e.stopPropagation(); onDelete(att.id); }}>Delete</button>
              </div>
            `;
          })
      }
    </div>
  `;
}

// ── PlumbingTestView ────────────────────────────────────────────────────────

export function PlumbingTestView({ connected }) {
  const [results, setResults] = useState({});

  const setResult = (key, status, detail) => {
    setResults((prev) => ({ ...prev, [key]: { status, detail } }));
  };

  // ── Connection Test ──
  const testConnection = async () => {
    setResult("connection", "running", "Pinging backend...");
    const t0 = performance.now();
    try {
      const data = await api.ping();
      const latency = Math.round(performance.now() - t0);
      setResult("connection", "pass", `Pong in ${latency}ms. Echo: ${data.echo}`);
    } catch (e) {
      setResult("connection", "fail", `Failed: ${e.message}`);
    }
  };

  // ── Voice Test ──
  const [voiceListening, setVoiceListening] = useState(false);

  const testVoice = () => {
    if (!voiceSupported()) {
      setResult("voice", "fail", "SpeechRecognition not supported in this browser");
      return;
    }
    if (voiceListening) {
      stopListening();
      setVoiceListening(false);
      return;
    }
    setVoiceListening(true);
    setResult("voice", "running", "Listening... speak now");
    startListening({
      onResult: (transcript) => {
        setResult("voice", "pass", `Final transcript: "${transcript}"`);
        setVoiceListening(false);
      },
      onInterim: (t) => {
        setResult("voice", "running", `Interim: "${t}"`);
      },
      onEnd: () => {
        setVoiceListening(false);
        setResults((prev) => {
          if (prev.voice?.status === "running") {
            return { ...prev, voice: { status: "info", detail: "Listening ended (no final result)" } };
          }
          return prev;
        });
      },
    });
  };

  // ── TTS Test ──
  const [ttsText, setTtsText] = useState("Hello! This is a text-to-speech test.");

  const testTTS = () => {
    if (!ttsSupported()) {
      setResult("tts", "fail", "SpeechSynthesis not supported in this browser");
      return;
    }
    setResult("tts", "running", `Speaking: "${ttsText}"`);
    try {
      speak(ttsText);
      setResult("tts", "pass", `Spoke: "${ttsText}"`);
    } catch (e) {
      setResult("tts", "fail", `TTS failed: ${e.message}`);
    }
  };

  // ── Screenshot Test ──
  const [screenshotThumb, setScreenshotThumb] = useState(null);

  const testScreenshot = async () => {
    setResult("screenshot", "running", "Capturing...");
    try {
      const data = await api.captureScreenshot(null, null);
      setResult("screenshot", "pass", `Stored: ${data.id}\nFile: ${data.file_path}`);
      // Try to load the thumbnail
      if (data.id) {
        setScreenshotThumb(`http://localhost:8000/screenshots/${data.id}/image`);
      }
    } catch (e) {
      setResult("screenshot", "fail", `Failed: ${e.message}`);
    }
  };

  // ── HAR Test (stub) ──
  const [harActive, setHarActive] = useState(false);

  const testHarStart = async () => {
    setResult("har", "running", "Starting HAR capture (stub)...");
    try {
      const data = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          { type: "START_HAR_CAPTURE", processId: "test" },
          (resp) => {
            if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
            else if (resp?.success) resolve(resp.data);
            else reject(new Error(resp?.error || "Failed"));
          }
        );
      });
      setHarActive(true);
      setResult("har", "info", `HAR capture: ${data.message}`);
    } catch (e) {
      setResult("har", "fail", `HAR start failed: ${e.message}`);
    }
  };

  const testHarStop = async () => {
    setResult("har", "running", "Stopping HAR capture (stub)...");
    try {
      const data = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          { type: "STOP_HAR_CAPTURE", processId: "test" },
          (resp) => {
            if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
            else if (resp?.success) resolve(resp.data);
            else reject(new Error(resp?.error || "Failed"));
          }
        );
      });
      setHarActive(false);
      setResult("har", "info", `HAR stopped: ${data.message}`);
    } catch (e) {
      setResult("har", "fail", `HAR stop failed: ${e.message}`);
    }
  };

  // ── Chat Test ──
  const testChat = async () => {
    setResult("chat", "running", "Creating test project...");
    try {
      // Create a temporary project for the test
      const proj = await api.createProject({ name: "_plumbing_test", description: "Temporary test project" });
      setResult("chat", "running", "Sending test message...");

      const msg = await api.createMessage({
        project_id: proj.id,
        role: "human",
        content: "Plumbing test message at " + new Date().toISOString(),
      });
      setResult("chat", "running", `Message stored (${msg.id}). Retrieving...`);

      const msgs = await api.listMessages({ project_id: proj.id });
      if (msgs.length > 0 && msgs[0].content === msg.content) {
        setResult("chat", "pass", `Stored and retrieved: "${msgs[0].content}"\nID: ${msgs[0].id}`);
      } else {
        setResult("chat", "fail", "Retrieved message doesn't match");
      }

      // Cleanup
      await api.deleteProject(proj.id);
    } catch (e) {
      setResult("chat", "fail", `Chat test failed: ${e.message}`);
    }
  };

  // ── Full Loop Test ──
  const testFullLoop = async () => {
    if (!voiceSupported()) {
      setResult("fullloop", "fail", "Requires SpeechRecognition support");
      return;
    }
    setResult("fullloop", "running", "Step 1/4: Listening for voice input... speak now");

    try {
      // Step 1: Capture voice
      const transcript = await new Promise((resolve, reject) => {
        let settled = false;
        let timeoutId;

        const resetTimeout = () => {
          clearTimeout(timeoutId);
          timeoutId = setTimeout(() => {
            if (settled) return;
            // Speech timed out — stop listening but give 2s grace for VOICE_FINAL
            stopListening();
            setTimeout(() => {
              if (!settled) {
                settled = true;
                reject(new Error("Voice timeout — no speech detected"));
              }
            }, 2000);
          }, 5000);
        };

        resetTimeout();

        startListening({
          onResult: (t) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeoutId);
            resolve(t);
          },
          onInterim: (t) => {
            setResult("fullloop", "running", `Step 1/4: Hearing: "${t}"`);
            resetTimeout(); // Speech detected — reset the timeout
          },
          onEnd: () => {
            clearTimeout(timeoutId);
          },
        });
      });

      setResult("fullloop", "running", `Step 2/4: Storing message: "${transcript}"`);

      // Step 2: Store as message
      const proj = await api.createProject({ name: "_fullloop_test", description: "Full loop test" });
      const msg = await api.createMessage({
        project_id: proj.id,
        role: "human",
        content_type: "voice_transcript",
        content: transcript,
      });

      setResult("fullloop", "running", "Step 3/4: Retrieving message...");

      // Step 3: Retrieve
      const msgs = await api.listMessages({ project_id: proj.id });
      const retrieved = msgs.find((m) => m.id === msg.id);
      if (!retrieved) throw new Error("Message not found after storage");

      setResult("fullloop", "running", `Step 4/4: Reading back via TTS...`);

      // Step 4: TTS readback
      if (ttsSupported()) {
        speak(retrieved.content);
      }

      setResult("fullloop", "pass",
        `Voice: "${transcript}"\nStored: ${msg.id}\nRetrieved: OK\nTTS: ${ttsSupported() ? "Playing" : "Not supported"}`
      );

      // Cleanup
      await api.deleteProject(proj.id);
    } catch (e) {
      setResult("fullloop", "fail", `Full loop failed: ${e.message}`);
    }
  };

  const renderResult = (key) => {
    const r = results[key];
    if (!r) return null;
    return html`<div class="test-result ${r.status}">${r.detail}</div>`;
  };

  return html`
    <div class="content">
      <p class="text-muted" style="margin-bottom:12px">
        Test each pipe in isolation. Backend: ${connected ? "connected" : "disconnected"}.
      </p>

      <div class="test-card">
        <h4>Connection Test</h4>
        <p>Ping the backend and measure latency.</p>
        <button class="btn btn-primary btn-sm" onClick=${testConnection}>Run</button>
        ${renderResult("connection")}
      </div>

      <div class="test-card">
        <h4>Voice Test (STT)</h4>
        <p>Start SpeechRecognition, show interim + final transcript.</p>
        <button class="btn btn-primary btn-sm" onClick=${testVoice}>
          ${voiceListening ? "Stop" : "Start Listening"}
        </button>
        ${renderResult("voice")}
      </div>

      <div class="test-card">
        <h4>TTS Test</h4>
        <p>Type text or use preset, click play to hear it spoken.</p>
        <input class="input" style="margin-bottom:6px" value=${ttsText}
          onInput=${(e) => setTtsText(e.target.value)} placeholder="Text to speak" />
        <button class="btn btn-primary btn-sm" onClick=${testTTS}>Play</button>
        ${renderResult("tts")}
      </div>

      <div class="test-card">
        <h4>Screenshot Test</h4>
        <p>Capture current tab, store in backend, show thumbnail.</p>
        <button class="btn btn-primary btn-sm" onClick=${testScreenshot}>Capture</button>
        ${renderResult("screenshot")}
        ${screenshotThumb && html`<img class="test-thumbnail" src=${screenshotThumb} alt="screenshot" />`}
      </div>

      <div class="test-card">
        <h4>HAR Test (Stub)</h4>
        <p>Start/stop HAR capture placeholder. Real implementation uses chrome.debugger.</p>
        <div class="flex gap-sm">
          <button class="btn btn-primary btn-sm" onClick=${testHarStart} disabled=${harActive}>Start</button>
          <button class="btn btn-danger btn-sm" onClick=${testHarStop} disabled=${!harActive}>Stop</button>
        </div>
        ${renderResult("har")}
      </div>

      <div class="test-card">
        <h4>Chat Test</h4>
        <p>Send a message to backend, verify storage and retrieval.</p>
        <button class="btn btn-primary btn-sm" onClick=${testChat}>Run</button>
        ${renderResult("chat")}
      </div>

      <div class="test-card">
        <h4>Full Loop Test</h4>
        <p>Voice capture \u2192 store as message \u2192 retrieve \u2192 TTS readback.</p>
        <button class="btn btn-primary btn-sm" onClick=${testFullLoop}>Run</button>
        ${renderResult("fullloop")}
      </div>
    </div>
  `;
}


// ── QuestionsPanel ──────────────────────────────────────────────────────────

export function QuestionsPanel({ questions, onAdd, onUpdate, onDelete }) {
  const [showForm, setShowForm] = useState(false);
  const [content, setContent] = useState("");
  const [contextUrl, setContextUrl] = useState("");
  const [filter, setFilter] = useState("all");
  const [answeringId, setAnsweringId] = useState(null);
  const [answerText, setAnswerText] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const handleAdd = () => {
    if (!content.trim()) return;
    onAdd({ content: content.trim(), context_url: contextUrl.trim() });
    setContent("");
    setContextUrl("");
    setShowForm(false);
  };

  const handleAnswer = (id) => {
    if (!answerText.trim()) return;
    onUpdate(id, { answer: answerText.trim(), status: "answered" });
    setAnsweringId(null);
    setAnswerText("");
  };

  const handleDelete = (id) => {
    if (confirmDeleteId === id) {
      onDelete(id);
      setConfirmDeleteId(null);
    } else {
      setConfirmDeleteId(id);
      setTimeout(() => setConfirmDeleteId(null), 3000);
    }
  };

  const filtered = filter === "all" ? questions : questions.filter((q) => q.status === filter);

  const statusBadge = (status) => {
    const cls = { open: "status-open", answered: "status-answered", resolved: "status-resolved" }[status] || "";
    return html`<span class="status-badge ${cls}">${status}</span>`;
  };

  return html`
    <div class="content">
      <div class="flex-between" style="margin-bottom:8px">
        <span class="section-title">Questions</span>
        <button class="btn btn-primary btn-sm" onClick=${() => setShowForm(!showForm)}>
          ${showForm ? "Cancel" : "+ Add"}
        </button>
      </div>

      ${showForm && html`
        <div style="margin-bottom:12px">
          <div class="form-group">
            <label>Question</label>
            <textarea class="input" rows="3" value=${content} onInput=${(e) => setContent(e.target.value)}
              placeholder="What needs clarification?"></textarea>
          </div>
          <div class="form-group">
            <label>Context URL (optional)</label>
            <input class="input" value=${contextUrl} onInput=${(e) => setContextUrl(e.target.value)}
              placeholder="https://example.com/page" />
          </div>
          <button class="btn btn-primary btn-full" onClick=${handleAdd} disabled=${!content.trim()}>Add Question</button>
        </div>
      `}

      <div class="filter-pills" style="margin-bottom:8px">
        ${["all", "open", "answered", "resolved"].map((f) => html`
          <button class="pill ${filter === f ? "active" : ""}" onClick=${() => setFilter(f)}>
            ${f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        `)}
      </div>

      ${filtered.length === 0
        ? html`<div class="empty-state">No ${filter === "all" ? "" : filter + " "}questions yet.</div>`
        : filtered.map((q) => html`
          <div class="question-card" key=${q.id}>
            <div class="flex-between" style="margin-bottom:4px">
              ${statusBadge(q.status)}
              <span class="text-muted" style="font-size:10px">${new Date(q.created_at).toLocaleDateString()}</span>
            </div>
            <p style="margin:4px 0;font-weight:500">${q.content}</p>
            ${q.context_url && html`<a href=${q.context_url} target="_blank" class="text-muted" style="font-size:11px">${q.context_url}</a>`}
            ${q.answer && html`<div class="question-answer"><strong>Answer:</strong> ${q.answer}</div>`}

            ${answeringId === q.id ? html`
              <div style="margin-top:6px">
                <textarea class="input" rows="2" value=${answerText} onInput=${(e) => setAnswerText(e.target.value)}
                  placeholder="Type the answer..."></textarea>
                <div class="flex gap-sm" style="margin-top:4px">
                  <button class="btn btn-primary btn-sm" onClick=${() => handleAnswer(q.id)} disabled=${!answerText.trim()}>Save</button>
                  <button class="btn btn-secondary btn-sm" onClick=${() => setAnsweringId(null)}>Cancel</button>
                </div>
              </div>
            ` : html`
              <div class="flex gap-sm" style="margin-top:6px">
                ${q.status === "open" && html`
                  <button class="btn btn-secondary btn-sm" onClick=${() => { setAnsweringId(q.id); setAnswerText(q.answer || ""); }}>Answer</button>
                `}
                ${q.status === "answered" && html`
                  <button class="btn btn-primary btn-sm" onClick=${() => onUpdate(q.id, { status: "resolved" })}>Resolve</button>
                `}
                ${q.status === "resolved" && html`
                  <button class="btn btn-secondary btn-sm" onClick=${() => onUpdate(q.id, { status: "open" })}>Reopen</button>
                `}
                <button class="btn ${confirmDeleteId === q.id ? "btn-danger-confirm" : "btn-danger"} btn-sm"
                  onClick=${() => handleDelete(q.id)}>
                  ${confirmDeleteId === q.id ? "Confirm?" : "Delete"}
                </button>
              </div>
            `}
          </div>
        `)
      }
    </div>
  `;
}


// ── CaptureSessionsList ─────────────────────────────────────────────────────

export function CaptureSessionsList({ sessions, onDownloadHar, onDelete }) {
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const statusColor = { idle: "#9ca3af", capturing: "#22c55e", paused: "#f59e0b", stopped: "#6b7280" };

  const handleDelete = (id) => {
    if (confirmDeleteId === id) {
      onDelete(id);
      setConfirmDeleteId(null);
    } else {
      setConfirmDeleteId(id);
      setTimeout(() => setConfirmDeleteId(null), 3000);
    }
  };

  const formatDuration = (start, stop) => {
    if (!start || !stop) return null;
    const ms = new Date(stop) - new Date(start);
    const secs = Math.floor(ms / 1000);
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    const remSecs = secs % 60;
    return `${mins}m ${remSecs}s`;
  };

  return html`
    <div class="content">
      <span class="section-title">Capture Sessions</span>
      ${(!sessions || sessions.length === 0)
        ? html`<div class="empty-state" style="margin-top:8px">No capture sessions yet.</div>`
        : sessions.map((s) => html`
          <div class="session-card" key=${s.id}>
            <div class="flex-between">
              <div class="flex gap-sm" style="align-items:center">
                <span class="session-status" style="background:${statusColor[s.status] || "#9ca3af"}"></span>
                <strong style="font-size:12px">${s.status.charAt(0).toUpperCase() + s.status.slice(1)}</strong>
              </div>
              <span class="text-muted" style="font-size:10px">${s.started_at ? new Date(s.started_at).toLocaleString() : "Not started"}</span>
            </div>
            <div class="flex gap-sm" style="margin-top:4px;font-size:11px;color:#6b7280">
              ${s.click_tracking && html`<span title="Click tracking">Clicks</span>`}
              ${s.url_monitoring && html`<span title="URL monitoring">URLs</span>`}
              ${s.har_capture && html`<span title="Network capture">Network</span>`}
              ${s.narration && html`<span title="Narration">Narrate</span>`}
              <span>${s.voice_mode}</span>
            </div>
            <div class="flex-between" style="margin-top:6px">
              ${formatDuration(s.started_at, s.stopped_at)
                ? html`<span style="font-size:11px;color:#6b7280">Duration: ${formatDuration(s.started_at, s.stopped_at)}</span>`
                : html`<span></span>`
              }
              <div class="flex gap-sm">
                ${s.har_file_path && onDownloadHar && html`
                  <button class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 8px" onClick=${() => onDownloadHar(s.id)}>
                    Download HAR
                  </button>
                `}
                ${onDelete && s.status !== "capturing" && html`
                  <button class="btn ${confirmDeleteId === s.id ? "btn-danger-confirm" : "btn-danger"} btn-sm" style="font-size:10px;padding:2px 8px"
                    onClick=${() => handleDelete(s.id)}>
                    ${confirmDeleteId === s.id ? "Confirm?" : "Delete"}
                  </button>
                `}
              </div>
            </div>
          </div>
        `)
      }
    </div>
  `;
}
