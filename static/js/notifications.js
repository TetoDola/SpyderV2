/**
 * Notification panel — bell icon, slide-out panel, status cards, polling, resolution UI
 * Depends on: graph.js (API, showToast, escapeHtml, formatDate)
 */

// ── DOM refs ──────────────────────────────────────────
const notifBell = document.getElementById("notif-bell");
const notifBadge = document.getElementById("notif-badge");
const notifOverlay = document.getElementById("notif-overlay");
const notifPanel = document.getElementById("notif-panel");
const notifClose = document.getElementById("notif-close");
const notifList = document.getElementById("notif-list");
const notifEmpty = document.getElementById("notif-empty");
const notifLoadMore = document.getElementById("notif-load-more");
const notifLoadMoreBtn = document.getElementById("notif-load-more-btn");

// ── State ─────────────────────────────────────────────
let notifFilter = "all";     // "all" | "active" | "done"
let notifItems = [];
let notifPage = 1;
let notifTotal = 0;
let notifBadgeCount = 0;
let notifPollingInterval = null;
let notifPanelOpen = false;
let expandedCardId = null;
let notifInitialLoadDone = false;

// ── Pipeline step mapping ─────────────────────────────
const STEP_MAP = {
  PENDING: { idx: 0, label: "Queued" },
  TRANSCRIBING: { idx: 1, label: "Transcribing" },
  EXTRACTING: { idx: 2, label: "Extracting entities" },
  RESOLVING: { idx: 3, label: "Resolving" },
  WRITING: { idx: 4, label: "Writing to graph" },
  SUMMARIZING: { idx: 5, label: "Summarizing" },
};
const TOTAL_STEPS = 6;

// ── Open/Close panel ──────────────────────────────────
function openNotifPanel() {
  notifPanelOpen = true;
  notifPanel.classList.add("notif-open");
  notifOverlay.classList.remove("hidden");
  refreshNotifications();
  startPolling();
}

function closeNotifPanel() {
  notifPanelOpen = false;
  notifPanel.classList.remove("notif-open");
  notifOverlay.classList.add("hidden");
  // Keep polling if badge > 0
  if (notifBadgeCount <= 0) stopPolling();
}

notifBell.addEventListener("click", () => {
  if (notifPanelOpen) closeNotifPanel();
  else openNotifPanel();
});
notifClose.addEventListener("click", closeNotifPanel);
notifOverlay.addEventListener("click", closeNotifPanel);

// ── Filter tabs ───────────────────────────────────────
document.querySelectorAll(".notif-filter-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    notifFilter = tab.dataset.filter;
    document.querySelectorAll(".notif-filter-tab").forEach((t) => {
      if (t.dataset.filter === notifFilter) {
        t.classList.add("bg-white/10", "text-white/70");
        t.classList.remove("bg-transparent", "text-white/30");
      } else {
        t.classList.remove("bg-white/10", "text-white/70");
        t.classList.add("bg-transparent", "text-white/30");
      }
    });
    notifPage = 1;
    notifItems = [];
    refreshNotifications();
  });
});

// ── Fetch & render ────────────────────────────────────
async function refreshNotifications() {
  let url = `${API.ingestions}?page=${notifPage}&page_size=20`;
  if (notifFilter === "active") url += "&status=active";
  else if (notifFilter === "done") url += "&status=complete";

  // On first load, limit to last 48 hours to avoid flooding with stale items
  if (!notifInitialLoadDone && notifPage === 1) {
    const since = new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString();
    url += `&updated_since=${encodeURIComponent(since)}`;
  }

  try {
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();

    if (notifPage === 1) {
      notifItems = data.results;
    } else {
      // Merge avoiding duplicates
      const existingIds = new Set(notifItems.map((i) => i.id));
      for (const item of data.results) {
        if (!existingIds.has(item.id)) notifItems.push(item);
      }
    }

    notifTotal = data.total;
    notifBadgeCount = data.badge_count;
    notifInitialLoadDone = true;
    updateBadge();
    renderCards();

    // Show/hide load more
    if (notifItems.length < notifTotal) {
      notifLoadMore.classList.remove("hidden");
    } else {
      notifLoadMore.classList.add("hidden");
    }

    // Auto-start polling if there are active items
    if (notifBadgeCount > 0) startPolling();
    else if (!notifPanelOpen) stopPolling();
  } catch {
    // Silently fail on poll errors
  }
}

function updateBadge() {
  if (notifBadgeCount > 0) {
    notifBadge.textContent = notifBadgeCount > 99 ? "99+" : String(notifBadgeCount);
    notifBadge.classList.remove("hidden");
  } else {
    notifBadge.classList.add("hidden");
  }
}

// ── Load more ─────────────────────────────────────────
notifLoadMoreBtn.addEventListener("click", () => {
  notifPage++;
  refreshNotifications();
});

// ── Polling ───────────────────────────────────────────
function startPolling() {
  if (notifPollingInterval) return;
  notifPollingInterval = setInterval(refreshNotifications, 5000);
}

function stopPolling() {
  if (notifPollingInterval) {
    clearInterval(notifPollingInterval);
    notifPollingInterval = null;
  }
}

// ── Render cards ──────────────────────────────────────
function renderCards() {
  notifList.innerHTML = "";

  if (notifItems.length === 0) {
    notifEmpty.classList.remove("hidden");
    notifList.classList.add("hidden");
    return;
  }

  notifEmpty.classList.add("hidden");
  notifList.classList.remove("hidden");

  // Group by date
  const groups = {};
  for (const item of notifItems) {
    const date = new Date(item.created_at);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    let label;
    if (date.toDateString() === today.toDateString()) label = "Today";
    else if (date.toDateString() === yesterday.toDateString()) label = "Yesterday";
    else label = date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

    if (!groups[label]) groups[label] = [];
    groups[label].push(item);
  }

  for (const [label, items] of Object.entries(groups)) {
    // Date header
    const header = document.createElement("div");
    header.className = "text-[10px] font-semibold text-white/20 uppercase tracking-widest px-1 py-2 mt-2 first:mt-0";
    header.textContent = label;
    notifList.appendChild(header);

    for (const item of items) {
      notifList.appendChild(createCard(item));
    }
  }
}

function getCardState(item) {
  const activeStatuses = ["PENDING", "TRANSCRIBING", "EXTRACTING", "RESOLVING", "WRITING", "SUMMARIZING"];
  if (activeStatuses.includes(item.status)) return "progress";
  if (item.status === "COMPLETE" && item.pending_resolutions && item.pending_resolutions.length > 0) return "review";
  if (item.status === "COMPLETE" || item.status === "DISMISSED") return "complete";
  if (item.status === "FAILED") return "failed";
  return "progress";
}

function createCard(item) {
  const state = getCardState(item);
  const card = document.createElement("div");
  card.className = `notif-card status-${state}`;
  card.dataset.id = item.id;

  const timeAgo = getTimeAgo(item.created_at);
  const icon = { progress: "\u25cf", review: "\u26a0", complete: "\u2713", failed: "\u2717" }[state];

  let content = "";

  if (state === "progress") {
    const step = STEP_MAP[item.status] || { idx: 0, label: item.status };
    const progress = Math.round(((step.idx + 1) / TOTAL_STEPS) * 100);
    content = `
      <div class="notif-card-title">${icon} ${escapeHtml(item.title)}</div>
      <div class="notif-card-subtitle">Step ${step.idx + 1}/${TOTAL_STEPS}: ${step.label}</div>
      <div class="notif-progress"><div class="notif-progress-bar" style="width: ${progress}%"></div></div>
      <div class="notif-card-subtitle mt-1">${timeAgo}</div>
    `;
  } else if (state === "review") {
    const count = item.pending_resolutions ? item.pending_resolutions.length : 0;
    content = `
      <div class="notif-card-title">${icon} ${escapeHtml(item.title)}</div>
      <div class="notif-card-subtitle">${count} entit${count === 1 ? "y" : "ies"} need${count === 1 ? "s" : ""} review</div>
      <div class="notif-card-subtitle">${timeAgo}</div>
      <div class="notif-card-actions">
        <button class="notif-btn-primary" data-action="review" data-id="${item.id}">Review</button>
      </div>
    `;
  } else if (state === "complete") {
    content = `
      <div class="notif-card-title">${icon} ${escapeHtml(item.title)}</div>
      <div class="notif-card-subtitle">Complete</div>
      <div class="notif-card-subtitle">${timeAgo}</div>
      <div class="notif-card-actions">
        <button class="notif-btn-ghost" data-action="expand" data-id="${item.id}">View details</button>
        <button class="notif-btn-ghost" data-action="viewgraph" data-id="${item.id}">View in Graph</button>
      </div>
    `;
  } else if (state === "failed") {
    content = `
      <div class="notif-card-title">${icon} ${escapeHtml(item.title)}</div>
      <div class="notif-card-subtitle">Failed at: ${item.failed_step || "Unknown"}</div>
      ${item.error_message ? `<div class="notif-card-subtitle" style="color: rgba(248,113,113,0.6)">${escapeHtml(item.error_message.slice(0, 100))}</div>` : ""}
      <div class="notif-card-subtitle">${timeAgo}</div>
      <div class="notif-card-actions">
        <button class="notif-btn-primary" data-action="retry" data-id="${item.id}">Retry</button>
        <button class="notif-btn-danger" data-action="dismiss" data-id="${item.id}">Dismiss</button>
      </div>
    `;
  }

  card.innerHTML = content;

  // Expanded review/details section
  if (expandedCardId === item.id) {
    if (state === "review") {
      const resSection = createResolutionSection(item);
      card.appendChild(resSection);
    } else if (state === "complete") {
      loadReviewExpansion(card, item.id);
    }
  }

  // Wire up action buttons
  card.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      handleCardAction(btn.dataset.action, btn.dataset.id, item);
    });
  });

  return card;
}

// ── Card actions ──────────────────────────────────────
async function handleCardAction(action, id, item) {
  if (action === "retry") {
    const res = await fetch(API.ingestionRetry(id), { method: "POST" });
    if (res.ok) {
      showToast("Retrying...", "info");
      refreshNotifications();
    }
  } else if (action === "dismiss") {
    const res = await fetch(API.ingestionDismiss(id), { method: "POST" });
    if (res.ok) {
      refreshNotifications();
    }
  } else if (action === "expand") {
    expandedCardId = expandedCardId === id ? null : id;
    renderCards();
  } else if (action === "review") {
    expandedCardId = expandedCardId === id ? null : id;
    renderCards();
  } else if (action === "viewgraph") {
    closeNotifPanel();
    // Reload graph to pick up new nodes
    if (typeof loadGraph === "function") loadGraph();
  }
}

// ── Review expansion (completed cards) ────────────────
async function loadReviewExpansion(cardEl, ingestionId) {
  const section = document.createElement("div");
  section.className = "notif-review-expand";
  section.innerHTML = '<div class="text-white/20 text-[11px]">Loading...</div>';
  cardEl.appendChild(section);

  try {
    const res = await fetch(API.ingestionReview(ingestionId));
    if (!res.ok) {
      section.innerHTML = '<div class="text-red-400/50 text-[11px]">Failed to load details</div>';
      return;
    }
    const data = await res.json();
    const results = data.results || {};

    let html = "";

    if (results.nodes_created && results.nodes_created.length) {
      html += '<div class="review-section">';
      html += '<div class="review-section-title">Created</div>';
      for (const n of results.nodes_created) {
        const icon = n.type === "PERSON" ? "\ud83d\udc64" : n.type === "COMPANY" ? "\ud83c\udfe2" : "\ud83d\udcc5";
        html += `<div class="review-item">${icon} ${escapeHtml(n.name)}${n.is_ghost ? " (ghost)" : ""}</div>`;
      }
      html += "</div>";
    }

    if (results.nodes_updated && results.nodes_updated.length) {
      html += '<div class="review-section">';
      html += '<div class="review-section-title">Updated</div>';
      for (const n of results.nodes_updated) {
        const icon = n.type === "PERSON" ? "\ud83d\udc64" : n.type === "COMPANY" ? "\ud83c\udfe2" : "\ud83d\udcc5";
        const changes = n.changes && n.changes.length ? ` (+${n.changes.join(", ")})` : "";
        html += `<div class="review-item">${icon} ${escapeHtml(n.name)}${changes}</div>`;
      }
      html += "</div>";
    }

    if (results.connections_created && results.connections_created.length) {
      html += '<div class="review-section">';
      html += '<div class="review-section-title">Connections</div>';
      for (const c of results.connections_created) {
        html += `<div class="review-item">${escapeHtml(c.from)} \u2014${escapeHtml(c.label)}\u2192 ${escapeHtml(c.to)}</div>`;
      }
      html += "</div>";
    }

    if (!html) html = '<div class="text-white/20 text-[11px]">No changes recorded.</div>';
    section.innerHTML = html;
  } catch {
    section.innerHTML = '<div class="text-red-400/50 text-[11px]">Failed to load details</div>';
  }
}

// ── Resolution UI (inline in notification cards) ──────
function createResolutionSection(item) {
  const section = document.createElement("div");
  section.className = "notif-review-expand";

  if (!item.pending_resolutions || item.pending_resolutions.length === 0) {
    section.innerHTML = '<div class="text-white/20 text-[11px]">No entities to resolve.</div>';
    return section;
  }

  for (const candidate of item.pending_resolutions) {
    const resCard = document.createElement("div");
    resCard.className = "resolution-card";

    let matchHtml = "";
    if (candidate.candidate_node) {
      const cn = candidate.candidate_node;
      const title = cn.properties?.Title || "";
      const company = cn.properties?.["Company Name"] || cn.properties?.Company || "";
      const subtitle = [title, company].filter(Boolean).join(" at ");
      matchHtml = `
        <div class="text-[11px] text-white/35 mb-1">Best match (${Math.round(candidate.confidence * 100)}% confidence):</div>
        <div class="resolution-match">
          <div class="text-[13px] text-white/70 font-medium">\ud83d\udc64 ${escapeHtml(cn.name)}</div>
          ${subtitle ? `<div class="text-[11px] text-white/35">${escapeHtml(subtitle)}</div>` : ""}
        </div>
      `;
    } else {
      matchHtml = `<div class="text-[11px] text-white/35 mb-2">No close match found.</div>`;
    }

    resCard.innerHTML = `
      <div class="resolution-question">Who is "${escapeHtml(candidate.extracted_name)}"?</div>
      ${matchHtml}
      <div class="resolution-actions">
        ${candidate.candidate_node ? `
          <button class="notif-btn-primary" data-res-action="confirm" data-res-id="${candidate.id}"
                  data-target-id="${candidate.candidate_node.id}">Yes, same person</button>
          <button class="notif-btn-ghost" data-res-action="reject" data-res-id="${candidate.id}">No, different</button>
        ` : ""}
        <button class="notif-btn-ghost" data-res-action="create_new" data-res-id="${candidate.id}">Create as new</button>
      </div>
    `;

    // Wire resolution action buttons
    resCard.querySelectorAll("[data-res-action]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const resAction = btn.dataset.resAction;
        const resId = btn.dataset.resId;
        const targetId = btn.dataset.targetId || "";

        // Disable all buttons in this card
        resCard.querySelectorAll("button").forEach((b) => { b.disabled = true; b.style.opacity = "0.4"; });

        try {
          const body = { action: resAction };
          if (resAction === "confirm" && targetId) body.target_node_id = targetId;

          const res = await fetch(API.resolutionResolve(resId), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });

          if (res.ok) {
            // Remove this resolution card visually
            resCard.style.opacity = "0.3";
            setTimeout(() => {
              resCard.remove();
              refreshNotifications();
            }, 300);
          } else {
            const err = await res.json();
            showToast(err.error || "Resolution failed", "error");
            resCard.querySelectorAll("button").forEach((b) => { b.disabled = false; b.style.opacity = "1"; });
          }
        } catch {
          showToast("Network error during resolution", "error");
          resCard.querySelectorAll("button").forEach((b) => { b.disabled = false; b.style.opacity = "1"; });
        }
      });
    });

    section.appendChild(resCard);
  }

  return section;
}

// ── Time ago helper ───────────────────────────────────
function getTimeAgo(isoDate) {
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  const diff = Math.floor((now - then) / 1000);

  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Initial load: check badge on page load ────────────
(async function initNotifications() {
  try {
    const res = await fetch(`${API.ingestions}?page=1&page_size=1`);
    if (!res.ok) return;
    const data = await res.json();
    notifBadgeCount = data.badge_count || 0;
    updateBadge();
    if (notifBadgeCount > 0) startPolling();
  } catch {
    // Silently fail
  }
})();
