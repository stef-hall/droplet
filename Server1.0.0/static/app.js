const form = document.getElementById("secretariat-form");
const promptInput = document.getElementById("prompt");
const feedEl = document.getElementById("chat-feed");
const metaEl = document.getElementById("meta");
const addAttachmentBtn = document.getElementById("add-attachment");
const imageUploadInput = document.getElementById("image-upload");
const attachmentPill = document.getElementById("attachment-pill");
const clearAttachmentBtn = document.getElementById("clear-attachment");
const SESSION_KEY = "secretariat_session_id";
let attachedImageDataUrl = null;

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
  metaEl.textContent = "Thinking...";

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
    appendMessage("assistant", data.message || "No message returned.");
    metaEl.textContent = `State: ${data.state || "UNKNOWN"}`;
    attachedImageDataUrl = null;
    imageUploadInput.value = "";
    showAttachmentPill(false);
  } catch (error) {
    appendMessage("assistant", `Error: ${error.message}`);
    metaEl.textContent = "";
  }
});
