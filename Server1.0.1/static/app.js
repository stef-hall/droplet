const form = document.getElementById("secretariat-form");
const promptInput = document.getElementById("prompt");
const feedEl = document.getElementById("chat-feed");
const metaEl = document.getElementById("meta");
const addAttachmentBtn = document.getElementById("add-attachment");
const imageUploadInput = document.getElementById("image-upload");
const attachmentPill = document.getElementById("attachment-pill");
const clearAttachmentBtn = document.getElementById("clear-attachment");
const composerEl = document.getElementById("secretariat-form");
const initialAssistantMessageEl = document.getElementById("initial-assistant-message");
const SESSION_KEY = "secretariat_session_id";
const MAX_PROMPT_HEIGHT = 180;
let attachedImageDataUrl = null;
let composerDocked = false;

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  });
}

function getSessionId() {
  return localStorage.getItem(SESSION_KEY) || "";
}

function setSessionId(sessionId) {
  if (!sessionId) return;
  localStorage.setItem(SESSION_KEY, sessionId);
}

function appendMessage(role, text) {
  const item = document.createElement("article");
  item.className = `msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  item.appendChild(bubble);
  feedEl.appendChild(item);
  feedEl.scrollTop = feedEl.scrollHeight;
}

function autoSizePrompt() {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, MAX_PROMPT_HEIGHT)}px`;
}

function appendThinkingMessage() {
  const item = document.createElement("article");
  item.className = "msg assistant";
  item.id = "thinking-message";
  item.innerHTML = `
    <div class="thinking-bubble">
      <span class="thinking-label">Thinking...</span>
    </div>
  `;
  feedEl.appendChild(item);
  feedEl.scrollTop = feedEl.scrollHeight;
}

function removeThinkingMessage() {
  const item = document.getElementById("thinking-message");
  if (item) item.remove();
}

function toDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Failed to read image."));
    reader.readAsDataURL(file);
  });
}

function showAttachmentPill(show) {
  if (show) {
    attachmentPill.classList.remove("hidden");
  } else {
    attachmentPill.classList.add("hidden");
  }
}

async function initSession() {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  try {
    const response = await fetch("/api/session/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: getSessionId(), timezone })
    });
    const data = await response.json();
    if (response.ok && data.ok) {
      setSessionId(data.session_id);
    }
  } catch (_) {
    // Non-fatal.
  }
}

initSession();
autoSizePrompt();
initializeComposerFloating();

promptInput.addEventListener("input", autoSizePrompt);
promptInput.addEventListener("focus", () => {
  dockComposer();
});
promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

addAttachmentBtn.addEventListener("click", () => {
  imageUploadInput.click();
});

imageUploadInput.addEventListener("change", async () => {
  const file = imageUploadInput.files && imageUploadInput.files[0];
  if (!file) return;

  try {
    attachedImageDataUrl = await toDataUrl(file);
    showAttachmentPill(true);
    metaEl.textContent = "Image attached.";
  } catch (error) {
    attachedImageDataUrl = null;
    showAttachmentPill(false);
    metaEl.textContent = error.message;
  }
});

clearAttachmentBtn.addEventListener("click", () => {
  attachedImageDataUrl = null;
  imageUploadInput.value = "";
  showAttachmentPill(false);
  metaEl.textContent = "Attachment removed.";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const prompt = promptInput.value.trim();
  if (!prompt) return;

  appendMessage("user", prompt);
  promptInput.value = "";
  autoSizePrompt();
  metaEl.textContent = "Thinking...";
  appendThinkingMessage();

  try {
    const response = await fetch("/api/secretariat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        session_id: getSessionId(),
        image_data_url: attachedImageDataUrl
      })
    });

    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Request failed.");
    }

    setSessionId(data.session_id);
    removeThinkingMessage();
    appendMessage("assistant", data.message || "No message returned.");
    metaEl.textContent = data.state && data.state !== "DONE" ? data.state : "";
    attachedImageDataUrl = null;
    imageUploadInput.value = "";
    showAttachmentPill(false);
  } catch (error) {
    removeThinkingMessage();
    appendMessage("assistant", `Error: ${error.message}`);
    metaEl.textContent = "";
  }
});

function updateComposerFloatOffset() {
  if (composerDocked) return;
  const rect = composerEl.getBoundingClientRect();
  const viewportCenterY = window.innerHeight / 2;
  const composerCenterY = rect.top + rect.height / 2;
  const offset = viewportCenterY - composerCenterY;
  composerEl.style.setProperty("--float-offset", `${Math.round(offset)}px`);
}

function initializeComposerFloating() {
  requestAnimationFrame(() => {
    updateComposerFloatOffset();
    composerEl.classList.add("floating");
  });
}

function dockComposer() {
  if (composerDocked) return;
  composerDocked = true;
  if (initialAssistantMessageEl) {
    initialAssistantMessageEl.classList.add("revealed");
  }
  const startOffset = getComputedStyle(composerEl).getPropertyValue("--float-offset").trim() || "0px";
  composerEl.style.setProperty("--dock-start-offset", startOffset);
  composerEl.classList.add("docking");
  composerEl.classList.remove("floating");
  composerEl.style.setProperty("--float-offset", "0px");
  setTimeout(() => {
    composerEl.classList.remove("docking");
  }, 440);
}

window.addEventListener("resize", updateComposerFloatOffset);
composerEl.addEventListener("pointerdown", dockComposer);

window.addEventListener("keydown", (event) => {
  if (event.defaultPrevented) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;

  const target = event.target;
  const isEditableTarget =
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLElement && target.isContentEditable);
  if (isEditableTarget) return;

  if (event.key.length === 1) {
    event.preventDefault();
    dockComposer();
    promptInput.focus();
    promptInput.setRangeText(event.key, promptInput.selectionStart, promptInput.selectionEnd, "end");
    autoSizePrompt();
    return;
  }

  if (event.key === "Backspace" && promptInput.value.length > 0) {
    event.preventDefault();
    dockComposer();
    promptInput.focus();
    const start = Math.max(0, promptInput.selectionStart - 1);
    promptInput.setRangeText("", start, promptInput.selectionEnd, "end");
    autoSizePrompt();
  }
});

