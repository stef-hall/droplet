const chatMenuTriggerEl = document.getElementById("chat-menu-trigger");
const chatMenuEl = document.getElementById("chat-menu");
const chatSettingsEl = document.getElementById("chat-settings");
const chatSignoutEl = document.getElementById("chat-signout");
const settingsBackLinkEl = document.querySelector(".settings-back-link");
const caldavSettingsFormEl = document.getElementById("caldav-settings-form");
const caldavProviderEl = document.getElementById("caldav-provider");
const caldavUsernameEl = document.getElementById("caldav-username");
const caldavPasswordEl = document.getElementById("caldav-password");
const caldavCalendarEl = document.getElementById("caldav-calendar");
const assistantModelEl = document.getElementById("assistant-model");
const settingsStatusEl = document.getElementById("settings-status");
const deleteUserFormEl = document.getElementById("delete-user-form");
const deleteConfirmationEl = document.getElementById("delete-confirmation");
const deleteUserStatusEl = document.getElementById("delete-user-status");

let currentUser = null;
const CALDAV_PROVIDER_URLS = {
  icloud: "https://caldav.icloud.com",
  google: "https://apidata.googleusercontent.com/caldav/v2/",
};

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

if (settingsBackLinkEl) {
  settingsBackLinkEl.addEventListener("click", (event) => {
    event.preventDefault();
    headerLeftTransition.navigateWithFade("/");
  });
}

function providerFromUrl(url) {
  const clean = String(url || "").trim().toLowerCase();
  if (clean === CALDAV_PROVIDER_URLS.google.toLowerCase()) return "google";
  return "icloud";
}

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
    caldavProviderEl.value = providerFromUrl(settings.caldav_url || "");
    caldavUsernameEl.value = settings.caldav_username || "";
    caldavCalendarEl.value = settings.caldav_calendar || "";
    if (assistantModelEl) {
      assistantModelEl.value = settings.assistant_model || "gpt-5.4-mini";
    }
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
          caldav_url: CALDAV_PROVIDER_URLS[caldavProviderEl.value] || CALDAV_PROVIDER_URLS.icloud,
          caldav_username: caldavUsernameEl.value.trim(),
          caldav_password: caldavPasswordEl.value,
          caldav_calendar: caldavCalendarEl.value.trim(),
          assistant_model: assistantModelEl ? assistantModelEl.value : "gpt-5.4-mini",
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

