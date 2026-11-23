/**
 * SpeechSynthesis wrapper for text-to-speech.
 */

export function isSupported() {
  return "speechSynthesis" in window;
}

export function speak(text) {
  if (!isSupported()) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  window.speechSynthesis.speak(utterance);
}

export function stop() {
  if (isSupported()) {
    window.speechSynthesis.cancel();
  }
}
