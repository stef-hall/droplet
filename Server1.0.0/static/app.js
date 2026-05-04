const form = document.getElementById("secretariat-form");
const promptInput = document.getElementById("prompt");
const feedEl = document.getElementById("chat-feed");
const metaEl = document.getElementById("meta");
const SESSION_KEY = "secretariat_session_id";

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
      body: JSON.stringify({ prompt, session_id: getSessionId() })
    });

    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Request failed.");
    }

    setSessionId(data.session_id);
    appendMessage("assistant", data.message || "No message returned.");
    metaEl.textContent = `State: ${data.state || "UNKNOWN"}`;
  } catch (error) {
    appendMessage("assistant", `Error: ${error.message}`);
    metaEl.textContent = "";
  }
});
