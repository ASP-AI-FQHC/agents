/* Progressive enhancement for the dashboard.
 *
 * Every interaction here also works without JavaScript: the filter form is a
 * plain GET, sort headers are links, review decisions are form posts, and the
 * refresh button is the only JS-only affordance (the CLI does the same job).
 * This file only removes full-page reloads.
 */

(function () {
  "use strict";

  // -- Live filtering -------------------------------------------------------

  const form = document.getElementById("filter-form");
  const tableContainer = document.getElementById("table-container");
  const resultCount = document.getElementById("result-count");

  function currentQuery(overrides) {
    const params = new URLSearchParams(new FormData(form));
    // Drop empty values so the URL stays readable and shareable.
    for (const [key, value] of Array.from(params.entries())) {
      if (value === "") params.delete(key);
    }
    Object.entries(overrides || {}).forEach(([key, value]) => {
      params.set(key, value);
    });
    return params;
  }

  async function loadTable(overrides) {
    if (!form || !tableContainer) return;
    const params = currentQuery(overrides);
    tableContainer.setAttribute("aria-busy", "true");

    try {
      const response = await fetch("/table?" + params.toString(), {
        headers: { "X-Requested-With": "fetch" },
      });
      if (!response.ok) throw new Error("Request failed: " + response.status);
      tableContainer.innerHTML = await response.text();

      // Keep the address bar, export links and history in step with the view.
      const query = params.toString();
      window.history.replaceState({}, "", query ? "/?" + query : "/");
      updateExportLinks(query);
      updateResultCount();
    } catch (error) {
      // Fall back to a normal navigation rather than leaving a stale table.
      window.location.search = params.toString();
    } finally {
      tableContainer.removeAttribute("aria-busy");
    }
  }

  function updateExportLinks(query) {
    const csv = document.getElementById("export-csv");
    const xlsx = document.getElementById("export-xlsx");
    if (csv) csv.href = "/export.csv" + (query ? "?" + query : "");
    if (xlsx) xlsx.href = "/export.xlsx" + (query ? "?" + query : "");
  }

  function updateResultCount() {
    const rendered = tableContainer.querySelector("[data-total]");
    if (rendered && resultCount) {
      resultCount.textContent = rendered.getAttribute("data-total");
    }
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      loadTable({ page: 1 });
    });

    // Debounced search-as-you-type; selects and numbers apply immediately.
    let timer = null;
    form.addEventListener("input", function (event) {
      if (event.target.type === "search") {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => loadTable({ page: 1 }), 250);
      }
    });
    form.addEventListener("change", function (event) {
      if (event.target.type !== "search") loadTable({ page: 1 });
    });
  }

  // Sort headers and pagination inside the fragment.
  if (tableContainer) {
    tableContainer.addEventListener("click", function (event) {
      const link = event.target.closest("a[data-sort], a[data-page]");
      if (!link) return;
      event.preventDefault();

      if (link.dataset.sort) {
        const sortInput = document.getElementById("sort");
        const directionInput = document.getElementById("direction");
        if (sortInput) sortInput.value = link.dataset.sort;
        if (directionInput) directionInput.value = link.dataset.direction;
        loadTable({ page: 1 });
      } else {
        loadTable({ page: link.dataset.page });
      }
    });
  }

  // -- Refresh --------------------------------------------------------------

  const refreshButton = document.getElementById("refresh-button");
  const panel = document.getElementById("refresh-panel");
  const stageLabel = document.getElementById("refresh-stage");
  const progressFill = document.getElementById("refresh-progress");
  const log = document.getElementById("refresh-log");
  let pollTimer = null;

  function renderRefresh(state) {
    if (!panel) return;
    panel.hidden = false;
    if (stageLabel) {
      stageLabel.textContent = state.running
        ? state.stage || "Working…"
        : state.error
        ? "Refresh failed"
        : "Refresh complete";
    }
    if (progressFill) progressFill.style.width = state.percent + "%";

    if (log) {
      const entries = (state.messages || []).slice(-12);
      log.innerHTML = "";
      entries.forEach(function (message) {
        const item = document.createElement("li");
        item.textContent = message;
        log.appendChild(item);
      });
      (state.warnings || []).forEach(function (warning) {
        const item = document.createElement("li");
        item.textContent = "⚠ " + warning;
        item.style.color = "var(--allstar-orange)";
        log.appendChild(item);
      });
      if (state.error) {
        const item = document.createElement("li");
        item.textContent = "⚠ " + state.error;
        item.style.color = "var(--allstar-pink)";
        log.appendChild(item);
      }
    }

    if (!state.running) {
      window.clearInterval(pollTimer);
      pollTimer = null;
      if (refreshButton) {
        refreshButton.disabled = false;
        refreshButton.textContent = "Refresh data";
      }
      // Pull in whatever the run produced.
      loadTable({});
    }
  }

  async function pollRefresh() {
    try {
      const response = await fetch("/refresh/status");
      renderRefresh(await response.json());
    } catch (error) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  if (refreshButton) {
    refreshButton.addEventListener("click", async function () {
      refreshButton.disabled = true;
      refreshButton.textContent = "Refreshing…";
      try {
        const response = await fetch("/refresh", { method: "POST" });
        const state = await response.json();
        renderRefresh(state);
        if (!pollTimer) pollTimer = window.setInterval(pollRefresh, 1500);
      } catch (error) {
        refreshButton.disabled = false;
        refreshButton.textContent = "Refresh data";
      }
    });
  }

  // -- Review decisions -----------------------------------------------------

  document.addEventListener("submit", async function (event) {
    const form = event.target.closest(".decision-form");
    if (!form) return;
    event.preventDefault();

    const card = form.closest(".review-card");
    const buttons = card ? card.querySelectorAll("button") : [];
    buttons.forEach((button) => (button.disabled = true));

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "fetch" },
      });
      if (!response.ok) throw new Error("Request failed");
      const html = await response.text();
      if (card) card.innerHTML = html;
      updateReviewBadge();
    } catch (error) {
      buttons.forEach((button) => (button.disabled = false));
      form.submit(); // fall back to a full page post
    }
  });

  function updateReviewBadge() {
    const badge = document.querySelector(".nav__count");
    if (!badge) return;
    const remaining = Math.max(parseInt(badge.textContent, 10) - 1, 0);
    if (remaining === 0) badge.remove();
    else badge.textContent = String(remaining);
  }
})();

/* --------------------------------------------------------------------------
   Profile contents list
   --------------------------------------------------------------------------
   Highlights the section currently in view. Pure enhancement: without
   JavaScript the links are ordinary in-page anchors and every section is
   already on the page, so nothing is lost.
   -------------------------------------------------------------------------- */
(() => {
  const links = Array.from(document.querySelectorAll("[data-profile-nav]"));
  if (!links.length || !("IntersectionObserver" in window)) return;

  const byId = new Map(links.map((link) => [link.dataset.profileNav, link]));
  const sections = links
    .map((link) => document.getElementById(link.dataset.profileNav))
    .filter(Boolean);
  if (!sections.length) return;

  // Track which sections are on screen rather than reacting to whichever one
  // fired last: scrolling fast can deliver entries out of order, and the
  // topmost visible section is the one a reader considers current.
  const visible = new Set();

  const highlight = () => {
    const current = sections.find((section) => visible.has(section.id));
    links.forEach((link) => link.classList.remove("profile-nav__link--active"));
    if (!current) return;
    const link = byId.get(current.id);
    if (link) link.classList.add("profile-nav__link--active");
  };

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      });
      highlight();
    },
    // A section counts as current once its heading has cleared the top of the
    // viewport and until it has left through the bottom third.
    { rootMargin: "-80px 0px -66% 0px", threshold: 0 }
  );

  sections.forEach((section) => observer.observe(section));
})();
