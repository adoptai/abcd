/**
 * Content script: region selector overlay.
 *
 * Injected into the active tab to let the user draw a rectangle
 * for a region screenshot. Sends REGION_SELECTED or REGION_CANCELLED
 * back to the extension via chrome.runtime.sendMessage.
 */

(function () {
  // Prevent double-injection
  if (document.getElementById("__adopt_region_overlay")) {
    document.getElementById("__adopt_region_overlay").remove();
  }

  const overlay = document.createElement("div");
  overlay.id = "__adopt_region_overlay";
  Object.assign(overlay.style, {
    position: "fixed",
    top: "0",
    left: "0",
    width: "100vw",
    height: "100vh",
    zIndex: "2147483647",
    cursor: "crosshair",
    background: "rgba(0, 0, 0, 0.15)",
  });

  const selBox = document.createElement("div");
  Object.assign(selBox.style, {
    position: "absolute",
    border: "2px dashed #2563eb",
    background: "rgba(37, 99, 235, 0.1)",
    display: "none",
  });
  overlay.appendChild(selBox);

  // Instruction tooltip
  const tooltip = document.createElement("div");
  tooltip.textContent = "Draw a rectangle to capture. Press ESC to cancel.";
  Object.assign(tooltip.style, {
    position: "absolute",
    top: "12px",
    left: "50%",
    transform: "translateX(-50%)",
    background: "rgba(0,0,0,0.75)",
    color: "#fff",
    padding: "8px 16px",
    borderRadius: "8px",
    fontSize: "14px",
    fontFamily: "system-ui, sans-serif",
    pointerEvents: "none",
    whiteSpace: "nowrap",
  });
  overlay.appendChild(tooltip);

  let startX, startY, drawing = false;

  function cleanup() {
    overlay.remove();
  }

  overlay.addEventListener("mousedown", (e) => {
    startX = e.clientX;
    startY = e.clientY;
    drawing = true;
    selBox.style.display = "block";
    selBox.style.left = startX + "px";
    selBox.style.top = startY + "px";
    selBox.style.width = "0";
    selBox.style.height = "0";
  });

  overlay.addEventListener("mousemove", (e) => {
    if (!drawing) return;
    const x = Math.min(e.clientX, startX);
    const y = Math.min(e.clientY, startY);
    const w = Math.abs(e.clientX - startX);
    const h = Math.abs(e.clientY - startY);
    selBox.style.left = x + "px";
    selBox.style.top = y + "px";
    selBox.style.width = w + "px";
    selBox.style.height = h + "px";
  });

  overlay.addEventListener("mouseup", (e) => {
    if (!drawing) return;
    drawing = false;

    const x = Math.min(e.clientX, startX);
    const y = Math.min(e.clientY, startY);
    const w = Math.abs(e.clientX - startX);
    const h = Math.abs(e.clientY - startY);

    cleanup();

    if (w < 10 || h < 10) {
      // Too small — treat as cancel
      chrome.runtime.sendMessage({ type: "REGION_CANCELLED" });
      return;
    }

    chrome.runtime.sendMessage({
      type: "REGION_SELECTED",
      rect: {
        x,
        y,
        width: w,
        height: h,
        devicePixelRatio: window.devicePixelRatio || 1,
      },
    });
  });

  document.addEventListener("keydown", function onKey(e) {
    if (e.key === "Escape") {
      cleanup();
      document.removeEventListener("keydown", onKey);
      chrome.runtime.sendMessage({ type: "REGION_CANCELLED" });
    }
  });

  document.body.appendChild(overlay);
})();
