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
  import: "/api/import/",
  // Pipeline & notifications
  ingestMeeting: "/api/ingest/meeting/",
  ingestNote: "/api/ingest/note/",
  ingestVoice: "/api/ingest/voice/",
  ingestDocument: "/api/ingest/document/",
  ingestions: "/api/ingestions/",
  ingestionReview: (id) => `/api/ingestions/${id}/review/`,
  ingestionRetry: (id) => `/api/ingestions/${id}/retry/`,
  ingestionDismiss: (id) => `/api/ingestions/${id}/dismiss/`,
  ingestionDelete: (id) => `/api/ingestions/${id}/delete/`,
  resolutionQueue: "/api/resolution-queue/",
  resolutionResolve: (id) => `/api/resolution-queue/${id}/resolve/`,
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
let showGhosts = true;
let currentPeekTab = "notes"; // "notes" | "summary"
let currentNodeSummary = null; // cached summary data for current peek node
let currentNodeType = "PERSON"; // cached node_type for current peek node
let currentNodeInteractions = []; // aggregated meeting history for PERSON nodes

// ── Node Type Filter ──────────────────────────────────
const NODE_TYPE_DEFAULTS = {
  PERSON: true,
  COMPANY: true,
};

const NODE_TYPE_COLORS = {
  PERSON: "#6366f1",
  COMPANY: "#10b981",
};

// Filter panel open/closed state
let filterPanelOpen = false;

let visibleTypes = new Set();

// Load from localStorage or apply defaults
(function initVisibleTypes() {
  const saved = localStorage.getItem("graphVisibleTypes");
  if (saved) {
    try {
      const arr = JSON.parse(saved);
      if (Array.isArray(arr) && arr.length > 0) {
        visibleTypes = new Set(arr);
        return;
      }
    } catch (_) { /* fall through to defaults */ }
  }
  for (const [type, on] of Object.entries(NODE_TYPE_DEFAULTS)) {
    if (on) visibleTypes.add(type);
  }
})();

// ── Marked.js config: enable single-line breaks ───────
marked.use({ breaks: true });

// ── Toast system ──────────────────────────────────────
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast-item toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add("toast-out");
    el.addEventListener("animationend", () => el.remove());
  }, 3000);
}

// ── Node Type Filter Panel ────────────────────────────
const typeFilterPanel = document.getElementById("type-filter-panel");
const typeFilterBar = document.getElementById("type-filter-bar");
const typeFilterToggle = document.getElementById("type-filter-toggle");

typeFilterToggle.addEventListener("click", () => {
  filterPanelOpen = !filterPanelOpen;
  typeFilterBar.classList.toggle("hidden", !filterPanelOpen);
  typeFilterToggle.classList.toggle("text-white/60", filterPanelOpen);
  typeFilterToggle.classList.toggle("text-white/35", !filterPanelOpen);
});

function buildTypeFilterBar() {
  typeFilterBar.innerHTML = "";
  const allNodes = originalGraphData.nodes;
  const typeCounts = {};
  for (const n of allNodes) {
    const t = n.group || "UNKNOWN";
    typeCounts[t] = (typeCounts[t] || 0) + 1;
  }

  // Ensure defaults appear even if count is 0
  for (const t of Object.keys(NODE_TYPE_DEFAULTS)) {
    if (!(t in typeCounts)) typeCounts[t] = 0;
  }
  // Ensure visibleTypes includes only known types; new types default to visible
  for (const t of Object.keys(typeCounts)) {
    if (!(t in NODE_TYPE_DEFAULTS) && !visibleTypes.has(t)) {
      visibleTypes.add(t);
    }
  }

  const sortedTypes = Object.keys(typeCounts).sort((a, b) => {
    const order = Object.keys(NODE_TYPE_DEFAULTS);
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });

  for (const type of sortedTypes) {
    const count = typeCounts[type];
    const isActive = visibleTypes.has(type);
    const color = NODE_TYPE_COLORS[type] || "#888";
    const btn = document.createElement("button");
    btn.className = `type-toggle ${isActive ? "active" : "inactive"}${count === 0 ? " dimmed" : ""}`;
    btn.dataset.nodeType = type;

    const label = type.charAt(0) + type.slice(1).toLowerCase();
    btn.innerHTML = `<span class="type-dot" style="background:${color}"></span>${label}<span class="type-count">${count}</span>`;

    if (count > 0) {
      btn.addEventListener("click", () => toggleNodeType(type));
    }
    typeFilterBar.appendChild(btn);
  }
}

function toggleNodeType(type) {
  if (visibleTypes.has(type)) {
    // Prevent hiding the last visible type
    if (visibleTypes.size <= 1) {
      showToast("At least one node type must be visible.", "info");
      return;
    }
    visibleTypes.delete(type);

    // If peek modal is open for a node of this type, close it
    if (currentNodeId && cachedGraphData) {
      const peekNode = cachedGraphData.nodes.find((n) => n.id === currentNodeId);
      if (peekNode && (peekNode.group || peekNode.node_type) === type) {
        closePeek();
      }
    }
  } else {
    visibleTypes.add(type);
  }

  // Persist
  localStorage.setItem("graphVisibleTypes", JSON.stringify([...visibleTypes]));

  // Update button states
  updateFilterButtons();

  // Re-filter the graph
  applySearchFilter();

  if (currentView === "table") renderTable();
}

function updateFilterButtons() {
  const buttons = typeFilterBar.querySelectorAll(".type-toggle");
  for (const btn of buttons) {
    const type = btn.dataset.nodeType;
    const isActive = visibleTypes.has(type);
    btn.classList.toggle("active", isActive);
    btn.classList.toggle("inactive", !isActive);
  }
}

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
const ghostToggle = document.getElementById("ghost-toggle");

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

// Import
const importBtn = document.getElementById("import-btn");
const importToast = document.getElementById("import-toast");
const importToastText = document.getElementById("import-toast-text");

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
    typeFilterPanel.classList.remove("hidden");
    toggleGraphBtn.classList.add("bg-gray-600", "text-white");
    toggleGraphBtn.classList.remove("bg-transparent", "text-white/40");
    toggleTableBtn.classList.add("bg-transparent", "text-white/40");
    toggleTableBtn.classList.remove("bg-gray-600", "text-white");
    // Resize graph after showing
    if (graph) graph.width(window.innerWidth).height(window.innerHeight);
  } else {
    canvas.classList.add("hidden");
    tableView.classList.remove("hidden");
    typeFilterPanel.classList.add("hidden");
    toggleTableBtn.classList.add("bg-gray-600", "text-white");
    toggleTableBtn.classList.remove("bg-transparent", "text-white/40");
    toggleGraphBtn.classList.add("bg-transparent", "text-white/40");
    toggleGraphBtn.classList.remove("bg-gray-600", "text-white");
    renderTable();
  }
}

// ── Table multi-select state ────────────────────────────
const bulkBar = document.getElementById("bulk-bar");
const bulkCount = document.getElementById("bulk-count");
const bulkDeleteBtn = document.getElementById("bulk-delete-btn");
const bulkCancelBtn = document.getElementById("bulk-cancel-btn");
const selectAllCb = document.getElementById("select-all-cb");
let selectedIds = new Set();

function updateBulkBar() {
  const count = selectedIds.size;
  if (count > 0) {
    bulkBar.classList.remove("hidden");
    bulkCount.textContent = `${count} selected`;
  } else {
    bulkBar.classList.add("hidden");
  }
  // Sync select-all checkbox state
  const allCbs = tableBody.querySelectorAll(".row-cb");
  if (allCbs.length > 0 && count === allCbs.length) {
    selectAllCb.checked = true;
    selectAllCb.indeterminate = false;
  } else if (count > 0) {
    selectAllCb.checked = false;
    selectAllCb.indeterminate = true;
  } else {
    selectAllCb.checked = false;
    selectAllCb.indeterminate = false;
  }
}

function clearSelection() {
  selectedIds.clear();
  tableBody.querySelectorAll(".row-cb").forEach((cb) => (cb.checked = false));
  tableBody.querySelectorAll("tr[data-id]").forEach((tr) =>
    tr.classList.remove("bg-white/[0.04]")
  );
  updateBulkBar();
}

selectAllCb.addEventListener("change", () => {
  const allCbs = tableBody.querySelectorAll(".row-cb");
  allCbs.forEach((cb) => {
    cb.checked = selectAllCb.checked;
    const id = cb.dataset.id;
    if (selectAllCb.checked) {
      selectedIds.add(id);
      cb.closest("tr").classList.add("bg-white/[0.04]");
    } else {
      selectedIds.delete(id);
      cb.closest("tr").classList.remove("bg-white/[0.04]");
    }
  });
  updateBulkBar();
});

bulkCancelBtn.addEventListener("click", clearSelection);

bulkDeleteBtn.addEventListener("click", async () => {
  const ids = [...selectedIds];
  if (!confirm(`Delete ${ids.length} node${ids.length > 1 ? "s" : ""} and all their connections?`)) return;

  bulkDeleteBtn.disabled = true;
  bulkDeleteBtn.textContent = "Deleting...";

  await Promise.all(
    ids.map((id) => fetch(API.nodeDelete(id), { method: "DELETE", headers: apiHeaders() }))
  );

  selectedIds.clear();
  bulkBar.classList.add("hidden");
  await loadGraph();
  renderTable();
});

function renderTable() {
  tableBody.innerHTML = "";
  selectedIds.clear();
  updateBulkBar();

  if (!cachedGraphData || cachedGraphData.nodes.length === 0) {
    tableEmpty.classList.remove("hidden");
    return;
  }
  tableEmpty.classList.add("hidden");

  // Filter by visible types and ghosts, then sort alphabetically
  let nodes = cachedGraphData.nodes.filter((n) => visibleTypes.has(n.group || n.node_type || "UNKNOWN"));
  if (!showGhosts) nodes = nodes.filter((n) => !n.is_ghost);
  nodes.sort((a, b) => a.label.localeCompare(b.label));

  for (const node of nodes) {
    const tr = document.createElement("tr");
    tr.dataset.id = node.id;
    tr.className =
      "border-b border-white/[0.04] cursor-pointer hover:bg-white/[0.03] transition-colors duration-100";

    const opacity = node.is_ghost ? "text-white/30" : "text-white/80";

    tr.innerHTML = `
      <td class="py-3 pl-4 pr-2" onclick="event.stopPropagation()">
        <input type="checkbox" class="row-cb w-3.5 h-3.5 rounded border-white/20 bg-transparent accent-white cursor-pointer"
               data-id="${node.id}">
      </td>
      <td class="py-3 px-4 text-sm ${opacity}">${escapeHtml(node.label)}</td>
      <td class="py-3 px-4">
        <span class="inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider
                     badge-${node.group}">${node.group}</span>
      </td>
      <td class="py-3 px-4 text-sm text-white/30">${node.created_at ? formatDate(node.created_at) : ""}</td>
      <td class="py-3 px-2 text-right">
        <button class="table-delete-btn p-1.5 text-white/15 hover:text-red-400/80 bg-transparent border-none
                       cursor-pointer rounded hover:bg-white/5 transition-colors duration-150 outline-none"
                title="Delete node">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/>
          </svg>
        </button>
      </td>
    `;

    // Checkbox toggle
    const cb = tr.querySelector(".row-cb");
    cb.addEventListener("change", () => {
      if (cb.checked) {
        selectedIds.add(node.id);
        tr.classList.add("bg-white/[0.04]");
      } else {
        selectedIds.delete(node.id);
        tr.classList.remove("bg-white/[0.04]");
      }
      updateBulkBar();
    });

    // Click row to open peek (but not on checkbox or delete button)
    tr.addEventListener("click", (e) => {
      if (e.target.closest(".table-delete-btn") || e.target.closest("td:first-child")) return;
      openPeek(node.id);
    });

    // Single delete button
    tr.querySelector(".table-delete-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete "${node.label}" and all its connections?`)) return;
      const res = await fetch(API.nodeDelete(node.id), {
        method: "DELETE",
        headers: apiHeaders(),
      });
      if (res.ok) {
        selectedIds.delete(node.id);
        await loadGraph();
        renderTable();
      } else {
        alert("Delete failed");
      }
    });

    tableBody.appendChild(tr);
  }
}

toggleGraphBtn.addEventListener("click", () => switchView("graph"));
toggleTableBtn.addEventListener("click", () => switchView("table"));

// ── force-graph rendering ──────────────────────────────
const NODE_MIN_RADIUS = 1.5;
const NODE_SCALE_FACTOR = 0.3;
const GHOST_COLOR = "#555555";
const BG_COLOR = "#191919";

const LABEL_FONT_SIZE = 3;
const LABEL_COLOR_GHOST = "#555555";
const LABEL_COLOR_HOVER = "#FFFFFF";
const TEXT_THRESHOLD = 2.0;
const LABEL_ALWAYS_SHOW_DEGREE = 3; // lowered: show labels for nodes with 3+ connections

// ── Visual hierarchy: colors & sizes by node type ──
const NODE_COLORS = {
  PERSON:  "#6366f1",  // Indigo
  COMPANY: "#10b981",  // Emerald
  MEETING: "#6b7280",  // Grey
};

const NODE_STROKE_COLORS = {
  PERSON:  "#818cf8",  // Lighter indigo
  COMPANY: "#34d399",  // Lighter emerald
  MEETING: "#9ca3af",  // Lighter grey
};

const NODE_LABEL_COLORS = {
  PERSON:  "#c7d2fe",  // Light indigo
  COMPANY: "#6ee7b7",  // Light emerald
  MEETING: "#d1d5db",  // Light grey
};

const NODE_TYPE_RADIUS = {
  PERSON:  5,
  COMPANY: 4,
  MEETING: 2.5,
};

function nodeRadius(degree, nodeType) {
  const base = NODE_TYPE_RADIUS[nodeType] || 3;
  return base + Math.sqrt(degree || 0) * NODE_SCALE_FACTOR;
}

// Focus/highlight state
let hoveredNode = null;
let focusedNeighbors = new Set(); // IDs of directly connected nodes
let focusedLinks = new Set(); // stringified link keys

function buildFocusSets(node, graphData) {
  focusedNeighbors.clear();
  focusedLinks.clear();
  if (!node) return;

  focusedNeighbors.add(node.id);
  const links = graphData.links || [];
  for (const link of links) {
    const srcId = typeof link.source === "object" ? link.source.id : link.source;
    const tgtId = typeof link.target === "object" ? link.target.id : link.target;
    if (srcId === node.id) {
      focusedNeighbors.add(tgtId);
      focusedLinks.add(`${srcId}__${tgtId}`);
    } else if (tgtId === node.id) {
      focusedNeighbors.add(srcId);
      focusedLinks.add(`${srcId}__${tgtId}`);
    }
  }
}

function isLinkFocused(link) {
  const srcId = typeof link.source === "object" ? link.source.id : link.source;
  const tgtId = typeof link.target === "object" ? link.target.id : link.target;
  return focusedLinks.has(`${srcId}__${tgtId}`) || focusedLinks.has(`${tgtId}__${srcId}`);
}

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
    metadata: e.metadata || {},
  }));

  // Compute degree (number of connections) for each node
  const degreeMap = {};
  for (const link of links) {
    degreeMap[link.source] = (degreeMap[link.source] || 0) + 1;
    degreeMap[link.target] = (degreeMap[link.target] || 0) + 1;
  }
  for (const node of nodes) {
    node.degree = degreeMap[node.id] || 0;
    node._radius = nodeRadius(node.degree, node.group);
  }

  // Store untouched copy for search filtering (plain ID strings, not object refs)
  originalGraphData = {
    nodes: nodes.map((n) => ({ ...n })),
    links: links.map((l) => ({ ...l })),
  };

  // Rebuild type filter toggles with updated counts
  buildTypeFilterBar();

  if (graph) {
    applySearchFilter();
    if (currentView === "table") renderTable();
    return;
  }

  // ── Label culling: bounding-box overlap detection ──
  // Each frame, we collect rendered label boxes and skip labels that would overlap.
  let _labelBoxes = [];
  let _lastFrameTime = 0; // used to detect new render frames

  /**
   * Predict the bounding box for a node's label.
   * Returns { x, y, w, h, degree } or null if no label.
   */
  function predictLabelBox(node, ctx, fontSize) {
    if (!node.label) return null;
    const r = node._radius || NODE_MIN_RADIUS;
    const textWidth = ctx.measureText(node.label).width;
    const textHeight = fontSize * 1.2;
    return {
      x: node.x - textWidth / 2,
      y: node.y + r + 2,
      w: textWidth,
      h: textHeight,
      degree: node.degree || 0,
    };
  }

  /** Check if two rectangles overlap */
  function boxesOverlap(a, b) {
    return !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y);
  }

  /** Check if a label box overlaps any nearby node circle */
  function overlapsNodeCircle(box, allNodes, selfId) {
    for (const n of allNodes) {
      if (n.id === selfId) continue;
      const r = n._radius || NODE_MIN_RADIUS;
      // Approximate circle as a bounding square for fast check
      const cx = n.x - r;
      const cy = n.y - r;
      const cs = r * 2;
      if (!(box.x + box.w < cx || cx + cs < box.x || box.y + box.h < cy || cy + cs < box.y)) {
        return true;
      }
    }
    return false;
  }

  /**
   * Try to register a label box. Returns true if the label can be drawn
   * (no overlap with existing labels or nearby node circles).
   */
  function tryRegisterLabel(box, allNodes, selfId) {
    // Check overlap with node circles
    if (overlapsNodeCircle(box, allNodes, selfId)) return false;
    // Check overlap with already-registered labels
    for (const existing of _labelBoxes) {
      if (boxesOverlap(box, existing)) {
        // Collision: lower-degree node loses
        if (box.degree < existing.degree) return false;
      }
    }
    // Remove any lower-degree boxes this one would replace
    _labelBoxes = _labelBoxes.filter(
      (existing) => !(boxesOverlap(box, existing) && existing.degree < box.degree)
    );
    _labelBoxes.push(box);
    return true;
  }

  graph = ForceGraph()(canvas)
    .graphData({ nodes, links })
    .backgroundColor(BG_COLOR)
    .width(window.innerWidth)
    .height(window.innerHeight)
    .nodeRelSize(NODE_MIN_RADIUS)
    .nodeCanvasObjectMode((node) => {
      // Use "replace" so we control all drawing (circle + label)
      return "replace";
    })
    .nodeCanvasObject((node, ctx, globalScale) => {
      // Reset label boxes at the start of each new render frame
      const now = performance.now();
      if (now - _lastFrameTime > 1) {
        _labelBoxes = [];
        _lastFrameTime = now;
      }

      const r = node._radius || NODE_MIN_RADIUS;
      const isFocusMode = hoveredNode !== null;
      const isHighlighted = !isFocusMode || focusedNeighbors.has(node.id);
      const isHovered = hoveredNode && hoveredNode.id === node.id;
      const alpha = isFocusMode ? (isHighlighted ? 1.0 : 0.1) : 1.0;
      const nodeType = node.group || "MEETING";

      // ── Type-based colors ──
      const fillColor = node.is_ghost ? GHOST_COLOR : (NODE_COLORS[nodeType] || "#6b7280");
      const strokeColor = node.is_ghost ? "#444" : (NODE_STROKE_COLORS[nodeType] || "#9ca3af");

      // ── Hover glow effect ──
      if (isHovered) {
        ctx.save();
        ctx.shadowColor = fillColor;
        ctx.shadowBlur = 15;
        ctx.beginPath();
        ctx.arc(node.x, node.y, r * 1.15, 0, 2 * Math.PI);
        ctx.fillStyle = fillColor;
        ctx.globalAlpha = 0.35;
        ctx.fill();
        ctx.restore();
      }

      // ── Draw node circle ──
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = fillColor;
      ctx.globalAlpha = alpha;
      ctx.fill();

      // ── Stroke for definition ──
      ctx.strokeStyle = strokeColor;
      ctx.lineWidth = 0.4;
      ctx.globalAlpha = alpha * 0.5;
      ctx.stroke();

      // ── Determine if this node's label should render ──
      const isOrphan = (node.degree || 0) === 0;
      const isHighDegree = (node.degree || 0) >= LABEL_ALWAYS_SHOW_DEGREE;
      const isCompany = nodeType === "COMPANY";
      const zoomedIn = globalScale >= TEXT_THRESHOLD;

      let shouldAttemptLabel = false;
      if (isHovered) {
        shouldAttemptLabel = true;
      } else if (isFocusMode && isHighlighted) {
        shouldAttemptLabel = true; // always label neighbors of hovered node
      } else if (isOrphan) {
        shouldAttemptLabel = false;
      } else if (isCompany) {
        shouldAttemptLabel = true; // companies are cluster anchors, always labeled
      } else if (zoomedIn) {
        shouldAttemptLabel = true;
      } else if (isHighDegree) {
        shouldAttemptLabel = true;
      }

      if (shouldAttemptLabel && node.label) {
        const isCompanyLabel = isCompany && !node.is_ghost;
        const fontSize = isCompanyLabel
          ? LABEL_FONT_SIZE - 0.3
          : LABEL_FONT_SIZE + (node.degree > 3 ? 0.5 : 0);
        const fontWeight = isCompanyLabel ? "600" : "500";
        ctx.font = `${fontWeight} ${fontSize}px Inter, system-ui, sans-serif`;
        const displayLabel = isCompanyLabel ? node.label.toUpperCase() : node.label;

        // Label color: type-based when not ghost
        const labelColor = node.is_ghost
          ? LABEL_COLOR_GHOST
          : (isHovered ? LABEL_COLOR_HOVER : (NODE_LABEL_COLORS[nodeType] || "#e5e7eb"));

        if (isHovered) {
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          // Text shadow for readability
          ctx.fillStyle = "rgba(0,0,0,0.8)";
          ctx.globalAlpha = 1.0;
          ctx.fillText(displayLabel, node.x + 0.3, node.y + r + 2.3);
          ctx.fillStyle = labelColor;
          ctx.fillText(displayLabel, node.x, node.y + r + 2);
        } else {
          const box = predictLabelBox(node, ctx, fontSize);
          const allNodes = graph.graphData().nodes;
          if (box && tryRegisterLabel(box, allNodes, node.id)) {
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            const labelAlpha = alpha * (isFocusMode && isHighlighted ? 1.0 : 0.8);
            // Text shadow
            ctx.fillStyle = "rgba(0,0,0,0.7)";
            ctx.globalAlpha = labelAlpha;
            ctx.fillText(displayLabel, node.x + 0.3, node.y + r + 2.3);
            ctx.fillStyle = labelColor;
            ctx.fillText(displayLabel, node.x, node.y + r + 2);
          }
        }
      }

      ctx.globalAlpha = 1.0;
    })
    .nodePointerAreaPaint((node, color, ctx) => {
      const r = node._radius || NODE_MIN_RADIUS;
      ctx.beginPath();
      ctx.arc(node.x, node.y, r + 6, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
    })
    .linkColor((link) => {
      const label = link.label || "";
      // Base opacity when no node is hovered
      if (hoveredNode === null) {
        if (label === "WORKS_AT" || label === "FOUNDED")   return "rgba(16, 185, 129, 0.15)";
        if (label === "WORKED_AT")                         return "rgba(16, 185, 129, 0.08)";
        if (label === "INVESTED_IN")                       return "rgba(20, 184, 166, 0.12)";
        if (label === "PARTNERED_WITH")                    return "rgba(245, 158, 11, 0.12)";
        if (label === "ACQUIRED")                          return "rgba(249, 115, 22, 0.12)";
        return "rgba(255, 255, 255, 0.12)";
      }
      // Focused edge (direct neighbour of hovered node)
      if (isLinkFocused(link)) {
        if (label === "WORKS_AT")     return "rgba(52, 211, 153, 0.85)";
        if (label === "FOUNDED")      return "rgba(16, 185, 129, 0.9)";
        if (label === "WORKED_AT")    return "rgba(110, 231, 183, 0.6)";
        if (label === "INVESTED_IN")  return "rgba(45, 212, 191, 0.8)";
        if (label === "KNOWS")        return "rgba(129, 140, 248, 0.75)";
        if (label === "REPORTS_TO")   return "rgba(167, 139, 250, 0.75)";
        if (label === "RELATED_TO")   return "rgba(244, 114, 182, 0.75)";
        if (label === "PARTNERED_WITH") return "rgba(251, 191, 36, 0.8)";
        if (label === "ACQUIRED")     return "rgba(251, 146, 60, 0.8)";
        return "rgba(255, 255, 255, 0.7)";
      }
      return "rgba(255, 255, 255, 0.03)";
    })
    .linkWidth((link) => {
      if (hoveredNode !== null && isLinkFocused(link)) {
        // Thicker for stronger relationships
        const count = (link.metadata && link.metadata.interaction_count) || 1;
        return Math.min(1 + (count - 1) * 0.4, 3);
      }
      // Base width also scales subtly by interaction count
      const count = (link.metadata && link.metadata.interaction_count) || 1;
      return Math.min(0.5 + (count - 1) * 0.2, 1.5);
    })
    .linkCanvasObjectMode(() => "after")
    .linkCanvasObject((link, ctx, globalScale) => {
      // Edge labels hidden by default — only show on focused edges when hovering a node
      if (!link.label) return;
      if (hoveredNode === null) return;        // no hover = no labels
      if (!isLinkFocused(link)) return;         // only show on direct edges

      const mid = {
        x: (link.source.x + link.target.x) / 2,
        y: (link.source.y + link.target.y) / 2,
      };
      ctx.font = "500 2.2px Inter, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      // Dark shadow for readability
      ctx.fillStyle = "rgba(0,0,0,0.7)";
      ctx.fillText(link.label, mid.x + 0.2, mid.y + 0.2);
      // Label in a muted color
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      ctx.fillText(link.label, mid.x, mid.y);
    })
    .d3AlphaDecay(0.015)
    .d3VelocityDecay(0.3)
    .d3AlphaMin(0.005)
    .warmupTicks(200)
    .cooldownTicks(200)
    .onNodeClick((node) => openPeek(node.id))
    .onNodeHover((node) => {
      hoveredNode = node || null;
      buildFocusSets(node, graph.graphData());
      canvas.style.cursor = node ? "pointer" : "default";
    })
    .onNodeDrag((node) => {
      // Reheat simulation so connected nodes react to the drag
      graph.d3ReheatSimulation();
    })
    .onNodeDragEnd((node) => {
      node.fx = undefined;
      node.fy = undefined;
    })
    .onLinkClick((link) => {
      if (link.label === "KNOWS" && link.metadata) {
        showEdgeTooltip(link);
      }
    })
    .onLinkHover((link) => {
      canvas.style.cursor = link && link.label === "KNOWS" && link.metadata ? "pointer" : "default";
    });

  // ── D3 force tuning: spacious layout with type-aware distances ──
  graph.d3Force("charge").strength(-200).distanceMax(400);
  graph.d3Force("link")
    .distance((link) => {
      const label = link.label || "";
      if (label === "WORKS_AT")       return 120;  // companies orbit outside people clusters
      if (label === "WORKED_AT")      return 140;  // past employers — further out
      if (label === "FOUNDED")        return 110;  // founders close to their company
      if (label === "INVESTED_IN")    return 150;  // investors — loose connection
      if (label === "KNOWS")          return 60;   // people cluster together
      if (label === "REPORTS_TO")     return 50;   // tight management chain
      if (label === "RELATED_TO")     return 45;   // personal — very close
      if (label === "PARTNERED_WITH") return 100;  // company peers
      if (label === "ACQUIRED")       return 80;   // acquired = merged close
      return 80;
    })
    .strength(0.3);  // weaker pull so repulsion can spread nodes
  graph.d3Force("center", null);
  graph.d3Force("gravity-x", d3.forceX().strength(0.05));
  graph.d3Force("gravity-y", d3.forceY().strength(0.05));
  graph.d3Force("collide", d3.forceCollide((node) => {
    const r = node._radius || NODE_MIN_RADIUS;
    return r + 8;  // generous padding to prevent overlap
  }).strength(0.8).iterations(3));

  window.addEventListener("resize", () => {
    if (graph && currentView === "graph") {
      graph.width(window.innerWidth).height(window.innerHeight);
    }
  });
}

// ── Edge Tooltip (KNOWS interaction history) ─────────────
let edgeTooltipEl = null;

function showEdgeTooltip(link) {
  const meta = link.metadata || {};
  const meetings = meta.meetings || [];
  const srcLabel = typeof link.source === "object" ? link.source.label : link.source;
  const tgtLabel = typeof link.target === "object" ? link.target.label : link.target;

  if (!edgeTooltipEl) {
    edgeTooltipEl = document.createElement("div");
    edgeTooltipEl.id = "edge-tooltip";
    edgeTooltipEl.className = "edge-tooltip";
    document.body.appendChild(edgeTooltipEl);

    // Close on outside click
    document.addEventListener("click", (e) => {
      if (edgeTooltipEl && !edgeTooltipEl.contains(e.target)) {
        hideEdgeTooltip();
      }
    });
  }

  const count = meta.interaction_count || meetings.length || 0;
  const firstMet = meta.first_met || "";
  const lastInteraction = meta.last_interaction || "";

  let html = `<div class="edge-tooltip-header">${escapeHtml(srcLabel)} \u2194 ${escapeHtml(tgtLabel)}</div>`;
  html += `<div class="edge-tooltip-meta">${count} interaction${count !== 1 ? "s" : ""}`;
  if (firstMet) html += ` \u00b7 First met: ${escapeHtml(firstMet)}`;
  if (lastInteraction && lastInteraction !== firstMet) html += ` \u00b7 Last: ${escapeHtml(lastInteraction)}`;
  html += `</div>`;

  if (meetings.length > 0) {
    html += `<ul class="edge-tooltip-meetings">`;
    // Show most recent first (up to 5)
    const sorted = [...meetings].sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    for (const m of sorted.slice(0, 5)) {
      const dateStr = m.date ? `<span class="edge-tooltip-date">${escapeHtml(m.date)}</span>` : "";
      html += `<li>${dateStr} ${escapeHtml(m.title || "Untitled")}`;
      if (m.context) html += `<br><span class="edge-tooltip-context">${escapeHtml(m.context)}</span>`;
      html += `</li>`;
    }
    if (sorted.length > 5) {
      html += `<li class="edge-tooltip-more">+${sorted.length - 5} more</li>`;
    }
    html += `</ul>`;
  }

  edgeTooltipEl.innerHTML = html;
  edgeTooltipEl.classList.remove("hidden");

  // Position at center of screen
  edgeTooltipEl.style.left = "50%";
  edgeTooltipEl.style.top = "50%";
  edgeTooltipEl.style.transform = "translate(-50%, -50%)";
}

function hideEdgeTooltip() {
  if (edgeTooltipEl) {
    edgeTooltipEl.classList.add("hidden");
  }
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

  // Store summary and interaction data for the summary tab
  currentNodeSummary = node.summary || null;
  currentNodeType = node.node_type || "PERSON";
  currentNodeInteractions = node.interactions || [];

  // Default to Summary tab if summary or interactions exist, Notes if not
  const hasSummary = (currentNodeSummary && Object.keys(currentNodeSummary).length > 0) || currentNodeInteractions.length > 0;
  switchPeekTab(hasSummary ? "summary" : "notes");

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

  // Reset summary state for create mode
  currentNodeSummary = null;
  currentNodeType = "PERSON";
  currentNodeInteractions = [];
  switchPeekTab("notes");

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
// FAB is now handled by fab.js — removed old fab.addEventListener("click", openPeekCreate);

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
    } else if (typeof closeMeetingPanel === "function" && !document.getElementById("meeting-backdrop").classList.contains("hidden")) {
      closeMeetingPanel();
    } else if (typeof closeAddNotesPanel === "function" && !document.getElementById("addnotes-backdrop").classList.contains("hidden")) {
      closeAddNotesPanel();
    } else if (typeof closeNotifPanel === "function" && document.getElementById("notif-panel").classList.contains("notif-open")) {
      closeNotifPanel();
    } else if (!settingsBackdrop.classList.contains("hidden")) {
      closeSettings();
    } else if (!peekBackdrop.classList.contains("hidden")) {
      closePeek();
    } else if (typeof collapseFab === "function") {
      collapseFab();
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

  // Start from all nodes, filtering by visible types and ghosts
  let pool = originalGraphData.nodes.filter((n) => visibleTypes.has(n.group || "UNKNOWN"));
  if (!showGhosts) {
    pool = pool.filter((n) => !n.is_ghost);
  }
  const poolIds = new Set(pool.map((n) => n.id));

  // No query → show full graph (minus ghosts if hidden)
  if (!query) {
    const links = originalGraphData.links
      .filter((l) => {
        const s = typeof l.source === "object" ? l.source.id : l.source;
        const t = typeof l.target === "object" ? l.target.id : l.target;
        return poolIds.has(s) && poolIds.has(t);
      })
      .map((l) => ({ ...l }));
    graph.graphData({ nodes: pool.map((n) => ({ ...n })), links });
    return;
  }

  // Hop 0: seed nodes matching the query
  const includedNodeIds = new Set();
  for (const node of pool) {
    if (node.label.toLowerCase().includes(query)) {
      includedNodeIds.add(node.id);
    }
  }

  // Hop 1..N: expand by traversing links
  for (let hop = 0; hop < hops; hop++) {
    const nextLevelIds = new Set();
    for (const link of originalGraphData.links) {
      const srcId = typeof link.source === "object" ? link.source.id : link.source;
      const tgtId = typeof link.target === "object" ? link.target.id : link.target;

      if (includedNodeIds.has(srcId) && !includedNodeIds.has(tgtId) && poolIds.has(tgtId)) {
        nextLevelIds.add(tgtId);
      }
      if (includedNodeIds.has(tgtId) && !includedNodeIds.has(srcId) && poolIds.has(srcId)) {
        nextLevelIds.add(srcId);
      }
    }
    for (const id of nextLevelIds) {
      includedNodeIds.add(id);
    }
  }

  // Filter nodes and links where both endpoints are included
  const filteredNodes = pool
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
      metadata: l.metadata || {},
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

ghostToggle.addEventListener("click", () => {
  showGhosts = !showGhosts;
  ghostToggle.classList.toggle("text-white/60", !showGhosts);
  ghostToggle.classList.toggle("line-through", !showGhosts);
  ghostToggle.classList.toggle("text-white/35", showGhosts);
  applySearchFilter();
  if (currentView === "table") renderTable();
});

// ── Peek Modal: Notes/Summary Tab Toggle ────────────────
const peekTabNotes = document.getElementById("peek-tab-notes");
const peekTabSummary = document.getElementById("peek-tab-summary");
const notesSubtabs = document.getElementById("notes-subtabs");
const peekNotesSection = document.getElementById("peek-notes-section");
const peekSummarySection = document.getElementById("peek-summary-section");
const peekSummaryContent = document.getElementById("peek-summary-content");
const peekSummaryEmpty = document.getElementById("peek-summary-empty");

function switchPeekTab(tab) {
  currentPeekTab = tab;
  if (tab === "notes") {
    peekNotesSection.classList.remove("hidden");
    peekSummarySection.classList.add("hidden");
    notesSubtabs.classList.remove("hidden");
    peekTabNotes.classList.add("bg-white/10", "text-white/70");
    peekTabNotes.classList.remove("bg-transparent", "text-white/30");
    peekTabSummary.classList.add("bg-transparent", "text-white/30");
    peekTabSummary.classList.remove("bg-white/10", "text-white/70");
  } else {
    peekNotesSection.classList.add("hidden");
    peekSummarySection.classList.remove("hidden");
    notesSubtabs.classList.add("hidden");
    peekTabSummary.classList.add("bg-white/10", "text-white/70");
    peekTabSummary.classList.remove("bg-transparent", "text-white/30");
    peekTabNotes.classList.add("bg-transparent", "text-white/30");
    peekTabNotes.classList.remove("bg-white/10", "text-white/70");
    renderSummary();
  }
}

function renderSummary() {
  const hasSummary = currentNodeSummary && Object.keys(currentNodeSummary).length > 0;
  const hasInteractions = currentNodeInteractions && currentNodeInteractions.length > 0;

  if (!hasSummary && !hasInteractions) {
    peekSummaryContent.classList.add("hidden");
    peekSummaryEmpty.classList.remove("hidden");
    return;
  }
  peekSummaryContent.classList.remove("hidden");
  peekSummaryEmpty.classList.add("hidden");

  const s = currentNodeSummary || {};
  let html = "";

  if (currentNodeType === "PERSON") {
    if (s.role) html += summaryField("Role", escapeHtml(s.role));
    if (s.how_we_know_each_other) html += summaryField("How we know each other", escapeHtml(s.how_we_know_each_other));
    if (s.key_context && s.key_context.length) html += summaryField("Key context", bulletList(s.key_context));
    if (s.follow_ups_involving_them && s.follow_ups_involving_them.length) html += summaryField("Open follow-ups", bulletList(s.follow_ups_involving_them));
    if (s.last_interaction) html += summaryField("Last interaction", escapeHtml(s.last_interaction));
    if (s.interaction_count != null) html += summaryField("Total interactions", String(s.interaction_count));

    // Interaction timeline from KNOWS edges
    if (hasInteractions) {
      html += renderInteractionTimeline(currentNodeInteractions);
    }
  } else if (currentNodeType === "COMPANY") {
    if (s.relationship_health) {
      const cls = s.relationship_health === "strong" ? "health-strong" : s.relationship_health === "moderate" ? "health-moderate" : "health-weak";
      html += summaryField("Relationship health", `<span class="summary-health-badge ${cls}">${escapeHtml(s.relationship_health)}</span>`);
    }
    if (s.total_contacts != null) html += summaryField("Total contacts", String(s.total_contacts));
    if (s.key_context && s.key_context.length) html += summaryField("Key context", bulletList(s.key_context));
    if (s.open_follow_ups && s.open_follow_ups.length) html += summaryField("Open follow-ups", bulletList(s.open_follow_ups));
    if (s.last_interaction) html += summaryField("Last interaction", escapeHtml(s.last_interaction));
  } else if (currentNodeType === "MEETING") {
    if (s.one_liner) html += `<p class="text-white/80 font-medium mb-3">${escapeHtml(s.one_liner)}</p>`;
    if (s.key_points && s.key_points.length) html += summaryField("Key points", bulletList(s.key_points));
    if (s.decisions && s.decisions.length) html += summaryField("Decisions", bulletList(s.decisions));
    if (s.follow_ups && s.follow_ups.length) html += summaryField("Follow-ups", bulletList(s.follow_ups));
  }

  peekSummaryContent.innerHTML = html || '<p class="text-white/20 text-sm">No summary data available.</p>';
}

function renderInteractionTimeline(interactions) {
  const INITIAL_SHOW = 5;
  const total = interactions.length;
  const visible = interactions.slice(0, INITIAL_SHOW);

  let html = `<div class="summary-field"><div class="summary-field-label">Recent Interactions</div><div class="summary-field-value">`;
  html += `<div class="interaction-timeline">`;
  for (const m of visible) {
    const dateStr = m.date ? `<span class="interaction-date">${escapeHtml(m.date)}</span>` : "";
    html += `<div class="interaction-item">`;
    html += `${dateStr}<span class="interaction-title">${escapeHtml(m.title || "Untitled")}</span>`;
    if (m.context) html += `<div class="interaction-context">${escapeHtml(m.context)}</div>`;
    html += `</div>`;
  }
  if (total > INITIAL_SHOW) {
    html += `<div class="interaction-more">Show all ${total} interactions</div>`;
  }
  html += `</div></div></div>`;
  return html;
}

function summaryField(label, value) {
  return `<div class="summary-field"><div class="summary-field-label">${label}</div><div class="summary-field-value">${value}</div></div>`;
}

function bulletList(items) {
  return "<ul>" + items.map((i) => `<li>${escapeHtml(i)}</li>`).join("") + "</ul>";
}

peekTabNotes.addEventListener("click", () => switchPeekTab("notes"));
peekTabSummary.addEventListener("click", () => switchPeekTab("summary"));

// ── Folder Import ───────────────────────────────────────
function showImportToast(msg) {
  showToast(msg, "info");
}

function hideImportToast() {
  // No-op — toasts auto-dismiss now
}

importBtn.addEventListener("click", () => {
  // Create a fresh file input each time — most reliable cross-browser approach
  const inp = document.createElement("input");
  inp.type = "file";
  inp.setAttribute("webkitdirectory", "");
  inp.setAttribute("directory", "");
  inp.multiple = true;
  inp.style.display = "none";
  document.body.appendChild(inp);

  inp.addEventListener("change", async () => {
    const files = inp.files;
    document.body.removeChild(inp);
    if (!files || files.length === 0) return;

    // Filter to only .md files
    const mdFiles = Array.from(files).filter((f) =>
      f.name.toLowerCase().endsWith(".md")
    );

    if (mdFiles.length === 0) {
      showImportToast("No .md files found in folder");
      setTimeout(hideImportToast, 3000);
      return;
    }

    showImportToast(`Processing ${mdFiles.length} file${mdFiles.length > 1 ? "s" : ""}...`);

    const formData = new FormData();
    for (const file of mdFiles) {
      formData.append("files", file, file.webkitRelativePath || file.name);
    }

    try {
      const res = await fetch(API.import, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json();
        showImportToast(err.error || "Import failed");
        setTimeout(hideImportToast, 4000);
        return;
      }

      const result = await res.json();
      showImportToast(
        `Imported ${result.total} node${result.total !== 1 ? "s" : ""} (${result.created} new, ${result.updated} updated)`
      );
      setTimeout(hideImportToast, 4000);

      await loadGraph();
    } catch (e) {
      showImportToast("Import failed: network error");
      setTimeout(hideImportToast, 4000);
    }
  });

  inp.click();
});

// ── Init ───────────────────────────────────────────────
loadGraph();
