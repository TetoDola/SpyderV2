/**
 * Spyder V2 — Graph CRM Frontend
 * force-graph + center peek (create/edit) + @mentions + markdown + table view
 */

const API = {
  graph: "/api/graph/",
  nodes: "/api/nodes/",
  nodeDetail: (id) => `/api/nodes/${id}/`,
  nodeUpdate: (id) => `/api/nodes/${id}/update/`,
  nodeDelete: (id) => `/api/nodes/${id}/delete/`,
  nodeImage: (id) => `/api/nodes/${id}/image/`,
  nodeSearch: "/api/nodes/search/",
  templates: "/api/templates/",
};

// ── System Locked Keys (cannot be deleted from node properties) ──
const LOCKED_KEYS = {
  PERSON: ["First Name", "Last Name", "Email", "Phone Number"],
  COMPANY: ["Company Name", "Website", "Phone Number"],
  MEETING: ["Date", "Attendees"],
};

// ── State ──────────────────────────────────────────────
let graph = null;
let currentNodeId = null; // null = create mode, string = edit mode
let isExpanded = false;
let notesMode = "edit";
let currentView = "graph"; // "graph" | "table"
let cachedGraphData = null; // store latest graph data for table
let originalGraphData = { nodes: [], links: [] }; // untouched deep copy for search filtering

// ── Marked.js config: enable single-line breaks ───────
marked.use({ breaks: true });

// ── DOM refs ───────────────────────────────────────────
const canvas = document.getElementById("graph-canvas");
const tableView = document.getElementById("table-view");
const tableBody = document.getElementById("table-body");
const tableEmpty = document.getElementById("table-empty");
const toggleGraphBtn = document.getElementById("toggle-graph");
const toggleTableBtn = document.getElementById("toggle-table");
const fab = document.getElementById("fab-add");
const searchInput = document.getElementById("search-input");
const hopInput = document.getElementById("hop-input");

// Peek modal
const peekBackdrop = document.getElementById("peek-backdrop");
const peekModal = document.getElementById("peek-modal");
const peekTitle = document.getElementById("peek-title");
const peekType = document.getElementById("peek-type");
const peekClose = document.getElementById("peek-close");
const peekExpand = document.getElementById("peek-expand");
const peekImageZone = document.getElementById("peek-image-zone");
const peekImage = document.getElementById("peek-image");
const peekImagePlaceholder = document.getElementById("peek-image-placeholder");
const peekImageInput = document.getElementById("peek-image-input");
const propsGrid = document.getElementById("properties-grid");
const addPropBtn = document.getElementById("add-property-btn");
const peekNotes = document.getElementById("peek-notes");
const peekNotesPreview = document.getElementById("peek-notes-preview");
const notesTabEdit = document.getElementById("notes-tab-edit");
const notesTabPreview = document.getElementById("notes-tab-preview");
const peekSave = document.getElementById("peek-save");
const peekDelete = document.getElementById("peek-delete");
const mentionDropdown = document.getElementById("mention-dropdown");

// Settings modal
const settingsBtn = document.getElementById("settings-btn");
const settingsBackdrop = document.getElementById("settings-backdrop");
const settingsClose = document.getElementById("settings-close");
const tplPropsGrid = document.getElementById("tpl-props-grid");
const tplAddProp = document.getElementById("tpl-add-prop");
const tplNotes = document.getElementById("tpl-notes");
const tplSave = document.getElementById("tpl-save");
const tplTabs = document.querySelectorAll(".tpl-tab");

// ── Helpers ────────────────────────────────────────────
function apiHeaders() {
  return { "Content-Type": "application/json" };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// ── View Toggle ────────────────────────────────────────
function switchView(view) {
  currentView = view;

  if (view === "graph") {
    canvas.classList.remove("hidden");
    tableView.classList.add("hidden");
    toggleGraphBtn.classList.add("bg-gray-600", "text-white");
    toggleGraphBtn.classList.remove("bg-transparent", "text-white/40");
    toggleTableBtn.classList.add("bg-transparent", "text-white/40");
    toggleTableBtn.classList.remove("bg-gray-600", "text-white");
    // Resize graph after showing
    if (graph) graph.width(window.innerWidth).height(window.innerHeight);
  } else {
    canvas.classList.add("hidden");
    tableView.classList.remove("hidden");
    toggleTableBtn.classList.add("bg-gray-600", "text-white");
    toggleTableBtn.classList.remove("bg-transparent", "text-white/40");
    toggleGraphBtn.classList.add("bg-transparent", "text-white/40");
    toggleGraphBtn.classList.remove("bg-gray-600", "text-white");
    renderTable();
  }
}

function renderTable() {
  tableBody.innerHTML = "";
  if (!cachedGraphData || cachedGraphData.nodes.length === 0) {
    tableEmpty.classList.remove("hidden");
    return;
  }
  tableEmpty.classList.add("hidden");

  // Sort by label alphabetically
  const sorted = [...cachedGraphData.nodes].sort((a, b) =>
    a.label.localeCompare(b.label)
  );

  for (const node of sorted) {
    const tr = document.createElement("tr");
    tr.className =
      "border-b border-white/[0.04] cursor-pointer hover:bg-white/[0.03] transition-colors duration-100";
    tr.addEventListener("click", () => openPeek(node.id));

    // Ghost indicator via opacity
    const opacity = node.is_ghost ? "text-white/30" : "text-white/80";

    tr.innerHTML = `
      <td class="py-3 px-4 text-sm ${opacity}">${escapeHtml(node.label)}</td>
      <td class="py-3 px-4">
        <span class="inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider
                     badge-${node.group}">${node.group}</span>
      </td>
      <td class="py-3 px-4 text-sm text-white/30">${node.created_at ? formatDate(node.created_at) : ""}</td>
    `;
    tableBody.appendChild(tr);
  }
}

toggleGraphBtn.addEventListener("click", () => switchView("graph"));
toggleTableBtn.addEventListener("click", () => switchView("table"));

// ── force-graph rendering ──────────────────────────────
const NODE_RADIUS = 4;
const GHOST_COLOR = "#444444";
const LIVE_COLOR = "#ffffff";
const LABEL_FONT = "4px Inter, system-ui, sans-serif";

async function loadGraph() {
  const res = await fetch(API.graph);
  const data = await res.json();
  cachedGraphData = data;

  const nodes = data.nodes.map((n) => ({
    id: n.id,
    label: n.label,
    group: n.group,
    is_ghost: n.is_ghost,
    created_at: n.created_at,
  }));

  const links = data.edges.map((e) => ({
    source: e.from,
    target: e.to,
    label: e.label || "",
  }));

  // Store untouched copy for search filtering (plain ID strings, not object refs)
  originalGraphData = {
    nodes: nodes.map((n) => ({ ...n })),
    links: links.map((l) => ({ ...l })),
  };

  if (graph) {
    applySearchFilter();
    if (currentView === "table") renderTable();
    return;
  }

  graph = ForceGraph()(canvas)
    .graphData({ nodes, links })
    .backgroundColor("#000000")
    .width(window.innerWidth)
    .height(window.innerHeight)
    .nodeRelSize(NODE_RADIUS)
    .nodeCanvasObject((node, ctx) => {
      const color = node.is_ghost ? GHOST_COLOR : LIVE_COLOR;
      ctx.beginPath();
      ctx.arc(node.x, node.y, NODE_RADIUS, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.font = LABEL_FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = node.is_ghost ? "#555555" : "rgba(255,255,255,0.6)";
      ctx.fillText(node.label, node.x, node.y + NODE_RADIUS + 2);
    })
    .nodePointerAreaPaint((node, color, ctx) => {
      ctx.beginPath();
      ctx.arc(node.x, node.y, NODE_RADIUS + 4, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
    })
    .linkColor(() => "rgba(255,255,255,0.35)")
    .linkWidth(1)
    .linkCanvasObjectMode(() => "after")
    .linkCanvasObject((link, ctx) => {
      if (!link.label) return;
      const mid = {
        x: (link.source.x + link.target.x) / 2,
        y: (link.source.y + link.target.y) / 2,
      };
      ctx.font = "3px Inter, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "rgba(255,255,255,0.35)";
      ctx.fillText(link.label, mid.x, mid.y);
    })
    .d3AlphaDecay(0.008)
    .d3VelocityDecay(0.4)
    .d3AlphaMin(0.005)
    .warmupTicks(50)
    .cooldownTicks(300)
    .onNodeClick((node) => openPeek(node.id))
    .onNodeDragEnd((node) => {
      // Release node so it flows back into the simulation
      node.fx = undefined;
      node.fy = undefined;
    });

  // ── Fluid D3 force tuning ──
  graph.d3Force("charge").strength(-25);
  graph.d3Force("link").distance(50).strength(0.3);
  graph.d3Force("center", d3.forceCenter().strength(0.03));
  graph.d3Force("collide", d3.forceCollide(NODE_RADIUS + 2).strength(0.7).iterations(2));

  window.addEventListener("resize", () => {
    if (graph && currentView === "graph") {
      graph.width(window.innerWidth).height(window.innerHeight);
    }
  });
}

// ── Center Peek Modal ──────────────────────────────────
async function openPeek(nodeId) {
  currentNodeId = nodeId;

  const res = await fetch(API.nodeDetail(nodeId));
  if (!res.ok) return;
  const node = await res.json();

  peekTitle.value = node.title;
  peekType.value = node.node_type;
  updateTypeDropdownStyle();

  if (node.profile_image) {
    peekImage.src = node.profile_image;
    peekImage.classList.remove("hidden");
    peekImagePlaceholder.classList.add("hidden");
  } else {
    peekImage.classList.add("hidden");
    peekImagePlaceholder.classList.remove("hidden");
  }

  peekImageZone.className = peekImageZone.className
    .replace(/rounded-full|rounded-md/g, "").trim();
  peekImageZone.classList.add(
    node.node_type === "COMPANY" ? "rounded-md" : "rounded-full"
  );

  peekNotes.value = node.notes || "";
  switchNotesMode("edit");
  renderProperties(node.properties || {});

  // Show delete button in edit mode
  peekDelete.classList.remove("hidden");

  if (isExpanded) toggleExpand();
  peekBackdrop.classList.remove("hidden");
}

function openPeekCreate() {
  // Create mode: no ID, blank fields
  currentNodeId = null;

  peekTitle.value = "";
  peekType.value = "PERSON";
  updateTypeDropdownStyle();

  peekImage.classList.add("hidden");
  peekImagePlaceholder.classList.remove("hidden");
  peekImageZone.className = peekImageZone.className
    .replace(/rounded-full|rounded-md/g, "").trim();
  peekImageZone.classList.add("rounded-full");

  peekNotes.value = "";
  switchNotesMode("edit");
  renderProperties({});

  // Hide delete button in create mode
  peekDelete.classList.add("hidden");

  if (isExpanded) toggleExpand();
  peekBackdrop.classList.remove("hidden");

  // Focus the title input
  setTimeout(() => peekTitle.focus(), 100);
}

function closePeek() {
  peekBackdrop.classList.add("hidden");
  currentNodeId = null;
  hideMentionDropdown();
  if (isExpanded) toggleExpand();
}

function toggleExpand() {
  isExpanded = !isExpanded;
  const expandIcon = document.getElementById("expand-icon");

  if (isExpanded) {
    peekModal.classList.remove("max-w-3xl", "rounded-xl", "max-h-[85vh]", "mx-4");
    peekModal.classList.add("w-full", "h-full", "max-w-none", "rounded-none");
    expandIcon.setAttribute("d", "M4 14h6v6M14 10h6V4M4 14l7-7M20 4l-7 7");
  } else {
    peekModal.classList.remove("w-full", "h-full", "max-w-none", "rounded-none");
    peekModal.classList.add("max-w-3xl", "rounded-xl", "max-h-[85vh]", "mx-4");
    expandIcon.setAttribute("d", "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7");
  }
}

function updateTypeDropdownStyle() {
  peekType.classList.remove("text-blue-400", "text-green-400", "text-amber-400");
  const colorMap = {
    PERSON: "text-blue-400",
    COMPANY: "text-green-400",
    MEETING: "text-amber-400",
  };
  peekType.classList.add(colorMap[peekType.value] || "text-white/50");
}

// ── Properties ─────────────────────────────────────────
function renderProperties(props) {
  propsGrid.innerHTML = "";
  const nodeType = peekType.value;
  const locked = LOCKED_KEYS[nodeType] || [];

  // Render locked keys first (in order), then custom keys
  for (const key of locked) {
    addPropertyRow(key, String(props[key] || ""), true);
  }
  for (const [key, value] of Object.entries(props)) {
    if (!locked.includes(key)) {
      addPropertyRow(key, String(value), false);
    }
  }
}

function addPropertyRow(key = "", value = "", isLocked = false) {
  const row = document.createElement("div");
  row.className = "prop-row";

  const keyInput = document.createElement("input");
  keyInput.type = "text";
  keyInput.placeholder = "Key";
  keyInput.value = key;
  keyInput.className = "prop-key";
  if (isLocked) {
    keyInput.readOnly = true;
    keyInput.style.opacity = "0.6";
  }

  const valInput = document.createElement("input");
  valInput.type = "text";
  valInput.placeholder = "Value — type @ to mention";
  valInput.value = value;
  valInput.className = "prop-value";

  // Bind @mention autocomplete to property value inputs
  bindMentionToInput(valInput);

  if (isLocked) {
    // No delete button for locked keys — add a spacer to keep alignment
    const spacer = document.createElement("span");
    spacer.style.width = "22px";
    spacer.style.flexShrink = "0";
    row.append(keyInput, valInput, spacer);
  } else {
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "prop-delete";
    delBtn.textContent = "\u00d7";
    delBtn.addEventListener("click", () => row.remove());
    row.append(keyInput, valInput, delBtn);
  }

  propsGrid.appendChild(row);
}

function collectProperties() {
  const props = {};
  const rows = propsGrid.querySelectorAll(".prop-row");
  for (const row of rows) {
    const key = row.querySelector(".prop-key").value.trim();
    const val = row.querySelector(".prop-value").value.trim();
    if (key) {
      props[key] = val;
    }
  }
  return props;
}

// ── Markdown Toggle ────────────────────────────────────
function switchNotesMode(mode) {
  notesMode = mode;
  if (mode === "edit") {
    peekNotes.classList.remove("hidden");
    peekNotesPreview.classList.add("hidden");
    notesTabEdit.classList.add("bg-white/10", "text-white/70");
    notesTabEdit.classList.remove("bg-transparent", "text-white/30");
    notesTabPreview.classList.add("bg-transparent", "text-white/30");
    notesTabPreview.classList.remove("bg-white/10", "text-white/70");
  } else {
    const raw = peekNotes.value || "";
    const html = DOMPurify.sanitize(marked.parse(raw));
    peekNotesPreview.innerHTML = html || '<p class="text-white/20 italic">Nothing to preview</p>';
    peekNotes.classList.add("hidden");
    peekNotesPreview.classList.remove("hidden");
    notesTabPreview.classList.add("bg-white/10", "text-white/70");
    notesTabPreview.classList.remove("bg-transparent", "text-white/30");
    notesTabEdit.classList.add("bg-transparent", "text-white/30");
    notesTabEdit.classList.remove("bg-white/10", "text-white/70");
  }
}

// ── @ Mention Autocomplete ─────────────────────────────
let mentionActiveIndex = -1;
let mentionResults = [];
let searchTimeout = null;
let mentionTarget = null; // currently active input/textarea for mention

function getMentionContext(el) {
  const pos = el.selectionStart;
  const text = el.value.substring(0, pos);

  let i = pos - 1;
  while (i >= 0 && text[i] !== "@" && text[i] !== "\n") {
    i--;
  }

  if (i < 0 || text[i] !== "@") return null;
  if (i > 0 && !/[\s,]/.test(text[i - 1])) return null;

  const query = text.substring(i + 1);
  return { start: i, query };
}

async function searchNodes(query) {
  const res = await fetch(`${API.nodeSearch}?q=${encodeURIComponent(query)}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.results || [];
}

function showMentionDropdown(results, el) {
  mentionResults = results;
  mentionActiveIndex = 0;
  mentionTarget = el;
  mentionDropdown.innerHTML = "";

  if (results.length === 0) {
    hideMentionDropdown();
    return;
  }

  // Position dropdown near the active element
  // For inputs inside peek modal, position relative to their container
  const rect = el.getBoundingClientRect();
  const parentRect = mentionDropdown.parentElement.getBoundingClientRect();
  mentionDropdown.style.top = `${rect.bottom - parentRect.top + 4}px`;
  mentionDropdown.style.left = `${rect.left - parentRect.left}px`;

  for (let i = 0; i < results.length; i++) {
    const item = document.createElement("div");
    item.className = "mention-item" + (i === 0 ? " active" : "");
    item.innerHTML = `
      <span class="mention-title">${escapeHtml(results[i].title)}</span>
      <span class="mention-type">${results[i].node_type}</span>
    `;
    item.addEventListener("mousedown", (e) => {
      e.preventDefault();
      selectMention(i);
    });
    mentionDropdown.appendChild(item);
  }

  mentionDropdown.classList.remove("hidden");
}

function hideMentionDropdown() {
  mentionDropdown.classList.add("hidden");
  mentionDropdown.innerHTML = "";
  mentionResults = [];
  mentionActiveIndex = -1;
  mentionTarget = null;
}

function selectMention(index) {
  const selected = mentionResults[index];
  const el = mentionTarget;
  if (!selected || !el) return;

  const ctx = getMentionContext(el);
  if (!ctx) return;

  const before = el.value.substring(0, ctx.start);
  const after = el.value.substring(el.selectionStart);
  const insertion = `@${selected.title}`;
  const suffix = after && /^[,.\s]/.test(after) ? "" : ", ";

  el.value = before + insertion + suffix + after;

  const newPos = before.length + insertion.length + suffix.length;
  el.selectionStart = newPos;
  el.selectionEnd = newPos;
  el.focus();

  hideMentionDropdown();
}

function handleMentionInput(el) {
  const ctx = getMentionContext(el);

  if (!ctx) {
    hideMentionDropdown();
    return;
  }

  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(async () => {
    const results = await searchNodes(ctx.query);
    showMentionDropdown(results, el);
  }, 150);
}

function handleMentionKeydown(e) {
  if (mentionDropdown.classList.contains("hidden")) return;

  if (e.key === "ArrowDown") {
    e.preventDefault();
    mentionActiveIndex = (mentionActiveIndex + 1) % mentionResults.length;
    updateDropdownHighlight();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    mentionActiveIndex = (mentionActiveIndex - 1 + mentionResults.length) % mentionResults.length;
    updateDropdownHighlight();
  } else if (e.key === "Enter" || e.key === "Tab") {
    if (mentionResults.length > 0) {
      e.preventDefault();
      selectMention(mentionActiveIndex);
    }
  } else if (e.key === "Escape") {
    hideMentionDropdown();
  }
}

function updateDropdownHighlight() {
  const items = mentionDropdown.querySelectorAll(".mention-item");
  items.forEach((item, i) => {
    item.classList.toggle("active", i === mentionActiveIndex);
  });
}

/** Bind @mention autocomplete to any input or textarea element */
function bindMentionToInput(el) {
  el.addEventListener("input", () => handleMentionInput(el));
  el.addEventListener("keydown", handleMentionKeydown);
  el.addEventListener("blur", () => setTimeout(hideMentionDropdown, 200));
}

// Bind mentions to the notes textarea
bindMentionToInput(peekNotes);

// ── Image upload ───────────────────────────────────────
peekImageZone.addEventListener("click", () => {
  // Only allow image upload in edit mode (node must exist first)
  if (!currentNodeId) return;
  peekImageInput.click();
});

peekImageInput.addEventListener("change", async () => {
  const file = peekImageInput.files[0];
  if (!file || !currentNodeId) return;

  const formData = new FormData();
  formData.append("image", file);

  const res = await fetch(API.nodeImage(currentNodeId), {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    alert("Image upload failed");
    return;
  }

  const data = await res.json();
  peekImage.src = data.profile_image;
  peekImage.classList.remove("hidden");
  peekImagePlaceholder.classList.add("hidden");
  peekImageInput.value = "";
});

// ── Save node (Create or Update) ───────────────────────
async function saveNode() {
  peekSave.disabled = true;
  peekSave.textContent = "Saving...";

  const title = peekTitle.value.trim();
  if (!title) {
    alert("Title is required");
    peekSave.disabled = false;
    peekSave.textContent = "Save";
    return;
  }

  try {
    if (currentNodeId) {
      // ── UPDATE existing node ──
      const payload = {
        title,
        node_type: peekType.value,
        properties: collectProperties(),
        notes: peekNotes.value,
      };

      const res = await fetch(API.nodeUpdate(currentNodeId), {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        alert(err.error || "Save failed");
        return;
      }
    } else {
      // ── CREATE new node ──
      const payload = {
        title,
        node_type: peekType.value,
        properties: collectProperties(),
        notes: peekNotes.value,
      };

      const res = await fetch(API.nodes, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        alert(err.error || "Create failed");
        return;
      }

      const created = await res.json();
      // Switch to edit mode for this new node
      currentNodeId = created.id;
      peekDelete.classList.remove("hidden");
    }

    await loadGraph();
  } finally {
    peekSave.disabled = false;
    peekSave.textContent = "Save";
  }
}

// ── Delete node ────────────────────────────────────────
async function deleteNode() {
  if (!currentNodeId) return;
  if (!confirm("Delete this node and all its connections? This cannot be undone.")) return;

  const res = await fetch(API.nodeDelete(currentNodeId), {
    method: "DELETE",
    headers: apiHeaders(),
  });

  if (!res.ok) {
    alert("Delete failed");
    return;
  }

  closePeek();
  await loadGraph();
}

// ── Event listeners ────────────────────────────────────
fab.addEventListener("click", openPeekCreate);

peekClose.addEventListener("click", closePeek);
peekExpand.addEventListener("click", toggleExpand);
peekSave.addEventListener("click", saveNode);
peekDelete.addEventListener("click", deleteNode);
peekType.addEventListener("change", updateTypeDropdownStyle);
addPropBtn.addEventListener("click", () => addPropertyRow());

notesTabEdit.addEventListener("click", () => switchNotesMode("edit"));
notesTabPreview.addEventListener("click", () => switchNotesMode("preview"));

peekBackdrop.addEventListener("click", (e) => {
  if (e.target === peekBackdrop) closePeek();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!mentionDropdown.classList.contains("hidden")) {
      hideMentionDropdown();
    } else if (!settingsBackdrop.classList.contains("hidden")) {
      closeSettings();
    } else if (!peekBackdrop.classList.contains("hidden")) {
      closePeek();
    }
  }
});

// ── Settings Modal (Node Templates) ─────────────────────
let currentTplTab = "PERSON";
let templateCache = {}; // { PERSON: { default_properties, default_notes }, ... }

async function loadTemplates() {
  const res = await fetch(API.templates);
  if (res.ok) {
    templateCache = await res.json();
  }
}

function openSettings() {
  loadTemplates().then(() => {
    switchTplTab("PERSON");
    settingsBackdrop.classList.remove("hidden");
  });
}

function closeSettings() {
  settingsBackdrop.classList.add("hidden");
}

function switchTplTab(type) {
  currentTplTab = type;

  // Update tab styling
  tplTabs.forEach((tab) => {
    if (tab.dataset.tplTab === type) {
      tab.classList.add("bg-white/10");
      tab.classList.remove("bg-transparent");
    } else {
      tab.classList.remove("bg-white/10");
      tab.classList.add("bg-transparent");
    }
  });

  // Populate fields from cache
  const tpl = templateCache[type] || { default_properties: {}, default_notes: "" };
  tplPropsGrid.innerHTML = "";

  const lockedKeys = LOCKED_KEYS[type] || [];
  for (const [key, value] of Object.entries(tpl.default_properties)) {
    // Skip system-locked keys in the template editor
    if (lockedKeys.includes(key)) continue;
    addTplPropertyRow(key, String(value));
  }

  tplNotes.value = tpl.default_notes || "";
}

function addTplPropertyRow(key = "", value = "") {
  const row = document.createElement("div");
  row.className = "prop-row";

  const keyInput = document.createElement("input");
  keyInput.type = "text";
  keyInput.placeholder = "Key";
  keyInput.value = key;
  keyInput.className = "prop-key";

  const valInput = document.createElement("input");
  valInput.type = "text";
  valInput.placeholder = "Default value";
  valInput.value = value;
  valInput.className = "prop-value";

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "prop-delete";
  delBtn.textContent = "\u00d7";
  delBtn.addEventListener("click", () => row.remove());

  row.append(keyInput, valInput, delBtn);
  tplPropsGrid.appendChild(row);
}

function collectTplProperties() {
  const props = {};
  const rows = tplPropsGrid.querySelectorAll(".prop-row");
  for (const row of rows) {
    const key = row.querySelector(".prop-key").value.trim();
    const val = row.querySelector(".prop-value").value.trim();
    if (key) {
      props[key] = val;
    }
  }
  return props;
}

async function saveTemplate() {
  tplSave.disabled = true;
  tplSave.textContent = "Saving...";

  try {
    const payload = {
      node_type: currentTplTab,
      default_properties: collectTplProperties(),
      default_notes: tplNotes.value,
    };

    const res = await fetch(API.templates, {
      method: "PUT",
      headers: apiHeaders(),
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      alert(err.error || "Save failed");
      return;
    }

    const saved = await res.json();
    templateCache[currentTplTab] = {
      default_properties: saved.default_properties,
      default_notes: saved.default_notes,
    };
  } finally {
    tplSave.disabled = false;
    tplSave.textContent = "Save Templates";
  }
}

settingsBtn.addEventListener("click", openSettings);
settingsClose.addEventListener("click", closeSettings);
settingsBackdrop.addEventListener("click", (e) => {
  if (e.target === settingsBackdrop) closeSettings();
});
tplTabs.forEach((tab) => {
  tab.addEventListener("click", () => switchTplTab(tab.dataset.tplTab));
});
tplAddProp.addEventListener("click", () => addTplPropertyRow());
tplSave.addEventListener("click", saveTemplate);

// ── Multi-Hop Graph Search ──────────────────────────────
let searchFilterTimeout = null;

function applySearchFilter() {
  if (!graph) return;

  const query = searchInput.value.trim().toLowerCase();
  const hops = Math.max(0, Math.min(3, parseInt(hopInput.value, 10) || 0));

  // No query → show full graph
  if (!query) {
    graph.graphData({
      nodes: originalGraphData.nodes.map((n) => ({ ...n })),
      links: originalGraphData.links.map((l) => ({ ...l })),
    });
    return;
  }

  // Hop 0: seed nodes matching the query
  const includedNodeIds = new Set();
  for (const node of originalGraphData.nodes) {
    if (node.label.toLowerCase().includes(query)) {
      includedNodeIds.add(node.id);
    }
  }

  // Hop 1..N: expand by traversing links
  for (let hop = 0; hop < hops; hop++) {
    const nextLevelIds = new Set();
    for (const link of originalGraphData.links) {
      // originalGraphData links always have plain string IDs (never object refs)
      const srcId = typeof link.source === "object" ? link.source.id : link.source;
      const tgtId = typeof link.target === "object" ? link.target.id : link.target;

      if (includedNodeIds.has(srcId) && !includedNodeIds.has(tgtId)) {
        nextLevelIds.add(tgtId);
      }
      if (includedNodeIds.has(tgtId) && !includedNodeIds.has(srcId)) {
        nextLevelIds.add(srcId);
      }
    }
    for (const id of nextLevelIds) {
      includedNodeIds.add(id);
    }
  }

  // Filter nodes and links where both endpoints are included
  const filteredNodes = originalGraphData.nodes
    .filter((n) => includedNodeIds.has(n.id))
    .map((n) => ({ ...n }));

  const filteredLinks = originalGraphData.links
    .filter((l) => {
      const srcId = typeof l.source === "object" ? l.source.id : l.source;
      const tgtId = typeof l.target === "object" ? l.target.id : l.target;
      return includedNodeIds.has(srcId) && includedNodeIds.has(tgtId);
    })
    .map((l) => ({
      source: typeof l.source === "object" ? l.source.id : l.source,
      target: typeof l.target === "object" ? l.target.id : l.target,
      label: l.label,
    }));

  graph.graphData({ nodes: filteredNodes, links: filteredLinks });
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchFilterTimeout);
  searchFilterTimeout = setTimeout(applySearchFilter, 200);
});

hopInput.addEventListener("input", () => {
  applySearchFilter();
});

// ── Init ───────────────────────────────────────────────
loadGraph();
