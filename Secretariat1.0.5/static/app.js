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
const navDrawerTriggerEl = document.getElementById("nav-drawer-trigger");
const navDrawerEl = document.getElementById("nav-drawer");
const navDrawerOverlayEl = document.getElementById("nav-drawer-overlay");
const darkModeToggleEl = document.getElementById("dark-mode-toggle");
const stickyNotesToggleEl = document.getElementById("sticky-notes-toggle");
const stickyNotesToggleIconEl = document.getElementById("sticky-notes-toggle-icon");
const themeColorMetaEl = document.getElementById("theme-color-meta");
const themeToggleIconLeftEl = document.getElementById("theme-toggle-icon-left");
const themeToggleIconRightEl = document.getElementById("theme-toggle-icon-right");
const authSubtitleEl = document.getElementById("auth-subtitle");
const authStatusEl = document.getElementById("auth-status");
const authTrustDeviceEl = document.getElementById("auth-trust-device");
const authTrustWrapEl = document.getElementById("auth-trust-wrap");
const stickyNoteEffectsLayerEl = document.getElementById("sticky-note-effects-layer");
const stickyNoteLayerEl = document.getElementById("sticky-note-layer");
const stickyNoteDockSlotEl = document.getElementById("sticky-note-dock-slot");
const stickyNoteDeleteTargetEl = document.getElementById("sticky-note-delete-target");
const MAX_PROMPT_HEIGHT = 180;
const STICKY_NOTE_DOCK_THRESHOLD = 110;
const STICKY_NOTE_DOCK_HYSTERESIS = 24;
const STICKY_NOTE_STOWED_PEEK_WIDTH = 118;
const STICKY_NOTE_DEFAULT_WIDTH = 182;
const STICKY_NOTE_STOWED_HEIGHT = 40;
const STICKY_NOTE_STACK_MIN_GAP = 46;
const STICKY_NOTE_STACK_GAP_PADDING = 11;
const STICKY_NOTE_SAFE_TOP = 64;
const STICKY_NOTE_MIN_WIDTH = 160;
const STICKY_NOTE_MIN_HEIGHT = 120;
const MAX_STICKY_NOTE_INPUT_HEIGHT = 220;
const MOBILE_STICKY_NOTE_VISIBLE_COUNT = 5;
const MOBILE_STICKY_NOTE_ENTRY_DISTANCE = 72;
const MOBILE_STICKY_NOTE_ENTRY_OFFSET = STICKY_NOTE_DEFAULT_WIDTH + 12;
const MOBILE_STICKY_NOTE_DRAG_THRESHOLD = 12;
const MOBILE_STICKY_NOTE_DRAG_HOLD_MS = 180;
const STICKY_NOTE_COLOR_CLASSES = [
  "color-yellow",
  "color-amber",
  "color-orange",
  "color-coral",
  "color-red",
  "color-rose",
  "color-lime",
  "color-blue",
  "color-cyan",
  "color-green",
  "color-violet"
];
const isTouchDevice = window.matchMedia("(pointer: coarse)").matches;
let attachedImageDataUrl = null;
let composerDocked = false;
let doneFadeTimer = null;
let detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
let detectedLocation = null;
let currentSessionId = "";
let currentUserId = "";
let initSessionPromise = null;
let isSignupMode = false;
let isAuthenticated = false;
let stickyNoteMobileViewportEl = null;
let stickyNoteMobileContentEl = null;
let stickyNoteMobileScrollTop = 0;
let stickyNoteMobileMaxScrollTop = 0;
const stickyNoteSaveTimers = new Map();
const ALLOWED_DESKTOP_META_STATUSES = new Set([
  "Attachment removed",
  "Attachment added",
  "Thinking...",
  "Waiting...",
  "Done"
]);
const THEME_STORAGE_KEY = "secretariat-theme";
const STICKY_NOTES_ENABLED_STORAGE_KEY = "secretariat-sticky-notes-enabled";
const STICKY_NOTE_LAYOUT_STORAGE_KEY = "secretariat-sticky-layout";

function isStickyNotesEnabled() {
  return !stickyNotesToggleEl || stickyNotesToggleEl.checked;
}

function refreshStickyNotesView() {
  if (!isStickyNotesEnabled() || !isAuthenticated) {
    clearStickyNotes();
    return;
  }
  void loadStickyNotes();
}

function updateStickyNotesToggleVisual() {
  if (!stickyNotesToggleIconEl || !stickyNotesToggleEl) return;
  stickyNotesToggleIconEl.classList.toggle("is-disabled", !stickyNotesToggleEl.checked);
}

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

function applyTheme(theme) {
  const normalizedTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", normalizedTheme);
  const prefix = normalizedTheme === "dark" ? "dm" : "lm";
  if (themeToggleIconLeftEl && themeToggleIconRightEl) {
    themeToggleIconLeftEl.classList.add("is-transitioning");
    themeToggleIconRightEl.classList.add("is-transitioning");
    window.setTimeout(() => {
      themeToggleIconLeftEl.src = `/static/icons/${prefix}_on.png`;
      themeToggleIconRightEl.src = `/static/icons/${prefix}_moon.png`;
      themeToggleIconLeftEl.classList.remove("is-transitioning");
      themeToggleIconRightEl.classList.remove("is-transitioning");
    }, 90);
  }
  if (themeColorMetaEl) {
    themeColorMetaEl.setAttribute("content", normalizedTheme === "dark" ? "#171a1d" : "#f4f4f4");
  }
  if (darkModeToggleEl) {
    darkModeToggleEl.checked = normalizedTheme === "dark";
  }
}

function initializeTheme() {
  let storedTheme = "";
  try {
    storedTheme = localStorage.getItem(THEME_STORAGE_KEY) || "";
  } catch (_) {
    storedTheme = "";
  }
  if (storedTheme === "dark" || storedTheme === "light") {
    applyTheme(storedTheme);
    return;
  }
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(prefersDark ? "dark" : "light");
}

initializeTheme();

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function getStickyNoteLayoutStorageKey() {
  const scope = String(currentUserId || "guest").trim() || "guest";
  return `${STICKY_NOTE_LAYOUT_STORAGE_KEY}:${scope}`;
}

function loadStickyNoteLayoutState() {
  try {
    const raw = localStorage.getItem(getStickyNoteLayoutStorageKey());
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    return {};
  }
}

function saveStickyNoteLayoutState(layoutState) {
  try {
    localStorage.setItem(getStickyNoteLayoutStorageKey(), JSON.stringify(layoutState || {}));
  } catch (_) {
    // Ignore storage failures.
  }
}

function getStickyNoteLayoutEntry(noteEl) {
  const listName = String(noteEl?.dataset?.listName || "").trim();
  if (!listName) return null;
  const layoutState = loadStickyNoteLayoutState();
  const entry = layoutState[listName];
  return entry && typeof entry === "object" ? entry : null;
}

function saveStickyNoteLayoutEntry(noteEl, partialEntry = {}) {
  const listName = String(noteEl?.dataset?.listName || "").trim();
  if (!listName) return;
  const layoutState = loadStickyNoteLayoutState();
  const previous = layoutState[listName];
  layoutState[listName] = {
    ...(previous && typeof previous === "object" ? previous : {}),
    ...partialEntry
  };
  saveStickyNoteLayoutState(layoutState);
}

function normalizeStickyNoteColorClass(value) {
  return STICKY_NOTE_COLOR_CLASSES.includes(value) ? value : null;
}

function syncStickyNoteLayoutState() {
  const layoutState = loadStickyNoteLayoutState();
  const liveNames = new Set();
  const stowedNotes = [];

  getStickyNotes().forEach((noteEl) => {
    const listName = String(noteEl.dataset.listName || "").trim();
    if (!listName) return;
    liveNames.add(listName);

    const left = Number.parseFloat(noteEl.style.left);
    const top = Number.parseFloat(noteEl.style.top);
    const width = Number.parseFloat(noteEl.dataset.preferredWidth || "");
    const height = Number.parseFloat(noteEl.dataset.preferredHeight || "");
    const entry = {
      isStowed: noteEl.classList.contains("is-stowed")
    };
    if (Number.isFinite(left)) entry.left = Math.round(left);
    if (Number.isFinite(top)) entry.top = Math.round(top);
    if (Number.isFinite(width)) entry.width = Math.round(width);
    if (Number.isFinite(height)) entry.height = Math.round(height);
    layoutState[listName] = {
      ...(layoutState[listName] && typeof layoutState[listName] === "object" ? layoutState[listName] : {}),
      ...entry
    };
    if (entry.isStowed) {
      stowedNotes.push(noteEl);
    }
  });

  stowedNotes.forEach((noteEl, index) => {
    const listName = String(noteEl.dataset.listName || "").trim();
    if (!listName || !layoutState[listName]) return;
    layoutState[listName].order = index;
  });

  Object.keys(layoutState).forEach((listName) => {
    if (!liveNames.has(listName)) {
      delete layoutState[listName];
    }
  });

  saveStickyNoteLayoutState(layoutState);
}

function setStickyNoteDockHintVisible(visible) {
  if (!stickyNoteEffectsLayerEl) return;
  stickyNoteEffectsLayerEl.classList.toggle("show-dock-hint", visible);
}

function setStickyNoteDockSlotVisible(visible) {
  if (!stickyNoteEffectsLayerEl) return;
  stickyNoteEffectsLayerEl.classList.toggle("show-dock-slot", visible);
}

function setStickyNoteDeleteTargetVisible(visible) {
  if (!stickyNoteEffectsLayerEl) return;
  stickyNoteEffectsLayerEl.classList.toggle("show-delete-target", visible);
}

function setStickyNoteDeleteTargetActive(active) {
  if (!stickyNoteEffectsLayerEl) return;
  stickyNoteEffectsLayerEl.classList.toggle("delete-target-active", active);
}

function setStickyNoteDragLayerActive(active) {
  if (!stickyNoteLayerEl) return;
  stickyNoteLayerEl.classList.toggle("is-drag-active", active);
}

function getStickyNotes() {
  if (!stickyNoteLayerEl) return [];
  return Array.from(stickyNoteLayerEl.querySelectorAll(".sticky-note"));
}

function ensureStickyNoteMobileViewport() {
  if (!stickyNoteLayerEl || !isTouchDevice) return null;
  if (stickyNoteMobileViewportEl instanceof HTMLElement && stickyNoteMobileContentEl instanceof HTMLElement) {
    return stickyNoteMobileViewportEl;
  }
  stickyNoteMobileViewportEl = document.createElement("div");
  stickyNoteMobileViewportEl.className = "sticky-note-mobile-viewport";
  stickyNoteMobileContentEl = document.createElement("div");
  stickyNoteMobileContentEl.className = "sticky-note-mobile-content";
  stickyNoteMobileViewportEl.appendChild(stickyNoteMobileContentEl);
  stickyNoteLayerEl.appendChild(stickyNoteMobileViewportEl);
  return stickyNoteMobileViewportEl;
}

function setStickyNoteMobileScrollTop(nextScrollTop) {
  const clampedScrollTop = clamp(nextScrollTop, 0, stickyNoteMobileMaxScrollTop);
  if (Math.abs(clampedScrollTop - stickyNoteMobileScrollTop) < 0.5) return;
  stickyNoteMobileScrollTop = clampedScrollTop;
  getStickyNotes()
    .filter((noteEl) => noteEl.classList.contains("is-stowed") && !noteEl.classList.contains("is-dragging"))
    .forEach((noteEl) => {
      noteEl.style.transition = "";
    });
  layoutStowedStickyNotes();
}

function clearStickyNotes() {
  for (const timerId of stickyNoteSaveTimers.values()) {
    clearTimeout(timerId);
  }
  stickyNoteSaveTimers.clear();
  getStickyNotes().forEach((noteEl) => noteEl.remove());
  stickyNoteMobileScrollTop = 0;
  stickyNoteMobileMaxScrollTop = 0;
  setStickyNoteDockHintVisible(false);
  setStickyNoteDockSlotVisible(false);
  setStickyNoteDeleteTargetVisible(false);
  setStickyNoteDeleteTargetActive(false);
}

function isPointerOverStickyNoteDeleteTarget(pointerX, pointerY) {
  if (!(stickyNoteDeleteTargetEl instanceof HTMLElement)) return false;
  const rect = stickyNoteDeleteTargetEl.getBoundingClientRect();
  return pointerX >= rect.left && pointerX <= rect.right && pointerY >= rect.top && pointerY <= rect.bottom;
}

function isStickyNoteTouchingDeleteTarget(noteEl) {
  if (!(stickyNoteDeleteTargetEl instanceof HTMLElement) || !(noteEl instanceof HTMLElement)) return false;
  const noteRect = noteEl.getBoundingClientRect();
  const targetRect = stickyNoteDeleteTargetEl.getBoundingClientRect();
  return (
    noteRect.left < targetRect.right &&
    noteRect.right > targetRect.left &&
    noteRect.top < targetRect.bottom &&
    noteRect.bottom > targetRect.top
  );
}

function removeStickyNoteLayoutEntry(noteEl) {
  const listName = String(noteEl?.dataset?.listName || "").trim();
  if (!listName) return;
  const layoutState = loadStickyNoteLayoutState();
  if (layoutState[listName]) {
    delete layoutState[listName];
    saveStickyNoteLayoutState(layoutState);
  }
}

async function deleteStickyNote(noteEl) {
  const listName = String(noteEl?.dataset?.listName || "").trim();
  if (!listName || !isAuthenticated) return false;
  try {
    const response = await fetch("/api/lists/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ list_name: listName })
    });
    const data = await response.json();
    if (response.status === 401) {
      isAuthenticated = false;
      currentUserId = "";
      updateAuthUi();
      clearStickyNotes();
      return false;
    }
    return Boolean(response.ok && data.ok);
  } catch (_) {
    return false;
  }
}

function autoSizeStickyNoteInput(inputEl, noteEl = null) {
  if (!(inputEl instanceof HTMLTextAreaElement)) return;
  inputEl.style.height = "100%";
  inputEl.style.overflowY = "auto";
}

function isStickyNoteNearDock(noteEl, left, pointerX = null, currentlyNearDock = false) {
  const edgeX = Number.isFinite(pointerX) ? pointerX : left + noteEl.offsetWidth;
  const enterThreshold = window.innerWidth - STICKY_NOTE_DOCK_THRESHOLD;
  const exitThreshold = enterThreshold - STICKY_NOTE_DOCK_HYSTERESIS;
  return currentlyNearDock ? edgeX >= exitThreshold : edgeX >= enterThreshold;
}

function updateStickyNoteDockPreview(noteEl, left) {
  const nearDock = isStickyNoteNearDock(noteEl, left);
  noteEl.classList.toggle("is-near-dock", nearDock);
  setStickyNoteDockHintVisible(nearDock);
  return nearDock;
}

function getStickyNoteDockIndex(pointerY, draggingNoteEl) {
  const stowedNotes = getStickyNotes().filter((noteEl) => noteEl.classList.contains("is-stowed") && noteEl !== draggingNoteEl);
  for (let index = 0; index < stowedNotes.length; index += 1) {
    const rect = stowedNotes[index].getBoundingClientRect();
    const midpoint = rect.top + rect.height / 2;
    if (pointerY < midpoint) {
      return index;
    }
  }
  return stowedNotes.length;
}

function getStickyNoteDockSlotSize(draggingNoteEl = null) {
  const sampleNoteEl = getStickyNotes().find((noteEl) => noteEl.classList.contains("is-stowed") && noteEl !== draggingNoteEl) || draggingNoteEl;
  if (!sampleNoteEl) {
    return { width: STICKY_NOTE_STOWED_PEEK_WIDTH, height: STICKY_NOTE_STOWED_HEIGHT };
  }
  return {
    width: STICKY_NOTE_STOWED_PEEK_WIDTH,
    height: Math.max(STICKY_NOTE_STOWED_HEIGHT, Math.round(sampleNoteEl.offsetHeight))
  };
}

function getStickyNoteStackTopStart() {
  const headerEl = document.querySelector(".chat-header");
  if (!(headerEl instanceof HTMLElement)) {
    return STICKY_NOTE_SAFE_TOP + STICKY_NOTE_STACK_GAP_PADDING;
  }
  const headerBottom = Math.round(headerEl.getBoundingClientRect().bottom);
  return Math.max(STICKY_NOTE_SAFE_TOP, headerBottom + STICKY_NOTE_STACK_GAP_PADDING);
}

function positionStickyNoteDockSlot(index, draggingNoteEl = null) {
  if (!stickyNoteDockSlotEl) return;
  const slotSize = getStickyNoteDockSlotSize(draggingNoteEl);
  const stackTopStart = getStickyNoteStackTopStart();
  const stowedLeft = isTouchDevice
    ? Math.max(0, window.innerWidth - STICKY_NOTE_DEFAULT_WIDTH + (STICKY_NOTE_DEFAULT_WIDTH - STICKY_NOTE_STOWED_PEEK_WIDTH))
    : Math.max(0, window.innerWidth - STICKY_NOTE_STOWED_PEEK_WIDTH);
  stickyNoteDockSlotEl.style.right = "auto";
  stickyNoteDockSlotEl.style.left = `${Math.round(stowedLeft)}px`;
  stickyNoteDockSlotEl.style.width = `${slotSize.width}px`;
  stickyNoteDockSlotEl.style.height = `${slotSize.height}px`;
  const stackGap = getStickyNoteStackGap();
  const desiredTop = stackTopStart + index * stackGap - (isTouchDevice ? stickyNoteMobileScrollTop : 0);
  const minTop = isTouchDevice ? 0 : stackTopStart;
  const safeTop = clamp(desiredTop, minTop, Math.max(minTop, window.innerHeight - stickyNoteDockSlotEl.offsetHeight));
  stickyNoteDockSlotEl.style.top = `${Math.round(safeTop)}px`;
}

function getStickyNoteStackGap(draggingNoteEl = null) {
  const sampleNoteEl = getStickyNotes().find((noteEl) => noteEl.classList.contains("is-stowed") && noteEl !== draggingNoteEl) || draggingNoteEl;
  if (!sampleNoteEl) return STICKY_NOTE_STACK_MIN_GAP;
  return Math.max(STICKY_NOTE_STACK_MIN_GAP, Math.round(sampleNoteEl.offsetHeight + STICKY_NOTE_STACK_GAP_PADDING));
}

function getStickyNoteRevealCurve(progress) {
  const clampedProgress = clamp(progress, 0, 1);
  return 1 - ((1 - clampedProgress) ** 2.2);
}

function layoutStowedStickyNotes({ draggingNoteEl = null, previewIndex = null } = {}) {
  const stowedNotes = getStickyNotes().filter((noteEl) => noteEl.classList.contains("is-stowed") && noteEl !== draggingNoteEl);
  const stackGap = getStickyNoteStackGap(draggingNoteEl);
  const stackTopStart = getStickyNoteStackTopStart();
  if (isTouchDevice) {
    const viewportEl = ensureStickyNoteMobileViewport();
    const contentEl = stickyNoteMobileContentEl;
    const sampleHeight = getStickyNoteDockSlotSize(draggingNoteEl).height;
    const visibleStackHeight = Math.max(sampleHeight, (Math.max(1, MOBILE_STICKY_NOTE_VISIBLE_COUNT) - 1) * stackGap + sampleHeight);
    const viewportHeight = Math.min(window.innerHeight, Math.round(stackTopStart + visibleStackHeight + sampleHeight + MOBILE_STICKY_NOTE_ENTRY_DISTANCE));
    const contentHeight = Math.round(stackTopStart + Math.max(0, stowedNotes.length - 1) * stackGap + (sampleHeight * 2) + MOBILE_STICKY_NOTE_ENTRY_DISTANCE);
    stickyNoteMobileMaxScrollTop = Math.max(0, contentHeight - viewportHeight);
    stickyNoteMobileScrollTop = clamp(stickyNoteMobileScrollTop, 0, stickyNoteMobileMaxScrollTop);
    stickyNoteLayerEl?.classList.toggle("is-mobile-scroll-enabled", stowedNotes.length > 0);
    if (viewportEl instanceof HTMLElement && contentEl instanceof HTMLElement) {
      viewportEl.style.height = `${viewportHeight}px`;
      viewportEl.style.width = `${STICKY_NOTE_DEFAULT_WIDTH}px`;
      contentEl.style.height = `${contentHeight}px`;
    }
    stowedNotes.forEach((noteEl, index) => {
      const slotIndex = previewIndex !== null && index >= previewIndex ? index + 1 : index;
      const desiredTop = stackTopStart + slotIndex * stackGap - stickyNoteMobileScrollTop;
      const revealProgress = clamp((viewportHeight - desiredTop) / MOBILE_STICKY_NOTE_ENTRY_DISTANCE, 0, 1);
      const revealCurve = getStickyNoteRevealCurve(revealProgress);
      const stowedLeft = STICKY_NOTE_DEFAULT_WIDTH - STICKY_NOTE_STOWED_PEEK_WIDTH;
      const revealOffset = Math.round((1 - revealCurve) * MOBILE_STICKY_NOTE_ENTRY_OFFSET);
      noteEl.classList.toggle("is-stowed-preview", previewIndex !== null);
      if (contentEl instanceof HTMLElement && noteEl.parentElement !== contentEl) {
        contentEl.appendChild(noteEl);
      }
      if (previewIndex === null) {
        noteEl.style.transition = "";
      }
      noteEl.style.left = `${Math.round(stowedLeft + revealOffset)}px`;
      noteEl.style.top = `${Math.round(desiredTop)}px`;
    });
  } else {
    stowedNotes.forEach((noteEl, index) => {
      const slotIndex = previewIndex !== null && index >= previewIndex ? index + 1 : index;
      const maxTop = Math.max(0, window.innerHeight - noteEl.offsetHeight);
      const desiredTop = stackTopStart + slotIndex * stackGap;
      const safeTop = clamp(desiredTop, stackTopStart, Math.max(stackTopStart, maxTop));
      const stowedLeft = Math.max(0, window.innerWidth - STICKY_NOTE_STOWED_PEEK_WIDTH);
      noteEl.classList.toggle("is-stowed-preview", previewIndex !== null);
      if (stickyNoteLayerEl && noteEl.parentElement !== stickyNoteLayerEl) {
        stickyNoteLayerEl.appendChild(noteEl);
      }
      noteEl.style.left = `${Math.round(stowedLeft)}px`;
      noteEl.style.top = `${Math.round(safeTop)}px`;
    });
  }
  if (previewIndex !== null) {
    positionStickyNoteDockSlot(previewIndex, draggingNoteEl);
    setStickyNoteDockSlotVisible(true);
  } else {
    setStickyNoteDockSlotVisible(false);
    stowedNotes.forEach((noteEl) => noteEl.classList.remove("is-stowed-preview"));
  }
}

function commitStickyNoteDockOrder(noteEl, insertIndex) {
  if (!stickyNoteLayerEl) return;
  const stowedNotes = getStickyNotes().filter((candidate) => candidate.classList.contains("is-stowed") && candidate !== noteEl);
  const clampedIndex = clamp(insertIndex, 0, stowedNotes.length);
  const anchorEl = stowedNotes[clampedIndex] || null;
  const stowedParentEl = isTouchDevice && stickyNoteMobileContentEl instanceof HTMLElement
    ? stickyNoteMobileContentEl
    : stickyNoteLayerEl;
  if (anchorEl) {
    stowedParentEl.insertBefore(noteEl, anchorEl);
  } else {
    stowedParentEl.appendChild(noteEl);
  }
  syncStickyNoteLayoutState();
}

async function saveStickyNote(noteEl) {
  const inputEl = noteEl.querySelector(".sticky-note-input");
  const listName = String(noteEl.dataset.listName || "").trim();
  if (!(inputEl instanceof HTMLTextAreaElement) || !listName || !isAuthenticated) return;

  noteEl.dataset.saveState = "saving";
  try {
    const response = await fetch("/api/lists/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        list_name: listName,
        content: inputEl.value
      })
    });
    const data = await response.json();
    if (response.status === 401) {
      isAuthenticated = false;
      currentUserId = "";
      updateAuthUi();
      clearStickyNotes();
      return;
    }
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Failed to save list.");
    }
    noteEl.dataset.saveState = "saved";
  } catch (_) {
    noteEl.dataset.saveState = "error";
  }
}

function queueStickyNoteSave(noteEl) {
  const key = String(noteEl.dataset.listName || "");
  if (!key) return;
  const existingTimer = stickyNoteSaveTimers.get(key);
  if (existingTimer) {
    clearTimeout(existingTimer);
  }
  noteEl.dataset.saveState = "dirty";
  const timerId = window.setTimeout(() => {
    stickyNoteSaveTimers.delete(key);
    void saveStickyNote(noteEl);
  }, 350);
  stickyNoteSaveTimers.set(key, timerId);
}

function createStickyNote(listEntry, colorClassName) {
  const noteEl = document.createElement("article");
  const savedLayoutEntry = loadStickyNoteLayoutState()[String(listEntry.list_name || "")] || null;
  const startsStowed = !savedLayoutEntry || savedLayoutEntry.isStowed !== false;
  const persistedColorClass = normalizeStickyNoteColorClass(savedLayoutEntry?.colorClass);
  const appliedColorClass = persistedColorClass || normalizeStickyNoteColorClass(colorClassName) || STICKY_NOTE_COLOR_CLASSES[0];
  noteEl.className = `sticky-note ${startsStowed ? "is-stowed" : ""} ${appliedColorClass}`.trim();
  noteEl.setAttribute("aria-label", `Draggable note for ${listEntry.list_name}`);
  noteEl.dataset.listName = String(listEntry.list_name || "");

  noteEl.innerHTML = `
    <div class="sticky-note-tape" aria-hidden="true"></div>
    <div class="sticky-note-title"></div>
    <div class="sticky-note-body">
      <textarea class="sticky-note-input" rows="4" spellcheck="false"></textarea>
    </div>
    <div class="sticky-note-resize-handle" aria-hidden="true"></div>
  `;

  const titleEl = noteEl.querySelector(".sticky-note-title");
  const inputEl = noteEl.querySelector(".sticky-note-input");
  const resizeHandleEl = noteEl.querySelector(".sticky-note-resize-handle");
  if (titleEl) {
    titleEl.textContent = String(listEntry.list_name || "");
  }
  if (inputEl instanceof HTMLTextAreaElement) {
    inputEl.value = String(listEntry.content || "");
    autoSizeStickyNoteInput(inputEl, noteEl);
    inputEl.addEventListener("input", () => {
      autoSizeStickyNoteInput(inputEl, noteEl);
      queueStickyNoteSave(noteEl);
    });
  }

  const savedLeft = Number.parseFloat(savedLayoutEntry?.left);
  const savedTop = Number.parseFloat(savedLayoutEntry?.top);
  const savedWidth = Number.parseFloat(savedLayoutEntry?.width);
  const savedHeight = Number.parseFloat(savedLayoutEntry?.height);
  if (Number.isFinite(savedWidth) && savedWidth >= STICKY_NOTE_MIN_WIDTH) {
    noteEl.dataset.preferredWidth = `${Math.round(savedWidth)}`;
    if (!startsStowed) {
      noteEl.style.width = `${Math.round(savedWidth)}px`;
    }
  }
  if (Number.isFinite(savedHeight) && savedHeight >= STICKY_NOTE_MIN_HEIGHT) {
    noteEl.dataset.preferredHeight = `${Math.round(savedHeight)}`;
    if (!startsStowed) {
      noteEl.style.height = `${Math.round(savedHeight)}px`;
    }
  }
  if (Number.isFinite(savedLeft)) {
    noteEl.style.left = `${Math.round(savedLeft)}px`;
  }
  if (Number.isFinite(savedTop)) {
    noteEl.style.top = `${Math.round(savedTop)}px`;
  }

  let dragState = null;
  let touchIntentState = null;
  let resizeState = null;
  let dockPreviewState = null;
  let swayFrameId = 0;

  function restoreExpandedSizeFromPreference() {
    const preferredWidth = Number.parseFloat(noteEl.dataset.preferredWidth || "");
    const preferredHeight = Number.parseFloat(noteEl.dataset.preferredHeight || "");
    noteEl.style.width = Number.isFinite(preferredWidth) ? `${Math.round(preferredWidth)}px` : "";
    noteEl.style.height = Number.isFinite(preferredHeight) ? `${Math.round(preferredHeight)}px` : "";
    noteEl.style.minHeight = "";
    if (inputEl instanceof HTMLTextAreaElement) {
      autoSizeStickyNoteInput(inputEl, noteEl);
    }
  }

  function enterDockPreview() {
    if (dockPreviewState) return;
    const rect = noteEl.getBoundingClientRect();
    dockPreviewState = {
      width: noteEl.style.width || "",
      height: noteEl.style.height || "",
      minHeight: noteEl.style.minHeight || "",
      pointerRatioX: 0.5,
      pointerRatioY: 0.5
    };
    noteEl.classList.add("is-near-dock");
    noteEl.style.width = `${STICKY_NOTE_DEFAULT_WIDTH}px`;
    noteEl.style.height = `${STICKY_NOTE_STOWED_HEIGHT}px`;
    noteEl.style.minHeight = `${STICKY_NOTE_STOWED_HEIGHT}px`;
    if (dragState && rect.width > 0 && rect.height > 0) {
      dockPreviewState.pointerRatioX = clamp((dragState.lastPointerX - rect.left) / rect.width, 0, 1);
      dockPreviewState.pointerRatioY = clamp((dragState.lastPointerY - rect.top) / rect.height, 0, 1);
    }
  }

  function exitDockPreview({ keepFolded = false } = {}) {
    if (!dockPreviewState) return;
    if (!keepFolded) {
      const preferredWidth = Number.parseFloat(noteEl.dataset.preferredWidth || "");
      const preferredHeight = Number.parseFloat(noteEl.dataset.preferredHeight || "");
      const shouldForcePreferredRestore = Boolean(dragState?.wasStowedAtGrab);
      noteEl.style.width = shouldForcePreferredRestore
        ? (Number.isFinite(preferredWidth) ? `${Math.round(preferredWidth)}px` : "")
        : (dockPreviewState.width || (Number.isFinite(preferredWidth) ? `${Math.round(preferredWidth)}px` : ""));
      noteEl.style.height = shouldForcePreferredRestore
        ? (Number.isFinite(preferredHeight) ? `${Math.round(preferredHeight)}px` : "")
        : (dockPreviewState.height || (Number.isFinite(preferredHeight) ? `${Math.round(preferredHeight)}px` : ""));
      noteEl.style.minHeight = shouldForcePreferredRestore ? "" : dockPreviewState.minHeight;
      if (inputEl instanceof HTMLTextAreaElement) {
        autoSizeStickyNoteInput(inputEl, noteEl);
      }
    }
    noteEl.classList.remove("is-near-dock");
    dockPreviewState = null;
  }

  function stopSwayAnimation() {
    if (swayFrameId) {
      cancelAnimationFrame(swayFrameId);
      swayFrameId = 0;
    }
  }

  function startSwayAnimation() {
    stopSwayAnimation();
    const tick = () => {
      if (!dragState) {
        swayFrameId = 0;
        return;
      }
      dragState.angle += (dragState.targetAngle - dragState.angle) * 0.28;
      noteEl.style.transform = `rotate(${dragState.angle.toFixed(2)}deg)`;
      swayFrameId = requestAnimationFrame(tick);
    };
    swayFrameId = requestAnimationFrame(tick);
  }

  function clearTouchIntent() {
    if (!touchIntentState) return;
    if (touchIntentState.holdTimerId) {
      clearTimeout(touchIntentState.holdTimerId);
    }
    touchIntentState = null;
  }

  function beginStickyNoteDrag(pointerState) {
    const rect = noteEl.getBoundingClientRect();
    stickyNoteLayerEl?.appendChild(noteEl);
    noteEl.style.transition = "";
    noteEl.style.left = `${Math.round(rect.left)}px`;
    noteEl.style.top = `${Math.round(rect.top)}px`;
    const wasStowedAtGrab = noteEl.classList.contains("is-stowed");
    dragState = {
      pointerId: pointerState.pointerId,
      wasStowedAtGrab,
      unstowedDuringDrag: false,
      previewDockIndex: wasStowedAtGrab ? getStickyNoteDockIndex(pointerState.clientY, noteEl) : null,
      lastLeft: rect.left,
      lastTop: rect.top,
      lastPointerX: pointerState.clientX,
      lastPointerY: pointerState.clientY,
      angle: 0,
      targetAngle: 0,
      offsetX: pointerState.clientX - rect.left,
      offsetY: pointerState.clientY - rect.top,
      deleteHoldTimerId: 0,
      deleteArmed: false
    };

    noteEl.style.transition = "transform 40ms linear, box-shadow 180ms ease, width 180ms ease, height 180ms ease, min-height 180ms ease, border-radius 180ms ease";
    noteEl.classList.add("is-dragging");
    setStickyNoteDragLayerActive(true);
    setStickyNoteDeleteTargetVisible(true);
    setStickyNoteDeleteTargetActive(false);
    noteEl.setPointerCapture(pointerState.pointerId);
    startSwayAnimation();
  }

  noteEl.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    const target = event.target;
    if (target instanceof HTMLElement && target.closest(".sticky-note-input")) return;
    if (target instanceof HTMLElement && target.closest(".sticky-note-resize-handle")) return;

    if (isTouchDevice) {
      clearTouchIntent();
      touchIntentState = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        startScrollTop: stickyNoteMobileScrollTop,
        scrolling: false,
        holdTimerId: window.setTimeout(() => {
          if (!touchIntentState || touchIntentState.pointerId !== event.pointerId || touchIntentState.scrolling) return;
          const snapshot = {
            pointerId: touchIntentState.pointerId,
            clientX: touchIntentState.lastX,
            clientY: touchIntentState.lastY
          };
          clearTouchIntent();
          beginStickyNoteDrag(snapshot);
        }, MOBILE_STICKY_NOTE_DRAG_HOLD_MS)
      };
      return;
    }

    beginStickyNoteDrag(event);
    event.preventDefault();
  });

  noteEl.addEventListener("pointermove", (event) => {
    if (touchIntentState && event.pointerId === touchIntentState.pointerId) {
      touchIntentState.lastX = event.clientX;
      touchIntentState.lastY = event.clientY;
      const deltaX = event.clientX - touchIntentState.startX;
      const deltaY = event.clientY - touchIntentState.startY;
      if (!touchIntentState.scrolling && Math.abs(deltaY) > MOBILE_STICKY_NOTE_DRAG_THRESHOLD && Math.abs(deltaY) > Math.abs(deltaX)) {
        touchIntentState.scrolling = true;
      }
      if (touchIntentState.scrolling) {
        setStickyNoteMobileScrollTop(touchIntentState.startScrollTop - deltaY);
        event.preventDefault();
        return;
      }
      if (Math.abs(deltaX) > MOBILE_STICKY_NOTE_DRAG_THRESHOLD && Math.abs(deltaX) >= Math.abs(deltaY)) {
        const snapshot = {
          pointerId: touchIntentState.pointerId,
          clientX: touchIntentState.lastX,
          clientY: touchIntentState.lastY
        };
        clearTouchIntent();
        beginStickyNoteDrag(snapshot);
      } else {
        return;
      }
    }
    if (!dragState || event.pointerId !== dragState.pointerId) return;

    dragState.lastPointerX = event.clientX;
    dragState.lastPointerY = event.clientY;
    const overDeleteTarget = isPointerOverStickyNoteDeleteTarget(event.clientX, event.clientY);
    const touchingDeleteTarget = isStickyNoteTouchingDeleteTarget(noteEl);
    setStickyNoteDeleteTargetActive(overDeleteTarget);
    noteEl.classList.toggle("is-touching-delete", touchingDeleteTarget);
    noteEl.classList.toggle("is-near-delete", overDeleteTarget);
    if (overDeleteTarget && !dragState.deleteHoldTimerId) {
      dragState.deleteHoldTimerId = window.setTimeout(() => {
        if (!dragState) return;
        if (!isPointerOverStickyNoteDeleteTarget(dragState.lastPointerX, dragState.lastPointerY)) return;
        dragState.deleteArmed = true;
        dragState.deleteHoldTimerId = 0;
      }, 320);
    }
    if (!overDeleteTarget && dragState.deleteHoldTimerId) {
      clearTimeout(dragState.deleteHoldTimerId);
      dragState.deleteHoldTimerId = 0;
    }
    if (!overDeleteTarget) {
      dragState.deleteArmed = false;
    }
    const nearDock = isStickyNoteNearDock(noteEl, dragState.lastLeft, event.clientX, Boolean(dockPreviewState));
    const effectiveNearDock = !overDeleteTarget && nearDock;
    const previewDockIndex = effectiveNearDock ? getStickyNoteDockIndex(event.clientY, noteEl) : null;

    if (dragState.wasStowedAtGrab) {
      if (effectiveNearDock) {
        noteEl.classList.add("is-stowed");
      } else {
        if (!dragState.unstowedDuringDrag) {
          restoreExpandedSizeFromPreference();
          dragState.unstowedDuringDrag = true;
        }
        noteEl.classList.remove("is-stowed");
      }
    }
    dragState.previewDockIndex = previewDockIndex;

    const previousLeft = dragState.lastLeft;
    if (overDeleteTarget) {
      enterDockPreview();
      const previewWidth = STICKY_NOTE_DEFAULT_WIDTH;
      const previewHeight = STICKY_NOTE_STOWED_HEIGHT;
      const anchorX = (dockPreviewState?.pointerRatioX ?? 0.5) * previewWidth;
      const anchorY = (dockPreviewState?.pointerRatioY ?? 0.5) * previewHeight;
      dragState.lastLeft = event.clientX - anchorX;
      dragState.lastTop = event.clientY - anchorY;
    } else if (effectiveNearDock) {
      enterDockPreview();
      const previewWidth = STICKY_NOTE_DEFAULT_WIDTH;
      const previewHeight = STICKY_NOTE_STOWED_HEIGHT;
      const anchorX = (dockPreviewState?.pointerRatioX ?? 0.5) * previewWidth;
      const anchorY = (dockPreviewState?.pointerRatioY ?? 0.5) * previewHeight;
      dragState.lastLeft = event.clientX - anchorX;
      dragState.lastTop = event.clientY - anchorY;
    } else {
      exitDockPreview();
      dragState.lastLeft = event.clientX - dragState.offsetX;
      dragState.lastTop = event.clientY - dragState.offsetY;
    }
    noteEl.style.left = `${Math.round(dragState.lastLeft)}px`;
    noteEl.style.top = `${Math.round(dragState.lastTop)}px`;
    const deltaX = dragState.lastLeft - previousLeft;
    dragState.targetAngle = clamp(deltaX * 0.9, -12, 12);
    setStickyNoteDockHintVisible(effectiveNearDock);
    if (effectiveNearDock) {
      layoutStowedStickyNotes({ draggingNoteEl: noteEl, previewIndex: previewDockIndex });
    } else {
      layoutStowedStickyNotes();
    }
  });

  function releaseStickyNote(event) {
    if (touchIntentState && event.pointerId === touchIntentState.pointerId) {
      clearTouchIntent();
      return;
    }
    if (!dragState || event.pointerId !== dragState.pointerId) return;
    if (dragState.deleteHoldTimerId) {
      clearTimeout(dragState.deleteHoldTimerId);
      dragState.deleteHoldTimerId = 0;
    }
    const shouldDelete = Boolean(dragState.deleteArmed) && isPointerOverStickyNoteDeleteTarget(dragState.lastPointerX, dragState.lastPointerY);
    const currentLeft = Number.parseFloat(noteEl.style.left) || 0;
    const shouldStow = isStickyNoteNearDock(noteEl, currentLeft, dragState.lastPointerX, Boolean(dockPreviewState));
    const previewDockIndex = dragState.previewDockIndex;

    noteEl.classList.remove("is-dragging");
    noteEl.classList.remove("is-touching-delete");
    noteEl.classList.remove("is-near-delete");
    exitDockPreview({ keepFolded: shouldStow });
    stopSwayAnimation();
    noteEl.style.transition = "transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1), box-shadow 180ms ease, width 180ms ease, height 180ms ease, min-height 180ms ease, border-radius 180ms ease";
    noteEl.style.transform = "";
    setStickyNoteDockHintVisible(false);
    setStickyNoteDockSlotVisible(false);
    setStickyNoteDeleteTargetVisible(false);
    setStickyNoteDeleteTargetActive(false);
    setStickyNoteDragLayerActive(false);

    if (shouldDelete) {
      const key = String(noteEl.dataset.listName || "");
      const timerId = key ? stickyNoteSaveTimers.get(key) : null;
      if (timerId) {
        clearTimeout(timerId);
        stickyNoteSaveTimers.delete(key);
      }
      void deleteStickyNote(noteEl).then((deleted) => {
        if (deleted) {
          noteEl.remove();
          removeStickyNoteLayoutEntry(noteEl);
          layoutStowedStickyNotes();
          syncStickyNoteLayoutState();
        }
      });
      if (noteEl.hasPointerCapture(event.pointerId)) {
        noteEl.releasePointerCapture(event.pointerId);
      }
      dragState = null;
      return;
    }

    if (shouldStow) {
      noteEl.style.transition = "left 180ms ease, top 180ms ease, transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1), box-shadow 180ms ease, width 180ms ease, height 180ms ease, min-height 180ms ease, border-radius 180ms ease";
      noteEl.classList.add("is-stowed");
      commitStickyNoteDockOrder(noteEl, previewDockIndex ?? getStickyNotes().length);
      layoutStowedStickyNotes();
      window.setTimeout(() => {
        if (!noteEl.isConnected || !noteEl.classList.contains("is-stowed") || noteEl.classList.contains("is-dragging")) return;
        noteEl.style.transition = "";
      }, 220);
    } else {
      layoutStowedStickyNotes();
      saveStickyNoteLayoutEntry(noteEl, {
        isStowed: false,
        left: Math.round(Number.parseFloat(noteEl.style.left) || 0),
        top: Math.round(Number.parseFloat(noteEl.style.top) || STICKY_NOTE_SAFE_TOP),
        width: Math.round(Number.parseFloat(noteEl.dataset.preferredWidth || noteEl.offsetWidth) || noteEl.offsetWidth),
        height: Math.round(Number.parseFloat(noteEl.dataset.preferredHeight || noteEl.offsetHeight) || noteEl.offsetHeight)
      });
    }

    if (noteEl.hasPointerCapture(event.pointerId)) {
      noteEl.releasePointerCapture(event.pointerId);
    }
    dragState = null;
    syncStickyNoteLayoutState();
  }

  noteEl.addEventListener("pointerup", releaseStickyNote);
  noteEl.addEventListener("pointercancel", releaseStickyNote);

  if (resizeHandleEl instanceof HTMLElement) {
    resizeHandleEl.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      if (noteEl.classList.contains("is-stowed")) return;
      stickyNoteLayerEl?.appendChild(noteEl);
      const rect = noteEl.getBoundingClientRect();
      resizeState = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startWidth: rect.width,
        startHeight: rect.height,
        startLeft: rect.left,
        startTop: rect.top
      };
      noteEl.classList.add("is-resizing");
      setStickyNoteDragLayerActive(true);
      resizeHandleEl.setPointerCapture(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
    });

    resizeHandleEl.addEventListener("pointermove", (event) => {
      if (!resizeState || event.pointerId !== resizeState.pointerId) return;
      const deltaX = event.clientX - resizeState.startX;
      const deltaY = event.clientY - resizeState.startY;
      const maxWidth = Math.max(STICKY_NOTE_MIN_WIDTH, window.innerWidth - resizeState.startLeft);
      const maxHeight = Math.max(STICKY_NOTE_MIN_HEIGHT, window.innerHeight - resizeState.startTop);
      const nextWidth = clamp(resizeState.startWidth + deltaX, STICKY_NOTE_MIN_WIDTH, maxWidth);
      const nextHeight = clamp(resizeState.startHeight + deltaY, STICKY_NOTE_MIN_HEIGHT, maxHeight);
      noteEl.style.width = `${Math.round(nextWidth)}px`;
      noteEl.style.height = `${Math.round(nextHeight)}px`;
      noteEl.dataset.preferredWidth = `${Math.round(nextWidth)}`;
      noteEl.dataset.preferredHeight = `${Math.round(nextHeight)}`;
    });

    const releaseResize = (event) => {
      if (!resizeState || event.pointerId !== resizeState.pointerId) return;
      if (resizeHandleEl.hasPointerCapture(event.pointerId)) {
        resizeHandleEl.releasePointerCapture(event.pointerId);
      }
      noteEl.classList.remove("is-resizing");
      setStickyNoteDragLayerActive(false);
      if (inputEl instanceof HTMLTextAreaElement) {
        autoSizeStickyNoteInput(inputEl, noteEl);
      }
      saveStickyNoteLayoutEntry(noteEl, {
        width: Math.round(Number.parseFloat(noteEl.dataset.preferredWidth || noteEl.offsetWidth) || noteEl.offsetWidth),
        height: Math.round(Number.parseFloat(noteEl.dataset.preferredHeight || noteEl.offsetHeight) || noteEl.offsetHeight)
      });
      resizeState = null;
      syncStickyNoteLayoutState();
    };

    resizeHandleEl.addEventListener("pointerup", releaseResize);
    resizeHandleEl.addEventListener("pointercancel", releaseResize);
  }
  return noteEl;
}

function renderStickyNotes(listEntries) {
  clearStickyNotes();
  if (!stickyNoteLayerEl || !Array.isArray(listEntries) || listEntries.length === 0) return;
  const layoutState = loadStickyNoteLayoutState();
  let didMutateLayoutState = false;
  const sortedEntries = [...listEntries].sort((a, b) => {
    const aEntry = layoutState[String(a?.list_name || "")];
    const bEntry = layoutState[String(b?.list_name || "")];
    const aOrder = Number.isFinite(Number(aEntry?.order)) ? Number(aEntry.order) : Number.MAX_SAFE_INTEGER;
    const bOrder = Number.isFinite(Number(bEntry?.order)) ? Number(bEntry.order) : Number.MAX_SAFE_INTEGER;
    if (aOrder !== bOrder) return aOrder - bOrder;
    return String(a?.list_name || "").localeCompare(String(b?.list_name || ""));
  });

  sortedEntries.forEach((listEntry, index) => {
    const listName = String(listEntry?.list_name || "");
    const existingEntry = layoutState[listName] && typeof layoutState[listName] === "object" ? layoutState[listName] : {};
    const persistedColorClass = normalizeStickyNoteColorClass(existingEntry.colorClass);
    const colorClassName = persistedColorClass || STICKY_NOTE_COLOR_CLASSES[index % STICKY_NOTE_COLOR_CLASSES.length];
    if (!persistedColorClass && listName) {
      layoutState[listName] = {
        ...existingEntry,
        colorClass: colorClassName
      };
      didMutateLayoutState = true;
    }
    const noteEl = createStickyNote(listEntry, colorClassName);
    stickyNoteLayerEl.appendChild(noteEl);
  });
  if (didMutateLayoutState) {
    saveStickyNoteLayoutState(layoutState);
  }
  layoutStowedStickyNotes();
  syncStickyNoteLayoutState();
}

async function loadStickyNotes() {
  if (!isAuthenticated || !isStickyNotesEnabled()) {
    clearStickyNotes();
    return;
  }

  try {
    const response = await fetch("/api/lists");
    const data = await response.json();
    if (response.status === 401) {
      isAuthenticated = false;
      currentUserId = "";
      updateAuthUi();
      clearStickyNotes();
      return;
    }
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Failed to load lists.");
    }
    renderStickyNotes(Array.isArray(data.lists) ? data.lists : []);
  } catch (_) {
    clearStickyNotes();
  }
}

window.addEventListener("resize", () => {
  getStickyNotes().forEach((noteEl) => {
    const inputEl = noteEl.querySelector(".sticky-note-input");
    if (inputEl instanceof HTMLTextAreaElement) {
      autoSizeStickyNoteInput(inputEl, noteEl);
    }
  });
  layoutStowedStickyNotes();
});






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

function closeNavDrawer() {
  if (!navDrawerEl) return;
  navDrawerEl.classList.remove("is-open");
  navDrawerEl.setAttribute("aria-hidden", "true");
  if (navDrawerOverlayEl) {
    navDrawerOverlayEl.classList.add("hidden");
    navDrawerOverlayEl.setAttribute("aria-hidden", "true");
  }
  if (navDrawerTriggerEl) {
    navDrawerTriggerEl.setAttribute("aria-expanded", "false");
  }
}

function openNavDrawer() {
  if (!navDrawerEl) return;
  navDrawerEl.classList.add("is-open");
  navDrawerEl.setAttribute("aria-hidden", "false");
  if (navDrawerOverlayEl) {
    navDrawerOverlayEl.classList.remove("hidden");
    navDrawerOverlayEl.setAttribute("aria-hidden", "false");
  }
  if (navDrawerTriggerEl) {
    navDrawerTriggerEl.setAttribute("aria-expanded", "true");
  }
}

function toggleNavDrawer() {
  if (!navDrawerEl) return;
  const isOpen = navDrawerEl.classList.contains("is-open");
  if (isOpen) {
    closeNavDrawer();
  } else {
    openNavDrawer();
  }
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
    currentUserId = isAuthenticated ? String(data?.user?.id || "") : "";
    updateAuthUi();
    if (isAuthenticated) {
      void loadStickyNotes();
    } else {
      clearStickyNotes();
    }
  } catch (_) {
    isAuthenticated = false;
    currentUserId = "";
    updateAuthUi();
    clearStickyNotes();
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
      currentUserId = "";
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
      currentUserId = "";
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
    void loadStickyNotes();
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
    currentUserId = String(data?.user?.id || "");
    updateAuthUi();
    setAuthStatus("");
    authPasswordEl.value = "";
    initSessionPromise = initSession();
    void loadStickyNotes();
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
  currentUserId = "";
  currentSessionId = "";
  updateAuthUi();
  clearStickyNotes();
  if (chatMenuEl) {
    chatMenuEl.classList.add("hidden");
  }
  if (chatMenuTriggerEl) {
    chatMenuTriggerEl.setAttribute("aria-expanded", "false");
  }
  closeNavDrawer();
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

if (navDrawerTriggerEl) {
  navDrawerTriggerEl.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleNavDrawer();
  });
}

if (navDrawerOverlayEl) {
  navDrawerOverlayEl.addEventListener("click", closeNavDrawer);
}

if (darkModeToggleEl) {
  darkModeToggleEl.addEventListener("change", () => {
    const theme = darkModeToggleEl.checked ? "dark" : "light";
    applyTheme(theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch (_) {
      // Ignore storage failures.
    }
  });
}

if (stickyNotesToggleEl) {
  stickyNotesToggleEl.addEventListener("change", () => {
    try {
      localStorage.setItem(STICKY_NOTES_ENABLED_STORAGE_KEY, stickyNotesToggleEl.checked ? "1" : "0");
    } catch (_) {
      // Ignore storage failures.
    }
    updateStickyNotesToggleVisual();
    refreshStickyNotesView();
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

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeNavDrawer();
  }
});

autoSizePrompt();
initializeComposerFloating();
if (stickyNotesToggleEl) {
  let stickyNotesEnabled = true;
  try {
    stickyNotesEnabled = localStorage.getItem(STICKY_NOTES_ENABLED_STORAGE_KEY) !== "0";
  } catch (_) {
    stickyNotesEnabled = true;
  }
  stickyNotesToggleEl.checked = stickyNotesEnabled;
  updateStickyNotesToggleVisual();
}
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


