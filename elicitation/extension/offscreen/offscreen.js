/**
 * Offscreen document for SpeechRecognition.
 *
 * Chrome blocks SpeechRecognition in extension popups/side panels.
 * This offscreen document runs in a context where USER_MEDIA is available,
 * allowing microphone access and speech recognition.
 *
 * Communication: receives commands from service worker, sends results back.
 */

let recognition = null;

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "OFFSCREEN_VOICE_START") {
    startRecognition(message.lang || "en-US");
    sendResponse({ success: true });
    return false;
  }

  if (message.type === "OFFSCREEN_VOICE_STOP") {
    stopRecognition();
    sendResponse({ success: true });
    return false;
  }
});

function startRecognition(lang) {
  // Stop any existing session
  stopRecognition();

  if (!SpeechRecognition) {
    chrome.runtime.sendMessage({
      type: "VOICE_ERROR",
      error: "SpeechRecognition not available in this browser",
    });
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = lang;

  recognition.onresult = (event) => {
    let interim = "";
    let final = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        final += transcript;
      } else {
        interim += transcript;
      }
    }
    if (final) {
      chrome.runtime.sendMessage({ type: "VOICE_FINAL", transcript: final });
    }
    if (interim) {
      chrome.runtime.sendMessage({ type: "VOICE_INTERIM", transcript: interim });
    }
  };

  recognition.onerror = (event) => {
    console.warn("[Offscreen Voice] Error:", event.error);
    chrome.runtime.sendMessage({
      type: "VOICE_ERROR",
      error: event.error,
      message: event.error === "not-allowed"
        ? "Microphone access denied. Please grant microphone permission."
        : `Speech recognition error: ${event.error}`,
    });
  };

  recognition.onend = () => {
    chrome.runtime.sendMessage({ type: "VOICE_ENDED" });
    recognition = null;
  };

  try {
    recognition.start();
    console.log("[Offscreen Voice] Recognition started");
  } catch (e) {
    console.warn("[Offscreen Voice] Failed to start:", e);
    chrome.runtime.sendMessage({
      type: "VOICE_ERROR",
      error: e.message,
      message: `Failed to start speech recognition: ${e.message}`,
    });
  }
}

function stopRecognition() {
  if (recognition) {
    try {
      recognition.stop();
    } catch (e) {
      // Already stopped
    }
    recognition = null;
  }
}

console.log("[Offscreen Voice] Document loaded");
