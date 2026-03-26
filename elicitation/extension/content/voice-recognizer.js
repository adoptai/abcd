/**
 * Content script: voice recognizer.
 *
 * Injected into the active tab to run SpeechRecognition in the page's
 * browsing context, where it actually works. Results are forwarded
 * back to the extension via chrome.runtime.sendMessage.
 */

(function () {
  // Prevent double-injection
  if (window.__adoptVoiceRecognizer) {
    // Already running — just restart
    window.__adoptVoiceRecognizer.stop();
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    chrome.runtime.sendMessage({
      type: "VOICE_ERROR",
      error: "not-supported",
      message: "SpeechRecognition is not supported in this browser",
    });
    return;
  }

  const recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  // Track the latest interim transcript so we can finalize it on stop
  let lastInterimTranscript = "";

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
      lastInterimTranscript = "";
      chrome.runtime.sendMessage({ type: "VOICE_FINAL", transcript: final });
    }
    if (interim) {
      lastInterimTranscript = interim;
      chrome.runtime.sendMessage({ type: "VOICE_INTERIM", transcript: interim });
    }
  };

  recognition.onerror = (event) => {
    console.warn("[Adopt.ai Voice] Error:", event.error);
    chrome.runtime.sendMessage({
      type: "VOICE_ERROR",
      error: event.error,
      message: event.error === "not-allowed"
        ? "Microphone access denied for this site. Please allow mic access when prompted."
        : "Speech recognition error: " + event.error,
    });
  };

  recognition.onend = () => {
    chrome.runtime.sendMessage({ type: "VOICE_ENDED" });
    window.__adoptVoiceRecognizer = null;
  };

  // Listen for stop command
  function onMessage(msg) {
    if (msg.type === "CONTENT_VOICE_STOP") {
      // If there's unsent interim text, send it as final before stopping
      if (lastInterimTranscript) {
        chrome.runtime.sendMessage({ type: "VOICE_FINAL", transcript: lastInterimTranscript });
        lastInterimTranscript = "";
      }
      recognition.stop();
      chrome.runtime.onMessage.removeListener(onMessage);
    }
  }
  chrome.runtime.onMessage.addListener(onMessage);

  window.__adoptVoiceRecognizer = recognition;

  try {
    recognition.start();
    console.log("[Adopt.ai Voice] Recognition started");
  } catch (e) {
    console.warn("[Adopt.ai Voice] Failed to start:", e);
    chrome.runtime.sendMessage({
      type: "VOICE_ERROR",
      error: e.message,
      message: "Failed to start speech recognition: " + e.message,
    });
  }
})();
