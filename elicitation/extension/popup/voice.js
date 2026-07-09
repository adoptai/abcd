/**
 * Voice input via content script injection.
 *
 * SpeechRecognition is blocked in extension popups/side panels/offscreen docs.
 * Instead, we inject a content script into the active tab where it works.
 * Results flow: content script → chrome.runtime messages → this listener.
 */

let messageListener = null;
let currentCallbacks = null;

export function isSupported() {
  return true;
}

export function startListening(callbacks = {}) {
  currentCallbacks = callbacks;

  // Register listener for voice events from the content script
  if (messageListener) {
    chrome.runtime.onMessage.removeListener(messageListener);
  }

  messageListener = (message) => {
    switch (message.type) {
      case "VOICE_INTERIM":
        currentCallbacks?.onInterim?.(message.transcript);
        break;
      case "VOICE_FINAL":
        currentCallbacks?.onResult?.(message.transcript);
        break;
      case "VOICE_ENDED":
        cleanup();
        currentCallbacks?.onEnd?.();
        break;
      case "VOICE_ERROR":
        console.warn("[Voice] Error:", message.error);
        cleanup();
        currentCallbacks?.onError?.(message.message || message.error);
        currentCallbacks?.onEnd?.();
        break;
    }
  };

  chrome.runtime.onMessage.addListener(messageListener);

  // Send start command to service worker (which injects the content script)
  chrome.runtime.sendMessage({ type: "VOICE_START" }, (response) => {
    if (chrome.runtime.lastError) {
      console.warn("[Voice] Failed to start:", chrome.runtime.lastError.message);
      cleanup();
      currentCallbacks?.onError?.("Failed to start voice: " + chrome.runtime.lastError.message);
      currentCallbacks?.onEnd?.();
      return;
    }
    if (response && !response.success) {
      cleanup();
      currentCallbacks?.onError?.(response.error || "Failed to start voice");
      currentCallbacks?.onEnd?.();
    }
  });
}

export function stopListening() {
  chrome.runtime.sendMessage({ type: "VOICE_STOP" }, () => {
    if (chrome.runtime.lastError) {
      console.warn("[Voice] Failed to stop:", chrome.runtime.lastError.message);
    }
  });
  // Don't cleanup immediately — let VOICE_ENDED handler do it
  // so that late-arriving VOICE_FINAL messages are still received.
  // Safety timeout in case VOICE_ENDED never arrives.
  setTimeout(() => {
    if (messageListener) {
      console.warn("[Voice] Safety timeout — cleaning up");
      cleanup();
      currentCallbacks?.onEnd?.();
    }
  }, 2000);
}

function cleanup() {
  if (messageListener) {
    chrome.runtime.onMessage.removeListener(messageListener);
    messageListener = null;
  }
  currentCallbacks = null;
}
