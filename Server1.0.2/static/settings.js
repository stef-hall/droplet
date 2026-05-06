const chatMenuTriggerEl = document.getElementById("chat-menu-trigger");
const chatMenuEl = document.getElementById("chat-menu");
const chatSettingsEl = document.getElementById("chat-settings");
const chatSignoutEl = document.getElementById("chat-signout");
const caldavSettingsFormEl = document.getElementById("caldav-settings-form");
const caldavUrlEl = document.getElementById("caldav-url");
const caldavUsernameEl = document.getElementById("caldav-username");
const caldavPasswordEl = document.getElementById("caldav-password");
const caldavCalendarEl = document.getElementById("caldav-calendar");
const settingsStatusEl = document.getElementById("settings-status");
const deleteUserFormEl = document.getElementById("delete-user-form");
const deleteConfirmationEl = document.getElementById("delete-confirmation");
const deleteUserStatusEl = document.getElementById("delete-user-status");

let currentUser = null;

function setSettingsStatus(text, isError = false) {
  if (!settingsStatusEl) return;
  settingsStatusEl.textContent = text || "";
  settingsStatusEl.classList.toggle("is-error", Boolean(isError && text));
}

function setDeleteStatus(text, isError = false) {
  if (!deleteUserStatusEl) return;
  deleteUserStatusEl.textContent = text || "";
  deleteUserStatusEl.classList.toggle("is-error", Boolean(isError && text));
}

async function requireAuth() {
  const response = await fetch("/api/auth/me");
  const data = await response.json();
  if (!response.ok || !data.ok || !data.authenticated || !data.user) {
    window.location.replace("/");
    return null;
  }
  currentUser = data.user;
  if (deleteConfirmationEl) {
    deleteConfirmationEl.placeholder = currentUser.email || "your username";
  }
  return currentUser;
}

async function loadSettings() {
  setSettingsStatus("Loading...");
  try {
    const response = await fetch("/api/settings/caldav");
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Unable to load settings.");
    }

    const settings = data.settings || {};
    caldavUrlEl.value = settings.caldav_url || "";
    caldavUsernameEl.value = settings.caldav_username || "";
    caldavCalendarEl.value = settings.caldav_calendar || "";
    caldavPasswordEl.value = "";
    setSettingsStatus(settings.has_password ? "Saved password is already on file." : "No CalDAV password saved yet.");
  } catch (error) {
    setSettingsStatus(error.message || "Unable to load settings.", true);
  }
}

async function signOut() {
  try {
    await fetch("/api/auth/signout", { method: "POST" });
  } finally {
    window.location.replace("/");
  }
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
  });
}

if (chatSignoutEl) {
  chatSignoutEl.addEventListener("click", signOut);
}

document.addEventListener("click", (event) => {
  if (!chatMenuEl || !chatMenuTriggerEl) return;
  const target = event.target;
  if (!(target instanceof Node)) return;
  if (chatMenuEl.contains(target) || chatMenuTriggerEl.contains(target)) return;
  chatMenuEl.classList.add("hidden");
  chatMenuTriggerEl.setAttribute("aria-expanded", "false");
});

if (caldavSettingsFormEl) {
  caldavSettingsFormEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    setSettingsStatus("Saving...");
    try {
      const response = await fetch("/api/settings/caldav", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          caldav_url: caldavUrlEl.value.trim(),
          caldav_username: caldavUsernameEl.value.trim(),
          caldav_password: caldavPasswordEl.value,
          caldav_calendar: caldavCalendarEl.value.trim(),
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Unable to save settings.");
      }

      caldavPasswordEl.value = "";
      setSettingsStatus("Settings saved.");
    } catch (error) {
      setSettingsStatus(error.message || "Unable to save settings.", true);
    }
  });
}

if (deleteUserFormEl) {
  deleteUserFormEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const typedValue = (deleteConfirmationEl.value || "").trim();
    if (!currentUser || typedValue.toLowerCase() !== String(currentUser.email || "").trim().toLowerCase()) {
      setDeleteStatus("Enter your username exactly to confirm.", true);
      return;
    }

    const confirmed = window.confirm("Delete this user and all stored account data?");
    if (!confirmed) {
      return;
    }

    setDeleteStatus("Deleting...");
    try {
      const response = await fetch("/api/account/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: typedValue }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Unable to delete this user.");
      }

      window.location.replace("/");
    } catch (error) {
      setDeleteStatus(error.message || "Unable to delete this user.", true);
    }
  });
}

requireAuth().then((user) => {
  if (!user) return;
  loadSettings();
});
