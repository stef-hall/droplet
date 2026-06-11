const chatMenuTriggerEl = document.getElementById("chat-menu-trigger");
const chatMenuEl = document.getElementById("chat-menu");
const chatSettingsEl = document.getElementById("chat-settings");
const chatSignoutEl = document.getElementById("chat-signout");
const settingsBackLinkEl = document.querySelector(".settings-back-link");
const caldavSettingsFormEl = document.getElementById("caldav-settings-form");
const caldavProviderEl = document.getElementById("caldav-provider");
const caldavUsernameEl = document.getElementById("caldav-username");
const caldavPasswordEl = document.getElementById("caldav-password");
const trelloTokenEl = document.getElementById("trello-token");
const trelloBoardsFieldEl = document.getElementById("trello-boards-field");
const caldavCalendarDropdownEl = document.getElementById("caldav-calendar-dropdown");
const caldavCalendarSummaryEl = document.getElementById("caldav-calendar-summary");
const caldavCalendarOptionsEl = document.getElementById("caldav-calendar-options");
const trelloBoardDropdownEl = document.getElementById("trello-board-dropdown");
const trelloBoardSummaryEl = document.getElementById("trello-board-summary");
const trelloBoardOptionsEl = document.getElementById("trello-board-options");
const assistantModelEl = document.getElementById("assistant-model");
const settingsStatusEl = document.getElementById("settings-status");
const deleteUserFormEl = document.getElementById("delete-user-form");
const deleteConfirmationEl = document.getElementById("delete-confirmation");
const deleteUserStatusEl = document.getElementById("delete-user-status");

let currentUser = null;
let selectedCalendars = [];
let availableCalendars = [];
let calendarsDropdownOpen = false;
let selectedTrelloBoardIds = [];
let selectedTrelloBoardNames = [];
let availableTrelloBoards = [];
let trelloBoardsDropdownOpen = false;
const CALDAV_PROVIDER_URLS = {
  icloud: "https://caldav.icloud.com",
  google: "https://www.google.com/calendar/dav/",
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
  if (clean.includes("googleusercontent.com/caldav/v2") || clean.includes("google.com/calendar/dav/")) return "google";
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

function normalizeCalendarNames(values) {
  if (!Array.isArray(values)) return [];
  const seen = new Set();
  const names = [];
  for (const raw of values) {
    const name = String(raw || "").trim();
    if (!name) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    names.push(name);
  }
  return names;
}

function splitCalendarNames(raw) {
  const text = String(raw || "").trim();
  if (!text) return [];
  return normalizeCalendarNames(text.split(","));
}

function updateTrelloBoardSummary() {
  if (!trelloBoardSummaryEl) return;
  if (!selectedTrelloBoardIds.length) {
    trelloBoardSummaryEl.textContent = "No boards selected";
    return;
  }
  const idSet = new Set(selectedTrelloBoardIds.map((value) => String(value || "").trim().toLowerCase()));
  const selectedNames = availableTrelloBoards
    .filter((board) => idSet.has(String(board.id || "").trim().toLowerCase()))
    .map((board) => String(board.name || "").trim())
    .filter(Boolean);
  const namesToShow = selectedNames.length ? selectedNames : normalizeCalendarNames(selectedTrelloBoardNames);
  trelloBoardSummaryEl.textContent = namesToShow.length ? namesToShow.join(", ") : "No boards selected";
}

function renderTrelloBoardOptions() {
  if (!trelloBoardOptionsEl) return;
  trelloBoardOptionsEl.innerHTML = "";

  if (!availableTrelloBoards.length) {
    const emptyEl = document.createElement("p");
    emptyEl.textContent = "No boards found.";
    trelloBoardOptionsEl.appendChild(emptyEl);
    updateTrelloBoardSummary();
    return;
  }

  for (const board of availableTrelloBoards) {
    const boardId = String(board.id || "").trim();
    const boardName = String(board.name || "").trim();
    if (!boardId || !boardName) continue;

    const labelEl = document.createElement("label");
    labelEl.className = "settings-multi-select-option";

    const inputEl = document.createElement("input");
    inputEl.type = "checkbox";
    inputEl.value = boardId;
    inputEl.checked = selectedTrelloBoardIds.some((id) => id.toLowerCase() === boardId.toLowerCase());
    inputEl.addEventListener("change", () => {
      if (inputEl.checked) {
        selectedTrelloBoardIds = normalizeCalendarNames([...selectedTrelloBoardIds, boardId]);
      } else {
        selectedTrelloBoardIds = selectedTrelloBoardIds.filter((id) => id.toLowerCase() !== boardId.toLowerCase());
      }
      updateTrelloBoardSummary();
    });

    const textEl = document.createElement("span");
    textEl.textContent = boardName;
    labelEl.appendChild(textEl);
    labelEl.appendChild(inputEl);
    trelloBoardOptionsEl.appendChild(labelEl);
  }

  updateTrelloBoardSummary();
}

function updateCalendarSummary() {
  if (!caldavCalendarSummaryEl) return;
  if (!selectedCalendars.length) {
    caldavCalendarSummaryEl.textContent = "No calendars selected";
    return;
  }
  caldavCalendarSummaryEl.textContent = selectedCalendars.join(", ");
}

function renderCalendarOptions() {
  if (!caldavCalendarOptionsEl) return;
  caldavCalendarOptionsEl.innerHTML = "";

  if (!availableCalendars.length) {
    const emptyEl = document.createElement("p");
    emptyEl.textContent = "No calendars found. Save settings first.";
    caldavCalendarOptionsEl.appendChild(emptyEl);
    updateCalendarSummary();
    return;
  }

  for (const calendarName of availableCalendars) {
    const labelEl = document.createElement("label");
    labelEl.className = "settings-multi-select-option";

    const inputEl = document.createElement("input");
    inputEl.type = "checkbox";
    inputEl.value = calendarName;
    inputEl.checked = selectedCalendars.some((name) => name.toLowerCase() === calendarName.toLowerCase());
    inputEl.addEventListener("change", () => {
      if (inputEl.checked) {
        selectedCalendars = normalizeCalendarNames([...selectedCalendars, calendarName]);
      } else {
        selectedCalendars = selectedCalendars.filter((name) => name.toLowerCase() !== calendarName.toLowerCase());
      }
      updateCalendarSummary();
    });

    const textEl = document.createElement("span");
    textEl.textContent = calendarName;
    labelEl.appendChild(textEl);
    labelEl.appendChild(inputEl);
    caldavCalendarOptionsEl.appendChild(labelEl);
  }

  updateCalendarSummary();
}

function setCalendarsDropdownOpen(open) {
  calendarsDropdownOpen = Boolean(open);
  if (!caldavCalendarOptionsEl || !caldavCalendarSummaryEl || !caldavCalendarDropdownEl) return;
  caldavCalendarOptionsEl.classList.toggle("hidden", !calendarsDropdownOpen);
  caldavCalendarDropdownEl.classList.toggle("is-open", calendarsDropdownOpen);
  caldavCalendarSummaryEl.setAttribute("aria-expanded", calendarsDropdownOpen ? "true" : "false");
}

function setTrelloBoardsDropdownOpen(open) {
  trelloBoardsDropdownOpen = Boolean(open);
  if (!trelloBoardOptionsEl || !trelloBoardSummaryEl || !trelloBoardDropdownEl) return;
  trelloBoardOptionsEl.classList.toggle("hidden", !trelloBoardsDropdownOpen);
  trelloBoardDropdownEl.classList.toggle("is-open", trelloBoardsDropdownOpen);
  trelloBoardSummaryEl.setAttribute("aria-expanded", trelloBoardsDropdownOpen ? "true" : "false");
}

async function fetchCalendarNames() {
  try {
    const response = await fetch("/api/settings/caldav/calendars");
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Unable to fetch calendar names.");
    }
    availableCalendars = normalizeCalendarNames(data.calendars || []);
    const selectedFromServer = normalizeCalendarNames(data.selected || []);
    const availableSet = new Set(availableCalendars.map((name) => name.toLowerCase()));
    const localSelected = normalizeCalendarNames(selectedCalendars).filter((name) => availableSet.has(name.toLowerCase()));
    const serverSelected = selectedFromServer.filter((name) => availableSet.has(name.toLowerCase()));
    selectedCalendars = normalizeCalendarNames([...serverSelected, ...localSelected]);
    renderCalendarOptions();
  } catch (error) {
    availableCalendars = [];
    renderCalendarOptions();
    throw error;
  }
}

async function fetchTrelloBoards() {
  const token = trelloTokenEl ? trelloTokenEl.value.trim() : "";
  if (!token) {
    availableTrelloBoards = [];
    selectedTrelloBoards = [];
    renderTrelloBoardOptions();
    if (trelloBoardsFieldEl) {
      trelloBoardsFieldEl.classList.add("hidden");
    }
    return;
  }

  try {
    const response = await fetch("/api/settings/trello/boards");
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Unable to fetch Trello boards.");
    }

    const rawBoards = Array.isArray(data.boards) ? data.boards : [];
    availableTrelloBoards = rawBoards
      .map((board) => ({
        id: String((board && board.id) || "").trim(),
        name: String((board && board.name) || "").trim(),
      }))
      .filter((board) => board.id && board.name);
    const selectedFromServer = normalizeCalendarNames(data.selected || []);
    const availableSet = new Set(availableTrelloBoards.map((board) => String(board.id || "").toLowerCase()));
    const localSelected = normalizeCalendarNames(selectedTrelloBoardIds).filter((id) => availableSet.has(id.toLowerCase()));
    const serverSelected = selectedFromServer.filter((id) => availableSet.has(id.toLowerCase()));
    selectedTrelloBoardIds = normalizeCalendarNames([...serverSelected, ...localSelected]);
    selectedTrelloBoardNames = availableTrelloBoards
      .filter((board) => selectedTrelloBoardIds.some((id) => id.toLowerCase() === String(board.id || "").toLowerCase()))
      .map((board) => String(board.name || "").trim())
      .filter(Boolean);
    renderTrelloBoardOptions();
    if (trelloBoardsFieldEl) {
      trelloBoardsFieldEl.classList.remove("hidden");
    }
  } catch (error) {
    availableTrelloBoards = [];
    renderTrelloBoardOptions();
    if (trelloBoardsFieldEl) {
      trelloBoardsFieldEl.classList.toggle("hidden", !selectedTrelloBoardIds.length);
    }
    throw error;
  }
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
    const response = await fetch("/api/settings/caldav/full");
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Unable to load settings.");
    }

    const settings = data.settings || {};
    caldavProviderEl.value = providerFromUrl(settings.caldav_url || "");
    caldavUsernameEl.value = settings.caldav_username || "";
    selectedCalendars = normalizeCalendarNames((settings.caldav_calendars && settings.caldav_calendars.length)
      ? settings.caldav_calendars
      : splitCalendarNames(settings.caldav_calendar || ""));
    if (trelloTokenEl) {
      trelloTokenEl.value = settings.trello_token || "";
    }
    selectedTrelloBoardNames = normalizeCalendarNames((settings.trello_boards && settings.trello_boards.length)
      ? settings.trello_boards
      : splitCalendarNames(settings.trello_board || ""));
    selectedTrelloBoardIds = normalizeCalendarNames((settings.trello_board_ids && settings.trello_board_ids.length)
      ? settings.trello_board_ids
      : splitCalendarNames(settings.trello_board_ids || ""));
    renderTrelloBoardOptions();
    if (trelloBoardsFieldEl) {
      trelloBoardsFieldEl.classList.toggle("hidden", !selectedTrelloBoardIds.length);
    }
    renderCalendarOptions();
    if (assistantModelEl) {
      assistantModelEl.value = settings.assistant_model || "gpt-5.4";
    }
    caldavPasswordEl.value = "";
    setSettingsStatus(settings.has_password ? "Saved password is already on file." : "No CalDAV password saved yet.");
    try {
      await fetchCalendarNames();
    } catch (error) {
      setSettingsStatus(error.message || "Unable to fetch calendar names.", true);
    }
    try {
      await fetchTrelloBoards();
    } catch (error) {
      // Token may be missing/invalid; keep this silent unless user saves.
    }
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

  const dropdownTarget = event.target;
  if (!(dropdownTarget instanceof Node)) return;

  if (caldavCalendarDropdownEl && caldavCalendarOptionsEl && calendarsDropdownOpen && !caldavCalendarDropdownEl.contains(dropdownTarget)) {
    setCalendarsDropdownOpen(false);
  }

  if (trelloBoardDropdownEl && trelloBoardOptionsEl && trelloBoardsDropdownOpen && !trelloBoardDropdownEl.contains(dropdownTarget)) {
    setTrelloBoardsDropdownOpen(false);
  }
});

if (caldavCalendarSummaryEl) {
  caldavCalendarSummaryEl.addEventListener("click", () => {
    setCalendarsDropdownOpen(!calendarsDropdownOpen);
  });
}

if (trelloBoardSummaryEl) {
  trelloBoardSummaryEl.addEventListener("click", () => {
    setTrelloBoardsDropdownOpen(!trelloBoardsDropdownOpen);
  });
}

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
          trello_token: trelloTokenEl ? trelloTokenEl.value.trim() : "",
          trello_board_ids: selectedTrelloBoardIds,
          caldav_calendars: selectedCalendars,
          assistant_model: assistantModelEl ? assistantModelEl.value : "gpt-5.4",
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Unable to save settings.");
      }

      caldavPasswordEl.value = "";
      await fetchCalendarNames();
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

