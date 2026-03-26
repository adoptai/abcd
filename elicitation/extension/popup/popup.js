/**
 * Preact popup app — no build step, vendored lib.
 */
import {
  h,
  html,
  render,
  useState,
  useEffect,
  useRef,
  useCallback,
} from "../lib/htm-preact.mjs";

import {
  MessageList,
  MessageInput,
  ChatSessionBar,
  CaptureSessionControls,
  ScreenshotControls,
  StatusBar,
  PlumbingTestView,
  ToastContainer,
  CapturedItems,
  Timeline,
  DocumentViewer,
  AttachmentsList,
  ImageLightbox,
  QuestionsPanel,
  CaptureSessionsList,
  OptionsView,
  loadSettings,
  saveSettings,
  applyTextSize,
} from "./components.js";
import * as api from "./api.js";

// ── State persistence helpers ──────────────────────────────────────────────

const STATE_KEY = "elicitation_agent_state";

async function saveState(state) {
  try {
    await chrome.storage.local.set({ [STATE_KEY]: state });
  } catch (e) {
    console.warn("Failed to save state:", e);
  }
}

async function loadState() {
  try {
    const result = await chrome.storage.local.get(STATE_KEY);
    return result[STATE_KEY] || null;
  } catch (e) {
    console.warn("Failed to load state:", e);
    return null;
  }
}

// ── App ─────────────────────────────────────────────────────────────────────

function App() {
  const [view, setView] = useState("projects"); // projects | projectDetail | processDetail | test
  const [projects, setProjects] = useState([]);
  const [currentProject, setCurrentProject] = useState(null);
  const [processes, setProcesses] = useState([]);
  const [currentProcess, setCurrentProcess] = useState(null);
  const [messages, setMessages] = useState([]);
  const [captureSession, setCaptureSession] = useState(null);
  const [connected, setConnected] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [showNewProcess, setShowNewProcess] = useState(false);
  const [stateRestored, setStateRestored] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [captures, setCaptures] = useState([]);
  const [timelineEvents, setTimelineEvents] = useState([]);
  const [projectDocs, setProjectDocs] = useState([]);
  const [processDocs, setProcessDocs] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [lightboxUrl, setLightboxUrl] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [captureSessions, setCaptureSessions] = useState([]);
  const [captureStarting, setCaptureStarting] = useState(false);
  const [narrationInterim, setNarrationInterim] = useState("");
  const [chatSessions, setChatSessions] = useState([]);
  const [activeChatSession, setActiveChatSession] = useState(null);
  const [settings, setSettings] = useState({ textSize: "normal" });
  const toastIdRef = useRef(0);
  const abortStreamRef = useRef(null);

  // Toast helpers
  const showToast = useCallback((message, type = "info", duration = 3000) => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, message, type }]);
    if (duration > 0) {
      setTimeout(() => dismissToast(id), duration);
    }
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Persist state on changes
  useEffect(() => {
    if (!stateRestored) return;
    saveState({
      view,
      currentProjectId: currentProject?.id || null,
      currentProcessId: currentProcess?.id || null,
      connected,
    });
  }, [view, currentProject?.id, currentProcess?.id, connected, stateRestored]);

  // Periodic connectivity check
  useEffect(() => {
    const check = () => api.ping().then(() => setConnected(true)).catch(() => setConnected(false));
    check();
    const intervalId = setInterval(check, 15000);
    return () => clearInterval(intervalId);
  }, []);

  // Load settings on mount
  useEffect(() => {
    loadSettings().then((s) => { setSettings(s); applyTextSize(s.textSize); });
  }, []);

  function handleUpdateSettings(newSettings) {
    setSettings(newSettings);
    saveSettings(newSettings);
    applyTextSize(newSettings.textSize);
  }

  // Restore state on mount
  useEffect(() => {
    (async () => {
      const saved = await loadState();
      if (saved) {
        // Restore navigation state
        if (saved.view === "processDetail" && saved.currentProcessId) {
          try {
            await openProcess(saved.currentProcessId, saved.currentProjectId);
            setStateRestored(true);
            return;
          } catch (e) {
            console.warn("Failed to restore process view:", e);
          }
        }
        if (saved.view === "projectDetail" && saved.currentProjectId) {
          try {
            await openProject(saved.currentProjectId);
            setStateRestored(true);
            return;
          } catch (e) {
            console.warn("Failed to restore project view:", e);
          }
        }
        if (saved.view === "test") {
          setView("test");
          setStateRestored(true);
          loadProjects();
          return;
        }
      }

      // Default: load projects
      loadProjects();
      setStateRestored(true);
    })();
  }, []);

  // Listen for storage changes from other views
  useEffect(() => {
    const listener = (changes, area) => {
      if (area === "local" && changes[STATE_KEY]) {
        // Another panel/popup changed state — we could sync here
        // For now, just log it
        console.log("[State Sync] External state change detected");
      }
    };
    chrome.storage.onChanged.addListener(listener);
    return () => chrome.storage.onChanged.removeListener(listener);
  }, []);

  // Auto-refresh timeline during capture (every 3 seconds)
  useEffect(() => {
    if (!captureSession || captureSession.status !== "capturing") return;
    const id = setInterval(() => {
      loadTimeline(currentProject?.id, currentProcess?.id);
    }, 3000);
    return () => clearInterval(id);
  }, [captureSession?.status, currentProject?.id, currentProcess?.id]);

  // Listen for voice events from content script during narration capture
  useEffect(() => {
    if (!captureSession?.narration) {
      setNarrationInterim("");
      return;
    }
    const listener = (message) => {
      if (message.type === "VOICE_INTERIM") {
        setNarrationInterim(message.transcript || "");
      } else if (message.type === "VOICE_FINAL") {
        setNarrationInterim("");
        showToast(`Narration: "${(message.transcript || "").substring(0, 60)}..."`, "success", 2000);
      } else if (message.type === "VOICE_ENDED") {
        setNarrationInterim("");
      }
    };
    chrome.runtime.onMessage.addListener(listener);
    return () => chrome.runtime.onMessage.removeListener(listener);
  }, [captureSession?.narration]);

  async function loadProjects() {
    try {
      const data = await api.listProjects();
      setProjects(data);
    } catch (e) {
      console.error("Failed to load projects:", e);
    }
  }

  async function openProject(id) {
    try {
      const proj = await api.getProject(id);
      setCurrentProject(proj);
      setProcesses(proj.processes || []);
      // Load chat sessions and set active to most recent
      const sessions = await api.listChatSessions({ project_id: id });
      setChatSessions(sessions);
      if (sessions.length > 0) {
        setActiveChatSession(sessions[0]); // sorted by updated_at desc
        const msgs = await api.listMessages({ project_id: id, chat_session_id: sessions[0].id });
        setMessages(msgs);
      } else {
        setActiveChatSession(null);
        setMessages([]);
      }
      loadCaptures(id, null);
      loadTimeline(id, null);
      loadDocuments(id, null);
      loadAttachments(id, null);
      loadQuestions(id, null);
      loadCaptureSessionsList(id, null);
      setView("projectDetail");
    } catch (e) {
      console.error("Failed to open project:", e);
      throw e;
    }
  }

  async function openProcess(id, projectId) {
    try {
      const proc = await api.getProcess(id);
      setCurrentProcess(proc);
      const pId = projectId || currentProject?.id;
      // Also load the parent project if we have the ID
      if (projectId) {
        try {
          const proj = await api.getProject(projectId);
          setCurrentProject(proj);
          setProcesses(proj.processes || []);
        } catch (e) {
          console.warn("Failed to load parent project:", e);
        }
      }
      // Load chat sessions scoped to this process
      const params = { project_id: pId };
      if (id) params.process_id = id;
      const sessions = await api.listChatSessions(params);
      setChatSessions(sessions);
      if (sessions.length > 0) {
        setActiveChatSession(sessions[0]);
        const msgs = await api.listMessages({ project_id: pId, chat_session_id: sessions[0].id });
        setMessages(msgs);
      } else {
        setActiveChatSession(null);
        setMessages([]);
      }
      loadCaptures(pId, id);
      loadTimeline(pId, id);
      loadDocuments(pId, id);
      loadAttachments(pId, id);
      loadQuestions(pId, id);
      loadCaptureSessionsList(pId, id);
      setCaptureSession(null);
      setView("processDetail");
    } catch (e) {
      console.error("Failed to open process:", e);
      throw e;
    }
  }

  async function sendMessage(content, contentType) {
    if (!currentProject && !currentProcess) return;
    const projectId = currentProject?.id;
    const processId = currentProcess?.id;

    // Auto-create a chat session if none exists
    let sessionId = activeChatSession?.id;
    if (!sessionId && projectId) {
      try {
        const newSession = await api.createChatSession({
          project_id: projectId,
          process_id: processId || null,
        });
        setChatSessions((prev) => [newSession, ...prev]);
        setActiveChatSession(newSession);
        sessionId = newSession.id;
      } catch (e) {
        console.warn("Failed to create chat session:", e);
      }
    }

    // Get current URL for tagging
    const url = await getCurrentTabUrl();

    // Optimistic: show human message immediately
    const optimisticId = "__optimistic_" + Date.now();
    const optimisticMsg = {
      id: optimisticId,
      role: "human",
      content,
      content_type: contentType || "text",
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticMsg]);

    // Try streaming chat (with Claude). Falls back to simple message store on error.
    setIsStreaming(true);
    setStreamingContent("");

    const abort = api.chatStream({
      projectId: projectId,
      processId: processId,
      chatSessionId: sessionId,
      content,
      contentType: contentType || "text",
      url,
      onHumanMessage: (msg) => {
        // Replace optimistic message with real one
        setMessages((prev) => prev.map((m) => m.id === optimisticId ? msg : m));
      },
      onDelta: (text) => {
        setStreamingContent((prev) => prev + text);
      },
      onAgentMessage: (msg) => {
        setStreamingContent("");
        setMessages((prev) => [...prev, msg]);
        // Refresh session list to pick up auto-title
        if (sessionId && projectId) {
          api.listChatSessions({ project_id: projectId, ...(processId ? { process_id: processId } : {}) })
            .then((sessions) => {
              setChatSessions(sessions);
              const updated = sessions.find((s) => s.id === sessionId);
              if (updated) setActiveChatSession(updated);
            })
            .catch(() => {});
        }
      },
      onDone: () => {
        setIsStreaming(false);
        setStreamingContent("");
      },
      onError: async (e) => {
        console.warn("Chat stream failed, falling back to simple message:", e);
        setIsStreaming(false);
        setStreamingContent("");
        showToast("Chat stream failed, sending as plain message", "error");
        // Fallback: store message directly
        try {
          const payload = {
            role: "human",
            content,
            content_type: contentType || "text",
          };
          if (processId) payload.process_id = processId;
          else if (projectId) payload.project_id = projectId;
          const msg = await api.createMessage(payload);
          setMessages((prev) => [...prev, msg]);
        } catch (fallbackErr) {
          console.error("Fallback message send also failed:", fallbackErr);
          showToast("Failed to send message", "error");
        }
      },
    });

    abortStreamRef.current = abort;
  }

  async function handleNewChatSession() {
    const projectId = currentProject?.id;
    if (!projectId) return;
    const processId = currentProcess?.id;
    try {
      const newSession = await api.createChatSession({
        project_id: projectId,
        process_id: processId || null,
      });
      setChatSessions((prev) => [newSession, ...prev]);
      setActiveChatSession(newSession);
      setMessages([]);
    } catch (e) {
      showToast("Failed to create chat session: " + e.message, "error");
    }
  }

  async function handleSwitchChatSession(sessionId) {
    const session = chatSessions.find((s) => s.id === sessionId);
    if (!session) return;
    setActiveChatSession(session);
    try {
      const msgs = await api.listMessages({
        project_id: currentProject?.id,
        chat_session_id: sessionId,
      });
      setMessages(msgs);
    } catch (e) {
      showToast("Failed to load messages: " + e.message, "error");
    }
  }

  async function handleDeleteChatSession(sessionId) {
    try {
      await api.deleteChatSession(sessionId);
      const remaining = chatSessions.filter((s) => s.id !== sessionId);
      setChatSessions(remaining);
      if (remaining.length > 0) {
        setActiveChatSession(remaining[0]);
        const msgs = await api.listMessages({
          project_id: currentProject?.id,
          chat_session_id: remaining[0].id,
        });
        setMessages(msgs);
      } else {
        setActiveChatSession(null);
        setMessages([]);
      }
      showToast("Chat deleted", "info");
    } catch (e) {
      showToast("Failed to delete chat: " + e.message, "error");
    }
  }

  async function handleRenameChatSession(sessionId, newTitle) {
    try {
      await api.updateChatSession(sessionId, { title: newTitle });
      const sessions = await api.listChatSessions({
        project_id: currentProject?.id,
        ...(currentProcess?.id ? { process_id: currentProcess.id } : {}),
      });
      setChatSessions(sessions);
      const updated = sessions.find((s) => s.id === sessionId);
      if (updated) setActiveChatSession(updated);
      showToast("Chat renamed", "info");
    } catch (e) {
      showToast("Failed to rename chat: " + e.message, "error");
    }
  }

  async function handleScreenshot() {
    try {
      await api.captureScreenshot(
        currentProject?.id || null,
        currentProcess?.id || null
      );
      showToast("Screenshot captured", "success");
      // Refresh captures list
      loadCaptures(currentProject?.id, currentProcess?.id);
    } catch (e) {
      console.error("Screenshot failed:", e);
      showToast(`Screenshot failed: ${e.message}`, "error");
    }
  }

  async function loadCaptures(projectId, processId) {
    try {
      const params = {};
      if (projectId) params.project_id = projectId;
      if (processId) params.process_id = processId;
      const data = await api.listScreenshots(params);
      setCaptures(data);
    } catch (e) {
      console.warn("Failed to load captures:", e);
    }
  }

  async function loadTimeline(projectId, processId) {
    try {
      const params = { project_id: projectId, limit: 500 };
      if (processId) params.process_id = processId;
      const data = await api.getTimeline(params);
      setTimelineEvents(data);
    } catch (e) {
      console.warn("Failed to load timeline:", e);
    }
  }

  async function handleClearTimeline() {
    try {
      const params = { project_id: currentProject?.id };
      if (currentProcess?.id) params.process_id = currentProcess.id;
      await api.clearTimeline(params);
      setTimelineEvents([]);
      showToast("Timeline cleared", "info");
    } catch (e) {
      showToast("Failed to clear timeline: " + e.message, "error");
    }
  }

  async function handleDownloadTimeline() {
    try {
      const params = { project_id: currentProject?.id };
      if (currentProcess?.id) params.process_id = currentProcess.id;
      const data = await api.downloadTimeline(params);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `timeline-${currentProcess?.name || currentProject?.name || "export"}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showToast("Timeline downloaded", "success");
    } catch (e) {
      showToast("Failed to download timeline: " + e.message, "error");
    }
  }

  async function handleDownloadHar(sessionId) {
    try {
      const data = await api.downloadHar(sessionId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `capture-${sessionId}.har`;
      a.click();
      URL.revokeObjectURL(url);
      showToast("HAR downloaded", "success");
    } catch (e) {
      showToast("Failed to download HAR: " + e.message, "error");
    }
  }

  async function handleDeleteCaptureSession(sessionId) {
    try {
      await api.deleteCaptureSession(sessionId);
      loadCaptureSessionsList(currentProject?.id, currentProcess?.id);
      showToast("Capture session deleted", "success");
    } catch (e) {
      showToast("Failed to delete session: " + e.message, "error");
    }
  }

  // ── Document & Attachment loaders ────────────────────────────────────────

  async function loadDocuments(projectId, processId) {
    try {
      if (processId) {
        const docs = await api.listProcessDocuments(processId);
        setProcessDocs(docs);
      } else if (projectId) {
        const docs = await api.listProjectDocuments(projectId);
        setProjectDocs(docs);
      }
    } catch (e) {
      console.warn("Failed to load documents:", e);
    }
  }

  async function loadAttachments(projectId, processId) {
    try {
      const data = await api.listAttachments(projectId, processId);
      setAttachments(data);
    } catch (e) {
      console.warn("Failed to load attachments:", e);
    }
  }

  async function loadQuestions(projectId, processId) {
    try {
      const params = {};
      if (processId) params.process_id = processId;
      const data = await api.listQuestions(projectId, params);
      setQuestions(data);
    } catch (e) {
      console.warn("Failed to load questions:", e);
    }
  }

  async function loadCaptureSessionsList(projectId, processId) {
    try {
      const data = processId
        ? await api.listCaptureSessions(processId)
        : await api.listProjectCaptureSessions(projectId);
      setCaptureSessions(data);
    } catch (e) {
      console.warn("Failed to load capture sessions:", e);
    }
  }

  // Question CRUD handlers
  async function handleAddQuestion(data) {
    if (!currentProject) return;
    try {
      await api.createQuestion(currentProject.id, { ...data, process_id: currentProcess?.id || data.process_id });
      loadQuestions(currentProject.id, currentProcess?.id);
      showToast("Question added", "success");
    } catch (e) {
      showToast("Failed to add question: " + e.message, "error");
    }
  }

  async function handleUpdateQuestion(id, data) {
    try {
      await api.updateQuestion(id, data);
      loadQuestions(currentProject?.id, currentProcess?.id);
      loadTimeline(currentProject?.id, currentProcess?.id);
    } catch (e) {
      showToast("Failed to update question: " + e.message, "error");
    }
  }

  async function handleDeleteQuestion(id) {
    try {
      await api.deleteQuestion(id);
      loadQuestions(currentProject?.id, currentProcess?.id);
    } catch (e) {
      showToast("Failed to delete question: " + e.message, "error");
    }
  }

  async function handleRegenerateProjectDoc() {
    if (!currentProject) return;
    try {
      await api.generateProjectDoc(currentProject.id);
      showToast("Document generated", "success");
      loadDocuments(currentProject.id, null);
    } catch (e) {
      showToast("Failed to generate: " + e.message, "error");
    }
  }

  async function handleRegenerateProcessDoc() {
    if (!currentProcess) return;
    try {
      await api.generateProcessDoc(currentProcess.id);
      showToast("Document generated", "success");
      loadDocuments(currentProject?.id, currentProcess.id);
    } catch (e) {
      showToast("Failed to generate: " + e.message, "error");
    }
  }

  async function handleSaveDoc(docId, data) {
    try {
      await api.updateDocument(docId, data);
      showToast("Document saved", "success");
      if (currentProcess) loadDocuments(currentProject?.id, currentProcess.id);
      else if (currentProject) loadDocuments(currentProject.id, null);
    } catch (e) {
      showToast("Failed to save: " + e.message, "error");
    }
  }

  async function handleDeleteDoc(docId) {
    try {
      await api.deleteDocument(docId);
      showToast("Document deleted", "info");
      if (currentProcess) loadDocuments(currentProject?.id, currentProcess.id);
      else if (currentProject) loadDocuments(currentProject.id, null);
    } catch (e) {
      showToast("Failed to delete: " + e.message, "error");
    }
  }

  async function handleCreateDoc(data) {
    if (!currentProject) return;
    try {
      await api.createDocument(currentProject.id, data);
      showToast("Document created", "success");
      if (currentProcess) loadDocuments(currentProject.id, currentProcess.id);
      else loadDocuments(currentProject.id, null);
    } catch (e) {
      showToast("Failed to create: " + e.message, "error");
    }
  }

  async function handleUploadAttachment(file) {
    if (!currentProject) return;
    try {
      await api.uploadAttachment(currentProject.id, file, currentProcess?.id || null);
      showToast("File uploaded", "success");
      loadAttachments(currentProject.id, currentProcess?.id);
    } catch (e) {
      showToast("Upload failed: " + e.message, "error");
    }
  }

  async function handleDeleteAttachment(id) {
    try {
      await api.deleteAttachment(id);
      showToast("Attachment deleted", "info");
      loadAttachments(currentProject?.id, currentProcess?.id);
    } catch (e) {
      showToast("Failed to delete: " + e.message, "error");
    }
  }

  // ── Project/Process CRUD handlers ────────────────────────────────────────

  async function handleEditProject(id, data) {
    try {
      await api.updateProject(id, data);
      showToast("Project updated", "success");
      const proj = await api.getProject(id);
      setCurrentProject(proj);
      setProcesses(proj.processes || []);
      loadProjects();
    } catch (e) {
      showToast("Failed to update project: " + e.message, "error");
    }
  }

  async function handleDeleteProject(id) {
    try {
      await api.deleteProject(id);
      showToast("Project deleted", "info");
      setCurrentProject(null);
      setView("projects");
      loadProjects();
    } catch (e) {
      showToast("Failed to delete project: " + e.message, "error");
    }
  }

  async function handleExportProject(id) {
    try {
      const data = await api.exportProject(id);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `project-${data.project?.name || id}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showToast("Export downloaded", "success");
    } catch (e) {
      showToast("Failed to export: " + e.message, "error");
    }
  }

  function handleImportProject() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const bundle = JSON.parse(text);
        if (!bundle.project) throw new Error("Invalid export file — no project data found");
        await api.importProject(bundle);
        showToast("Project imported successfully", "success");
        loadProjects();
      } catch (err) {
        showToast("Import failed: " + err.message, "error");
      }
    };
    input.click();
  }

  async function handleEditProcess(id, data) {
    try {
      await api.updateProcess(id, data);
      showToast("Process updated", "success");
      if (currentProject) {
        const proj = await api.getProject(currentProject.id);
        setCurrentProject(proj);
        setProcesses(proj.processes || []);
      }
      if (currentProcess?.id === id) {
        const proc = await api.getProcess(id);
        setCurrentProcess(proc);
      }
    } catch (e) {
      showToast("Failed to update process: " + e.message, "error");
    }
  }

  async function handleDeleteProcess(id) {
    try {
      await api.deleteProcess(id);
      showToast("Process deleted", "info");
      if (currentProcess?.id === id) {
        setCurrentProcess(null);
        if (currentProject) openProject(currentProject.id);
        else { setView("projects"); loadProjects(); }
      } else if (currentProject) {
        const proj = await api.getProject(currentProject.id);
        setCurrentProject(proj);
        setProcesses(proj.processes || []);
      }
    } catch (e) {
      showToast("Failed to delete process: " + e.message, "error");
    }
  }

  // ── Capture Session handlers ────────────────────────────────────────────

  async function handleStartCapture() {
    if (!currentProcess) return;
    if (captureStarting) return;
    if (captureSession && captureSession.status === "capturing") return;
    setCaptureStarting(true);
    try {
      // Resolve the target tab ID HERE (popup/sidepanel has reliable window context)
      // The service worker does NOT have a window context and gets the wrong tab.
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const targetTabId = activeTab?.id;
      if (!targetTabId) throw new Error("No active tab found");

      const session = await api.createCaptureSession(currentProcess.id, {
        click_tracking: true,
        url_monitoring: true,
        har_capture: true,
        voice_mode: "narrate",
        narration: false,
      });
      const started = await api.startCaptureSession(session.id);
      setCaptureSession(started);

      // Helper: send message and await response
      const sendMsg = (msg) => new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(msg, (resp) => {
          if (chrome.runtime.lastError) { reject(new Error(chrome.runtime.lastError.message)); return; }
          if (resp?.success) resolve(resp.data);
          else reject(new Error(resp?.error || "Unknown error"));
        });
      });

      // Set capture state so click events get tagged with IDs
      await sendMsg({
        type: "SET_CAPTURE_STATE",
        projectId: currentProject?.id,
        processId: currentProcess.id,
        captureSessionId: session.id,
      });
      // Inject click tracker — pass explicit tabId
      await sendMsg({ type: "INJECT_CLICK_TRACKER", tabId: targetTabId });
      // Start URL monitoring
      await sendMsg({
        type: "START_URL_MONITORING",
        projectId: currentProject?.id,
        processId: currentProcess.id,
        captureSessionId: session.id,
      });
      // Start HAR capture — pass explicit tabId
      if (session.har_capture) {
        const harResult = await sendMsg({
          type: "START_HAR_CAPTURE",
          captureSessionId: session.id,
          tabId: targetTabId,
        });
        console.log("[Capture] HAR started on tab:", harResult?.tabId);
      }
      // Auto-start narration capture if enabled
      if (session.narration) {
        sendMsg({ type: "START_NARRATION_CAPTURE", tabId: targetTabId }).catch((e) =>
          console.warn("Narration auto-start failed:", e.message)
        );
      }
      showToast("Capture started", "success");
      loadTimeline(currentProject?.id, currentProcess?.id);
      loadCaptureSessionsList(currentProject?.id, currentProcess?.id);
    } catch (e) {
      console.error("Failed to start capture:", e);
      showToast("Failed to start capture: " + e.message, "error");
    } finally {
      setCaptureStarting(false);
    }
  }

  async function handleStopCapture(id) {
    try {
      const sendMsg = (msg) => new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(msg, (resp) => {
          if (chrome.runtime.lastError) { reject(new Error(chrome.runtime.lastError.message)); return; }
          if (resp?.success) resolve(resp.data);
          else reject(new Error(resp?.error || "Unknown error"));
        });
      });

      // Stop HAR capture first (uploads HAR file to backend) — MUST complete before stopping session
      try {
        const harResult = await sendMsg({ type: "STOP_HAR_CAPTURE", captureSessionId: id });
        console.log("[Capture] HAR stopped, entries:", harResult?.entries);
      } catch (e) {
        console.warn("HAR stop failed:", e.message);
      }

      await api.stopCaptureSession(id);
      setCaptureSession(null); // session is now historical — clear active state

      // Clean up — fire-and-forget is fine for these
      sendMsg({ type: "REMOVE_CLICK_TRACKER" }).catch(() => {});
      sendMsg({ type: "CLEAR_CAPTURE_STATE" }).catch(() => {});
      sendMsg({ type: "STOP_URL_MONITORING" }).catch(() => {});
      sendMsg({ type: "STOP_NARRATION_CAPTURE" }).catch(() => {});

      showToast("Capture stopped", "info");
      const projId = currentProject?.id;
      const procId = currentProcess?.id;
      loadTimeline(projId, procId);
      await loadCaptureSessionsList(projId, procId);
      // Safety-net: refresh again after a short delay to catch any
      // edge-case where the HAR file path hasn't propagated yet
      setTimeout(() => loadCaptureSessionsList(projId, procId), 1000);
    } catch (e) {
      console.error("Failed to stop capture:", e);
      showToast("Failed to stop capture: " + e.message, "error");
    }
  }

  async function handleToggleCaptureFeature(id, field, value) {
    try {
      const updated = await api.patchCaptureSessionSettings(id, { [field]: value });
      setCaptureSession(updated);
      // Toggle click tracker if needed
      if (field === "click_tracking") {
        chrome.runtime.sendMessage({ type: value ? "INJECT_CLICK_TRACKER" : "REMOVE_CLICK_TRACKER" });
      }
      if (field === "url_monitoring") {
        if (value) {
          chrome.runtime.sendMessage({
            type: "START_URL_MONITORING",
            projectId: currentProject?.id,
            processId: currentProcess?.id,
            captureSessionId: id,
          });
        } else {
          chrome.runtime.sendMessage({ type: "STOP_URL_MONITORING" });
        }
      }
      if (field === "har_capture") {
        if (value) {
          const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
          chrome.runtime.sendMessage({ type: "START_HAR_CAPTURE", captureSessionId: id, tabId: activeTab?.id });
        } else {
          chrome.runtime.sendMessage({ type: "STOP_HAR_CAPTURE", captureSessionId: id });
        }
      }
      if (field === "narration") {
        if (value) {
          const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
          chrome.runtime.sendMessage({ type: "START_NARRATION_CAPTURE", tabId: activeTab?.id });
        } else {
          chrome.runtime.sendMessage({ type: "STOP_NARRATION_CAPTURE" });
        }
      }
    } catch (e) {
      console.error("Failed to toggle feature:", e);
    }
  }

  // ── Voice & narration handlers ──────────────────────────────────────────

  async function getCurrentTabUrl() {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "GET_CURRENT_URL" }, (response) => {
        if (chrome.runtime.lastError || !response?.success) {
          resolve("");
          return;
        }
        resolve(response.data?.url || "");
      });
    });
  }

  // ── Region screenshot handler ──────────────────────────────────────────

  async function handleRegionScreenshot() {
    try {
      showToast("Select a region on the page...", "info", 5000);
      const result = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          {
            type: "CAPTURE_REGION_SCREENSHOT",
            projectId: currentProject?.id || null,
            processId: currentProcess?.id || null,
          },
          (response) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
              return;
            }
            if (response && response.success) resolve(response.data);
            else reject(new Error(response?.error || "Region capture failed"));
          }
        );
      });
      showToast("Region screenshot captured", "success");
      loadCaptures(currentProject?.id, currentProcess?.id);
    } catch (e) {
      if (e.message !== "Region selection cancelled") {
        console.error("Region screenshot failed:", e);
        showToast("Region screenshot failed: " + e.message, "error");
      }
    }
  }

  function goBack() {
    if (view === "test" || view === "options") {
      setView("projects");
      loadProjects();
    } else if (view === "processDetail") {
      setCurrentProcess(null);
      if (currentProject) openProject(currentProject.id);
      else setView("projects");
    } else if (view === "projectDetail") {
      setCurrentProject(null);
      setView("projects");
      loadProjects();
    }
  }

  // Context string for status bar
  const contextStr =
    view === "processDetail" && currentProcess
      ? `${currentProject?.name} / ${currentProcess.name}`
      : view === "projectDetail" && currentProject
        ? currentProject.name
        : view === "test"
          ? "Plumbing Tests"
          : "";

  // ── Render ──────────────────────────────────────────────────────────────

  return html`
    <div class="app">
      <div class="header">
        ${view !== "projects" &&
        html`<button class="back-btn" onClick=${goBack}>\u2190</button>`}
        <img src="../icons/icon48.png" alt="logo" />
        <h1>
          ${view === "projects"
            ? "Projects"
            : view === "projectDetail"
              ? `Project: ${currentProject?.name || "..."}`
              : view === "test"
                ? "Plumbing Tests"
                : view === "options"
                  ? "Options"
                  : `Process: ${currentProcess?.name || "..."}`}
        </h1>
        ${(view === "projectDetail" || view === "processDetail") &&
        html`<${ScreenshotControls}
          onCapture=${handleScreenshot}
          onRegionCapture=${handleRegionScreenshot}
        />`}
        ${view !== "options" && html`
          <button class="icon-btn" style="width:28px;height:28px;font-size:14px" title="Options"
            onClick=${() => setView("options")}>\u2699</button>
        `}
      </div>

      ${view === "projects" && html`<${ProjectListView}
        projects=${projects}
        onOpen=${openProject}
        onOpenTest=${() => setView("test")}
        showNew=${showNewProject}
        onToggleNew=${() => setShowNewProject(!showNewProject)}
        onCreated=${() => { setShowNewProject(false); loadProjects(); }}
        onEditProject=${handleEditProject}
        onDeleteProject=${handleDeleteProject}
        onExportProject=${handleExportProject}
        onImportProject=${handleImportProject}
        getCurrentTabUrl=${getCurrentTabUrl}
      />`}

      ${view === "projectDetail" && html`<${ProjectDetailView}
        project=${currentProject}
        processes=${processes}
        messages=${messages}
        captures=${captures}
        timelineEvents=${timelineEvents}
        streamingContent=${streamingContent}
        isStreaming=${isStreaming}
        onOpenProcess=${openProcess}
        onSendMessage=${sendMessage}
        onScreenshot=${handleScreenshot}
        showNewProcess=${showNewProcess}
        onToggleNewProcess=${() => setShowNewProcess(!showNewProcess)}
        onProcessCreated=${() => { setShowNewProcess(false); openProject(currentProject.id); }}
        projectDocs=${projectDocs}
        attachments=${attachments}
        onRegenerateDoc=${handleRegenerateProjectDoc}
        onSaveDoc=${handleSaveDoc}
        onDeleteDoc=${handleDeleteDoc}
        onCreateDoc=${handleCreateDoc}
        onUploadAttachment=${handleUploadAttachment}
        onDeleteAttachment=${handleDeleteAttachment}
        showToast=${showToast}
        onEditProject=${handleEditProject}
        onDeleteProject=${handleDeleteProject}
        onExportProject=${handleExportProject}
        onEditProcess=${handleEditProcess}
        onDeleteProcess=${handleDeleteProcess}
        getCurrentTabUrl=${getCurrentTabUrl}
        onImageClick=${setLightboxUrl}
        onDownloadTimeline=${handleDownloadTimeline}
        onClearTimeline=${handleClearTimeline}
        questions=${questions}
        captureSessions=${captureSessions}
        onAddQuestion=${handleAddQuestion}
        onUpdateQuestion=${handleUpdateQuestion}
        onDeleteQuestion=${handleDeleteQuestion}
        onDownloadHar=${handleDownloadHar}
        onDeleteCaptureSession=${handleDeleteCaptureSession}
        chatSessions=${chatSessions}
        activeChatSession=${activeChatSession}
        onNewChatSession=${handleNewChatSession}
        onSwitchChatSession=${handleSwitchChatSession}
        onDeleteChatSession=${handleDeleteChatSession}
        onRenameChatSession=${handleRenameChatSession}
      />`}

      ${view === "processDetail" && html`<${ProcessDetailView}
        process=${currentProcess}
        messages=${messages}
        captures=${captures}
        timelineEvents=${timelineEvents}
        streamingContent=${streamingContent}
        isStreaming=${isStreaming}
        captureSession=${captureSession}
        narrationInterim=${narrationInterim}
        onSendMessage=${sendMessage}
        onScreenshot=${handleScreenshot}
        onStartCapture=${handleStartCapture}
        captureStarting=${captureStarting}
        onStopCapture=${handleStopCapture}
        onToggleCaptureFeature=${handleToggleCaptureFeature}
        processDocs=${processDocs}
        attachments=${attachments}
        onRegenerateDoc=${handleRegenerateProcessDoc}
        onSaveDoc=${handleSaveDoc}
        onDeleteDoc=${handleDeleteDoc}
        onCreateDoc=${handleCreateDoc}
        onUploadAttachment=${handleUploadAttachment}
        onDeleteAttachment=${handleDeleteAttachment}
        showToast=${showToast}
        onEditProcess=${handleEditProcess}
        onDeleteProcess=${handleDeleteProcess}
        onImageClick=${setLightboxUrl}
        onDownloadTimeline=${handleDownloadTimeline}
        onClearTimeline=${handleClearTimeline}
        questions=${questions}
        captureSessions=${captureSessions}
        onAddQuestion=${handleAddQuestion}
        onUpdateQuestion=${handleUpdateQuestion}
        onDeleteQuestion=${handleDeleteQuestion}
        onDownloadHar=${handleDownloadHar}
        onDeleteCaptureSession=${handleDeleteCaptureSession}
        chatSessions=${chatSessions}
        activeChatSession=${activeChatSession}
        onNewChatSession=${handleNewChatSession}
        onSwitchChatSession=${handleSwitchChatSession}
        onDeleteChatSession=${handleDeleteChatSession}
        onRenameChatSession=${handleRenameChatSession}
      />`}

      ${view === "test" && html`<${PlumbingTestView} connected=${connected} />`}

      ${view === "options" && html`<${OptionsView}
        settings=${settings}
        onUpdate=${handleUpdateSettings}
      />`}

      <${StatusBar} connected=${connected} context=${contextStr} />
      <${ToastContainer} toasts=${toasts} onDismiss=${dismissToast} />
      <${ImageLightbox} url=${lightboxUrl} onClose=${() => setLightboxUrl(null)} />
    </div>
  `;
}

// ── Views ───────────────────────────────────────────────────────────────────

function ProjectListView({ projects, onOpen, onOpenTest, showNew, onToggleNew, onCreated, onEditProject, onDeleteProject, onExportProject, onImportProject, getCurrentTabUrl }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editBaseUrl, setEditBaseUrl] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  // Auto-fill base_url when opening the new project form
  useEffect(() => {
    if (showNew && !baseUrl) {
      getCurrentTabUrl?.().then((url) => {
        if (url) {
          try { setBaseUrl(new URL(url).origin); } catch { setBaseUrl(url); }
        }
      });
    }
  }, [showNew]);

  const handleCreate = async () => {
    if (!name.trim()) return;
    try {
      await api.createProject({ name: name.trim(), description: desc.trim(), base_url: baseUrl.trim() });
      setName("");
      setDesc("");
      setBaseUrl("");
      onCreated();
    } catch (e) {
      console.error("Failed to create project:", e);
    }
  };

  const startEdit = (e, p) => {
    e.stopPropagation();
    setEditingId(p.id);
    setEditName(p.name);
    setEditDesc(p.description || "");
    setEditBaseUrl(p.base_url || "");
  };

  const saveEdit = async (e) => {
    e.stopPropagation();
    await onEditProject(editingId, { name: editName.trim(), description: editDesc.trim(), base_url: editBaseUrl.trim() });
    setEditingId(null);
  };

  const handleDelete = (e, id) => {
    e.stopPropagation();
    if (confirmDeleteId === id) {
      onDeleteProject(id);
      setConfirmDeleteId(null);
    } else {
      setConfirmDeleteId(id);
      setTimeout(() => setConfirmDeleteId(null), 3000);
    }
  };

  const handleExport = (e, id) => {
    e.stopPropagation();
    onExportProject(id);
  };

  return html`
    <div class="content">
      <div class="flex-between" style="margin-bottom:12px">
        <span class="section-title">Your Projects</span>
        <div class="flex gap-sm">
          <button class="btn btn-secondary btn-sm" onClick=${onImportProject}>Import</button>
          <button class="btn btn-secondary btn-sm" onClick=${onOpenTest}>Test</button>
          <button class="btn btn-primary btn-sm" onClick=${onToggleNew}>
            ${showNew ? "Cancel" : "+ New"}
          </button>
        </div>
      </div>

      ${showNew && html`
        <div style="margin-bottom:12px">
          <div class="form-group">
            <label>Name</label>
            <input class="input" value=${name} onInput=${(e) => setName(e.target.value)} placeholder="Project name" />
          </div>
          <div class="form-group">
            <label>Description</label>
            <input class="input" value=${desc} onInput=${(e) => setDesc(e.target.value)} placeholder="Optional description" />
          </div>
          <div class="form-group">
            <label>Base URL</label>
            <input class="input" value=${baseUrl} onInput=${(e) => setBaseUrl(e.target.value)} placeholder="https://example.com" />
          </div>
          <button class="btn btn-primary btn-full" onClick=${handleCreate} disabled=${!name.trim()}>Create Project</button>
        </div>
      `}

      ${projects.length === 0
        ? html`<div class="empty-state">No projects yet. Create one to get started.</div>`
        : projects.map(
            (p) => editingId === p.id
              ? html`
                <div class="card" key=${p.id} onClick=${(e) => e.stopPropagation()}>
                  <div class="form-group">
                    <label>Name</label>
                    <input class="input" value=${editName} onInput=${(e) => setEditName(e.target.value)} />
                  </div>
                  <div class="form-group">
                    <label>Description</label>
                    <input class="input" value=${editDesc} onInput=${(e) => setEditDesc(e.target.value)} />
                  </div>
                  <div class="form-group">
                    <label>Base URL</label>
                    <input class="input" value=${editBaseUrl} onInput=${(e) => setEditBaseUrl(e.target.value)} />
                  </div>
                  <div class="flex gap-sm">
                    <button class="btn btn-primary btn-sm" onClick=${saveEdit}>Save</button>
                    <button class="btn btn-secondary btn-sm" onClick=${(e) => { e.stopPropagation(); setEditingId(null); }}>Cancel</button>
                  </div>
                </div>
              `
              : html`
                <div class="card" key=${p.id} onClick=${() => onOpen(p.id)}>
                  <div class="flex-between">
                    <h3>${p.name}</h3>
                    <div class="card-actions flex gap-sm" onClick=${(e) => e.stopPropagation()}>
                      <button class="btn btn-secondary btn-sm" onClick=${(e) => startEdit(e, p)} title="Edit">Edit</button>
                      <button class="btn btn-secondary btn-sm" onClick=${(e) => handleExport(e, p.id)} title="Export">Export</button>
                      <button class="btn ${confirmDeleteId === p.id ? "btn-danger-confirm" : "btn-danger"} btn-sm" onClick=${(e) => handleDelete(e, p.id)}>
                        ${confirmDeleteId === p.id ? "Confirm?" : "Delete"}
                      </button>
                    </div>
                  </div>
                  ${p.description && html`<p>${p.description}</p>`}
                  ${p.base_url && html`<p style="font-size:11px;color:#2563eb">${p.base_url}</p>`}
                  <p>${p.status} \u2022 ${new Date(p.created_at).toLocaleDateString()}</p>
                </div>
              `
          )}
    </div>
  `;
}

function ProjectDetailView({
  project,
  processes,
  messages,
  captures,
  timelineEvents,
  streamingContent,
  isStreaming,
  onOpenProcess,
  onSendMessage,
  onScreenshot,
  showNewProcess,
  onToggleNewProcess,
  onProcessCreated,
  projectDocs,
  attachments,
  onRegenerateDoc,
  onSaveDoc,
  onDeleteDoc,
  onCreateDoc,
  onUploadAttachment,
  onDeleteAttachment,
  showToast,
  onEditProject,
  onDeleteProject,
  onExportProject,
  onEditProcess,
  onDeleteProcess,
  getCurrentTabUrl,
  onImageClick,
  onDownloadTimeline,
  onClearTimeline,
  questions,
  captureSessions,
  onAddQuestion,
  onUpdateQuestion,
  onDeleteQuestion,
  onDownloadHar,
  onDeleteCaptureSession,
  chatSessions,
  activeChatSession,
  onNewChatSession,
  onSwitchChatSession,
  onDeleteChatSession,
  onRenameChatSession,
}) {
  const [tab, setTab] = useState("chat");
  const [procName, setProcName] = useState("");
  const [procDesc, setProcDesc] = useState("");
  const [procBaseUrl, setProcBaseUrl] = useState("");
  const [editingProject, setEditingProject] = useState(false);
  const [editProjName, setEditProjName] = useState("");
  const [editProjDesc, setEditProjDesc] = useState("");
  const [editProjBaseUrl, setEditProjBaseUrl] = useState("");
  const [editingProcessId, setEditingProcessId] = useState(null);
  const [editProcName, setEditProcName] = useState("");
  const [editProcDesc, setEditProcDesc] = useState("");
  const [editProcBaseUrl, setEditProcBaseUrl] = useState("");
  const [confirmDeleteProcId, setConfirmDeleteProcId] = useState(null);

  // Auto-fill base_url when creating new process
  useEffect(() => {
    if (showNewProcess && !procBaseUrl) {
      getCurrentTabUrl?.().then((url) => {
        if (url) {
          try { setProcBaseUrl(new URL(url).origin); } catch { setProcBaseUrl(url); }
        }
      });
    }
  }, [showNewProcess]);

  const handleCreateProcess = async () => {
    if (!procName.trim()) return;
    try {
      await api.createProcess(project.id, { name: procName.trim(), description: procDesc.trim(), base_url: procBaseUrl.trim() });
      setProcName("");
      setProcDesc("");
      setProcBaseUrl("");
      onProcessCreated();
    } catch (e) {
      console.error("Failed to create process:", e);
    }
  };

  const startEditProject = () => {
    setEditingProject(true);
    setEditProjName(project.name);
    setEditProjDesc(project.description || "");
    setEditProjBaseUrl(project.base_url || "");
  };

  const saveEditProject = async () => {
    await onEditProject(project.id, { name: editProjName.trim(), description: editProjDesc.trim(), base_url: editProjBaseUrl.trim() });
    setEditingProject(false);
  };

  const startEditProcess = (e, p) => {
    e.stopPropagation();
    setEditingProcessId(p.id);
    setEditProcName(p.name);
    setEditProcDesc(p.description || "");
    setEditProcBaseUrl(p.base_url || "");
  };

  const saveEditProcess = async (e) => {
    e.stopPropagation();
    await onEditProcess(editingProcessId, { name: editProcName.trim(), description: editProcDesc.trim(), base_url: editProcBaseUrl.trim() });
    setEditingProcessId(null);
  };

  const handleDeleteProc = (e, id) => {
    e.stopPropagation();
    if (confirmDeleteProcId === id) {
      onDeleteProcess(id);
      setConfirmDeleteProcId(null);
    } else {
      setConfirmDeleteProcId(id);
      setTimeout(() => setConfirmDeleteProcId(null), 3000);
    }
  };

  const canonicalDoc = projectDocs?.find((d) => d.is_canonical);
  const userDocs = projectDocs?.filter((d) => !d.is_canonical) || [];

  return html`
    <div class="tabs">
      <button class="tab ${tab === "chat" ? "active" : ""}" onClick=${() => setTab("chat")}>Chat</button>
      <button class="tab ${tab === "processes" ? "active" : ""}" onClick=${() => setTab("processes")}>Processes</button>
      <button class="tab ${tab === "captures" ? "active" : ""}" onClick=${() => setTab("captures")}>Captures</button>
      <button class="tab ${tab === "assets" ? "active" : ""}" onClick=${() => setTab("assets")}>Assets</button>
      <button class="tab ${tab === "questions" ? "active" : ""}" onClick=${() => setTab("questions")}>Questions</button>
      <button class="tab ${tab === "timeline" ? "active" : ""}" onClick=${() => setTab("timeline")}>Timeline</button>
    </div>

    ${tab === "chat" && html`
      <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
        ${editingProject ? html`
          <div style="padding:8px 16px;background:#fffbeb;border-bottom:1px solid #fde68a">
            <div class="form-group">
              <label>Name</label>
              <input class="input" value=${editProjName} onInput=${(e) => setEditProjName(e.target.value)} />
            </div>
            <div class="form-group">
              <label>Description</label>
              <input class="input" value=${editProjDesc} onInput=${(e) => setEditProjDesc(e.target.value)} />
            </div>
            <div class="form-group">
              <label>Base URL</label>
              <input class="input" value=${editProjBaseUrl} onInput=${(e) => setEditProjBaseUrl(e.target.value)} />
            </div>
            <div class="flex gap-sm">
              <button class="btn btn-primary btn-sm" onClick=${saveEditProject}>Save</button>
              <button class="btn btn-secondary btn-sm" onClick=${() => setEditingProject(false)}>Cancel</button>
            </div>
          </div>
        ` : html`
          <div class="flex-between" style="padding:4px 16px;background:#eff6ff;border-bottom:1px solid #e5e7eb">
            <span class="text-muted" style="font-size:11px">${project?.base_url || "No base URL"}</span>
            <div class="flex gap-sm">
              <button class="btn btn-secondary btn-sm" style="padding:2px 8px;font-size:11px" onClick=${startEditProject}>Edit</button>
              <button class="btn btn-secondary btn-sm" style="padding:2px 8px;font-size:11px" onClick=${() => onExportProject(project.id)}>Export</button>
            </div>
          </div>
        `}
        <${ChatSessionBar}
          sessions=${chatSessions}
          activeSession=${activeChatSession}
          onNew=${onNewChatSession}
          onSwitch=${onSwitchChatSession}
          onDelete=${onDeleteChatSession}
          onRename=${onRenameChatSession}
        />
        <div class="content" style="flex:1;overflow-y:auto">
          <${MessageList} messages=${messages} streamingContent=${streamingContent} />
        </div>
        <${MessageInput}
          onSend=${onSendMessage}
          disabled=${isStreaming}
        />
      </div>
    `}

    ${tab === "processes" && html`
      <div class="content">
        <div class="flex-between" style="margin-bottom:12px">
          <span class="section-title">Processes</span>
          <button class="btn btn-primary btn-sm" onClick=${onToggleNewProcess}>
            ${showNewProcess ? "Cancel" : "+ New"}
          </button>
        </div>

        ${showNewProcess && html`
          <div style="margin-bottom:12px">
            <div class="form-group">
              <label>Name</label>
              <input class="input" value=${procName} onInput=${(e) => setProcName(e.target.value)} placeholder="Process name" />
            </div>
            <div class="form-group">
              <label>Description</label>
              <input class="input" value=${procDesc} onInput=${(e) => setProcDesc(e.target.value)} placeholder="Optional description" />
            </div>
            <div class="form-group">
              <label>Base URL</label>
              <input class="input" value=${procBaseUrl} onInput=${(e) => setProcBaseUrl(e.target.value)} placeholder="https://example.com/app" />
            </div>
            <button class="btn btn-primary btn-full" onClick=${handleCreateProcess} disabled=${!procName.trim()}>Create Process</button>
          </div>
        `}

        ${processes.length === 0
          ? html`<div class="empty-state">No processes yet.</div>`
          : processes.map(
              (p) => editingProcessId === p.id
                ? html`
                  <div class="card" key=${p.id} onClick=${(e) => e.stopPropagation()}>
                    <div class="form-group">
                      <label>Name</label>
                      <input class="input" value=${editProcName} onInput=${(e) => setEditProcName(e.target.value)} />
                    </div>
                    <div class="form-group">
                      <label>Description</label>
                      <input class="input" value=${editProcDesc} onInput=${(e) => setEditProcDesc(e.target.value)} />
                    </div>
                    <div class="form-group">
                      <label>Base URL</label>
                      <input class="input" value=${editProcBaseUrl} onInput=${(e) => setEditProcBaseUrl(e.target.value)} />
                    </div>
                    <div class="flex gap-sm">
                      <button class="btn btn-primary btn-sm" onClick=${saveEditProcess}>Save</button>
                      <button class="btn btn-secondary btn-sm" onClick=${(e) => { e.stopPropagation(); setEditingProcessId(null); }}>Cancel</button>
                    </div>
                  </div>
                `
                : html`
                  <div class="card" key=${p.id} onClick=${() => onOpenProcess(p.id)}>
                    <div class="flex-between">
                      <h3>${p.name}</h3>
                      <div class="card-actions flex gap-sm" onClick=${(e) => e.stopPropagation()}>
                        <button class="btn btn-secondary btn-sm" onClick=${(e) => startEditProcess(e, p)}>Edit</button>
                        <button class="btn ${confirmDeleteProcId === p.id ? "btn-danger-confirm" : "btn-danger"} btn-sm" onClick=${(e) => handleDeleteProc(e, p.id)}>
                          ${confirmDeleteProcId === p.id ? "Confirm?" : "Delete"}
                        </button>
                      </div>
                    </div>
                    ${p.description && html`<p>${p.description}</p>`}
                    ${p.base_url && html`<p style="font-size:11px;color:#2563eb">${p.base_url}</p>`}
                    <p>${p.status}</p>
                  </div>
                `
            )}
      </div>
    `}

    ${tab === "captures" && html`
      <${CaptureSessionsList} sessions=${captureSessions} onDownloadHar=${onDownloadHar} onDelete=${onDeleteCaptureSession} />
    `}

    ${tab === "assets" && html`
      <div class="content">
        <${CapturedItems} items=${captures} onCapture=${onScreenshot} onImageClick=${onImageClick} />
        <${DocumentViewer}
          projectId=${project?.id}
          processId=${null}
          canonicalDoc=${canonicalDoc}
          userDocs=${userDocs}
          onRegenerate=${onRegenerateDoc}
          onSaveDoc=${onSaveDoc}
          onDeleteDoc=${onDeleteDoc}
          onCreateDoc=${onCreateDoc}
          showToast=${showToast}
        />
        <${AttachmentsList}
          attachments=${attachments}
          onUpload=${onUploadAttachment}
          onDelete=${onDeleteAttachment}
          onImageClick=${onImageClick}
        />
      </div>
    `}

    ${tab === "questions" && html`
      <${QuestionsPanel}
        questions=${questions}
        onAdd=${onAddQuestion}
        onUpdate=${onUpdateQuestion}
        onDelete=${onDeleteQuestion}
      />
    `}

    ${tab === "timeline" && html`
      <${Timeline} events=${timelineEvents} onDownload=${onDownloadTimeline} onClear=${onClearTimeline} />
    `}
  `;
}

function ProcessDetailView({
  process,
  messages,
  captures,
  timelineEvents,
  streamingContent,
  isStreaming,
  captureSession,
  narrationInterim,
  onSendMessage,
  onScreenshot,
  onStartCapture,
  captureStarting,
  onStopCapture,
  onToggleCaptureFeature,
  processDocs,
  attachments,
  onRegenerateDoc,
  onSaveDoc,
  onDeleteDoc,
  onCreateDoc,
  onUploadAttachment,
  onDeleteAttachment,
  showToast,
  onEditProcess,
  onDeleteProcess,
  onImageClick,
  onDownloadTimeline,
  onClearTimeline,
  questions,
  captureSessions,
  onAddQuestion,
  onUpdateQuestion,
  onDeleteQuestion,
  onDownloadHar,
  onDeleteCaptureSession,
  chatSessions,
  activeChatSession,
  onNewChatSession,
  onSwitchChatSession,
  onDeleteChatSession,
  onRenameChatSession,
}) {
  const [tab, setTab] = useState("chat");
  const [editingProcess, setEditingProcess] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editBaseUrl, setEditBaseUrl] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const startEdit = () => {
    setEditingProcess(true);
    setEditName(process.name);
    setEditDesc(process.description || "");
    setEditBaseUrl(process.base_url || "");
  };

  const saveEdit = async () => {
    await onEditProcess(process.id, { name: editName.trim(), description: editDesc.trim(), base_url: editBaseUrl.trim() });
    setEditingProcess(false);
  };

  const handleDelete = () => {
    if (confirmDelete) {
      onDeleteProcess(process.id);
      setConfirmDelete(false);
    } else {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 3000);
    }
  };

  const canonicalDoc = processDocs?.find((d) => d.is_canonical);
  const userDocs = processDocs?.filter((d) => !d.is_canonical) || [];

  return html`
    <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:8px 16px;background:#fff;border-bottom:1px solid #e5e7eb">
        <${CaptureSessionControls}
          session=${captureSession}
          onStart=${onStartCapture}
          starting=${captureStarting}
          onStop=${onStopCapture}
          onToggleFeature=${onToggleCaptureFeature}
        />
        ${narrationInterim && html`
          <div class="narration-interim">${narrationInterim}...</div>
        `}
      </div>
      ${editingProcess ? html`
        <div style="padding:8px 16px;background:#fffbeb;border-bottom:1px solid #fde68a">
          <div class="form-group">
            <label>Name</label>
            <input class="input" value=${editName} onInput=${(e) => setEditName(e.target.value)} />
          </div>
          <div class="form-group">
            <label>Description</label>
            <input class="input" value=${editDesc} onInput=${(e) => setEditDesc(e.target.value)} />
          </div>
          <div class="form-group">
            <label>Base URL</label>
            <input class="input" value=${editBaseUrl} onInput=${(e) => setEditBaseUrl(e.target.value)} />
          </div>
          <div class="flex gap-sm">
            <button class="btn btn-primary btn-sm" onClick=${saveEdit}>Save</button>
            <button class="btn btn-secondary btn-sm" onClick=${() => setEditingProcess(false)}>Cancel</button>
          </div>
        </div>
      ` : html`
        <div class="flex-between" style="padding:4px 16px;background:#eff6ff;border-bottom:1px solid #e5e7eb">
          <span class="text-muted" style="font-size:11px">${process?.base_url || "No base URL"}</span>
          <div class="flex gap-sm">
            <button class="btn btn-secondary btn-sm" style="padding:2px 8px;font-size:11px" onClick=${startEdit}>Edit</button>
            <button class="btn ${confirmDelete ? "btn-danger-confirm" : "btn-danger"} btn-sm" style="padding:2px 8px;font-size:11px" onClick=${handleDelete}>
              ${confirmDelete ? "Confirm?" : "Delete"}
            </button>
          </div>
        </div>
      `}
      <div class="tabs">
        <button class="tab ${tab === "chat" ? "active" : ""}" onClick=${() => setTab("chat")}>Chat</button>
        <button class="tab ${tab === "captures" ? "active" : ""}" onClick=${() => setTab("captures")}>Captures</button>
        <button class="tab ${tab === "assets" ? "active" : ""}" onClick=${() => setTab("assets")}>Assets</button>
        <button class="tab ${tab === "questions" ? "active" : ""}" onClick=${() => setTab("questions")}>Questions</button>
        <button class="tab ${tab === "timeline" ? "active" : ""}" onClick=${() => setTab("timeline")}>Timeline</button>
      </div>
      ${tab === "chat" && html`
        <${ChatSessionBar}
          sessions=${chatSessions}
          activeSession=${activeChatSession}
          onNew=${onNewChatSession}
          onSwitch=${onSwitchChatSession}
          onDelete=${onDeleteChatSession}
          onRename=${onRenameChatSession}
        />
        <div class="content" style="flex:1;overflow-y:auto">
          <${MessageList} messages=${messages} streamingContent=${streamingContent} />
        </div>
        <${MessageInput}
          onSend=${onSendMessage}
          disabled=${isStreaming}
        />
      `}
      ${tab === "captures" && html`
        <${CaptureSessionsList} sessions=${captureSessions} onDownloadHar=${onDownloadHar} onDelete=${onDeleteCaptureSession} />
      `}
      ${tab === "assets" && html`
        <div class="content">
          <${CapturedItems} items=${captures} onCapture=${onScreenshot} onImageClick=${onImageClick} />
          <${DocumentViewer}
            projectId=${process?.project_id}
            processId=${process?.id}
            canonicalDoc=${canonicalDoc}
            userDocs=${userDocs}
            onRegenerate=${onRegenerateDoc}
            onSaveDoc=${onSaveDoc}
            onDeleteDoc=${onDeleteDoc}
            onCreateDoc=${onCreateDoc}
            showToast=${showToast}
          />
          <${AttachmentsList}
            attachments=${attachments}
            onUpload=${onUploadAttachment}
            onDelete=${onDeleteAttachment}
            onImageClick=${onImageClick}
          />
        </div>
      `}
      ${tab === "questions" && html`
        <${QuestionsPanel}
          questions=${questions}
          onAdd=${onAddQuestion}
          onUpdate=${onUpdateQuestion}
          onDelete=${onDeleteQuestion}
        />
      `}
      ${tab === "timeline" && html`
        <${Timeline} events=${timelineEvents} onDownload=${onDownloadTimeline} onClear=${onClearTimeline} />
      `}
    </div>
  `;
}

// ── Mount ───────────────────────────────────────────────────────────────────

render(html`<${App} />`, document.getElementById("app"));
