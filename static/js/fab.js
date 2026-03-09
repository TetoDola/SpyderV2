/**
 * Expandable FAB — replaces the old single + button.
 * Three options: Create Node, Log Meeting, Add Notes.
 * Depends on: graph.js (openPeekCreate, API), ingestion.js (openMeetingPanel, openAddNotesPanel)
 */

const fabMain = document.getElementById("fab-main");
const fabOptions = document.getElementById("fab-options");
const fabOverlay = document.getElementById("fab-overlay");
const fabIcon = document.getElementById("fab-icon");
let fabExpanded = false;

function expandFab() {
  fabExpanded = true;
  fabMain.classList.add("expanded");
  fabOptions.classList.remove("fab-options-hidden");
  fabOptions.classList.add("fab-options-visible");
  fabOverlay.classList.remove("hidden");
}

function collapseFab() {
  if (!fabExpanded) return;
  fabExpanded = false;
  fabMain.classList.remove("expanded");
  fabOptions.classList.remove("fab-options-visible");
  fabOptions.classList.add("fab-options-hidden");
  fabOverlay.classList.add("hidden");
}

function toggleFab() {
  if (fabExpanded) {
    collapseFab();
  } else {
    expandFab();
  }
}

fabMain.addEventListener("click", toggleFab);
fabOverlay.addEventListener("click", collapseFab);

// Handle FAB option clicks
document.querySelectorAll(".fab-option").forEach((btn) => {
  btn.addEventListener("click", () => {
    const action = btn.dataset.action;
    collapseFab();

    if (action === "create") {
      openPeekCreate();
    } else if (action === "meeting") {
      if (typeof openMeetingPanel === "function") {
        openMeetingPanel();
      }
    } else if (action === "notes") {
      if (typeof openAddNotesPanel === "function") {
        openAddNotesPanel();
      }
    }
  });
});
