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
let doneFadeTimer = null;

function keepPromptFocused() {
  if (!promptInput) return;
  if (document.activeElement !== promptInput) {
    promptInput.focus();
  }
}

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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderInlineMarkdown(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  return out;
}

function renderMarkdown(text) {
  const source = String(text ?? "").replace(/\r\n/g, "\n");
  const lines = source.split("\n");
  const chunks = [];
  let listBuffer = [];

  function flushList() {
    if (listBuffer.length === 0) return;
    const items = listBuffer.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("");
    chunks.push(`<ul>${items}</ul>`);
    listBuffer = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (!line.trim()) {
      flushList();
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      listBuffer.push(line.replace(/^[-*]\s+/, ""));
      continue;
    }
    flushList();
    chunks.push(`<p>${renderInlineMarkdown(line)}</p>`);
  }
  flushList();
  return chunks.join("");
}

function appendMessage(role, text) {
  const item = document.createElement("article");
  item.className = `msg ${role} entering`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") {
    bubble.classList.add("md");
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }

  item.appendChild(bubble);
  feedEl.appendChild(item);
  requestAnimationFrame(() => {
    item.classList.remove("entering");
  });
  scrollFeedToBottom();
}

function autoSizePrompt() {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, MAX_PROMPT_HEIGHT)}px`;
}

function scrollFeedToBottom() {
  feedEl.scrollTo({ top: feedEl.scrollHeight, behavior: "smooth" });
}

function setMetaStatus(text, { autoFade = false, fadeDelayMs = 4000 } = {}) {
  if (doneFadeTimer) {
    clearTimeout(doneFadeTimer);
    doneFadeTimer = null;
  }
  metaEl.classList.remove("meta-fading");
  metaEl.textContent = text || "";

  if (autoFade && text) {
    doneFadeTimer = setTimeout(() => {
      metaEl.classList.add("meta-fading");
      setTimeout(() => {
        metaEl.textContent = "";
        metaEl.classList.remove("meta-fading");
      }, 240);
    }, fadeDelayMs);
  }
}

function appendThinkingMessage() {
  const item = document.createElement("article");
  item.className = "msg assistant";
  item.id = "thinking-message";
  item.innerHTML = `
    <div class="thinking-bubble">
      <span class="eclipse" aria-hidden="true"></span>
      <span class="thinking-label">Thinking...</span>
    </div>
  `;
  feedEl.appendChild(item);
  scrollFeedToBottom();
}

function removeThinkingMessage() {
  const item = document.getElementById("thinking-message");
  if (item) item.remove();
}

function resolveThinkingMessage(text, role = "assistant") {
  const item = document.getElementById("thinking-message");
  if (!item) {
    appendMessage(role, text);
    return;
  }

  item.id = "";
  item.className = `msg ${role} entering`;
  item.innerHTML = "";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") {
    bubble.classList.add("md");
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }
  item.appendChild(bubble);

  requestAnimationFrame(() => {
    item.classList.remove("entering");
  });
  scrollFeedToBottom();
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

addAttachmentBtn.addEventListener("pointerdown", (event) => {
  if (composerDocked) return;
  // Prevent the floating-to-docked transition from swallowing the intended attach action.
  event.preventDefault();
  dockComposer();
  setTimeout(() => {
    imageUploadInput.click();
  }, 120);
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
  setMetaStatus("Thinking...");
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
    resolveThinkingMessage(data.message || "No message returned.", "assistant");
    const stateLabelMap = {
      WAITING: "Waiting...",
      DONE: "Done."
    };
    const mappedState = stateLabelMap[data.state] || data.state || "";
    setMetaStatus(mappedState, { autoFade: data.state === "DONE", fadeDelayMs: 5000 });
    attachedImageDataUrl = null;
    imageUploadInput.value = "";
    showAttachmentPill(false);
  } catch (error) {
    resolveThinkingMessage(`Error: ${error.message}`, "assistant");
    setMetaStatus("");
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
  requestAnimationFrame(keepPromptFocused);
  setTimeout(keepPromptFocused, 80);
  setTimeout(() => {
    composerEl.classList.remove("docking");
  }, 440);
}

window.addEventListener("resize", updateComposerFloatOffset);
composerEl.addEventListener("pointerdown", (event) => {
  dockComposer();
  const target = event.target;
  const clickedControl =
    target instanceof HTMLElement &&
    target.closest("button, input[type='file'], .pill-clear");
  if (!clickedControl) {
    requestAnimationFrame(keepPromptFocused);
  }
});

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

