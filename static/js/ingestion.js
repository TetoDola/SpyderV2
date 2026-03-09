/**
 * Ingestion panels — Meeting Logger + Add Notes
 * Includes: people autocomplete (chip-based), file drag-and-drop, form submission
 * Depends on: graph.js (API, showToast, escapeHtml)
 */

// ── Upload with progress (XHR) ────────────────────────────
// Returns a Promise. onProgress receives 0-100 percent.
function uploadWithProgress(url, formData, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);

    if (onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });
    }

    xhr.addEventListener("load", () => {
      let body;
      try { body = JSON.parse(xhr.responseText); } catch { body = {}; }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
      } else {
        reject({ status: xhr.status, body });
      }
    });

    xhr.addEventListener("error", () => reject({ status: 0, body: {} }));
    xhr.addEventListener("abort", () => reject({ status: 0, body: {} }));

    xhr.send(formData);
  });
}


// ── People Autocomplete Engine ────────────────────────────
// Reusable chip-based autocomplete. Used by both panels.

function createPeopleAutocomplete(config) {
  const {
    fieldEl,      // container div
    inputEl,      // text input inside container
    dropdownEl,   // dropdown div
    errorEl,      // error message element
    multiSelect,  // true = multiple people, false = single
  } = config;

  const state = {
    selected: [],    // [{node_id, name, create_new, is_ghost}]
    searchTimeout: null,
    activeIndex: -1,
    results: [],
  };

  function getSelected() {
    return state.selected;
  }

  function reset() {
    state.selected = [];
    renderChips();
    inputEl.value = "";
    hideDropdown();
    if (errorEl) errorEl.classList.add("hidden");
  }

  function renderChips() {
    // Remove existing chips
    fieldEl.querySelectorAll(".people-chip").forEach((c) => c.remove());

    state.selected.forEach((person, idx) => {
      const chip = document.createElement("span");
      chip.className = person.create_new ? "people-chip chip-new" : "people-chip";
      chip.innerHTML = `
        ${escapeHtml(person.name)}${person.create_new ? " (new)" : ""}
        <button type="button" class="chip-remove" data-idx="${idx}">&times;</button>
      `;
      chip.querySelector(".chip-remove").addEventListener("click", (e) => {
        e.stopPropagation();
        state.selected.splice(idx, 1);
        renderChips();
      });
      fieldEl.insertBefore(chip, inputEl);
    });

    // Hide input if single-select and already selected
    if (!multiSelect && state.selected.length >= 1) {
      inputEl.classList.add("hidden");
    } else {
      inputEl.classList.remove("hidden");
    }
  }

  function addPerson(person) {
    if (!multiSelect && state.selected.length >= 1) return;
    // Prevent duplicates
    if (person.node_id && state.selected.some((p) => p.node_id === person.node_id)) return;
    state.selected.push(person);
    renderChips();
    inputEl.value = "";
    hideDropdown();
    if (errorEl) errorEl.classList.add("hidden");
  }

  function hideDropdown() {
    dropdownEl.classList.add("hidden");
    dropdownEl.innerHTML = "";
    state.results = [];
    state.activeIndex = -1;
  }

  function showDropdown(results, query) {
    state.results = results;
    state.activeIndex = 0;
    dropdownEl.innerHTML = "";

    results.forEach((node, i) => {
      const item = document.createElement("div");
      item.className = "mention-item" + (i === 0 ? " active" : "");
      const title = node.properties?.Title || "";
      const company = node.properties?.["Company Name"] || node.properties?.Company || "";
      let subtitle = [title, company].filter(Boolean).join(" at ");
      item.innerHTML = `
        <span class="mention-title">${escapeHtml(node.title)}</span>
        <span class="mention-type">${subtitle ? escapeHtml(subtitle) : node.node_type}</span>
      `;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        addPerson({ node_id: node.id, name: node.title, create_new: false, is_ghost: node.is_ghost });
      });
      dropdownEl.appendChild(item);
    });

    // "+ Create" option
    if (query.trim()) {
      const createItem = document.createElement("div");
      createItem.className = "mention-item" + (results.length === 0 ? " active" : "");
      createItem.innerHTML = `
        <span class="mention-title" style="color: #60a5fa;">+ Create "${escapeHtml(query)}" as new person</span>
        <span class="mention-type">NEW</span>
      `;
      createItem.addEventListener("mousedown", (e) => {
        e.preventDefault();
        addPerson({ node_id: null, name: query.trim(), create_new: true, is_ghost: false });
      });
      dropdownEl.appendChild(createItem);
      if (results.length === 0) state.activeIndex = 0;
    }

    dropdownEl.classList.remove("hidden");
  }

  function updateHighlight() {
    const items = dropdownEl.querySelectorAll(".mention-item");
    items.forEach((item, i) => {
      item.classList.toggle("active", i === state.activeIndex);
    });
  }

  // Event: input search
  inputEl.addEventListener("input", () => {
    const query = inputEl.value.trim();
    if (query.length < 2) {
      hideDropdown();
      return;
    }
    clearTimeout(state.searchTimeout);
    state.searchTimeout = setTimeout(async () => {
      const res = await fetch(`${API.nodeSearch}?q=${encodeURIComponent(query)}&node_type=PERSON`);
      if (!res.ok) return;
      const data = await res.json();
      showDropdown(data.results || [], query);
    }, 300);
  });

  // Event: keyboard navigation
  inputEl.addEventListener("keydown", (e) => {
    if (dropdownEl.classList.contains("hidden")) return;
    const totalItems = dropdownEl.querySelectorAll(".mention-item").length;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      state.activeIndex = (state.activeIndex + 1) % totalItems;
      updateHighlight();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      state.activeIndex = (state.activeIndex - 1 + totalItems) % totalItems;
      updateHighlight();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const items = dropdownEl.querySelectorAll(".mention-item");
      if (items[state.activeIndex]) {
        items[state.activeIndex].dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      }
    } else if (e.key === "Escape") {
      hideDropdown();
    } else if (e.key === "Backspace" && inputEl.value === "" && state.selected.length > 0) {
      // Remove last chip on backspace in empty input
      state.selected.pop();
      renderChips();
    }
  });

  // Event: blur hides dropdown
  inputEl.addEventListener("blur", () => {
    setTimeout(hideDropdown, 200);
  });

  // Event: click on field focuses input
  fieldEl.addEventListener("click", () => {
    inputEl.focus();
  });

  return { getSelected, reset, addPerson };
}


// ── File Drop Zone ────────────────────────────────────
function setupDropZone(config) {
  const { dropzoneEl, promptEl, fileEl, nameEl, sizeEl, removeEl, inputEl } = config;
  let selectedFile = null;

  function getFile() { return selectedFile; }

  function reset() {
    selectedFile = null;
    promptEl.classList.remove("hidden");
    fileEl.classList.add("hidden");
    inputEl.value = "";
    dropzoneEl.classList.remove("dropzone-active");
  }

  function showFile(file) {
    selectedFile = file;
    nameEl.textContent = file.name;
    sizeEl.textContent = `(${(file.size / 1024).toFixed(0)} KB)`;
    promptEl.classList.add("hidden");
    fileEl.classList.remove("hidden");
  }

  const ALLOWED_EXTS = [".m4a", ".mp3", ".wav", ".ogg", ".webm", ".pdf", ".docx", ".txt", ".md"];

  function validateFile(file) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!ALLOWED_EXTS.includes(ext)) {
      showToast(`Unsupported file format: ${ext}`, "error");
      return false;
    }
    if (file.size > 25 * 1024 * 1024) {
      showToast("File too large (max 25MB)", "error");
      return false;
    }
    return true;
  }

  // Click to browse
  dropzoneEl.addEventListener("click", (e) => {
    if (e.target === removeEl || e.target.closest("#" + removeEl.id)) return;
    inputEl.click();
  });

  inputEl.addEventListener("change", () => {
    const file = inputEl.files[0];
    if (file && validateFile(file)) {
      showFile(file);
    }
  });

  // Drag and drop
  dropzoneEl.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzoneEl.classList.add("dropzone-active");
  });

  dropzoneEl.addEventListener("dragleave", () => {
    dropzoneEl.classList.remove("dropzone-active");
  });

  dropzoneEl.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzoneEl.classList.remove("dropzone-active");
    const file = e.dataTransfer.files[0];
    if (file && validateFile(file)) {
      showFile(file);
    }
  });

  // Remove button
  removeEl.addEventListener("click", (e) => {
    e.stopPropagation();
    reset();
  });

  return { getFile, reset };
}


// ═══ Meeting Logger Panel ═══════════════════════════════

const meetingBackdrop = document.getElementById("meeting-backdrop");
const meetingClose = document.getElementById("meeting-close");
const meetingSubmit = document.getElementById("meeting-submit");
const meetingError = document.getElementById("meeting-error");

// Set date default to today
const meetingDate = document.getElementById("meeting-date");
meetingDate.value = new Date().toISOString().slice(0, 10);

// People autocomplete
const meetingPeople = createPeopleAutocomplete({
  fieldEl: document.getElementById("meeting-people-field"),
  inputEl: document.getElementById("meeting-people-input"),
  dropdownEl: document.getElementById("meeting-people-dropdown"),
  errorEl: document.getElementById("meeting-people-error"),
  multiSelect: true,
});

// File drop zone
const meetingDropZone = setupDropZone({
  dropzoneEl: document.getElementById("meeting-dropzone"),
  promptEl: document.getElementById("meeting-dropzone-prompt"),
  fileEl: document.getElementById("meeting-dropzone-file"),
  nameEl: document.getElementById("meeting-file-name"),
  sizeEl: document.getElementById("meeting-file-size"),
  removeEl: document.getElementById("meeting-file-remove"),
  inputEl: document.getElementById("meeting-file-input"),
});

function openMeetingPanel() {
  meetingDate.value = new Date().toISOString().slice(0, 10);
  document.getElementById("meeting-title").value = "";
  document.getElementById("meeting-notes").value = "";
  document.getElementById("meeting-auto-create").checked = true;
  meetingPeople.reset();
  meetingDropZone.reset();
  meetingError.classList.add("hidden");
  meetingSubmit.disabled = false;
  meetingSubmit.textContent = "Save & Process";
  meetingBackdrop.classList.remove("hidden");
}

function closeMeetingPanel() {
  meetingBackdrop.classList.add("hidden");
}

meetingClose.addEventListener("click", closeMeetingPanel);
meetingBackdrop.addEventListener("click", (e) => {
  if (e.target === meetingBackdrop) closeMeetingPanel();
});

meetingSubmit.addEventListener("click", async () => {
  meetingError.classList.add("hidden");
  const peopleErrorEl = document.getElementById("meeting-people-error");

  // Validate
  const people = meetingPeople.getSelected();
  if (people.length === 0) {
    peopleErrorEl.classList.remove("hidden");
    return;
  }
  peopleErrorEl.classList.add("hidden");

  const notes = document.getElementById("meeting-notes").value.trim();
  const file = meetingDropZone.getFile();
  if (!notes && !file) {
    meetingError.textContent = "Add notes or attach a file";
    meetingError.classList.remove("hidden");
    return;
  }

  meetingSubmit.disabled = true;
  meetingSubmit.textContent = file ? "Uploading 0%..." : "Processing...";

  // Build form data
  const formData = new FormData();
  formData.append("title", document.getElementById("meeting-title").value.trim());
  formData.append("date", meetingDate.value);
  formData.append("notes", notes);
  formData.append("auto_create", document.getElementById("meeting-auto-create").checked ? "true" : "false");

  const linkedPeople = people.map((p) => {
    if (p.node_id) return { node_id: p.node_id };
    return { name: p.name, create_new: true };
  });
  formData.append("linked_people", JSON.stringify(linkedPeople));

  if (file) formData.append("file", file);

  try {
    await uploadWithProgress(API.ingestMeeting, formData, {
      onProgress: (pct) => {
        meetingSubmit.textContent = pct < 100 ? `Uploading ${pct}%...` : "Processing...";
      },
    });

    closeMeetingPanel();
    showToast("Meeting queued for processing", "success");

    if (typeof refreshNotifications === "function") {
      refreshNotifications();
    }
  } catch (err) {
    const msg = err.body?.error || "Failed to submit meeting";
    if (err.status === 0) {
      meetingError.textContent = "Network error. Please try again.";
    } else {
      meetingError.textContent = msg;
    }
    meetingError.classList.remove("hidden");
  } finally {
    meetingSubmit.disabled = false;
    meetingSubmit.textContent = "Save & Process";
  }
});


// ═══ Add Notes Panel ════════════════════════════════════

const addnotesBackdrop = document.getElementById("addnotes-backdrop");
const addnotesClose = document.getElementById("addnotes-close");
const addnotesSubmit = document.getElementById("addnotes-submit");
const addnotesError = document.getElementById("addnotes-error");

// Person autocomplete (single-select)
const addnotesAbout = createPeopleAutocomplete({
  fieldEl: document.getElementById("addnotes-about-field"),
  inputEl: document.getElementById("addnotes-about-input"),
  dropdownEl: document.getElementById("addnotes-about-dropdown"),
  errorEl: document.getElementById("addnotes-about-error"),
  multiSelect: false,
});

// File drop zone
const addnotesDropZone = setupDropZone({
  dropzoneEl: document.getElementById("addnotes-dropzone"),
  promptEl: document.getElementById("addnotes-dropzone-prompt"),
  fileEl: document.getElementById("addnotes-dropzone-file"),
  nameEl: document.getElementById("addnotes-file-name"),
  sizeEl: document.getElementById("addnotes-file-size"),
  removeEl: document.getElementById("addnotes-file-remove"),
  inputEl: document.getElementById("addnotes-file-input"),
});

function openAddNotesPanel() {
  document.getElementById("addnotes-notes").value = "";
  document.getElementById("addnotes-auto-create").checked = true;
  addnotesAbout.reset();
  addnotesDropZone.reset();
  addnotesError.classList.add("hidden");
  addnotesSubmit.disabled = false;
  addnotesSubmit.textContent = "Save & Process";
  addnotesBackdrop.classList.remove("hidden");
}

function closeAddNotesPanel() {
  addnotesBackdrop.classList.add("hidden");
}

addnotesClose.addEventListener("click", closeAddNotesPanel);
addnotesBackdrop.addEventListener("click", (e) => {
  if (e.target === addnotesBackdrop) closeAddNotesPanel();
});

addnotesSubmit.addEventListener("click", async () => {
  addnotesError.classList.add("hidden");
  const aboutErrorEl = document.getElementById("addnotes-about-error");

  // Validate
  const aboutPeople = addnotesAbout.getSelected();
  if (aboutPeople.length === 0) {
    aboutErrorEl.classList.remove("hidden");
    return;
  }
  aboutErrorEl.classList.add("hidden");

  const notes = document.getElementById("addnotes-notes").value.trim();
  const file = addnotesDropZone.getFile();
  if (!notes && !file) {
    addnotesError.textContent = "Notes or a file attachment is required";
    addnotesError.classList.remove("hidden");
    return;
  }

  addnotesSubmit.disabled = true;
  addnotesSubmit.textContent = file ? "Uploading 0%..." : "Processing...";

  const about = aboutPeople[0];
  const formData = new FormData();
  if (about.node_id) {
    formData.append("about_node_id", about.node_id);
  } else {
    formData.append("about_name", about.name);
    formData.append("about_create_new", "true");
  }
  formData.append("notes", notes);
  formData.append("auto_create", document.getElementById("addnotes-auto-create").checked ? "true" : "false");
  if (file) formData.append("file", file);

  try {
    await uploadWithProgress(API.ingestNote, formData, {
      onProgress: (pct) => {
        addnotesSubmit.textContent = pct < 100 ? `Uploading ${pct}%...` : "Processing...";
      },
    });

    closeAddNotesPanel();
    showToast("Notes queued for processing", "success");

    if (typeof refreshNotifications === "function") {
      refreshNotifications();
    }
  } catch (err) {
    const msg = err.body?.error || "Failed to submit notes";
    if (err.status === 0) {
      addnotesError.textContent = "Network error. Please try again.";
    } else {
      addnotesError.textContent = msg;
    }
    addnotesError.classList.remove("hidden");
  } finally {
    addnotesSubmit.disabled = false;
    addnotesSubmit.textContent = "Save & Process";
  }
});
