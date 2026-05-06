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
const authOverlayEl = document.getElementById("auth-overlay");
const authFormEl = document.getElementById("auth-form");
const authEmailEl = document.getElementById("auth-email");
const authPasswordEl = document.getElementById("auth-password");
const authSubmitEl = document.getElementById("auth-submit");
const authToggleEl = document.getElementById("auth-toggle");
const authSignoutEl = document.getElementById("auth-signout");
const chatMenuTriggerEl = document.getElementById("chat-menu-trigger");
const chatMenuEl = document.getElementById("chat-menu");
const chatSettingsEl = document.getElementById("chat-settings");
const chatSignoutEl = document.getElementById("chat-signout");
const authSubtitleEl = document.getElementById("auth-subtitle");
const authStatusEl = document.getElementById("auth-status");
const authTrustDeviceEl = document.getElementById("auth-trust-device");
const authTrustWrapEl = document.getElementById("auth-trust-wrap");
const MAX_PROMPT_HEIGHT = 180;
const isTouchDevice = window.matchMedia("(pointer: coarse)").matches;
let attachedImageDataUrl = null;
let composerDocked = false;
let doneFadeTimer = null;
let detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
let detectedLocation = null;
let currentSessionId = "";
let initSessionPromise = null;
let isSignupMode = false;
let isAuthenticated = false;

function getBrowserLocation() {
  return new Promise((resolve) => {
    if (!("geolocation" in navigator)) {
      resolve(null);
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const coords = position && position.coords ? position.coords : null;
        if (!coords) {
          resolve(null);
          return;
        }
        resolve({
          latitude: coords.latitude,
          longitude: coords.longitude,
          accuracy_m: coords.accuracy
        });
      },
      () => resolve(null),
      {
        enableHighAccuracy: false,
        timeout: 5000,
        maximumAge: 30 * 60 * 1000
      }
    );
  });
}

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
  out = out.replace(/\[\[\s*send\s*:\s*([^\]]+?)\s*\]\]/gi, (_, payloadRaw) => {
    const payload = String(payloadRaw || "").trim();
    if (!payload) return "";

    const separatorIndex = payload.indexOf("|");
    const visibleText = separatorIndex >= 0 ? payload.slice(0, separatorIndex).trim() : payload;
    const replyText = separatorIndex >= 0 ? payload.slice(separatorIndex + 1).trim() : payload;
    if (!visibleText || !replyText) return "";

    const escapedVisibleText = escapeHtml(visibleText);
    const escapedReply = escapeHtml(replyText);
    return `<button type="button" class="quick-reply-inline" data-reply="${escapedReply}">${escapedVisibleText}</button>`;
  });
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
  let listType = null;

  function flushList() {
    if (listBuffer.length === 0 || !listType) return;
    const items = listBuffer.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("");
    chunks.push(`<${listType}>${items}</${listType}>`);
    listBuffer = [];
    listType = null;
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const itemText = line.replace(/^[-*]\s+/, "");
      if (listType && listType !== "ul") {
        flushList();
      }
      listType = "ul";
      listBuffer.push(itemText);
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const itemText = line.replace(/^\d+\.\s+/, "");
      if (listType && listType !== "ol") {
        flushList();
      }
      listType = "ol";
      listBuffer.push(itemText);
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
    bubble.classList.add("assistant-enter");
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
  ensureFeedPinnedToBottom();
}

function autoSizePrompt() {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, MAX_PROMPT_HEIGHT)}px`;
}

function scrollFeedToBottom() {
  feedEl.scrollTop = feedEl.scrollHeight;
}

function ensureFeedPinnedToBottom() {
  scrollFeedToBottom();
  requestAnimationFrame(() => {
    scrollFeedToBottom();
    setTimeout(scrollFeedToBottom, 80);
  });
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
  ensureFeedPinnedToBottom();
}

function updateThinkingLabel(text) {
  const labelEl = document.querySelector("#thinking-message .thinking-label");
  if (labelEl) {
    labelEl.textContent = text || "Thinking...";
  }
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
    bubble.classList.add("assistant-enter");
    bubble.classList.add("md");
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }
  item.appendChild(bubble);

  requestAnimationFrame(() => {
    item.classList.remove("entering");
  });
  ensureFeedPinnedToBottom();
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

async function attachImageFile(file) {
  if (!file) return false;
  try {
    attachedImageDataUrl = await toDataUrl(file);
    showAttachmentPill(true);
    setMetaStatus("Image attached.");
    return true;
  } catch (_) {
    attachedImageDataUrl = null;
    showAttachmentPill(false);
    setMetaStatus("Failed to read pasted image.");
    return false;
  }
}

function setAuthStatus(text) {
  if (!authStatusEl) return;
  authStatusEl.textContent = text || "";
}

function updateAuthUi() {
  if (!authOverlayEl) return;
  authOverlayEl.classList.toggle("hidden", isAuthenticated);
  form.querySelectorAll("input, textarea, button").forEach((el) => {
    el.disabled = !isAuthenticated;
  });
  if (authSignoutEl) {
    authSignoutEl.classList.toggle("hidden", !isAuthenticated);
  }
  if (!isAuthenticated && chatMenuEl) {
    chatMenuEl.classList.add("hidden");
    if (chatMenuTriggerEl) {
      chatMenuTriggerEl.setAttribute("aria-expanded", "false");
    }
  }
  if (chatSignoutEl) {
    chatSignoutEl.disabled = !isAuthenticated;
  }
  if (authSubmitEl) {
    authSubmitEl.textContent = isSignupMode ? "Sign Up" : "Sign In";
  }
  if (authToggleEl) {
    authToggleEl.textContent = isSignupMode
      ? "Already have an account? Sign in"
      : "Need an account? Sign up";
  }
  if (authSubtitleEl) {
    authSubtitleEl.textContent = isSignupMode ? "Create your account." : "Sign in to continue.";
  }
  if (authTrustWrapEl) {
    authTrustWrapEl.classList.toggle("hidden", isSignupMode);
  }
}

async function checkAuth() {
  try {
    const response = await fetch("/api/auth/me");
    const data = await response.json();
    isAuthenticated = Boolean(response.ok && data.ok && data.authenticated);
    updateAuthUi();
  } catch (_) {
    isAuthenticated = false;
    updateAuthUi();
  }
}

async function initSession() {
  if (!isAuthenticated) return;
  detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  detectedLocation = await getBrowserLocation();
  try {
    const response = await fetch("/api/session/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: currentSessionId,
        timezone: detectedTimezone,
        location: detectedLocation
      })
    });
    const data = await response.json();
    if (response.status === 401) {
      isAuthenticated = false;
      updateAuthUi();
      return;
    }
    if (response.ok && data.ok) {
      currentSessionId = String(data.session_id || "");
    }
  } catch (_) {
    // Non-fatal.
  }
}

async function submitPromptText(prompt) {
  if (!isAuthenticated) {
    setMetaStatus("Please sign in first.");
    return;
  }
  if (initSessionPromise) {
    try {
      await initSessionPromise;
    } catch (_) {
      // Non-fatal.
    }
  }

  appendMessage("user", prompt);
  promptInput.value = "";
  autoSizePrompt();
  setMetaStatus("Thinking...");
  appendThinkingMessage();
  updateThinkingLabel("Thinking...");

  try {
    const response = await fetch("/api/secretariat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        session_id: currentSessionId,
        image_data_url: attachedImageDataUrl,
        timezone: detectedTimezone,
        location: detectedLocation
      })
    });

    if (response.status === 401) {
      isAuthenticated = false;
      updateAuthUi();
      throw new Error("Please sign in.");
    }

    if (!response.ok || !response.body) {
      throw new Error("Request failed.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalPayload = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newlineIndex = buffer.indexOf("\n");
      while (newlineIndex >= 0) {
        const line = buffer.slice(0, newlineIndex).trim();
        buffer = buffer.slice(newlineIndex + 1);
        if (line) {
          let evt = null;
          try {
            evt = JSON.parse(line);
          } catch (_) {
            evt = null;
          }
          if (evt && evt.type === "status") {
            const label = evt.label || "Thinking...";
            setMetaStatus(label);
            updateThinkingLabel(label);
          }
          if (evt && evt.type === "final") {
            finalPayload = evt;
          }
        }
        newlineIndex = buffer.indexOf("\n");
      }
    }

    const data = finalPayload;
    if (!data || !data.ok) {
      throw new Error((data && data.error) || "Request failed.");
    }

    currentSessionId = String(data.session_id || "");
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
}

authToggleEl.addEventListener("click", () => {
  isSignupMode = !isSignupMode;
  setAuthStatus("");
  updateAuthUi();
});

authFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = (authEmailEl.value || "").trim();
  const password = authPasswordEl.value || "";
  const trustDevice = Boolean(authTrustDeviceEl && authTrustDeviceEl.checked && !isSignupMode);
  const endpoint = isSignupMode ? "/api/auth/signup" : "/api/auth/signin";
  setAuthStatus("Working...");
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
        password,
        trust_device: trustDevice,
        device_label: navigator.userAgent || "Browser device"
      })
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Authentication failed.");
    }
    isAuthenticated = true;
    updateAuthUi();
    setAuthStatus("");
    authPasswordEl.value = "";
    initSessionPromise = initSession();
  } catch (error) {
    setAuthStatus(error.message || "Authentication failed.");
  }
});

async function signOut() {
  try {
    await fetch("/api/auth/signout", { method: "POST" });
  } catch (_) {
    // Best-effort.
  }
  isAuthenticated = false;
  currentSessionId = "";
  updateAuthUi();
  if (chatMenuEl) {
    chatMenuEl.classList.add("hidden");
  }
  if (chatMenuTriggerEl) {
    chatMenuTriggerEl.setAttribute("aria-expanded", "false");
  }
  setAuthStatus("Signed out.");
}

authSignoutEl.addEventListener("click", signOut);
if (chatSignoutEl) {
  chatSignoutEl.addEventListener("click", signOut);
}

if (chatMenuTriggerEl && chatMenuEl) {
  chatMenuTriggerEl.addEventListener("click", (event) => {
    event.stopPropagation();
    const isOpen = !chatMenuEl.classList.contains("hidden");
    chatMenuEl.classList.toggle("hidden", isOpen);
    chatMenuTriggerEl.setAttribute("aria-expanded", String(!isOpen));
  });
}

if (chatSettingsEl) {
  chatSettingsEl.addEventListener("click", () => {
    if (chatMenuEl) {
      chatMenuEl.classList.add("hidden");
    }
    if (chatMenuTriggerEl) {
      chatMenuTriggerEl.setAttribute("aria-expanded", "false");
    }
    setMetaStatus("Settings coming soon.", { autoFade: true, fadeDelayMs: 2400 });
  });
}

if (chatSignoutEl) {
  chatSignoutEl.addEventListener("click", () => {
    if (!isAuthenticated) {
      setMetaStatus("You're already signed out.", { autoFade: true, fadeDelayMs: 2200 });
    }
  });
}

document.addEventListener("click", (event) => {
  if (!chatMenuEl || !chatMenuTriggerEl) return;
  const target = event.target;
  if (!(target instanceof Node)) return;
  if (chatMenuEl.contains(target) || chatMenuTriggerEl.contains(target)) return;
  chatMenuEl.classList.add("hidden");
  chatMenuTriggerEl.setAttribute("aria-expanded", "false");
});

autoSizePrompt();
initializeComposerFloating();
updateAuthUi();
checkAuth().then(() => {
  if (isAuthenticated) {
    initSessionPromise = initSession();
  }
});

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
  await attachImageFile(file);
});

clearAttachmentBtn.addEventListener("click", () => {
  attachedImageDataUrl = null;
  imageUploadInput.value = "";
  showAttachmentPill(false);
  setMetaStatus("Attachment removed.");
});

document.addEventListener("paste", async (event) => {
  const clipboardData = event.clipboardData;
  if (!clipboardData) return;
  const items = Array.from(clipboardData.items || []);
  const imageItem = items.find((item) => item.kind === "file" && item.type.startsWith("image/"));
  if (!imageItem) return;
  const imageFile = imageItem.getAsFile();
  if (!imageFile) return;
  event.preventDefault();
  dockComposer();
  await attachImageFile(imageFile);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const prompt = promptInput.value.trim();
  if (!prompt) return;
  await submitPromptText(prompt);
});

feedEl.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const quickReplyButton = target.closest(".quick-reply-inline");
  if (!(quickReplyButton instanceof HTMLButtonElement)) return;
  const reply = (quickReplyButton.dataset.reply || "").trim();
  if (!reply) return;
  promptInput.value = reply;
  dockComposer();
  await submitPromptText(reply);
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
  if (isTouchDevice) {
    dockComposer();
    return;
  }
  requestAnimationFrame(() => {
    updateComposerFloatOffset();
    composerEl.classList.add("floating");
  });
}

function dockComposer() {
  if (composerDocked) return;
  composerDocked = true;
  if (initialAssistantMessageEl) {
    initialAssistantMessageEl.classList.remove("hero");
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

window.addEventListener("resize", () => {
  if (isTouchDevice) return;
  updateComposerFloatOffset();
});
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

