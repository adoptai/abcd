/**
 * Tiny content script that keeps the MV3 service worker alive by holding
 * an open chrome.runtime.connect() port.  Injected during HAR capture and
 * disconnected when capture stops.
 */
(() => {
  if (window.__harKeepalivePort) return; // already connected
  const port = chrome.runtime.connect({ name: "har-keepalive" });
  window.__harKeepalivePort = port;
  port.onDisconnect.addListener(() => {
    window.__harKeepalivePort = null;
  });
})();
