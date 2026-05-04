const form = document.getElementById("secretariat-form");
const promptInput = document.getElementById("prompt");
const outputEl = document.getElementById("output");
const metaEl = document.getElementById("meta");
const SESSION_KEY = "secretariat_session_id";

function getSessionId() {
  return localStorage.getItem(SESSION_KEY) || "";
}

function setSessionId(sessionId) {
  if (!sessionId) return;
  localStorage.setItem(SESSION_KEY, sessionId);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const prompt = promptInput.value.trim();
  if (!prompt) {
    outputEl.textContent = "Please enter a prompt.";
    outputEl.className = "output error";
    metaEl.textContent = "";
    return;
  }

  outputEl.textContent = "Thinking...";
  outputEl.className = "output";
  metaEl.textContent = "";

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
    outputEl.textContent = data.message || "No message returned.";
    outputEl.className = "output ok";
    metaEl.textContent = `State: ${data.state || "UNKNOWN"}`;
  } catch (error) {
    outputEl.textContent = `Error: ${error.message}`;
    outputEl.className = "output error";
    metaEl.textContent = "";
  }
});
