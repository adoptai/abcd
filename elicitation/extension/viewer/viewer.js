import { marked } from "../lib/marked.esm.mjs";

const BACKEND_URL = "http://localhost:8000";
const params = new URLSearchParams(window.location.search);
const docId = params.get("id");

let currentDoc = null;
let editing = false;

function toggleEdit() {
  if (!currentDoc) return;
  editing = true;
  document.getElementById("editor").value = currentDoc.content;
  document.getElementById("content").style.display = "none";
  document.getElementById("editor").style.display = "block";
  document.getElementById("btn-edit").style.display = "none";
  document.getElementById("btn-save").style.display = "";
  document.getElementById("btn-cancel").style.display = "";
}

function cancelEdit() {
  editing = false;
  document.getElementById("content").style.display = "";
  document.getElementById("editor").style.display = "none";
  document.getElementById("btn-edit").style.display = "";
  document.getElementById("btn-save").style.display = "none";
  document.getElementById("btn-cancel").style.display = "none";
}

async function saveDoc() {
  if (!currentDoc) return;
  const newContent = document.getElementById("editor").value;
  try {
    const resp = await fetch(`${BACKEND_URL}/documents/${currentDoc.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: newContent }),
    });
    if (!resp.ok) throw new Error(`Save failed: ${resp.status}`);
    currentDoc = await resp.json();
    renderDoc();
    cancelEdit();
  } catch (e) {
    alert("Failed to save: " + e.message);
  }
}

function renderDoc() {
  document.getElementById("doc-title").textContent = currentDoc.title;
  document.getElementById("content").innerHTML = marked.parse(currentDoc.content || "");
  document.title = currentDoc.title + " — Document Viewer";
}

async function loadDoc() {
  if (!docId) {
    document.getElementById("loading").style.display = "none";
    document.getElementById("error").style.display = "";
    document.getElementById("error").textContent = "No document ID provided. Use ?id=<docId>";
    return;
  }

  try {
    const resp = await fetch(`${BACKEND_URL}/documents/${docId}`);
    if (!resp.ok) throw new Error(`Failed to load: ${resp.status}`);
    currentDoc = await resp.json();

    document.getElementById("loading").style.display = "none";
    document.getElementById("toolbar").style.display = "flex";
    document.getElementById("content").style.display = "";
    renderDoc();
  } catch (e) {
    document.getElementById("loading").style.display = "none";
    document.getElementById("error").style.display = "";
    document.getElementById("error").textContent = "Failed to load document: " + e.message;
  }
}

// Wire up button event listeners
document.getElementById("btn-edit").addEventListener("click", toggleEdit);
document.getElementById("btn-save").addEventListener("click", saveDoc);
document.getElementById("btn-cancel").addEventListener("click", cancelEdit);

loadDoc();
