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
const ALLOWED_DESKTOP_META_STATUSES = new Set([
  "Attachment removed",
  "Attachment added",
  "Thinking...",
  "Waiting...",
  "Done"
]);

(function () {
  const isMobile = window.matchMedia("(max-width: 768px)").matches;
  if (!isMobile) return;

  let ticking = false;

  function fixHeaderForIOSKeyboard() {
    if (ticking) return;

    ticking = true;

    requestAnimationFrame(() => {
      try {
        if (!window.visualViewport) return;

        const isStandalone =
          window.matchMedia("(display-mode: standalone)").matches ||
          window.navigator.standalone === true;

        const offsetTop = window.visualViewport.offsetTop;
        const targets = isStandalone
          ? document.querySelectorAll(".chat-header, .pwa-header-spacer")
          : document.querySelectorAll(".chat-header");

        targets.forEach((el) => {
          el.style.transform = `translateY(${offsetTop}px)`;
        });
      } finally {
        ticking = false;
      }
    });
  }

  window.visualViewport?.addEventListener("resize", fixHeaderForIOSKeyboard);
  window.visualViewport?.addEventListener("scroll", fixHeaderForIOSKeyboard);
  window.addEventListener("load", fixHeaderForIOSKeyboard);

  fixHeaderForIOSKeyboard();
})();


const isPWA =
  window.matchMedia("(display-mode: standalone)").matches ||
  window.navigator.standalone === true;

document.documentElement.classList.toggle("is-pwa", isPWA);
document.documentElement.classList.toggle("is-browser", !isPWA);

function createHeaderLeftTransition() {
  const headerLeftEl = document.querySelector(".chat-header-left");
  if (!headerLeftEl) {
    return {
      navigateWithFade: (destination) => {
        window.location.href = destination;
      }
    };
  }

  headerLeftEl.classList.add("header-left-fx-ready");
  headerLeftEl.classList.remove("header-left-visible");
  void headerLeftEl.offsetWidth;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      headerLeftEl.classList.add("header-left-visible");
    });
  });

  const fadeMs = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 160;

  return {
    navigateWithFade: (destination) => {
      headerLeftEl.classList.remove("header-left-visible");
      window.setTimeout(() => {
        window.location.href = destination;
      }, fadeMs);
    }
  };
}

const headerLeftTransition = createHeaderLeftTransition();






function getBrowserLocation() {
  return new Promise(async (resolve) => {
    if (!("geolocation" in navigator)) {
      resolve(null);
      return;
    }

    // Never trigger a prompt automatically. Only read location when permission is already granted.
    if (!navigator.permissions || !navigator.permissions.query) {
      resolve(null);
      return;
    }

    try {
      const permission = await navigator.permissions.query({ name: "geolocation" });
      if (!permission || permission.state !== "granted") {
        resolve(null);
        return;
      }
    } catch (_) {
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

function decodeHtmlEntities(value) {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = String(value ?? "");
  return textarea.value;
}

function decodeHtmlEntitiesDeep(value) {
  let out = String(value ?? "");
  for (let i = 0; i < 3; i += 1) {
    const next = decodeHtmlEntities(out);
    if (next === out) break;
    out = next;
  }
  return out;
}

function renderInlineMarkdown(text) {
  let out = escapeHtml(text);
  out = out.replace(/&lt;br\s*\/?&gt;/gi, "<br>");
  out = out.replace(/\[\[\s*send\s*:\s*([^\]]+?)\s*\]\]/gi, (_, payloadRaw) => {
    const payload = String(payloadRaw || "").trim();
    if (!payload) return "";

    const separatorIndex = payload.indexOf("|");
    const visibleText = decodeHtmlEntitiesDeep(separatorIndex >= 0 ? payload.slice(0, separatorIndex).trim() : payload);
    const replyText = decodeHtmlEntitiesDeep(separatorIndex >= 0 ? payload.slice(separatorIndex + 1).trim() : payload);
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

function parseMarkdownTableCells(line) {
  const trimmed = String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isMarkdownTableSeparator(line) {
  const cells = parseMarkdownTableCells(line);
  if (cells.length === 0) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdownTable(tableLines) {
  const headerCells = parseMarkdownTableCells(tableLines[0] || "");
  const bodyLines = tableLines.slice(2).filter((line) => String(line || "").trim());
  const headerHtml = headerCells.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("");
  const bodyHtml = bodyLines
    .map((line) => {
      const cells = parseMarkdownTableCells(line);
      const row = headerCells.map((_, idx) => `<td>${renderInlineMarkdown(cells[idx] || "")}</td>`).join("");
      return `<tr>${row}</tr>`;
    })
    .join("");
  return `<div class="md-table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

function renderMarkdown(text) {
  const source = String(text ?? "").replace(/\r\n/g, "\n");
  const lines = source.split("\n");
  const chunks = [];
  let listBuffer = [];
  let listType = null;
  let idx = 0;

  function flushList() {
    if (listBuffer.length === 0 || !listType) return;
    const items = listBuffer.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("");
    chunks.push(`<${listType}>${items}</${listType}>`);
    listBuffer = [];
    listType = null;
  }

  while (idx < lines.length) {
    const rawLine = lines[idx];
    const line = rawLine.trim();
    if (/^```/.test(line)) {
      flushList();
      const fenceMatch = line.match(/^```(\w+)?/);
      const fenceType = (fenceMatch && fenceMatch[1] ? fenceMatch[1] : "").toLowerCase();
      idx += 1;
      const codeLines = [];
      while (idx < lines.length && !/^```/.test(String(lines[idx] || "").trim())) {
        codeLines.push(lines[idx]);
        idx += 1;
      }
      if (idx < lines.length && /^```/.test(String(lines[idx] || "").trim())) {
        idx += 1;
      }
      const renderedCode = codeLines
        .map((raw) => {
          const lineText = String(raw ?? "");
          const escaped = escapeHtml(lineText);
          if (/[+]\d+/.test(lineText)) {
            return escaped.replace(/([+]\d+)/, '<span class="report-pos">$1</span>');
          }
          if (/[-]\d+/.test(lineText)) {
            return escaped.replace(/([-]\d+)/, '<span class="report-neg">$1</span>');
          }
          if (/\b\d+\b/.test(lineText)) {
            return escaped.replace(/(\b\d+\b)/, '<span class="report-neutral">$1</span>');
          }
          return escaped;
        })
        .join("\n");
      if (fenceType === "summary") {
        chunks.push(`<section class="md-summary"><pre><code><span class="md-summary-title">Summary</span>\n${renderedCode}</code></pre></section>`);
      } else {
        chunks.push(`<pre><code>${renderedCode}</code></pre>`);
      }
      continue;
    }
    if (!line) {
      flushList();
      idx += 1;
      continue;
    }
    if (line.includes("|") && idx + 1 < lines.length && isMarkdownTableSeparator(lines[idx + 1])) {
      flushList();
      const tableLines = [lines[idx], lines[idx + 1]];
      idx += 2;
      while (idx < lines.length) {
        const rowLine = lines[idx];
        const rowTrimmed = String(rowLine || "").trim();
        if (!rowTrimmed || !rowTrimmed.includes("|")) break;
        tableLines.push(rowLine);
        idx += 1;
      }
      chunks.push(renderMarkdownTable(tableLines));
      continue;
    }
    if (/^###\s+/.test(line)) {
      flushList();
      chunks.push(`<h3>${renderInlineMarkdown(line.replace(/^###\s+/, ""))}</h3>`);
      idx += 1;
      continue;
    }
    if (/^##\s+/.test(line)) {
      flushList();
      chunks.push(`<h2>${renderInlineMarkdown(line.replace(/^##\s+/, ""))}</h2>`);
      idx += 1;
      continue;
    }
    if (/^#\s+/.test(line)) {
      flushList();
      chunks.push(`<h1>${renderInlineMarkdown(line.replace(/^#\s+/, ""))}</h1>`);
      idx += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const itemText = line.replace(/^[-*]\s+/, "");
      if (listType && listType !== "ul") {
        flushList();
      }
      listType = "ul";
      listBuffer.push(itemText);
      idx += 1;
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const itemText = line.replace(/^\d+\.\s+/, "");
      if (listType && listType !== "ol") {
        flushList();
      }
      listType = "ol";
      listBuffer.push(itemText);
      idx += 1;
      continue;
    }
    flushList();
    chunks.push(`<p>${renderInlineMarkdown(line)}</p>`);
    idx += 1;
  }
  flushList();
  return chunks.join("");
}

function appendMessage(role, text, { hadAttachment = false } = {}) {
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
  if (role === "user" && hadAttachment) {
    const attachmentCount = document.createElement("div");
    attachmentCount.className = "user-attachment-count";
    attachmentCount.textContent = "+1";
    item.appendChild(attachmentCount);
  }
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
  const normalizedDesktopStatus = normalizeDesktopMetaStatus(text);
  const safeText = normalizedDesktopStatus || "";
  if (doneFadeTimer) {
    clearTimeout(doneFadeTimer);
    doneFadeTimer = null;
  }
  metaEl.classList.remove("meta-fading");
  metaEl.style.removeProperty("opacity");
  metaEl.textContent = safeText;

  if (autoFade && safeText) {
    doneFadeTimer = setTimeout(() => {
      metaEl.classList.add("meta-fading");
    }, fadeDelayMs);
  }
}

function normalizeDesktopMetaStatus(text) {
  if (isTouchDevice) {
    return text || "";
  }
  const raw = String(text || "").trim();
  if (!raw) return "";
  const compact = raw.toLowerCase().replace(/[.\s]+$/g, "");
  if (compact === "attachment removed" || compact === "attchment removed") return "Attachment removed";
  if (compact === "attachment added" || compact === "image attached") return "Attachment added";
  if (compact.startsWith("thinking")) return "Thinking...";
  if (compact.startsWith("waiting")) return "Waiting...";
  if (compact.startsWith("done")) return "Done";
  if (ALLOWED_DESKTOP_META_STATUSES.has(raw)) return raw;
  return "";
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

function clearAttachedImage() {
  attachedImageDataUrl = null;
  imageUploadInput.value = "";
  showAttachmentPill(false);
}

async function attachImageFile(file) {
  if (!file) return false;
  try {
    attachedImageDataUrl = await toDataUrl(file);
    showAttachmentPill(true);
    setMetaStatus("Attachment added");
    return true;
  } catch (_) {
    attachedImageDataUrl = null;
    showAttachmentPill(false);
    setMetaStatus("Attachment removed");
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

  const hadAttachment = Boolean(attachedImageDataUrl);
  appendMessage("user", prompt, { hadAttachment });
  promptInput.value = "";
  autoSizePrompt();
  const imageDataUrlForRequest = attachedImageDataUrl;
  if (imageDataUrlForRequest) {
    clearAttachedImage();
  }
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
        image_data_url: imageDataUrlForRequest,
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
            updateThinkingLabel(label);
            const compact = String(label).trim().toLowerCase().replace(/[.\s]+$/g, "");
            if (compact.startsWith("waiting")) {
              setMetaStatus("Waiting...");
            } else if (compact.startsWith("done")) {
              setMetaStatus("Done");
            } else {
              setMetaStatus("Thinking...");
            }
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
      DONE: "Done"
    };
    const mappedState = stateLabelMap[data.state] || data.state || "";
    setMetaStatus(mappedState, { autoFade: data.state === "DONE", fadeDelayMs: 5000 });
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
    headerLeftTransition.navigateWithFade("/settings");
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
  clearAttachedImage();
  setMetaStatus("Attachment removed");
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
  const reply = decodeHtmlEntitiesDeep(quickReplyButton.dataset.reply || "").trim();
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


