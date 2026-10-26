// ---------------------------------------------------------------- config
// Default to the page's OWN origin, not a hardcoded hostname: the frontend is served by the same
// FastAPI app the API lives on (main.py mounts it at "/"), so whatever host/port/protocol the
// browser actually used to load this page is always correct and always same-origin. A hardcoded
// "http://localhost:8000" silently diverges the moment the page is reached any other way — e.g.
// start.bat opens the browser at http://127.0.0.1:8000, a different origin from "localhost" as far
// as the browser's own connection/keepalive handling is concerned, even though both route to the
// same loopback server. That mismatch was traced to heartbeat POSTs never registering at all
// (autoshutdown's "ever_connected" stayed false for the whole session) while ordinary step calls
// still worked — consistent with keepalive fetches to a cross-origin hostname behaving differently
// from the page's own already-open connection.
const API_BASE = window.API_BASE
  || (window.location.protocol === "file:" ? "http://localhost:8000" : window.location.origin);

// ---------------------------------------------------------------- tab heartbeat
// Lets a server started by start.bat notice when the last tab closes and stop itself. A
// reload sends "closing" then a fresh heartbeat within seconds, well inside the server's grace
// period. Harmless against a server that doesn't use it (errors are swallowed).
const TAB_ID = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Math.random()).slice(2);
const heartbeatUrl = (extra = "") => `${API_BASE}/app/heartbeat?tab=${TAB_ID}${extra}`;
function sendHeartbeat() {
  fetch(heartbeatUrl(), { method: "POST", keepalive: true }).catch(() => {});
}
sendHeartbeat();
setInterval(sendHeartbeat, 10000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) sendHeartbeat(); });
window.addEventListener("pageshow", sendHeartbeat);
window.addEventListener("pagehide", () => { navigator.sendBeacon(heartbeatUrl("&closing=1")); });

// ---------------------------------------------------------------- theme
const THEME_KEY = "cmi-revamp-theme";

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  const label = document.getElementById("theme-toggle-label");
  if (label) label.textContent = theme === "dark" ? "Dark mode" : "Light mode";
  localStorage.setItem(THEME_KEY, theme);
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  const current = document.body.dataset.theme === "dark" ? "dark" : "light";
  applyTheme(current === "dark" ? "light" : "dark");
});

// ---------------------------------------------------------------- mobile sidebar
const sidebar = document.getElementById("sidebar");
const overlay = document.getElementById("sidebar-overlay");

function openSidebar() {
  sidebar.classList.add("open");
  overlay.classList.add("visible");
}
function closeSidebar() {
  sidebar.classList.remove("open");
  overlay.classList.remove("visible");
}

document.getElementById("menu-toggle").addEventListener("click", openSidebar);
overlay.addEventListener("click", closeSidebar);

// ---------------------------------------------------------------- app state
const state = {
  article: null,        // { title, paragraphs: [{index, text, links: [{anchor, url, isExternal}]}] }
  filename: null,
  sponsorIdx: 0,
  run: null,             // { stage, orient, candidates, hits, selection, report, evidence, title, sections, chosenCandidate }
  result: null,          // final RevampResult-equivalent from the backend
  nav: "article",        // article | step1 | step2 | step3 | step4 | final
};

function iconify(root = document) {
  if (window.lucide) window.lucide.createIcons({ nameAttr: "data-lucide", attrs: { "stroke-width": 2 } });
}

// ---------------------------------------------------------------- run persistence (survive a page reload)
// Only a pointer is kept client-side — the actual run (article, orient, selection, draft…)
// lives server-side in _RUNS, keyed by run_token. On reload we just ask the server for it back.
const RUN_KEY = "cmi-revamp-run";

// The server keeps a finalized run around (bounded by its own TTL/eviction, see _prune_runs)
// so the final page's regenerate button can still resend /step4+/finalize on it, but a page
// reload has no other way to recover the finished draft — so the result itself, not just a
// pointer to it, is what gets persisted client-side.
const RESULT_KEY = "cmi-revamp-result";

function saveResult() {
  if (state.result) {
    localStorage.setItem(RESULT_KEY, JSON.stringify({ article: state.article, result: state.result, run: state.run }));
  } else {
    localStorage.removeItem(RESULT_KEY);
  }
}

function restoreResultIfAny() {
  let saved;
  try {
    saved = JSON.parse(localStorage.getItem(RESULT_KEY) || "null");
  } catch {
    saved = null;
  }
  if (!saved || !saved.result) return false;
  state.article = saved.article;
  state.result = saved.result;
  state.run = saved.run || null;
  state.nav = "final";
  return true;
}

function saveRunPointer() {
  if (state.run && state.run.token) {
    localStorage.setItem(RUN_KEY, JSON.stringify({ token: state.run.token, nav: state.nav }));
  } else {
    localStorage.removeItem(RUN_KEY);
  }
}

async function resumeRunIfAny() {
  let pointer;
  try {
    pointer = JSON.parse(localStorage.getItem(RUN_KEY) || "null");
  } catch {
    pointer = null;
  }
  if (!pointer || !pointer.token) return;

  try {
    const data = await apiGetRun(pointer.token);
    state.filename = data.filename;
    state.article = data.article;
    state.sponsorIdx = sponsorCandidatesFrom(data.article).findIndex((p) => p.index === data.sponsor_index);
    if (state.sponsorIdx < 0) state.sponsorIdx = 0;

    const run = { token: data.run_token, orient: data.orient, candidates: data.candidates, hits: data.hits, claimHits: data.claim_hits || {}, timings: data.timings || null, questions: data.questions || {}, longtails: data.long_tail_keywords || [],
      factSheet: data.fact_sheet || null, demand: data.demand || null, publisher: data.publisher || null, plan: data.plan || null };
    if (data.sections && data.sections.length) {
      run.stage = "write_done";
      run.report = data.report;
      run.selection = data.selection;
      run.chosenCandidate = data.candidates.find((c) => c.news_id === data.report.news_id) || data.report;
      run.title = data.title;
      run.sections = data.sections;
    } else if (data.selection) {
      run.stage = "select_done";
      run.report = data.report;
      run.selection = data.selection;
      run.chosenCandidate = data.candidates.find((c) => c.news_id === data.report.news_id) || data.report;
    } else if (data.report) {
      run.stage = "search_done";
      run.report = data.report;
      run.chosenCandidate = data.candidates.find((c) => c.news_id === data.report.news_id) || data.report;
    } else {
      run.stage = "orient_done";
    }
    state.run = run;
    state.nav = pointer.nav || "step1";

    document.getElementById("sponsor-section").hidden = false;
    document.getElementById("run-controls").hidden = false;
  } catch (e) {
    console.warn("Could not resume previous run:", e.message);
    localStorage.removeItem(RUN_KEY);
  }
}

function sponsorCandidatesFrom(article) {
  return article.paragraphs.filter((p) => p.links.some((l) => l.isExternal));
}

// Builds an Error carrying the backend's structured failure info (error_code/message/technical)
// when present, so showError() can render a plain-language banner instead of raw JSON/exc text.
async function _apiError(resp) {
  let detail;
  try {
    const body = await resp.json();
    detail = body.detail ?? body;
  } catch {
    detail = await resp.text().catch(() => resp.statusText);
  }
  const err = new Error(typeof detail === "string" ? detail : detail.message || resp.statusText);
  if (detail && typeof detail === "object") {
    err.errorCode = detail.error_code;
    err.where = detail.where;
    err.technical = detail.technical;
  } else {
    err.technical = `${resp.status}: ${typeof detail === "string" ? detail : resp.statusText}`;
  }
  err.status = resp.status;
  return err;
}

async function apiGetRun(token) {
  const resp = await fetch(`${API_BASE}/run/${encodeURIComponent(token)}`);
  if (!resp.ok) throw await _apiError(resp);
  return resp.json();
}

// ---------------------------------------------------------------- API helpers
async function apiPost(path, body) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : JSON.stringify(body),
  });
  if (!resp.ok) throw await _apiError(resp);
  return resp.json();
}

// Plain-language banner for the end user, with the technical detail tucked away (collapsed)
// for them to copy into a bug report — see index.html's #error-banner.
function showError(stepLabel, err) {
  const banner = document.getElementById("error-banner");
  if (!banner) { alert(`${stepLabel}: ${err.message}`); console.error(err); return; }
  const message = err.status >= 500 || err.errorCode ? err.message : `${stepLabel}: ${err.message}`;
  const technical = [
    `Step: ${err.where || stepLabel}`,
    err.errorCode ? `Error code: ${err.errorCode}` : null,
    `Detail: ${err.technical || err.message}`,
    `Time: ${new Date().toISOString()}`,
  ].filter(Boolean).join("\n");
  banner.hidden = false;
  banner.querySelector(".error-banner-message").textContent = message;
  banner.querySelector(".error-banner-technical").textContent = technical;
  banner.querySelector(".error-banner-technical").hidden = true;
  banner.querySelector(".error-banner-toggle").textContent = "Show technical details";
  banner.dataset.technical = technical;
  console.error(stepLabel, err);
}

function hideError() {
  const banner = document.getElementById("error-banner");
  if (banner) banner.hidden = true;
}

(() => {
  const banner = document.getElementById("error-banner");
  if (!banner) return;
  banner.querySelector("#error-banner-close").addEventListener("click", hideError);
  banner.querySelector(".error-banner-toggle").addEventListener("click", () => {
    const pre = banner.querySelector(".error-banner-technical");
    pre.hidden = !pre.hidden;
    banner.querySelector(".error-banner-toggle").textContent = pre.hidden ? "Show technical details" : "Hide technical details";
  });
  banner.querySelector(".error-banner-copy").addEventListener("click", async () => {
    const btn = banner.querySelector(".error-banner-copy");
    try {
      await navigator.clipboard.writeText(banner.dataset.technical || "");
      btn.textContent = "Copied!";
    } catch {
      btn.textContent = "Couldn't copy";
    }
    setTimeout(() => { btn.textContent = "Copy for support"; }, 1500);
  });
})();

// ---------------------------------------------------------------- file upload
const fileInput = document.getElementById("file-input");
const fileDrop = document.getElementById("file-drop");
const fileChip = document.getElementById("file-chip");
const fileChipName = document.getElementById("file-chip-name");

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) handleFile(fileInput.files[0]);
});

// Single unified drag-and-drop path: all drag/drop handling lives on window, so a file can be
// dropped anywhere — the small box or the rest of the page — without two competing listener
// sets fighting over propagation. (A previous version had the box's own listeners call
// stopPropagation(), which silently broke the page-wide overlay's enter/leave counter whenever
// a drop landed on the box, leaving it stuck visible.)
// dragenter/dragleave fire on every element the pointer crosses, so a naive show-on-enter /
// hide-on-leave flickers constantly — track depth with a counter instead, only hiding at 0.
const pageDropOverlay = document.getElementById("page-drop-overlay");
let dragDepth = 0;

function isFileDrag(e) {
  return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
}

window.addEventListener("dragenter", (e) => {
  if (!isFileDrag(e)) return;
  e.preventDefault();
  dragDepth++;
  showDropOverlay();
  fileDrop.classList.toggle("drag-over", fileDrop.contains(e.target));
});

window.addEventListener("dragover", (e) => {
  if (!isFileDrag(e)) return;
  e.preventDefault();
  fileDrop.classList.toggle("drag-over", fileDrop.contains(e.target));
});

window.addEventListener("dragleave", (e) => {
  if (!isFileDrag(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) {
    hideDropOverlay();
    fileDrop.classList.remove("drag-over");
  }
});

window.addEventListener("drop", (e) => {
  if (!isFileDrag(e)) return;
  e.preventDefault();
  dragDepth = 0;
  hideDropOverlay();
  fileDrop.classList.remove("drag-over");
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});

function showDropOverlay() {
  pageDropOverlay.hidden = false;
  // Force a layout flush before adding .visible, so the display:none -> flex change and the
  // opacity transition don't collapse into a single un-animated jump.
  void pageDropOverlay.offsetWidth;
  pageDropOverlay.classList.add("visible");
}

function hideDropOverlay() {
  pageDropOverlay.classList.remove("visible");
  pageDropOverlay.hidden = true;
}

document.getElementById("file-remove").addEventListener("click", () => resetForNewUpload());
document.getElementById("file-replace").addEventListener("click", () => fileInput.click());

async function handleFile(file) {
  resetForNewUpload();
  state.filename = file.name;
  fileChipName.textContent = file.name;

  const formData = new FormData();
  formData.append("file", file);
  try {
    const data = await apiPost("/upload", formData);
    state.article = data.article; // { title, paragraphs }
    renderSponsorCandidates();
    renderArticlePanel();
    document.getElementById("sponsor-section").hidden = false;
    document.getElementById("run-controls").hidden = false;
    setNav("article");
    render();
  } catch (e) {
    showError("Could not read this file", e);
  }
}

function resetForNewUpload() {
  state.article = null;
  state.filename = null;
  state.sponsorIdx = -1;
  state.run = null;
  state.result = null;
  localStorage.removeItem(RUN_KEY);
  localStorage.removeItem(RESULT_KEY);
  for (const key of Object.keys(_lastIconCls)) delete _lastIconCls[key];
  document.getElementById("sponsor-section").hidden = true;
  document.getElementById("run-controls").hidden = true;
  setNav("article");
  render();
}

// ---------------------------------------------------------------- sponsor paragraph picker
function sponsorCandidates() {
  if (!state.article) return [];
  return state.article.paragraphs.filter((p) => p.links.some((l) => l.isExternal));
}

function renderSponsorCandidates() {
  const container = document.getElementById("sponsor-candidates");
  const candidates = sponsorCandidates();
  container.innerHTML = "";

  if (!candidates.length) {
    container.innerHTML = `<p class="field-hint" style="color:var(--danger)">No paragraph has a hyperlink outside coherentmarketinsights.com. The agent needs this to know what to preserve.</p>`;
    document.getElementById("start-btn").disabled = true;
    return;
  }

  // Exactly one candidate is unambiguous — safe to auto-select. With 2+, a silent default
  // previously let an unreviewed run lock in the wrong paragraph as "sponsor" (it defaulted to
  // the first candidate, which is sometimes just the article's own opening paragraph, not the
  // actual paid-backlink paragraph) — so here the user must actively pick one before continuing.
  if (candidates.length === 1 && state.sponsorIdx !== 0) {
    state.sponsorIdx = 0;
  }

  const runActive = state.run && !state.result;
  const needsChoice = candidates.length > 1 && state.sponsorIdx < 0;

  candidates.forEach((p, i) => {
    const externalLinks = p.links.filter((l) => l.isExternal).map((l) => l.anchor || l.url).join(", ");
    const label = document.createElement("label");
    label.className = "radio-option" + (i === state.sponsorIdx ? " selected" : "") + (runActive ? " disabled" : "");
    label.innerHTML = `
      <input type="radio" name="sponsor" value="${i}" ${i === state.sponsorIdx ? "checked" : ""} ${runActive ? "disabled" : ""}>
      <span>&para;${p.index + 1} &middot; ${externalLinks}</span>
    `;
    label.querySelector("input").addEventListener("change", () => {
      state.sponsorIdx = i;
      renderSponsorCandidates();
      updateSponsorPreview();
    });
    container.appendChild(label);
  });

  const hint = document.getElementById("sponsor-choice-hint");
  if (hint) hint.hidden = !needsChoice;

  updateSponsorPreview();
  document.getElementById("start-btn").disabled = runActive || needsChoice;
  document.getElementById("sponsor-locked-note").hidden = !runActive;
}

function updateSponsorPreview() {
  const candidates = sponsorCandidates();
  const chosen = candidates[state.sponsorIdx];
  document.getElementById("sponsor-preview-text").textContent = chosen ? chosen.text : "Select a paragraph above to preview it.";
}

function currentSponsor() {
  return sponsorCandidates()[state.sponsorIdx] || null;
}

// ---------------------------------------------------------------- article panel
function renderArticlePanel() {
  if (!state.article) return;
  document.getElementById("article-title").textContent = state.article.title;
  document.getElementById("article-meta").textContent = `${state.article.paragraphs.length} paragraphs · original article`;
  const body = document.getElementById("article-body");
  body.innerHTML = state.article.paragraphs.map((p) => `<p>${renderParagraphWithLinks(p)}</p>`).join("");
}

// Splices each link's anchor text (a literal substring of p.text) into an <a>, so the
// reviewer can click straight through to a cited/sponsor link without opening the .docx.
function renderParagraphWithLinks(p) {
  const links = (p.links || [])
    .map((l) => ({ ...l, start: p.text.indexOf(l.anchor) }))
    .filter((l) => l.anchor && l.start !== -1)
    .sort((a, b) => a.start - b.start);

  let html = "";
  let cursor = 0;
  for (const l of links) {
    if (l.start < cursor) continue; // overlapping/duplicate anchor match — skip, keep it safe
    html += escapeHtml(p.text.slice(cursor, l.start));
    const cls = l.isExternal ? "sponsor-link" : "";
    html += `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener" class="${cls}">${escapeHtml(l.anchor)}</a>`;
    cursor = l.start + l.anchor.length;
  }
  html += escapeHtml(p.text.slice(cursor));
  return html;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------------------------------------------------------------- run controls
document.getElementById("start-btn").addEventListener("click", async () => {
  const sponsor = currentSponsor();
  if (!sponsor) return;
  state.run = { stage: "pending", sponsor };
  setNav("step1");
  render();
  await runStep1();
});

document.getElementById("cancel-btn").addEventListener("click", () => {
  state.run = null;
  saveRunPointer();
  render();
});
document.getElementById("new-run-btn").addEventListener("click", resetForNewUpload);
document.getElementById("step1-cancel-btn").addEventListener("click", () => { state.run = null; saveRunPointer(); render(); });
document.getElementById("step2-cancel-btn").addEventListener("click", () => { state.run = null; saveRunPointer(); render(); });
document.getElementById("step3-cancel-btn").addEventListener("click", () => { state.run = null; saveRunPointer(); render(); });
document.getElementById("step4-cancel-btn").addEventListener("click", () => { state.run = null; saveRunPointer(); render(); });

async function runStep1() {
  try {
    const data = await apiPost("/step1", {
      filename: state.filename,
      article: state.article,
      sponsor_index: currentSponsorIndexInParagraphs(),
    });
    state.run.stage = "orient_done";
    state.run.token = data.token || data.run_token;
    state.run.orient = data.orient;
    state.run.candidates = data.candidates;
    saveRunPointer();
  } catch (e) {
    showError("Step 1 (identify market)", e);
    state.run = null;
    saveRunPointer();
  }
  render();
}

function currentSponsorIndexInParagraphs() {
  const sponsor = currentSponsor();
  return sponsor ? sponsor.index : null;
}

// Every "continue"/"regenerate" button below guards against firing its request twice
// concurrently (a double-click, a stray repeat event, or two browser tabs on the same run) by
// bailing out immediately if the run is already mid-flight on ANY step — found live: a /step3
// call landing while /step2's own search was still running server-side, which the backend
// correctly rejected (400) but which should never have been sent in the first place.
function _runBusy() {
  return !!state.run && typeof state.run.stage === "string" && state.run.stage.endsWith("_pending");
}

// Step 1 -> Step 2: human confirms the market. Only now does the backend search the web —
// for the CONFIRMED report's name, not the LLM's pre-confirmation guess.
document.getElementById("step1-confirm-btn").addEventListener("click", async () => {
  if (_runBusy()) return;
  const picked = state.run.pickedReport;
  if (!picked) return;
  state.run.stage = "search_pending";
  setNav("step2");
  render();
  try {
    const data = await apiPost("/step2", {
      run_token: state.run.token,
      report_id: picked.news_id,
    });
    state.run.stage = "search_done";
    state.run.chosenCandidate = picked;
    state.run.report = data.report;
    state.run.hits = data.hits;
    state.run.claimHits = data.claim_hits || {};
    state.run.timings = data.timings || null;
    state.run.longtails = data.long_tail_keywords || [];
    state.run.factSheet = data.fact_sheet || null;
    state.run.demand = data.demand || null;
    state.run.publisher = data.publisher || null;
  } catch (e) {
    showError("Step 2 (fetch market evidence)", e);
    state.run.stage = "orient_done";
  }
  saveRunPointer();
  render();
});

// Step 2 -> Step 3: select usable facts from the gathered evidence.
document.getElementById("step2-confirm-btn").addEventListener("click", async () => {
  if (_runBusy()) return;
  state.run.stage = "select_pending";
  setNav("step3");
  render();
  try {
    const data = await apiPost("/step3", { run_token: state.run.token });
    state.run.stage = "select_done";
    state.run.selection = data.selection;
    state.run.questions = data.questions || {};
    if (data.demand) state.run.demand = data.demand;   // market-first: answerability is now marked
    if (data.long_tail_keywords) state.run.longtails = data.long_tail_keywords;   // market-first: they arrive at Step 3, not Step 2
  } catch (e) {
    showError("Step 3 (fetch report facts)", e);
    state.run.stage = "search_done";
  }
  saveRunPointer();
  render();
});

// Step 3 -> Step 4: write the article.
document.getElementById("step3-confirm-btn").addEventListener("click", async () => {
  if (_runBusy()) return;
  state.run.stage = "write_pending";
  setNav("step4");
  render();
  try {
    const data = await apiPost("/step4", { run_token: state.run.token });
    state.run.stage = "write_done";
    state.run.title = data.title;
    state.run.sections = data.sections;
  } catch (e) {
    showError("Step 4 (write)", e);
    state.run.stage = "select_done";
  }
  saveRunPointer();
  render();
});

// Resends the SAME write request (two LLM calls: publisher block, then market block) against the run's already-frozen
// evidence (run.evidence never changes here) to get a different draft from the LLM — lets a user
// reroll Step 4's output without re-running Steps 1-3 if the first draft wasn't a good fit.
document.getElementById("step4-regenerate-btn").addEventListener("click", async () => {
  if (_runBusy()) return;
  state.run.stage = "write_pending";
  render();
  try {
    const data = await apiPost("/step4", { run_token: state.run.token });
    state.run.stage = "write_done";
    state.run.title = data.title;
    state.run.sections = data.sections;
  } catch (e) {
    showError("Step 4 (regenerate draft)", e);
    state.run.stage = "write_done";
  }
  saveRunPointer();
  render();
});

document.getElementById("step4-confirm-btn").addEventListener("click", async () => {
  if (_runBusy()) return;
  state.run.stage = "finalize_pending";
  setNav("final");
  render();
  try {
    const data = await apiPost("/finalize", { run_token: state.run.token });
    state.result = data.result;
    // Keep state.run (not null) so Steps 1-4 can still show their own content read-only after
    // finalize, and so the final page's regenerate button still has a run_token to resend —
    // nulling it here was why those tabs went blank after finalizing and then navigating back
    // (or reloading, since RESULT_KEY persists this same run object).
    state.run.stage = "finalized";
    saveResult();
  } catch (e) {
    showError("Final checks", e);
  }
  saveRunPointer();
  render();
});

// Resends /step4 (write) then /finalize against the same frozen evidence, to get a
// different finished draft without re-running Steps 1-3 — same idea as step4-regenerate-btn,
// just one step further along, for when the issue only became visible after seeing the final
// SOP-checked/highlighted version.
document.getElementById("final-regenerate-btn").addEventListener("click", async () => {
  if (_runBusy()) return;
  state.run.stage = "write_pending";
  render();
  try {
    const written = await apiPost("/step4", { run_token: state.run.token });
    state.run.title = written.title;
    state.run.sections = written.sections;
    state.run.stage = "finalize_pending";
    render();
    const data = await apiPost("/finalize", { run_token: state.run.token });
    state.result = data.result;
    state.run.stage = "finalized";
    saveResult();
  } catch (e) {
    showError("Final checks (regenerate draft)", e);
    state.run.stage = "finalized";
  }
  saveRunPointer();
  render();
});

// ---------------------------------------------------------------- step nav
const NAV_ORDER = ["article", "step1", "step2", "step3", "step4", "final"];

function stepDoneFlags() {
  const stage = state.run ? state.run.stage : null;
  const atLeast = (stages) => stages.includes(stage) || !!state.result;
  return {
    step1: atLeast(["orient_done", "search_pending", "search_done", "select_pending", "select_done", "write_pending", "write_done"]),
    step2: atLeast(["search_done", "select_pending", "select_done", "write_pending", "write_done"]),
    step3: atLeast(["select_done", "write_pending", "write_done"]),
    step4: stage === "write_done" || !!state.result,
    final: !!state.result,
  };
}

function setNav(target) {
  state.nav = target;
  closeSidebar();
  saveRunPointer();
}

document.querySelectorAll(".step-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    setNav(btn.dataset.nav);
    render();
  });
});

// Tracks each step's last-rendered icon class, so the "done" pop animation only plays on the
// render where a step actually BECOMES done, not on every re-render (e.g. clicking the nav
// re-renders all steps, which previously replayed icon-pop on every already-done icon at once).
const _lastIconCls = {};

function renderStepNav() {
  const done = stepDoneFlags();
  const started = !!state.run;
  const stage = state.run ? state.run.stage : null;

  // "unlocked" (can be navigated to) and "actively running" (its own API call is in flight)
  // are different things — a step becomes unlocked the instant the previous one finishes,
  // well before the user has even clicked into it, so the spinner must key off the specific
  // pending stage for THAT step, not just "some run exists".
  const specs = {
    article: { always: true },
    step1: { unlocked: started, done: done.step1, pending: stage === "pending" },
    step2: { unlocked: done.step1, done: done.step2, pending: stage === "search_pending" },
    step3: { unlocked: done.step2, done: done.step3, pending: stage === "select_pending" },
    step4: { unlocked: done.step3, done: done.step4, pending: stage === "write_pending" },
    final: { unlocked: done.step4, done: done.final, pending: stage === "finalize_pending" },
  };

  NAV_ORDER.forEach((key) => {
    const btn = document.querySelector(`.step-item[data-nav="${key}"]`);
    const spec = specs[key];
    const unlocked = spec.always || spec.unlocked;
    btn.disabled = !unlocked;
    btn.classList.toggle("active", state.nav === key);

    const iconEl = btn.querySelector(".step-icon");
    if (iconEl) {
      let iconName = "lock";
      let cls = "locked";
      if (spec.done) { iconName = "check-circle-2"; cls = "done"; }
      else if (spec.pending) { iconName = "loader-circle"; cls = "in-progress"; }
      // iconEl may already be the <svg> Lucide swapped in on a previous render, and SVG
      // elements expose a read-only SVGAnimatedString for .className — setAttribute works
      // uniformly on both the original <i> and the replaced <svg>.
      iconEl.setAttribute("data-lucide", iconName);
      // Only replay the "done" pop animation on the render where this step actually just
      // became done — re-setting the same class on every render would restart the CSS
      // animation for every already-done icon simultaneously on each unrelated nav click.
      // A step with NO prior recorded state (first render, e.g. on page load/reload/resume)
      // must NOT pop even though it looks like a fresh transition — it was already done before
      // this session's renders began, there's just no history for it yet.
      const prior = _lastIconCls[key];
      const justBecameDone = cls === "done" && prior !== undefined && prior !== "done";
      iconEl.setAttribute("class", `step-icon ${cls}`
        + (cls === "in-progress" ? " spin" : "")
        + (justBecameDone ? "" : " no-pop"));
      _lastIconCls[key] = cls;
    }
  });
  iconify();
}

// ---------------------------------------------------------------- panel visibility
function showPanel(name) {
  document.querySelectorAll(".panel").forEach((p) => {
    p.hidden = p.dataset.panel !== name;
  });
}

// ---------------------------------------------------------------- render: sidebar run-state controls
function renderRunControls() {
  const startBtn = document.getElementById("start-btn");
  const cancelBtn = document.getElementById("cancel-btn");
  const activeBanner = document.getElementById("run-active-banner");
  const completeBanner = document.getElementById("run-complete-banner");
  const newRunBtn = document.getElementById("new-run-btn");

  const runActive = !!state.run && !state.result;

  startBtn.hidden = runActive || !!state.result;
  cancelBtn.hidden = !runActive;
  activeBanner.hidden = !runActive;
  completeBanner.hidden = !state.result;
  newRunBtn.hidden = !state.result;

  if (runActive) {
    const stageLabel = {
      pending: "Step 1 · Identify Market", orient_done: "Step 1 · Identify Market",
      search_pending: "Step 2 · Fetch Evidence Online", search_done: "Step 2 · Fetch Evidence Online",
      select_pending: "Step 3 · Fetch Report Facts", select_done: "Step 3 · Fetch Report Facts",
      write_pending: "Step 4 · Write", write_done: "Step 4 · Write",
      finalize_pending: "Final",
    }[state.run.stage] || "Step 1 · Identify Market";
    document.getElementById("run-active-text").textContent = `Run in progress — see ${stageLabel}`;
  }
}

// ---------------------------------------------------------------- render: step1 panel
function renderStep1Panel() {
  const spinner = document.getElementById("step1-spinner");
  const content = document.getElementById("step1-content");
  const confirmBlock = document.getElementById("step1-confirm-block");
  const doneBlock = document.getElementById("step1-done-block");

  if (!state.run) { spinner.hidden = true; content.hidden = true; return; }

  const pending = state.run.stage === "pending";
  spinner.hidden = !pending;
  content.hidden = pending;
  if (pending) return;

  const orient = state.run.orient || {};
  document.getElementById("step1-topic").textContent = orient.topic_summary || "";
  document.getElementById("step1-bridge").textContent = orient.bridge_angle || "";

  const marketsList = document.getElementById("step1-markets");
  marketsList.innerHTML = (orient.candidate_markets || []).map((m) => `<li>${escapeHtml(m)}</li>`).join("");

  const claims = orient.claims_to_back || [];
  document.getElementById("step1-claims").innerHTML = claims.length
    ? claims.map((c) => `<li><strong>${escapeHtml(c.id)}</strong> ${escapeHtml(c.claim)}`
        + (c.why ? ` <span class="option-caption">${escapeHtml(c.why)}</span>` : "") + `</li>`).join("")
    : `<li class="empty-note">No unsourced claims were found in the article, so nothing needs outside backing.</li>`;

  const isOrientDone = state.run.stage === "orient_done";
  confirmBlock.hidden = !isOrientDone;
  doneBlock.hidden = isOrientDone;

  if (isOrientDone) {
    const candidates = (state.run.candidates || []).slice(0, 5);
    const container = document.getElementById("step1-report-candidates");
    container.innerHTML = "";
    if (!state.run.pickedReport && candidates.length) state.run.pickedReport = candidates[0];

    candidates.forEach((c, i) => {
      const label = document.createElement("label");
      const selected = state.run.pickedReport && state.run.pickedReport.news_id === c.news_id;
      label.className = "radio-option" + (selected ? " selected" : "");
      label.innerHTML = `
        <input type="radio" name="report-pick" value="${c.news_id}" ${selected ? "checked" : ""}>
        <span>${escapeHtml(c.keyword)}<span class="option-caption">report id ${c.news_id}</span></span>
      `;
      label.querySelector("input").addEventListener("change", () => {
        state.run.pickedReport = c;
        renderStep1Panel();
      });
      container.appendChild(label);
    });

    const picked = state.run.pickedReport;
    const linkEl = document.getElementById("step1-report-link");
    linkEl.textContent = picked ? picked.url : "";
    linkEl.href = picked ? picked.url : "#";
  } else {
    const chosen = state.run.chosenCandidate;
    const link = document.getElementById("step1-confirmed-link");
    if (chosen) { link.textContent = chosen.keyword; link.href = chosen.url; }

    const all = document.getElementById("step1-all-candidates");
    all.innerHTML = (state.run.candidates || []).slice(0, 10)
      .map((c) => `<li><a href="${c.url}" target="_blank" rel="noopener">${escapeHtml(c.keyword)}</a></li>`).join("");
  }
}

// ---------------------------------------------------------------- render: step2 panel (fetch market evidence online)
function formatDuration(totalSeconds) {
  const s = Math.round(Number(totalSeconds) || 0);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

// Market-first chain (§3.11): renders the fact sheet with gaps highlighted and each figure's own
// source named. A gap (size / forecast / CAGR the report does not carry) always stays visible —
// a figure that never got a source is never stated anywhere in the article, so an editor wants to
// see which of the three headline figures came from where.
function renderFactSheet(fs) {
  const gaps = fs.gaps || [];
  const sourceEl = (entry) => {
    if (!entry || !entry.source) return "";
    return entry.url
      ? `<a href="${escapeHtml(entry.url)}" target="_blank" rel="noopener">${escapeHtml(entry.source)}</a>`
      : escapeHtml(entry.source);
  };
  const hist = (entry) => (entry && entry.historical ? " (dated — used with its year only)" : "");
  const headline = (entry, label, key) => {
    if (entry && entry.value) {
      return `<li>${label}: <strong>${escapeHtml(entry.value)}</strong>${entry.when ? ` <span class="option-caption">(${escapeHtml(entry.when)})</span>` : ""}` +
        ` <span class="option-caption">source: ${sourceEl(entry)}</span>${hist(entry)}</li>`;
    }
    return `<li class="factsheet-gap">${label}: <strong>gap</strong> <span class="option-caption">the report is silent — ` +
      (gaps.includes(key)
        ? "no outside page filled it, so the article states no figure for it"
        : "the figure gap search ran for this") + `</span></li>`;
  };
  const statementList = (label, items, max = 3) => {
    const n = (items || []).length;
    if (!n) return "";
    const shown = items.slice(0, max).map((it) => `${escapeHtml(it.text)}${it.historical ? " (dated)" : ""}`);
    const more = n > max ? ` +${n - max} more` : "";
    return `<li>${label} (${n}): <span class="option-caption">${shown.join(" &middot; ")}${more}</span></li>`;
  };
  const out = [
    headline(fs.size, "Market size", "size"),
    headline(fs.forecast, "Forecast", "forecast"),
    headline(fs.cagr, "Growth rate", "cagr"),
  ];
  if (fs.figure_source) {
    const fsrc = fs.figure_source;
    out.push(`<li>Figure gap filled from an outside page: <strong>${escapeHtml(fsrc.figures.join(", "))}</strong>` +
      ` <span class="option-caption">source: <a href="${escapeHtml(fsrc.url)}" target="_blank" rel="noopener">${escapeHtml(fsrc.name)}</a></span></li>`);
  }
  for (const seg of fs.segments || []) {
    out.push(`<li>Segmentation — ${escapeHtml(seg.type)}: <strong>${escapeHtml((seg.items || []).join(", "))}</strong> ` +
      `<span class="option-caption">source: CMI report</span></li>`);
  }
  const regions = fs.regions || {};
  if (regions.leader || regions.fastest) {
    out.push(`<li>Regions — leading: ${escapeHtml(regions.leader ? regions.leader.name : "(gap)")}${hist(regions.leader)}` +
      ` · fastest: ${escapeHtml(regions.fastest ? regions.fastest.name : "(gap)")}${hist(regions.fastest)}` +
      ` <span class="option-caption">source: CMI report</span></li>`);
  }
  for (const it of fs.us || []) {
    out.push(`<li>U.S.: <strong>${escapeHtml(it.text)}</strong>${hist(it)} <span class="option-caption">source: CMI report</span></li>`);
  }
  const extra = statementList("Drivers", fs.drivers) + statementList("Restraints", fs.restraints)
    + statementList("Opportunities", fs.opportunities);
  out.push(extra);
  if ((fs.players || []).length) {
    out.push(`<li>Key players: <strong>${escapeHtml(fs.players.join(", "))}</strong> <span class="option-caption">source: CMI report</span></li>`);
  }
  return out.filter(Boolean).join("");
}

function renderStep2Panel() {
  const spinner = document.getElementById("step2-spinner");
  const content = document.getElementById("step2-content");
  if (!state.run || !["search_pending", "search_done", "select_pending", "select_done", "write_pending", "write_done", "finalized"].includes(state.run.stage)) {
    spinner.hidden = true; content.hidden = true; return;
  }

  const pending = state.run.stage === "search_pending";
  spinner.hidden = !pending;
  content.hidden = pending;
  if (pending) return;

  const report = state.run.report;
  const link = document.getElementById("step2-report-link");
  if (report) { link.textContent = report.name; link.href = report.url; }

  const hits = state.run.hits || [];
  document.getElementById("step2-hits-count").textContent = hits.length;
  const timings = state.run.timings;
  document.getElementById("step2-timing").hidden = !timings;
  document.getElementById("step2-timing-stages").hidden = !timings;
  if (timings) {
    document.getElementById("step2-timing-total").textContent = `Evidence collected in ${formatDuration(timings.total_s)} (stages overlap, so they do not add up to the total).`;
    document.getElementById("step2-timing-stages").innerHTML = (timings.stages || []).map((s) =>
      `<li>${escapeHtml(s.name)} — ${formatDuration(s.seconds)} <span class="field-hint">(started at +${formatDuration(s.start_s)})</span></li>`
    ).join("");
  }
  document.getElementById("step2-hits").innerHTML = hits.length
    ? hits.map((h) => {
        let host = "web";
        try { host = new URL(h.url).hostname.replace(/^www\./, ""); } catch { /* keep "web" */ }
        const kind = h.is_news ? "News" : "Web";
        const date = h.date ? h.date.slice(0, 10) : "undated";
        const snippet = (h.snippet || "").trim();
        return `<li><a href="${escapeHtml(h.url)}" target="_blank" rel="noopener">${escapeHtml(h.title)}</a>`
          + ` <span class="option-caption">${kind} &middot; ${escapeHtml(date)} &middot; ${escapeHtml(h.source || host)}</span>`
          + (snippet ? `<span class="option-caption">${escapeHtml(snippet.length > 220 ? snippet.slice(0, 220) + "…" : snippet)}</span>` : "")
          + `</li>`;
      }).join("")
    : `<li class="empty-note">No results survived filtering for this market (too old, off-topic, or the search returned nothing). You can still continue — the writer will rely on the CMI report alone.</li>`;

  const claimList = (state.run.orient && state.run.orient.claims_to_back) || [];
  const claimHits = state.run.claimHits || {};
  document.getElementById("step2-claims-block").hidden = claimList.length === 0;
  document.getElementById("step2-claim-hits").innerHTML = claimList.map((c) => {
    const pages = claimHits[c.id] || [];
    const pageLis = pages.length
      ? pages.map((h) => {
          let host = h.source || "web";
          return `<span class="option-caption"><a href="${escapeHtml(h.url)}" target="_blank" rel="noopener">${escapeHtml(h.title)}</a> &middot; ${escapeHtml(host)}</span>`;
        }).join("")
      : `<span class="option-caption">No usable page found for this claim.</span>`;
    return `<li><strong>${escapeHtml(c.id)}</strong> ${escapeHtml(c.claim)}${pageLis}</li>`;
  }).join("");

  const sectionLabel = { report_context: "Market overview", trends: "Market trends", segmentation: "Segmentation", country_us: "U.S. market analysis", key_players: "Key players" };
  const subheadingQuestions = (state.run.questions && state.run.questions.subheading_questions) || [];
  document.getElementById("step2-questions-block").hidden = subheadingQuestions.length === 0;
  document.getElementById("step2-questions").innerHTML = subheadingQuestions.map((q) => `
    <li><strong>${escapeHtml(sectionLabel[q.section] || q.section)}</strong> section — ${escapeHtml(q.question)}</li>
  `).join("");

  // Market-first chain (restructure plan §3.11): the built fact sheet — with gaps highlighted and
  // the source of every figure — and the most-searched questions. The old chain never sends these
  // keys, so both blocks stay hidden on an old run.
  const factSheet = state.run.factSheet;
  document.getElementById("step2-factsheet-block").hidden = !factSheet;
  if (factSheet) document.getElementById("step2-factsheet").innerHTML = renderFactSheet(factSheet);

  const msqs = (state.run.demand && state.run.demand.questions) || [];
  document.getElementById("step2-msqs-block").hidden = msqs.length === 0;
  const answerableLabel = { size: "size", growth: "growth", trends: "trend", drivers: "drivers", challenges: "challenges",
    technology_impact: "technology", segments: "segments", region: "region", us: "U.S.", players: "players",
    outlook: "outlook", opportunities: "opportunities" };
  document.getElementById("step2-msqs").innerHTML = msqs.map((q) => {
    // answerable appears only once Step 3 ran mark_answerability; before that the badge says so.
    const badge = typeof q.answerable === "boolean"
      ? (q.answerable
          ? `<span class="msq-badge">answerable</span>`
          : `<span class="msq-badge" title="No fact in the fact sheet and no selected evidence can answer this kind of question. It can never become a heading, and the FAQ never invents one.">not answerable</span>`)
      : `<span class="msq-badge" title="Checked once Step 3 has finished deciding which facts are usable.">answerability checked in Step 3</span>`;
    return `<li><strong>${escapeHtml(q.question)}</strong> <span class="option-caption">${escapeHtml(answerableLabel[q.intent] || q.intent)} &middot; could head the ${escapeHtml(sectionLabel[q.section] || q.section)} section</span>${badge}</li>`;
  }).join("");

  const isSearchDone = state.run.stage === "search_done";
  document.getElementById("step2-confirm-block").hidden = !isSearchDone;
}

// ---------------------------------------------------------------- render: step3 panel (fetch market report facts)
function renderStep3Panel() {
  const spinner = document.getElementById("step3-spinner");
  const content = document.getElementById("step3-content");
  if (!state.run || !["select_pending", "select_done", "write_pending", "write_done", "finalized"].includes(state.run.stage)) {
    spinner.hidden = true; content.hidden = true; return;
  }

  const pending = state.run.stage === "select_pending";
  spinner.hidden = !pending;
  content.hidden = pending;
  if (pending) return;

  const report = state.run.report;
  const link = document.getElementById("step3-report-link");
  if (report) { link.textContent = report.name; link.href = report.url; }
  const reportTextEl = document.getElementById("step3-report-text");
  reportTextEl.innerHTML = report && report.text
    ? report.text.split(/\n+/).filter((p) => p.trim()).map((p) => `<p>${escapeHtml(p)}</p>`).join("")
    : `<p class="empty-note">No report text available.</p>`;

  const evidenceList = document.getElementById("step3-evidence");
  const facts = (state.run.selection && state.run.selection.evidence) || [];
  const hits = state.run.hits || [];
  const useForLabel = { trends: "Trend", us: "U.S. market", players: "Key player", context: "Context" };
  evidenceList.innerHTML = facts.length
    ? facts.map((e) => {
        const idx = parseInt(String(e.id || "").replace(/\D/g, ""), 10) - 1;
        const hit = hits[idx];
        let sourceLabel = "web";
        if (hit) {
          sourceLabel = hit.source || (() => { try { return new URL(hit.url).hostname.replace(/^www\./, ""); } catch { return "web"; } })();
        }
        const tag = (e.use_for_all && e.use_for_all.length ? e.use_for_all : [e.use_for])
          .map((t) => useForLabel[t] || "Context").join(" + ");
        const sourceEl = hit && hit.url
          ? `<a href="${escapeHtml(hit.url)}" target="_blank" rel="noopener">${escapeHtml(sourceLabel)}</a>`
          : escapeHtml(sourceLabel);
        return `<li>${escapeHtml(e.fact)} <span class="option-caption">${escapeHtml(tag)} &middot; source: ${sourceEl}</span></li>`;
      }).join("")
    : `<li class="empty-note">No supporting web facts survived review — the writer will rely on the confirmed CMI report alone. You can still continue.</li>`;

  const claimsForBacking = (state.run.orient && state.run.orient.claims_to_back) || [];
  const backing = (state.run.selection && state.run.selection.claim_backing) || [];
  const stanceLabel = { supports: "Supports", qualifies: "Qualifies", contradicts: "Contradicts" };
  document.getElementById("step3-claims-block").hidden = claimsForBacking.length === 0;
  document.getElementById("step3-claim-backing").innerHTML = claimsForBacking.map((c) => {
    const items = backing.filter((b) => b.claim_id === c.id);
    const body = items.length
      ? items.map((b) => {
          const src = b.url
            ? `<a href="${escapeHtml(b.url)}" target="_blank" rel="noopener">${escapeHtml(b.source || "web")}</a>`
            : escapeHtml(b.source || "web");
          return `<span class="option-caption"><strong>${escapeHtml(stanceLabel[b.stance] || b.stance)}:</strong> ${escapeHtml(b.fact)} &middot; source: ${src}</span>`;
        }).join("")
      : `<span class="option-caption">No outside backing found. The writer will soften this claim or drop it.</span>`;
    return `<li><strong>${escapeHtml(c.id)}</strong> ${escapeHtml(c.claim)}${body}</li>`;
  }).join("");

  const subheadingQuestions = (state.run.questions && state.run.questions.subheading_questions) || [];
  const questionBacking = (state.run.selection && state.run.selection.question_backing) || [];
  document.getElementById("step3-questions-block").hidden = subheadingQuestions.length === 0;
  document.getElementById("step3-question-backing").innerHTML = subheadingQuestions.map((q) => {
    const items = questionBacking.filter((b) => b.question_id === q.id);
    const body = items.length
      ? items.map((b) => {
          const src = b.url
            ? `<a href="${escapeHtml(b.url)}" target="_blank" rel="noopener">${escapeHtml(b.source || "web")}</a>`
            : escapeHtml(b.source || "web");
          return `<span class="option-caption">${escapeHtml(b.fact)} &middot; source: ${src}</span>`;
        }).join("")
      : `<span class="option-caption">No outside source found — answered from the CMI report/article content only.</span>`;
    return `<li><strong>${escapeHtml(q.question)}</strong>${body}</li>`;
  }).join("");

  // Long-tail keywords: generated at Step 2 (reused from Step 1's orient guess when the confirmed
  // market matched it, regenerated otherwise), handed to the writer in Step 4. This screen shows
  // the pool the writer will pick from — intent cluster and reader intent per phrase — so a human
  // can sanity-check what will be woven into the draft before it is written. Nothing is editable
  // here by design: an untouched pool is what the writer's caps (at most 3, skip-if-no-fit) are for.
  const longtails = state.run.longtails || [];
  const longtailIntentLabel = {
    commercial_review: "Commercial / review",
    niche_b2b: "Niche / B2B",
    research_b2b: "Research / B2B",
    commercial_pricing: "Commercial / pricing",
    vendor_comparison: "Vendor comparison",
  };
  document.getElementById("step3-longtails-block").hidden = longtails.length === 0;
  document.getElementById("step3-longtails").innerHTML = longtails.map((t) => {
    const intent = longtailIntentLabel[t.intent] || t.intent;
    const why = t.why ? ` &middot; ${escapeHtml(t.why)}` : "";
    return `<li><strong>${escapeHtml(t.keyword)}</strong> <span class="option-caption">${escapeHtml(intent)}${why}</span></li>`;
  }).join("");

  // Exact tally of what the selector kept, so "how many did the LLM find useful" never has to be counted by eye.
  const claimsBacked = new Set(backing.map((b) => b.claim_id)).size;
  const countParts = [`<strong>${facts.length}</strong> of ${hits.length} gathered market items judged useful`];
  if (claimsForBacking.length) {
    countParts.push(`<strong>${backing.length}</strong> backing source${backing.length === 1 ? "" : "s"} chosen, covering ${claimsBacked} of ${claimsForBacking.length} claim${claimsForBacking.length === 1 ? "" : "s"}`);
  }
  document.getElementById("step3-counts").innerHTML = countParts.join(" &middot; ");

  const isSelectDone = state.run.stage === "select_done";
  document.getElementById("step3-confirm-block").hidden = !isSelectDone;
  document.getElementById("step3-done-note").hidden = isSelectDone;
}

// ---------------------------------------------------------------- render: step4 panel (write)
function renderStep4Panel() {
  const spinner = document.getElementById("step4-spinner");
  const content = document.getElementById("step4-content");
  if (!state.run || !["write_pending", "write_done", "finalized"].includes(state.run.stage)) {
    spinner.hidden = true; content.hidden = true; return;
  }

  const pending = state.run.stage === "write_pending";
  spinner.hidden = !pending;
  content.hidden = pending;
  if (pending) return;

  document.getElementById("step4-title").textContent = state.run.title || "";
  document.getElementById("step4-sections").innerHTML = renderSections(state.run.sections || []);

  // Once finalized, this step4 tab becomes read-only history — Finish/Cancel no longer apply,
  // and regenerating from here on is done via final-regenerate-btn on the final page instead.
  const isFinalized = state.run.stage === "finalized";
  document.getElementById("step4-confirm-btn").hidden = isFinalized;
  document.getElementById("step4-regenerate-btn").hidden = isFinalized;
  document.getElementById("step4-cancel-btn").hidden = isFinalized;
}

// Each paragraph arrives as [{text, url}] segments (the backend already spliced in the
// sponsor's original links and the CMI report citation) — render real <a> tags, same as
// the original-article preview, so a reviewer never has to leave this screen to check a link.
function renderSegments(segments) {
  return (segments || [])
    .map((seg) => (seg.url ? `<a href="${escapeHtml(seg.url)}" target="_blank" rel="noopener">${escapeHtml(seg.text)}</a>` : escapeHtml(seg.text)))
    .join("");
}

// A paragraph can be a lead-in sentence followed by "\n- " bulleted lines (trends, segmentation —
// see docx_io.py's _split_bullet_lines, the source of truth for this convention) instead of plain
// prose, in the SAME segments array the backend already sent (the CMI-link splitting in
// main.py:_segment_dicts only breaks on the link token, so a "\n" inside a segment's own text
// survives untouched). Splits that one segments array into (leadSegments, [bulletSegments, ...])
// by cutting each segment's text at its own "\n" boundaries and re-tagging which output line each
// slice landed on — a segment whose url carries into a bullet (e.g. a link embedded in a bullet
// line) stays attached to the right line, not just the first one.
const _BULLET_LINE_RE = /^-\s+/;

function _splitBulletSegments(segments) {
  const full = (segments || []).map((s) => s.text).join("");
  if (!full.includes("\n- ") && !_BULLET_LINE_RE.test(full)) return null;
  const lines = [];
  let current = [];
  for (const seg of segments || []) {
    const parts = seg.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) { lines.push(current); current = []; }
      // kw (the backend's keyword-highlight label) must travel with the piece: dropping it here made every
      // keyword mark inside a bulleted paragraph disappear while the legend still counted it.
      if (part) current.push({ text: part, url: seg.url, kw: seg.kw });
    });
  }
  lines.push(current);
  const lead = lines[0] || [];
  const bullets = lines.slice(1)
    .map((line) => {
      if (!line.length) return null;
      const first = { ...line[0], text: line[0].text.replace(_BULLET_LINE_RE, "") };
      return [first, ...line.slice(1)];
    })
    .filter((line) => line && line.some((s) => s.text.trim()));
  return bullets.length ? { lead, bullets } : null;
}

// Shared by the plain and highlighted renderers: `renderInline(segments)` turns one line's
// segments into inline HTML (escaping/highlighting is the caller's job), this just decides
// whether a paragraph becomes <p> or <p>+<ul><li>.
function _renderParagraph(segments, renderInline) {
  const split = _splitBulletSegments(segments);
  if (!split) return `<p>${renderInline(segments)}</p>`;
  const leadHtml = split.lead.some((s) => s.text.trim()) ? `<p>${renderInline(split.lead)}</p>` : "";
  const items = split.bullets.map((line) => `<li>${renderInline(line)}</li>`).join("");
  return `${leadHtml}<ul>${items}</ul>`;
}

function renderSections(sections) {
  return sections.map((s) => {
    const heading = s.heading ? `<h4>${escapeHtml(s.heading)}</h4>` : "";
    const paras = (s.paragraphs || []).map((p) => _renderParagraph(p, renderSegments)).join("");
    return heading + paras;
  }).join("");
}

// ---------------------------------------------------------------- keyword/evidence highlighting (final panel only)
//
// Highlighting runs on the PLAIN text of each segment, before it becomes HTML — splitting a
// string already containing <a> tags would risk matching inside tag attributes or breaking the
// markup. Each segment's text is cut into alternating plain/highlighted runs first, THEN each run
// is escaped and wrapped, so escaping and highlighting never fight each other.

// Finds every non-overlapping, case-insensitive occurrence of `phrase` in `text` and returns
// [{start, end}, ...] in the text's own coordinates — used to merge matches from several
// matchers (primary, each secondary keyword, each evidence paragraph) into one pass per segment.
function _findMatches(text, phrase) {
  if (!phrase) return [];
  const hay = text.toLowerCase();
  const needle = phrase.toLowerCase();
  const out = [];
  let from = 0;
  while (true) {
    const i = hay.indexOf(needle, from);
    if (i < 0) break;
    out.push({ start: i, end: i + needle.length });
    from = i + needle.length;
  }
  return out;
}

// Merges matches from multiple phrases/classes into disjoint, priority-ordered spans: earlier
// entries in `matchers` win on overlap (primary keyword is checked before secondary, so a
// secondary keyword that happens to be a substring of the primary phrase never steals its span).
function _highlightRuns(text, matchers) {
  const taken = new Array(text.length).fill(null);
  for (const { phrase, cls, title } of matchers) {
    // One mark object per MATCH (not per character) — _highlightRuns below merges consecutive
    // positions into a single <mark> only when they share the exact same object reference, so
    // creating a fresh object per character here would silently defeat that merge and wrap every
    // character in its own tag.
    for (const { start, end } of _findMatches(text, phrase)) {
      let free = true;
      for (let i = start; i < end; i++) if (taken[i]) { free = false; break; }
      if (!free) continue;
      const mark = { cls, title };
      for (let i = start; i < end; i++) taken[i] = mark;
    }
  }
  const runs = [];
  let i = 0;
  while (i < text.length) {
    const mark = taken[i];
    let j = i + 1;
    while (j < text.length && taken[j] === mark) j++;
    runs.push({ text: text.slice(i, j), mark });
    i = j;
  }
  return runs;
}

function _renderHighlightedText(text, matchers) {
  if (!matchers.length) return escapeHtml(text);
  return _highlightRuns(text, matchers).map((run) => {
    const safe = escapeHtml(run.text);
    if (!run.mark) return safe;
    const titleAttr = run.mark.title ? ` title="${escapeHtml(run.mark.title)}"` : "";
    return `<mark class="${run.mark.cls}"${titleAttr}>${safe}</mark>`;
  }).join("");
}

// Maps a segment's server-assigned "kw" label (checks.find_keyword_spans ran concept-matching
// there, not a literal phrase search — see main.py's _apply_keyword_spans) to its highlight class.
const _KW_LABEL_CLASS = { primary: "hl-primary", secondary: "hl-secondary", longtail: "hl-longtail", tertiary: "hl-tertiary" };
const _KW_LABEL_TITLE = { primary: "Primary keyword", secondary: "Secondary keyword", longtail: "Long-tail keyword", tertiary: "Related term" };

function renderSegmentsHighlighted(segments, matchers) {
  return (segments || [])
    .map((seg) => {
      // A segment already labeled by the backend is rendered as its own single mark — no
      // client-side phrase search needed (and none would work: the matched words may be a
      // reworded form of the keyword, not the literal phrase).
      const inner = seg.kw
        ? `<mark class="${_KW_LABEL_CLASS[seg.kw] || ""}" title="${escapeHtml(_KW_LABEL_TITLE[seg.kw] || "")}">${escapeHtml(seg.text)}</mark>`
        : _renderHighlightedText(seg.text, matchers);
      return seg.url
        ? `<a href="${escapeHtml(seg.url)}" target="_blank" rel="noopener">${inner}</a>`
        : inner;
    })
    .join("");
}

// Builds the matcher list for one run's result: one matcher per cited evidence paragraph.
// Primary/secondary keywords are NOT included here — they arrive pre-highlighted per segment
// (seg.kw, set server-side by main.py's _apply_keyword_spans using the same concept-matching
// algorithm the pipeline itself judges keyword usage with) since a client-side literal-phrase
// search would miss almost every real use once a keyword has been naturally reworded. Matching
// the evidence FACT text isn't possible (WRITE paraphrases it), so citations instead highlight
// whichever paragraph the model itself reported using that evidence in (see pipeline.run_write's
// evidence_citations, verified server-side against the real draft before being sent here).
function _buildHighlightMatchers(result) {
  const matchers = [];
  for (const c of result.evidence_citations || []) {
    if (c.paragraph) matchers.push({ phrase: c.paragraph, cls: "hl-evidence", title: c.fact ? `Evidence ${c.id}: ${c.fact}` : `Evidence ${c.id}` });
  }
  return matchers;
}

function renderSectionsHighlighted(sections, result) {
  const matchers = _buildHighlightMatchers(result);
  const msqHeadings = new Set((result.msq_headings || []).map((h) => h.trim().toLowerCase()));
  return sections.map((s) => {
    // A heading the plan built from a Most Searched Question carries a small tag — the reader sees
    // which headings are also real search phrases at a glance (§3.11 final view).
    const isMsq = s.heading && msqHeadings.has(s.heading.trim().toLowerCase())
      ? `<span class="msq-badge" title="This heading is one of the article's Most Searched Questions.">MSQ</span>`
      : "";
    const heading = s.heading ? `<h4>${escapeHtml(s.heading)}${isMsq}</h4>` : "";
    const paras = (s.paragraphs || [])
      .map((p) => _renderParagraph(p, (segs) => renderSegmentsHighlighted(segs, matchers)))
      .join("");
    return heading + paras;
  }).join("");
}

// Counts actual occurrences for the legend — independent of which spans "won" an overlap above,
// so the legend's numbers always reflect genuine keyword presence in the text, not rendering
// priority. Evidence count is simply how many citations came back from the backend.
function _countOccurrences(sections, phrase) {
  if (!phrase) return 0;
  const full = (sections || []).flatMap((s) => s.paragraphs || []).map((p) => (p || []).map((seg) => seg.text).join("")).join("\n");
  return _findMatches(full.toLowerCase(), phrase.toLowerCase()).length;
}

// ---------------------------------------------------------------- render: final panel
function renderFinalPanel() {
  const spinner = document.getElementById("final-spinner");
  const content = document.getElementById("final-content");

  // write_pending is included here too: the regenerate button on this page drives the run
  // through /step4 again before /finalize, and both legs should read as one continuous spinner
  // rather than flashing the stale draft back in between the two requests.
  const finalizing = state.run && (state.run.stage === "finalize_pending" || state.run.stage === "write_pending");
  spinner.hidden = !finalizing;
  content.hidden = finalizing || !state.result;
  if (finalizing || !state.result) return;

  const result = state.result;
  const passed = result.checks.filter((c) => c.passed).length;
  document.getElementById("final-checks-passed").textContent = `${passed} / ${result.checks.length}`;
  document.getElementById("final-report-name").textContent = result.report.name;

  document.getElementById("final-title").textContent = result.title;
  document.getElementById("final-caption").innerHTML =
    `Revised from: <em>${escapeHtml(state.article.title)}</em> &middot; Report: <a href="${result.report.url}" target="_blank" rel="noopener">${escapeHtml(result.report.name)}</a>`;

  document.getElementById("final-article").innerHTML = renderSectionsHighlighted(result.sections, result);

  const faq = result.faq || [];
  document.getElementById("final-faq-block").hidden = faq.length === 0;
  document.getElementById("final-faq").innerHTML = faq.map((qa) => `
    <div class="faq-item">
      <p class="faq-question"><strong>${escapeHtml(qa.question)}</strong>${qa.intent
        ? `<span class="faq-intent" title="Every FAQ entry here is a Most Searched Question from the search-demand pass, answered from the article's own facts.">MSQ &middot; ${escapeHtml(qa.intent)}</span>` : ""}</p>
      <p class="faq-answer">${escapeHtml(qa.answer)}</p>
    </div>
  `).join("");

  // The legend's numbers come from the backend's own highlighting pass (result.keyword_counts, filled by the
  // same spans that produce the marks in the text) so they always match what is highlighted. A literal phrase
  // search here cannot: it misses every secondary keyword the writer reordered or split across a sentence.
  const counts = result.keyword_counts || {};
  const secondaryTotal = Object.values(counts.secondary || {}).reduce((sum, n) => sum + n, 0);
  const secondaryUsed = Object.keys(counts.secondary || {}).length;
  // Tertiary (related) terms exist only on the market-first chain — the legend row stays hidden on
  // an old run (no terms and no counts were ever sent for it).
  const tertiaryTotal = Object.values(counts.tertiary || {}).reduce((sum, n) => sum + n, 0);
  const tertiaryUsed = Object.keys(counts.tertiary || {}).length;
  const tertiaryTerms = result.tertiary_terms || [];
  document.getElementById("legend-tertiary").hidden = !tertiaryTerms.length;
  document.getElementById("legend-count-tertiary").textContent =
    tertiaryTerms.length ? `(${tertiaryTotal}x, ${tertiaryUsed}/${tertiaryTerms.length} used)` : "";
  const longtailTotal = Object.values(counts.longtail || {}).reduce((sum, n) => sum + n, 0);
  const longtailUsed = Object.keys(counts.longtail || {}).length;
  document.getElementById("legend-count-primary").textContent = `(${counts.primary || 0}x)`;
  document.getElementById("legend-count-secondary").textContent =
    `(${secondaryTotal}x, ${secondaryUsed}/${(result.secondary_keywords || []).length} used)`;
  document.getElementById("legend-count-longtail").textContent =
    `(${longtailTotal}x, ${longtailUsed}/${(result.long_tail_keywords || []).length} used)`;
  document.getElementById("legend-longtail").hidden = (result.long_tail_keywords || []).length === 0;
  document.getElementById("legend-count-evidence").textContent =
    `(${(result.evidence_citations || []).length})`;

  const checklist = document.getElementById("final-checklist");
  checklist.innerHTML = result.checks.map((c) => `
    <li><i data-lucide="${c.passed ? "check" : "x"}" class="${c.passed ? "pass" : "fail"}"></i>
    <span>${escapeHtml(c.name)}${c.detail ? " — " + escapeHtml(c.detail) : ""}</span></li>
  `).join("");

  const downloadBtn = document.getElementById("final-download-btn");
  downloadBtn.onclick = () => {
    window.location.href = `${API_BASE}/download/${encodeURIComponent(result.output_filename)}`;
  };
}

// ---------------------------------------------------------------- master render
function render() {
  renderRunControls();
  renderStepNav();
  showPanel(state.article ? state.nav : "empty");
  if (state.article) { renderArticlePanel(); renderSponsorCandidates(); }
  renderStep1Panel();
  renderStep2Panel();
  renderStep3Panel();
  renderStep4Panel();
  renderFinalPanel();
  iconify();
}

// ---------------------------------------------------------------- init
initTheme();
iconify();
render();
if (restoreResultIfAny()) {
  document.getElementById("sponsor-section").hidden = false;
  document.getElementById("run-controls").hidden = false;
  render();
} else {
  resumeRunIfAny().then(render);
}
