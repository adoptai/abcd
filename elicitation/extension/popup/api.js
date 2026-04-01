/**
 * Backend API client — proxies all requests through the service worker.
 */

const BACKEND_URL = "http://localhost:8000";

function api(method, path, body) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "BACKEND_API", method, path, body },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (response && response.success) {
          resolve(response.data);
        } else {
          reject(new Error(response ? response.error : "No response"));
        }
      }
    );
  });
}

// Projects
export const createProject = (data) => api("POST", "/projects", data);
export const listProjects = () => api("GET", "/projects");
export const getProject = (id) => api("GET", `/projects/${id}`);
export const updateProject = (id, data) => api("PUT", `/projects/${id}`, data);
export const deleteProject = (id) => api("DELETE", `/projects/${id}`);

// Processes
export const createProcess = (projectId, data) =>
  api("POST", `/projects/${projectId}/processes`, data);
export const listProcesses = (projectId) =>
  api("GET", `/projects/${projectId}/processes`);
export const getProcess = (id) => api("GET", `/processes/${id}`);
export const updateProcess = (id, data) => api("PUT", `/processes/${id}`, data);
export const deleteProcess = (id) => api("DELETE", `/processes/${id}`);

// Messages
export const createMessage = (data) => api("POST", "/messages", data);
export const listMessages = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/messages?${qs}`);
};

// Screenshots
export function captureScreenshot(projectId, processId) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "CAPTURE_SCREENSHOT", projectId, processId },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (response && response.success) {
          resolve(response.data);
        } else {
          reject(new Error(response ? response.error : "No response"));
        }
      }
    );
  });
}

export const listScreenshots = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/screenshots?${qs}`);
};

// Capture Sessions
export const createCaptureSession = (processId, data = {}) =>
  api("POST", `/processes/${processId}/capture-sessions`, data);
export const startCaptureSession = (id) => api("PUT", `/capture-sessions/${id}/start`);
export const stopCaptureSession = (id) => api("PUT", `/capture-sessions/${id}/stop`);
export const pauseCaptureSession = (id) => api("PUT", `/capture-sessions/${id}/pause`);
export const patchCaptureSessionSettings = (id, data) =>
  api("PATCH", `/capture-sessions/${id}/settings`, data);
export const getCaptureSession = (id) => api("GET", `/capture-sessions/${id}`);

// Narrations
export const createNarration = (data) => api("POST", "/narrations", data);
export const listNarrations = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/narrations?${qs}`);
};

// Click events
export const listClicks = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/clicks?${qs}`);
};

// Timeline
export const getTimeline = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/timeline?${qs}`);
};
export const downloadTimeline = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/timeline/download?${qs}`);
};
export const clearTimeline = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("DELETE", `/timeline?${qs}`);
};

// URL events
export const createUrlEvent = (data) => api("POST", "/url-events", data);

// Documents
export const createDocument = (projectId, data) =>
  api("POST", `/projects/${projectId}/documents`, data);
export const listProjectDocuments = (projectId) =>
  api("GET", `/projects/${projectId}/documents`);
export const listProcessDocuments = (processId) =>
  api("GET", `/processes/${processId}/documents`);
export const getDocument = (id) => api("GET", `/documents/${id}`);
export const updateDocument = (id, data) => api("PUT", `/documents/${id}`, data);
export const deleteDocument = (id) => api("DELETE", `/documents/${id}`);
export const generateProjectDoc = (projectId) =>
  api("POST", `/projects/${projectId}/documents/generate`);
export const generateProcessDoc = (processId) =>
  api("POST", `/processes/${processId}/documents/generate`);

// Attachments (upload uses direct fetch — multipart form)
export async function uploadAttachment(projectId, file, processId = null) {
  const formData = new FormData();
  formData.append("file", file);
  if (processId) formData.append("process_id", processId);

  const resp = await fetch(`${BACKEND_URL}/projects/${projectId}/attachments`, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Upload failed: ${resp.status} ${text}`);
  }
  return resp.json();
}

export const listAttachments = (projectId, processId) => {
  const params = { project_id: projectId };
  if (processId) params.process_id = processId;
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/projects/${projectId}/attachments${qs ? "?" + qs : ""}`);
};
export const deleteAttachment = (id) => api("DELETE", `/attachments/${id}`);

// Questions
export const createQuestion = (projectId, data) =>
  api("POST", `/projects/${projectId}/questions`, data);
export const listQuestions = (projectId, params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/projects/${projectId}/questions${qs ? "?" + qs : ""}`);
};
export const updateQuestion = (id, data) => api("PUT", `/questions/${id}`, data);
export const deleteQuestion = (id) => api("DELETE", `/questions/${id}`);

// Capture session listing + delete
export const listCaptureSessions = (processId) =>
  api("GET", `/processes/${processId}/capture-sessions`);
export const listProjectCaptureSessions = (projectId) =>
  api("GET", `/projects/${projectId}/capture-sessions`);
export const deleteCaptureSession = (id) =>
  api("DELETE", `/capture-sessions/${id}`);

// HAR download (direct fetch — returns file)
export async function downloadHar(sessionId) {
  const resp = await fetch(`${BACKEND_URL}/capture-sessions/${sessionId}/har`);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`HAR download failed: ${resp.status} ${text}`);
  }
  return resp.json();
}

// Export & Import
export const exportProject = (id) => api("GET", `/projects/${id}/export`);
export const importProject = (bundle) => api("POST", "/projects/import", bundle);

// Health check
export const ping = () => api("POST", "/sessions/ping", { message: "hello" });

// ── Chat with Claude (SSE streaming) ──────────────────────────────────────

/**
 * Send a chat message and stream Claude's response via SSE.
 *
 * @param {Object} params
 * @param {string} params.projectId
 * @param {string|null} params.processId
 * @param {string} params.content
 * @param {string} params.contentType
 * @param {function} params.onHumanMessage - called with the stored human message
 * @param {function} params.onDelta - called with each text delta
 * @param {function} params.onAgentMessage - called with the final stored agent message
 * @param {function} params.onDone - called when stream completes
 * @param {function} params.onError - called on error
 * @returns {function} abort function to cancel the stream
 */
export function chatStream({
  projectId,
  processId,
  chatSessionId,
  content,
  contentType = "text",
  url = "",
  onHumanMessage,
  onDelta,
  onAgentMessage,
  onDone,
  onError,
}) {
  const controller = new AbortController();

  (async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          process_id: processId || null,
          chat_session_id: chatSessionId || null,
          content,
          content_type: contentType,
          url,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`Chat request failed: ${response.status} ${text}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventType = null;
      let eventData = "";
      let doneReceived = false;

      const dispatchEvent = () => {
        if (!eventType || !eventData) return;
        try {
          const data = JSON.parse(eventData.trim());
          switch (eventType) {
            case "human_message":
              onHumanMessage?.(data);
              break;
            case "delta":
              onDelta?.(data.text);
              break;
            case "agent_message":
              onAgentMessage?.(data);
              break;
            case "done":
              doneReceived = true;
              onDone?.();
              break;
          }
        } catch (e) {
          console.warn("Failed to parse SSE event:", eventType, eventData, e);
        }
        eventType = null;
        eventData = "";
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer — handle \r\n, \r, or \n line endings
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || ""; // Keep incomplete line in buffer

        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, "");
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            eventData = line.slice(5).trimStart();
          } else if (line === "") {
            dispatchEvent();
          }
        }
      }
      // Dispatch any remaining event
      dispatchEvent();
      // Ensure done is always called even if the stream didn't send a done event
      if (!doneReceived) onDone?.();
    } catch (e) {
      if (e.name !== "AbortError") {
        onError?.(e);
      }
    }
  })();

  return () => controller.abort();
}

// Chat history (non-streaming)
export const getChatHistory = (projectId, processId, limit) => {
  const params = { project_id: projectId };
  if (processId) params.process_id = processId;
  if (limit) params.limit = limit;
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/chat/history?${qs}`);
};

// Chat sessions
export const createChatSession = (data) => api("POST", "/chat/sessions", data);
export const listChatSessions = (params) => {
  const qs = new URLSearchParams(params).toString();
  return api("GET", `/chat/sessions?${qs}`);
};
export const deleteChatSession = (id) => api("DELETE", `/chat/sessions/${id}`);
export const updateChatSession = (id, data) => api("PATCH", `/chat/sessions/${id}`, data);

// ── Login Sessions ──────────────────────────────────────────────────────────

export const createLoginSession = (data) => api("POST", "/login-sessions", data);
export const listLoginSessions = () => api("GET", "/login-sessions");
export const getLoginSession = (id) => api("GET", `/login-sessions/${id}`);
export const startLoginSession = (id) => api("PUT", `/login-sessions/${id}/start`);
export const completeLoginSession = (id) => api("PUT", `/login-sessions/${id}/complete`);
export const analyzeLoginSession = (id) => api("POST", `/login-sessions/${id}/analyze`);
export const getLoginBundle = (id) => api("GET", `/login-sessions/${id}/bundle`);

export function startLoginRecording({ captureSessionId, projectId, processId }) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "SET_LOGIN_RECORDING_STATE", captureSessionId, projectId, processId },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(response?.data);
      }
    );
  });
}

export function stopLoginRecording() {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "CLEAR_LOGIN_RECORDING_STATE" },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(response?.data);
      }
    );
  });
}

export function injectLoginRecorder(tabId) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "INJECT_LOGIN_RECORDER", tabId },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (response?.success) resolve(response.data);
        else reject(new Error(response?.error || "Failed to inject login recorder"));
      }
    );
  });
}

export function removeLoginRecorder(tabId) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "REMOVE_LOGIN_RECORDER", tabId },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (response?.success) resolve(response.data);
        else reject(new Error(response?.error || "Failed to remove login recorder"));
      }
    );
  });
}
