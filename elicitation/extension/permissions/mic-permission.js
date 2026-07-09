document.getElementById("allowBtn").addEventListener("click", async () => {
  const btn = document.getElementById("allowBtn");
  const status = document.getElementById("status");
  btn.disabled = true;
  status.textContent = "Requesting microphone access...";
  status.className = "status";

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    // Stop tracks immediately — we just needed the permission grant
    stream.getTracks().forEach((t) => t.stop());

    status.textContent = "Permission granted! You can close this tab.";
    status.className = "status success";

    // Notify the extension
    chrome.runtime.sendMessage({ type: "MIC_PERMISSION_GRANTED" });

    // Auto-close after 2 seconds
    setTimeout(() => window.close(), 2000);
  } catch (e) {
    status.textContent = "Permission denied: " + e.message + ". Please try again.";
    status.className = "status error";
    btn.disabled = false;
  }
});
