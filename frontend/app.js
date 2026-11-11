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
  clientPicks: {},          // host -> {on, paraIdx}: the clients the editor ticked and the paragraph chosen for each
  run: null,             // { stage, orient, candidates, hits, selection, report, evidence, title, sections, pickedCmi, pickedMarkets, reportUrls, chosenCandidate }
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
    restoreClientPicks(data.sponsor_indexes || [data.sponsor_index], (data.clients || []).map((c) => c.host));

    const run = { token: data.run_token, orient: data.orient, candidates: data.candidates, hits: data.hits, claimHits: data.claim_hits || {}, timings: data.timings || null, questions: data.questions || {}, longtails: data.long_tail_keywords || [],
      factSheet: data.fact_sheet || null, demand: data.demand || null, publisher: data.publisher || null, plan: data.plan || null,
      pickedCmi: null, pickedMarkets: [], reportUrls: [""], reportMode: data.report_mode || null, warnings: data.warnings || [], clients: data.clients || [] };
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
    } else if ((data.found_reports || []).length) {
      run.stage = "reports_found";
      run.foundReports = data.found_reports;
      run.pickedFound = [];
      run.pickedMarkets = data.picked_markets || [];
    } else {
      run.stage = "orient_done";
      run.pickedMarkets = data.picked_markets || [];
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
  if (e.dataTransfer.files.length) {
    // A file dropped on the final screen is a humanized draft to recheck, never a new upload: a new
    // upload would wipe the very run it is meant to be checked against.
    if (dropTarget() === "sop") runSopCheck(e.dataTransfer.files[0]);
    else if (dropTarget() === "recheck") runRecheck(e.dataTransfer.files[0]);
    else handleFile(e.dataTransfer.files[0]);
  }
});

const DROP_TEXT = {
  revamp: "Drop your .docx file anywhere to upload",
  sop: "Drop your final draft anywhere to check its SOP compliance",
  recheck: "Drop your humanized draft anywhere to recheck its SOPs",
};

function dropTarget() {
  if (sopOpen) return "sop";
  return canRecheck() ? "recheck" : "revamp";
}

function showDropOverlay() {
  document.getElementById("page-drop-text").textContent = DROP_TEXT[dropTarget()];
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
  state.clientPicks = {};
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

// ---------------------------------------------------------------- client paragraph picker
function sponsorCandidates() {
  if (!state.article) return [];
  return state.article.paragraphs.filter((p) => p.links.some((l) => l.isExternal));
}

// The client's own domain: 'shop.acme.com' and 'www.acme.com' are one client, 'acme.co.uk' keeps three labels (as clients.py does).
function registrableHost(url) {
  let host = "";
  try { host = new URL(url).hostname.toLowerCase().replace(/^www\./, ""); } catch { return String(url); }
  const parts = host.split(".");
  if (parts.length >= 3 && parts[parts.length - 1].length === 2 && ["co", "com", "org", "net", "ac", "gov", "edu"].includes(parts[parts.length - 2])) {
    return parts.slice(-3).join(".");
  }
  return parts.length >= 2 ? parts.slice(-2).join(".") : host;
}

// The article's outside links grouped by the client they belong to: [{host, anchors, paragraphs}].
function clientGroups() {
  const groups = new Map();
  for (const p of sponsorCandidates()) {
    for (const l of p.links.filter((x) => x.isExternal)) {
      const host = registrableHost(l.url);
      if (!groups.has(host)) groups.set(host, { host, anchors: [], paragraphs: [] });
      const g = groups.get(host);
      if (l.anchor && !g.anchors.includes(l.anchor)) g.anchors.push(l.anchor);
      if (!g.paragraphs.includes(p)) g.paragraphs.push(p);
    }
  }
  return [...groups.values()];
}

function clientPick(host) {
  if (!state.clientPicks[host]) state.clientPicks[host] = { on: false, paraIdx: null };
  return state.clientPicks[host];
}

function selectedClientHosts() {
  return clientGroups().filter((g) => clientPick(g.host).on && clientPick(g.host).paraIdx !== null).map((g) => g.host);
}

function selectedParagraphIndexes() {
  const out = [];
  for (const g of clientGroups()) {
    const pick = clientPick(g.host);
    if (pick.on && pick.paraIdx !== null && !out.includes(pick.paraIdx)) out.push(pick.paraIdx);
  }
  return out;
}

function restoreClientPicks(indexes, hosts) {
  state.clientPicks = {};
  for (const g of clientGroups()) {
    if ((hosts || []).length && !hosts.includes(g.host)) continue;
    const hit = g.paragraphs.find((p) => (indexes || []).includes(p.index));
    if (hit) state.clientPicks[g.host] = { on: true, paraIdx: hit.index };
  }
}

function renderSponsorCandidates() {
  const container = document.getElementById("sponsor-candidates");
  const groups = clientGroups();
  container.innerHTML = "";

  if (!groups.length) {
    container.innerHTML = `<p class="field-hint" style="color:var(--danger)">No paragraph has a hyperlink outside coherentmarketinsights.com. The agent needs this to know what to preserve.</p>`;
    document.getElementById("start-btn").disabled = true;
    return;
  }

  const runActive = state.run && !state.result;
  const several = groups.length > 1;
  groups.forEach((g) => {
    const pick = clientPick(g.host);
    // One client is unambiguous. With several outside domains the editor ticks the clients: some links are an author's own
    // site or a plain citation, and a silent default once locked in the wrong paragraph.
    if (!several) pick.on = true;
    if (pick.on && g.paragraphs.length === 1) pick.paraIdx = g.paragraphs[0].index;
    const wrap = document.createElement("div");
    wrap.className = "client-group" + (pick.on ? " on" : "");
    const head = document.createElement("label");
    head.className = "radio-option" + (pick.on ? " selected" : "") + (runActive ? " disabled" : "");
    const name = `<strong>${escapeHtml(g.host)}</strong> &middot; ${escapeHtml(g.anchors.join(", "))}`;
    head.innerHTML = several
      ? `<input type="checkbox" ${pick.on ? "checked" : ""} ${runActive ? "disabled" : ""}><span>${name}</span>`
      : `<span>${name}</span>`;
    if (several) {
      head.querySelector("input").addEventListener("change", (ev) => {
        pick.on = ev.target.checked;
        if (!pick.on) pick.paraIdx = null;
        renderSponsorCandidates();
      });
    }
    wrap.appendChild(head);
    if (pick.on && g.paragraphs.length > 1) {
      const sub = document.createElement("div");
      sub.className = "client-paragraphs";
      sub.innerHTML = `<p class="field-hint">Choose the paragraph that suits the post best:</p>`;
      g.paragraphs.forEach((p) => {
        const opt = document.createElement("label");
        opt.className = "radio-option" + (pick.paraIdx === p.index ? " selected" : "") + (runActive ? " disabled" : "");
        opt.innerHTML = `<input type="radio" name="client-${escapeHtml(g.host)}" ${pick.paraIdx === p.index ? "checked" : ""} ${runActive ? "disabled" : ""}>`
          + `<span>&para;${p.index + 1} &middot; ${escapeHtml(p.text.slice(0, 90))}&hellip;</span>`;
        opt.querySelector("input").addEventListener("change", () => { pick.paraIdx = p.index; renderSponsorCandidates(); });
        sub.appendChild(opt);
      });
      wrap.appendChild(sub);
    }
    container.appendChild(wrap);
  });

  const incomplete = groups.some((g) => clientPick(g.host).on && clientPick(g.host).paraIdx === null);
  const needsChoice = selectedParagraphIndexes().length === 0 || incomplete;
  const hint = document.getElementById("sponsor-choice-hint");
  if (hint) hint.hidden = !needsChoice;

  updateSponsorPreview();
  document.getElementById("start-btn").disabled = runActive || needsChoice;
  document.getElementById("sponsor-locked-note").hidden = !runActive;
}

function updateSponsorPreview() {
  const paras = selectedParagraphIndexes()
    .map((i) => (state.article ? state.article.paragraphs.find((p) => p.index === i) : null))
    .filter(Boolean);
  document.getElementById("sponsor-preview-text").textContent = paras.length
    ? paras.map((p) => p.text).join("\n\n")
    : "Select the client paragraph(s) above to preview them.";
}

// The first chosen client paragraph (the run's primary one); every chosen paragraph travels in selectedParagraphIndexes().
function currentSponsor() {
  const idx = selectedParagraphIndexes()[0];
  if (idx === undefined || !state.article) return null;
  return state.article.paragraphs.find((p) => p.index === idx) || null;
}

// What each client really offers (read from its own website), the notes the editor should read, and the client's original
// paragraph to compare the restated opening with.
function clientCheckHtml(clients) {
  return (clients || []).map((c) => {
    const b = c.brief || {};
    const links = (c.links || []).map((l) => `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.anchor)}</a>`).join(", ");
    const offer = b.offer ? escapeHtml(b.offer) : `<span class="option-caption">not available &mdash; the client's website could not be read</span>`;
    const notes = (c.notes || []).map((n) => `<p class="option-caption">${escapeHtml(n)}</p>`).join("");
    return `<div class="client-card">
      <p><strong>${escapeHtml(b.name || c.host)}</strong> &middot; ${escapeHtml(c.host)}</p>
      <p class="option-caption">Linked words: ${links}</p>
      <p>What the client offers (from its website): ${offer}</p>${notes}
      <details><summary>Original client paragraph (&para;${(c.paragraph_index || 0) + 1})</summary><p>${escapeHtml(c.paragraph || "")}</p></details>
    </div>`;
  }).join("");
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
  state.run = { stage: "pending", sponsor, pickedCmi: null, pickedMarkets: [], reportUrls: [""] };
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
      sponsor_indexes: selectedParagraphIndexes(),
      client_hosts: selectedClientHosts(),
    });
    state.run.stage = "orient_done";
    state.run.token = data.token || data.run_token;
    state.run.orient = data.orient;
    state.run.candidates = data.candidates;
    state.run.warnings = data.warnings || [];
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

// Step 1 -> Step 2. The editor chose any of: ONE CMI report (radio), any number of the agent's suggested market names
// (checkboxes) and report links of their own. Suggested markets need competitor reports found first, so those go through
// a picker (stage "reports_found", shown in Step 2); everything else goes straight to the evidence search.
document.getElementById("step1-add-url-btn").addEventListener("click", () => {
  if (!state.run || !Array.isArray(state.run.reportUrls)) return;
  if (state.run.reportUrls.length >= MAX_REPORT_URLS) return;
  state.run.reportUrls.push("");
  renderStep1UrlInputs();
});

function clearCmiPick() {
  if (!state.run) return;
  state.run.pickedCmi = null;
  renderStep1Panel();
}
document.getElementById("step1-cmi-clear").addEventListener("click", clearCmiPick);
document.getElementById("step1-links-unlock").addEventListener("click", clearCmiPick);

document.getElementById("step1-agent-clear").addEventListener("click", () => {
  if (!state.run) return;
  state.run.pickedMarkets = [];
  renderStep1Panel();
});

function step1HasSelection() {
  return !!state.run && (!!state.run.pickedCmi || (state.run.pickedMarkets || []).length > 0 || step1PickedUrls().length > 0);
}

document.getElementById("step1-confirm-btn").addEventListener("click", async () => {
  if (_runBusy() || !step1HasSelection()) return;
  if ((state.run.pickedMarkets || []).length) {
    state.run.stage = "finding_pending";
    setNav("step2");
    render();
    try {
      const data = await apiPost("/step2/find_reports", { run_token: state.run.token, markets: state.run.pickedMarkets });
      state.run.foundReports = data.reports || [];
      state.run.pickedFound = [];
      state.run.earlyState = "running";
      state.run.earlyHits = [];
      state.run.stage = "reports_found";
    } catch (e) {
      showError("Step 2 (find competitor reports)", e);
      state.run.stage = "orient_done";
      setNav("step1");
    }
    saveRunPointer();
    render();
    return;
  }
  await runStep2();
});

// The evidence search itself. Reports sent: the CMI pick (0 or 1), the competitor reports ticked in the picker plus the
// links the editor pasted, and the market each found report was found for (its figures are checked against that market).
async function runStep2() {
  const cmi = state.run.pickedCmi;
  const found = state.run.pickedFound || [];
  const urlMarkets = {};
  (state.run.foundReports || []).forEach((r) => { if (found.includes(r.url)) urlMarkets[r.url] = r.market; });
  const body = {
    run_token: state.run.token,
    report_ids: cmi ? [cmi.news_id] : [],
    markets: state.run.pickedMarkets || [],
    report_urls: [...found, ...step1PickedUrls()],
    url_markets: urlMarkets,
  };
  if (cmi) body.report_id = cmi.news_id;
  const cameFromPicker = (state.run.pickedMarkets || []).length > 0;
  state.run.stage = "search_pending";
  setNav("step2");
  render();
  try {
    const data = await apiPost("/step2", body);
    state.run.stage = "search_done";
    state.run.chosenCandidate = cmi || null;
    state.run.report = data.report;
    state.run.hits = data.hits;
    state.run.claimHits = data.claim_hits || {};
    state.run.timings = data.timings || null;
    state.run.longtails = data.long_tail_keywords || [];
    state.run.factSheet = data.fact_sheet || null;
    state.run.demand = data.demand || null;
    state.run.publisher = data.publisher || null;
    state.run.reportMode = data.report_mode || null;
    state.run.warnings = data.warnings || [];
    state.run.clients = data.clients || [];
  } catch (e) {
    showError("Step 2 (fetch market evidence)", e);
    state.run.stage = cameFromPicker ? "reports_found" : "orient_done";
    if (!cameFromPicker) setNav("step1");
  }
  saveRunPointer();
  render();
}

document.getElementById("step2-pick-confirm-btn").addEventListener("click", async () => {
  if (_runBusy() || !state.run || state.run.stage !== "reports_found") return;
  await runStep2();
});

document.getElementById("step2-pick-back-btn").addEventListener("click", () => {
  if (_runBusy() || !state.run || state.run.stage !== "reports_found") return;
  state.run.stage = "orient_done";
  setNav("step1");
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
    state.run.clients = data.clients || state.run.clients;
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
    state.run.clients = data.clients || state.run.clients;
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
    state.run.clients = written.clients || state.run.clients;
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
    step1: atLeast(["finding_pending", "reports_found", "search_pending", "search_done", "select_pending", "select_done", "write_pending", "write_done"]),
    step2: atLeast(["search_done", "select_pending", "select_done", "write_pending", "write_done"]),
    step3: atLeast(["select_done", "write_pending", "write_done"]),
    step4: stage === "write_done" || !!state.result,
    final: !!state.result,
  };
}

// The SOP Check tool sits outside the revamp steps: opening it never touches the run, and any
// step navigation (including a new upload) closes it again.
let sopOpen = false;

function setNav(target) {
  sopOpen = false;
  if (state.nav !== target) window.scrollTo({ top: 0, behavior: "smooth" });
  state.nav = target;
  closeSidebar();
  saveRunPointer();
}

document.querySelectorAll(".step-item:not(.sop-item)").forEach((btn) => {
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
    step2: { unlocked: done.step1, done: done.step2, pending: stage === "search_pending" || stage === "finding_pending" },
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
      pending: "Step 1 · Choose the Market", orient_done: "Step 1 · Choose the Market",
      finding_pending: "Step 2 · Reports & Evidence", reports_found: "Step 2 · Reports & Evidence",
      search_pending: "Step 2 · Reports & Evidence", search_done: "Step 2 · Reports & Evidence",
      select_pending: "Step 3 · Pick the Facts", select_done: "Step 3 · Pick the Facts",
      write_pending: "Step 4 · Write the Article", write_done: "Step 4 · Write the Article",
      finalize_pending: "Final · Check & Download",
    }[state.run.stage] || "Step 1 · Choose the Market";
    document.getElementById("run-active-text").textContent = `Run in progress — see ${stageLabel}`;
  }
}

// ---------------------------------------------------------------- step 1 helpers (report pick + own links)
// At most this many "own link" inputs exist; the Add button disappears once reached.
const MAX_REPORT_URLS = 6;

// Non-empty trimmed own links — exactly what gets sent to /step2 as report_urls.
function step1PickedUrls() {
  if (state.run.pickedCmi) return [];      // a CMI report is used on its own: pasted links are not used with it
  // A pasted link on CMI's own site is sent like any other; the backend treats it as the CMI report (figures not adjusted).
  return (state.run.reportUrls || []).map((u) => String(u || "").trim()).filter(Boolean);
}

// What each tier means, shown with its group so the editor sees why a market is offered.
const TIER_INFO = {
  1: { label: "Tier 1", name: "Direct match", note: "The market's core subject is your topic, or something your article names directly." },
  2: { label: "Tier 2", name: "Adjacent match", note: "Directly upstream, downstream, or a component of your topic." },
  3: { label: "Tier 3", name: "Shared driver", note: "A different market driven by the same demand, rule or end-use industry." },
  0: { label: "Matches", name: "Closest matches", note: "Not graded; shown in order of match strength." },
};

// Groups [{tier, items}] -> HTML-free DOM: one block per non-empty tier with a coloured badge and its description.
function renderTierGroups(container, groups, renderItem, emptyText) {
  container.innerHTML = "";
  const filled = groups.filter((g) => g.items.length);
  if (!filled.length) {
    container.innerHTML = `<p class="market-empty">${escapeHtml(emptyText)}</p>`;
    return;
  }
  filled.forEach((g) => {
    const info = TIER_INFO[g.tier] || TIER_INFO[0];
    const block = document.createElement("div");
    block.className = "tier-group";
    block.innerHTML = `
      <div class="tier-head">
        <span class="tier-badge tier-${g.tier}">${escapeHtml(info.label)}</span>
        <span class="tier-name">${escapeHtml(info.name)}</span>
      </div>
      <p class="tier-note">${escapeHtml(info.note)}</p>
      <div class="tier-items"></div>`;
    const list = block.querySelector(".tier-items");
    g.items.forEach((item) => list.appendChild(renderItem(item)));
    container.appendChild(block);
  });
}

function suggestedGroups() {
  const s = ((state.run.orient || {}).suggested_markets) || {};
  return [1, 2, 3].map((t) => ({ tier: t, items: (s[`tier${t}`] || []).filter(Boolean) }));
}

function cmiGroups() {
  const cands = (state.run.candidates || []).slice(0, 25);
  const groups = [1, 2, 3].map((t) => ({ tier: t, items: cands.filter((c) => c.tier === t) }));
  groups.push({ tier: 0, items: cands.filter((c) => ![1, 2, 3].includes(c.tier)) });
  return groups;
}

// Confirm stays disabled until something is chosen; the label says what will happen next.
function updateStep1ConfirmLabel() {
  const btn = document.getElementById("step1-confirm-btn");
  const label = document.getElementById("step1-confirm-label");
  const hint = document.getElementById("step1-none-selected-hint");
  if (!btn || !label || !hint || !state.run || state.run.stage !== "orient_done") return;
  const has = step1HasSelection();
  btn.disabled = !has;
  hint.hidden = has;
  label.textContent = (state.run.pickedMarkets || []).length
    ? "Find competitor reports and continue to Step 2"
    : state.run.pickedCmi ? "Continue to Step 2 with this CMI report" : "Continue to Step 2";
  document.getElementById("step1-cmi-clear").hidden = !state.run.pickedCmi;
  document.getElementById("step1-agent-clear").hidden = !(state.run.pickedMarkets || []).length;
  document.getElementById("step1-links-locked").hidden = !state.run.pickedCmi;

  // With a CMI report chosen, the report stands alone: the link boxes are switched off and say why.
  const cmiOnly = !!state.run.pickedCmi;
  const links = document.getElementById("step1-links-section");
  links.classList.toggle("is-off", cmiOnly);
  links.querySelectorAll("input, button").forEach((el) => { el.disabled = cmiOnly; });
  document.getElementById("step1-links-hint").textContent = cmiOnly
    ? "Not used: a CMI report is chosen and is used on its own. Clear the CMI choice to add report links."
    : "Paste any report pages you already have. Checking a link is optional: it only gives early feedback on whether the link looks usable and never blocks anything.";
  renderSelectionSummary();
}

// A live recap of what is chosen, so the editor can see the whole decision before continuing.
function renderSelectionSummary() {
  const el = document.getElementById("step1-selection-summary");
  if (!el || !state.run) return;
  const chips = [];
  (state.run.pickedMarkets || []).forEach((m) => chips.push({ kind: "Market", text: m }));
  if (state.run.pickedCmi) chips.push({ kind: "CMI report", text: state.run.pickedCmi.keyword });
  step1PickedUrls().forEach((u) => chips.push({ kind: "Link", text: u.replace(/^https?:\/\/(www\.)?/, "") }));
  el.hidden = !chips.length;
  el.innerHTML = chips.length
    ? `<span class="selection-summary-label">Selected</span>` + chips.map((c) =>
        `<span class="sel-chip"><span class="sel-chip-kind">${escapeHtml(c.kind)}</span>${escapeHtml(clip(c.text, 46))}</span>`).join("")
    : "";
}

// Search-engine titles can be very long; the full title stays in the link's tooltip.
function clip(text, max) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1).replace(/\s+\S*$/, "") + "\u2026" : s;
}

// Own-link rows are (re)built from state.run.reportUrls, so a re-render never loses what was
// typed; typing itself only writes back to state and never re-renders (that would drop focus).
function renderStep1UrlInputs() {
  const wrap = document.getElementById("step1-report-urls");
  const addBtn = document.getElementById("step1-add-url-btn");
  if (!wrap || !addBtn || !state.run || state.run.stage !== "orient_done") return;
  if (!Array.isArray(state.run.reportUrls)) state.run.reportUrls = [""];

  wrap.innerHTML = "";
  state.run.reportUrls.forEach((url, i) => {
    const entry = document.createElement("div");
    entry.className = "report-url-entry";
    entry.innerHTML = `
      <div class="report-url-row">
        <input type="text" class="text-input report-url-input" placeholder="Paste a report link (https://...)" value="${escapeHtml(url || "")}">
        <button type="button" class="btn btn-secondary btn-small report-url-check">Check</button>
        <button type="button" class="report-url-remove" title="Remove this link"${i === 0 ? " hidden" : ""}>&times;</button>
      </div>
      <p class="url-check-result" hidden></p>
    `;
    entry.querySelector("input").addEventListener("input", (e) => {
      state.run.reportUrls[i] = e.target.value;
      updateStep1ConfirmLabel();
    });
    entry.querySelector(".report-url-check").addEventListener("click", () => checkReportUrl(entry));
    entry.querySelector(".report-url-remove").addEventListener("click", () => {
      state.run.reportUrls.splice(i, 1);
      if (!state.run.reportUrls.length) state.run.reportUrls = [""];   // one input remains as the minimum
      renderStep1UrlInputs();
      updateStep1ConfirmLabel();
    });
    wrap.appendChild(entry);
  });
  addBtn.hidden = state.run.reportUrls.length >= MAX_REPORT_URLS;
}

// Optional early feedback on one own link — asks the backend to look at it. Whatever the check
// says, it never gates anything: Confirm stays enabled and the decision stays with the editor.
async function checkReportUrl(entry) {
  if (!state.run || state.run.stage !== "orient_done") return;
  const resultEl = entry.querySelector(".url-check-result");
  const url = String(entry.querySelector("input").value || "").trim();
  resultEl.hidden = false;
  if (!url) {
    resultEl.className = "url-check-result";
    resultEl.textContent = "Paste a link first, then check it.";
    return;
  }
  resultEl.className = "url-check-result";
  resultEl.innerHTML = `<i data-lucide="loader-circle" class="spin"></i> Checking this link...`;
  iconify();
  try {
    const data = await apiPost("/check_report_url", { run_token: state.run.token, url, market: (state.run.pickedMarkets || [])[0] || "" });
    resultEl.className = `url-check-result ${data.ok ? "ok" : "fail"}`;
    resultEl.textContent = data.message || (data.ok ? "This link looks usable." : "This link could not be checked.");
  } catch (e) {
    resultEl.className = "url-check-result fail";
    resultEl.textContent = e.message || `The check could not run just now. You can still continue.`;
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

  const claims = orient.claims_to_back || [];
  document.getElementById("step1-claims").innerHTML = claims.length
    ? claims.map((c) => `<li><strong>${escapeHtml(c.id)}</strong> ${escapeHtml(c.claim)}`
        + (c.why ? ` <span class="option-caption">${escapeHtml(c.why)}</span>` : "") + `</li>`).join("")
    : `<li class="empty-note">No unsourced claims were found in the article, so nothing needs outside backing.</li>`;

  const isOrientDone = state.run.stage === "orient_done";
  confirmBlock.hidden = !isOrientDone;
  doneBlock.hidden = isOrientDone;

  if (isOrientDone) {
    if (!Array.isArray(state.run.pickedMarkets)) state.run.pickedMarkets = [];

    // Agent-suggested markets: radio buttons, ONE market at most, none pre-selected. Picking one means "find competitor
    // reports for it" (the reports themselves are chosen in Step 2). It excludes a CMI pick.
    renderTierGroups(document.getElementById("step1-suggested"), suggestedGroups(), (name) => {
      const label = document.createElement("label");
      const on = state.run.pickedMarkets.includes(name);
      label.className = "market-option" + (on ? " selected" : "");
      label.innerHTML = `<input type="radio" name="agent-pick" value="${escapeHtml(name)}" ${on ? "checked" : ""}><span class="market-name">${escapeHtml(name)}</span>`;
      label.querySelector("input").addEventListener("change", () => {
        state.run.pickedMarkets = [name];
        if (state.run.pickedCmi) {
          state.run.pickedCmi = null;
          document.querySelectorAll("#step1-cmi .market-option").forEach((el) => {
            el.classList.remove("selected");
            el.querySelector("input").checked = false;
          });
        }
        document.querySelectorAll("#step1-suggested .market-option").forEach((el) => el.classList.remove("selected"));
        label.classList.add("selected");
        updateStep1ConfirmLabel();
      });
      return label;
    }, "The agent has no market names to suggest for this article. Pick a CMI market or add a report link.");

    // CMI markets: radio buttons, one report at most (its link goes into the article).
    renderTierGroups(document.getElementById("step1-cmi"), cmiGroups(), (c) => {
      const label = document.createElement("label");
      const on = !!state.run.pickedCmi && state.run.pickedCmi.news_id === c.news_id;
      label.className = "market-option" + (on ? " selected" : "");
      label.innerHTML = `<input type="radio" name="cmi-pick" value="${c.news_id}" ${on ? "checked" : ""}>
        <span class="market-name">${escapeHtml(c.keyword)}<span class="option-caption">report id ${c.news_id} &middot; <a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">open report</a></span></span>`;
      label.querySelector("input").addEventListener("change", () => {
        state.run.pickedCmi = c;
        state.run.pickedMarkets = [];          // a CMI report is used on its own
        document.querySelectorAll("#step1-suggested .market-option").forEach((el) => {
          el.classList.remove("selected");
          el.querySelector("input").checked = false;
        });
        document.querySelectorAll("#step1-cmi .market-option").forEach((el) => el.classList.remove("selected"));
        label.classList.add("selected");
        updateStep1ConfirmLabel();
      });
      return label;
    }, "No CMI report fits this article closely enough to suggest. Tick an agent-suggested market or add a report link.");
    // Step 1's own warning (e.g. no CMI report carries the article's own market name), shown above the report list.
    (state.run.warnings || []).slice().reverse().forEach((w) => {
      const note = document.createElement("p");
      note.className = "market-empty";
      note.textContent = w;
      document.getElementById("step1-cmi").prepend(note);
    });

    renderStep1UrlInputs();
    updateStep1ConfirmLabel();
  } else {
    const rep = state.run.report;
    const link = document.getElementById("step1-confirmed-link");
    if (rep) {
      link.textContent = rep.name;
      if (rep.url) link.href = rep.url; else link.removeAttribute("href");
    }

    const sources = (state.run.factSheet && state.run.factSheet.sources) || [];
    document.getElementById("step1-sources-block").hidden = !sources.length;
    document.getElementById("step1-sources").innerHTML = sources.map((s) => {
      const kind = s.kind === "cmi" ? "CMI report" : "Competitor report (figures adjusted)";
      const name = s.url ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)}</a>` : escapeHtml(s.name);
      return `<li>${name} <span class="option-caption">${kind}</span></li>`;
    }).join("");

    const all = document.getElementById("step1-all-candidates");
    all.innerHTML = (state.run.candidates || []).slice(0, 10)
      .map((c) => `<li><a href="${c.url}" target="_blank" rel="noopener">${escapeHtml(c.keyword)}</a></li>`).join("");
  }
}

// ---------------------------------------------------------------- render: step2 competitor-report picker
function renderStep2Pick() {
  const found = state.run.foundReports || [];
  if (!Array.isArray(state.run.pickedFound)) state.run.pickedFound = [];
  const list = document.getElementById("step2-found-list");
  list.innerHTML = "";
  const markets = [...new Set([...(state.run.pickedMarkets || []), ...found.map((r) => r.market)])];
  markets.forEach((m) => {
    const rows = found.filter((r) => r.market === m);
    const block = document.createElement("div");
    block.className = "tier-group";
    block.innerHTML = `<div class="tier-head"><span class="tier-name">${escapeHtml(m)}</span></div><div class="tier-items"></div>`;
    const items = block.querySelector(".tier-items");
    if (!rows.length) {
      items.innerHTML = `<p class="market-empty">No competitor report was found for this market. You can go back and add a link of your own, or let the agent search again by continuing.</p>`;
    }
    rows.forEach((r) => {
      const label = document.createElement("label");
      const on = state.run.pickedFound.includes(r.url);
      label.className = "market-option" + (on ? " selected" : "");
      label.innerHTML = `<input type="checkbox" value="${escapeHtml(r.url)}" ${on ? "checked" : ""}>
        <span class="market-name"><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener" title="${escapeHtml(r.title || r.host)}">${escapeHtml(clip(r.title || r.host, 110))}</a>
        <span class="option-caption"><span class="host-chip">${escapeHtml(r.host)}</span>${r.snippet ? escapeHtml(clip(r.snippet, 150)) : ""}</span></span>`;
      label.querySelector("input").addEventListener("change", (e) => {
        state.run.pickedFound = e.target.checked
          ? [...state.run.pickedFound, r.url]
          : state.run.pickedFound.filter((u) => u !== r.url);
        label.classList.toggle("selected", e.target.checked);
        updateStep2PickLabel();
      });
      items.appendChild(label);
    });
    list.appendChild(block);
  });
  updateStep2PickLabel();
}

function updateStep2PickLabel() {
  const n = (state.run.pickedFound || []).length;
  document.getElementById("step2-pick-confirm-label").textContent = n
    ? `Continue with ${n} selected report${n === 1 ? "" : "s"}`
    : "Continue and let the agent choose the reports";
  document.getElementById("step2-pick-hint").textContent = n
    ? "Only the ticked reports are read for figures."
    : "Nothing ticked: the agent reads the best-matching reports it found for each market.";
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
    return `<li class="factsheet-gap">${label}: <strong>gap</strong> <span class="option-caption">no figure could be confirmed from the report page —` +
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
  // Where the non-headline facts came from: a competitor report is never labelled as CMI's.
  const base = fs.report_mode === "external" ? "competitor report (figures adjusted)"
    : fs.report_mode === "mixed" ? "CMI and competitor reports" : "CMI report";
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
    const figs = seg.figures || {};
    const names = (seg.items || []).map((n) => figs[n] ? `${n} (${figs[n].figure})` : n).join(", ");
    const lead = seg.dominant
      ? ` <span class="option-caption">leader: ${escapeHtml(seg.dominant.name)} (${seg.dominant.basis === "stated" ? "stated by the source" : "worked out from its figures"})</span>`
      : "";
    out.push(`<li>Segmentation — ${escapeHtml(seg.type)}: <strong>${escapeHtml(names)}</strong>${lead} ` +
      `<span class="option-caption">source: ${base}</span></li>`);
  }
  const regions = fs.regions || {};
  if (regions.leader || regions.fastest) {
    out.push(`<li>Regions — leading: ${escapeHtml(regions.leader ? regions.leader.name : "(gap)")}${hist(regions.leader)}` +
      ` · fastest: ${escapeHtml(regions.fastest ? regions.fastest.name : "(gap)")}${hist(regions.fastest)}` +
      ` <span class="option-caption">source: ${base}</span></li>`);
  }
  for (const it of fs.us || []) {
    out.push(`<li>U.S.: <strong>${escapeHtml(it.text)}</strong>${hist(it)} <span class="option-caption">source: ${base}</span></li>`);
  }
  const extra = statementList("Drivers", fs.drivers)
    + statementList("Opportunities", fs.opportunities);
  out.push(extra);
  if ((fs.players || []).length) {
    out.push(`<li>Key players: <strong>${escapeHtml(fs.players.join(", "))}</strong> <span class="option-caption">source: ${base}</span></li>`);
  }
  // The audit of every figure moved by 3-5%: the source's own value beside the value the article uses.
  const audit = fs.variance || [];
  if (audit.length) {
    out.push(`<li class="factsheet-audit"><strong>Adjusted figures (${audit.length})</strong> <span class="option-caption">the source's value, then the value the article uses</span><ul class="plain-list">` +
      audit.map((a) => `<li>${escapeHtml(a.field)}: ${escapeHtml(a.original)} &rarr; <strong>${escapeHtml(a.shown)}</strong> ` +
        `<span class="option-caption">${a.url ? `<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${escapeHtml(a.source || a.url)}</a>` : escapeHtml(a.source)}</span></li>`).join("") +
      `</ul></li>`);
  }
  return out.filter(Boolean).join("");
}

// One evidence result per row: title link, then kind, date and source, then a short snippet.
function hitListHtml(hits) {
  return hits.map((h) => {
    let host = "web";
    try { host = new URL(h.url).hostname.replace(/^www\./, ""); } catch { /* keep "web" */ }
    const kind = h.is_news ? "News" : "Web";
    const date = h.date ? h.date.slice(0, 10) : "undated";
    const snippet = (h.snippet || "").trim();
    return `<li><a href="${escapeHtml(h.url)}" target="_blank" rel="noopener">${escapeHtml(h.title)}</a>`
      + ` <span class="option-caption">${kind} &middot; ${escapeHtml(date)} &middot; ${escapeHtml(h.source || host)}</span>`
      + (snippet ? `<span class="option-caption">${escapeHtml(snippet.length > 220 ? snippet.slice(0, 220) + "…" : snippet)}</span>` : "")
      + `</li>`;
  }).join("");
}

// The evidence search for the picked market starts when its competitor reports are being found; this shows it beside
// the reports as it arrives. Step 2 reuses it once the editor continues.
let earlyPoll = null;
function stopEarlyPoll() {
  if (earlyPoll) { clearTimeout(earlyPoll); earlyPoll = null; }
}

function renderEarlyEvidence() {
  const run = state.run;
  const status = document.getElementById("step2-early-status");
  const skeleton = document.getElementById("step2-early-skeleton");
  const list = document.getElementById("step2-early-hits");
  const st = run.earlyState || "running";
  const hits = run.earlyHits || [];
  skeleton.hidden = st !== "running";
  list.innerHTML = st === "done" && hits.length ? hitListHtml(hits) : "";
  status.textContent = st === "running"
    ? "This search started on its own and keeps going while you choose the reports. You do not need to wait for it\u2026"
    : st === "done"
      ? (hits.length
          ? `${hits.length} supporting web result(s) found for this market. They are kept: once you continue, only what the ticked reports add (their segments and players) is searched on top.`
          : "No supporting results survived filtering yet. Once you continue, the agent searches again using the ticked reports' segments and players.")
      : "This search did not finish. Once you continue, the agent searches again.";
}

function pollEarlyEvidence() {
  stopEarlyPoll();
  const run = state.run;
  if (!run || run.stage !== "reports_found" || run.earlyState === "done" || run.earlyState === "failed") return;
  earlyPoll = setTimeout(async () => {
    earlyPoll = null;
    if (!state.run || state.run !== run || run.stage !== "reports_found") return;
    try {
      const data = await fetch(`${API_BASE}/step2/early_evidence?run_token=${encodeURIComponent(run.token)}`).then((r) => r.json());
      run.earlyState = data.state === "none" ? "failed" : data.state;
      run.earlyHits = data.hits || [];
    } catch { /* keep polling */ }
    if (state.run === run && run.stage === "reports_found") renderEarlyEvidence();
    pollEarlyEvidence();
  }, 3000);
}

// Step 2 is one path: find the reports (only for a market the agent suggested), you choose, then the agent reads
// them and gathers the evidence. The tracker shows where the editor is on that path; the live list below it shows
// what is running during the last part, read from GET /step2/progress.
function renderStep2Tracker() {
  const stage = state.run.stage;
  const viaPicker = (state.run.pickedMarkets || []).length > 0;
  const steps = viaPicker
    ? [["finding_pending", "Find reports"], ["reports_found", "Choose reports"], ["search_pending", "Read the reports and gather evidence"]]
    : [["search_pending", "Read the report and gather evidence"]];
  const order = ["finding_pending", "reports_found", "search_pending"];
  const at = order.indexOf(stage);   // -1 once Step 2 has finished: everything is done
  document.getElementById("step2-tracker").innerHTML = steps.map(([key, label], i) => {
    const pos = order.indexOf(key);
    const cls = at === -1 || pos < at ? "done" : pos === at ? "active" : "";
    return `<li class="${cls}"><span class="flow-num">${cls === "done" ? "&#10003;" : i + 1}</span>${label}</li>`;
  }).join("");
  document.getElementById("step2-tracker").hidden = false;
}

const STEP2_STAGE_LABELS = {
  "Fetch CMI reports and fact sheets": "Reading the CMI report",
  "Outside reports": "Reading the reports you ticked",
  "Headline figures from web": "Looking up the market's headline figures",
  "Evidence search begun while you chose the reports": "Collecting the evidence search that started while you chose",
  "Keyword research": "Finding what people type into search",
  "Search demand": "Finding the questions people ask most",
  "Client check": "Reading each client's own site",
  "Publisher points": "Reading the article's own points",
  "Plan market search queries": "Planning the evidence searches",
  "Market web search": "Searching the web for evidence",
  "Market web search (segments and players)": "Searching the web for the reports' segments and players",
};

let progressPoll = null;
function stopProgressPoll() {
  if (progressPoll) { clearTimeout(progressPoll); progressPoll = null; }
  const live = document.getElementById("step2-live");
  if (live) live.hidden = true;
}

function renderStep2Progress(p) {
  const row = (name, cls) => `<li class="${cls}">${cls === "done" ? "&#10003; " : "&hellip; "}${escapeHtml(STEP2_STAGE_LABELS[name] || name)}</li>`;
  const live = document.getElementById("step2-live");
  live.innerHTML = (p.done || []).map((n) => row(n, "done")).concat((p.active || []).map((n) => row(n, "active"))).join("");
  live.hidden = !live.innerHTML;
}

function pollStep2Progress() {
  if (progressPoll) return;   // already polling: a re-render must not stack timers
  const run = state.run;
  const tick = async () => {
    progressPoll = null;
    if (!state.run || state.run !== run || run.stage !== "search_pending") return;
    try {
      const data = await fetch(`${API_BASE}/step2/progress?run_token=${encodeURIComponent(run.token)}`).then((r) => r.json());
      if (state.run === run && run.stage === "search_pending") renderStep2Progress(data);
    } catch { /* keep polling */ }
    if (state.run === run && run.stage === "search_pending") progressPoll = setTimeout(tick, 2000);
  };
  progressPoll = setTimeout(tick, 1000);
}

function renderStep2Panel() {
  const spinner = document.getElementById("step2-spinner");
  const content = document.getElementById("step2-content");
  const pickBlock = document.getElementById("step2-pick-block");
  if (!state.run || !["finding_pending", "reports_found", "search_pending", "search_done", "select_pending", "select_done", "write_pending", "write_done", "finalized"].includes(state.run.stage)) {
    spinner.hidden = true; content.hidden = true; pickBlock.hidden = true;
    document.getElementById("step2-skeleton").hidden = true;
    document.getElementById("step2-tracker").hidden = true;
    document.getElementById("step2-live").hidden = true;
    return;
  }

  const finding = state.run.stage === "finding_pending";
  const picking = state.run.stage === "reports_found";
  const reading = state.run.stage === "search_pending";
  const pending = reading || finding;
  document.getElementById("step2-spinner-text").textContent = finding
    ? "Finding competitor reports for the market you picked…"
    : "Reading the reports and gathering the evidence (this can take a little while)…";
  spinner.hidden = !pending;
  document.getElementById("step2-skeleton").hidden = !finding;
  pickBlock.hidden = !picking;
  renderStep2Tracker();
  if (picking) { renderStep2Pick(); renderEarlyEvidence(); pollEarlyEvidence(); } else stopEarlyPoll();
  if (reading) pollStep2Progress(); else stopProgressPoll();
  content.hidden = pending || picking;
  if (pending || picking) return;

  // Report-source warnings (e.g. when the agent had to find the report itself on the internet):
  // the very first thing on this screen, above the confirmed report and the fact sheet, so the
  // editor reads them before any figure.
  const warnings = state.run.warnings || [];
  const warnBox = document.getElementById("step2-warnings");
  warnBox.hidden = warnings.length === 0;
  document.getElementById("step2-warnings-list").innerHTML =
    warnings.map((w) => `<p>${escapeHtml(w)}</p>`).join("");
  const clientList = state.run.clients || [];
  document.getElementById("step2-clients-card").hidden = clientList.length === 0;
  document.getElementById("step2-clients-list").innerHTML = clientCheckHtml(clientList);

  const report = state.run.report;
  document.getElementById("step2-report-label").textContent =
    state.run.reportMode && state.run.reportMode !== "cmi" ? "Market (no CMI report)" : "Confirmed CMI report";
  const link = document.getElementById("step2-report-link");
  if (report) {
    link.textContent = report.name;
    if (report.url) link.href = report.url; else link.removeAttribute("href");
  }

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
    ? hitListHtml(hits)
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

  // How the keywords were found: the real phrases people type, and the keyword forms in the order they will be offered to the planner.
  const kw = (state.run.demand && state.run.demand.keyword_research) || null;
  document.getElementById("step2-keywords-block").hidden = !kw;
  if (kw) {
    const sourceLabel = { search: "typed in searches", "ranking pages": "used by the pages that rank", standard: "standard keyword" };
    document.getElementById("step2-keywords-method").textContent = kw.method || "";
    document.getElementById("step2-keywords-pool").innerHTML = (kw.pool || []).slice(0, 10).map((r) =>
      `<li><strong>${escapeHtml(r.key)}</strong> <span class="option-caption">${escapeHtml(sourceLabel[r.source] || r.source)} &middot; fits the ${escapeHtml(r.section.replace("_", " "))} section</span></li>`).join("");
    const phrases = (kw.suggestions || []).slice(0, 12);
    document.getElementById("step2-keywords-phrases").innerHTML = phrases.length
      ? phrases.map((s) => `<li>${escapeHtml(s.query)} <span class="option-caption">${(s.engines || []).length > 1 ? "both Google and Bing" : escapeHtml(((s.engines || [])[0] || "search").replace(/^./, (c) => c.toUpperCase()))}</span></li>`).join("")
      : `<li class="empty-note">The search boxes returned nothing for this market, so the standard keyword forms are used.</li>`;
  }

  const msqs = (state.run.demand && state.run.demand.questions) || [];
  document.getElementById("step2-msqs-block").hidden = msqs.length === 0;
  const answerableLabel = { size: "size", growth: "growth", trends: "trend", drivers: "drivers",
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
  // With no CMI report the figures come from competitor reports (moved a few percent) and the article names none of them.
  const outside = !!state.run.reportMode && state.run.reportMode !== "cmi";
  document.getElementById("step3-report-label").textContent = outside ? "Market (no CMI report)" : "CMI report used by the writer";
  document.getElementById("step3-report-hint").textContent = outside
    ? "The writer takes market sizing, segmentation and trend language from the competitor reports you chose (their figures are adjusted by a few percent). The article names no report and links none."
    : "The writer draws market sizing, segmentation and trend language directly from this report.";
  const link = document.getElementById("step3-report-link");
  if (report) {
    link.textContent = report.name;
    if (report.url) link.href = report.url; else link.removeAttribute("href");
  }
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
  const step4Clients = state.run.clients || [];
  document.getElementById("step4-clients-card").hidden = step4Clients.length === 0;
  document.getElementById("step4-clients-list").innerHTML = clientCheckHtml(step4Clients);

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
// A trend's own heading travels as a paragraph starting with "### " (the backend's subheads.SUBHEAD_PREFIX).
const SUBHEAD_PREFIX = "### ";
function _renderParagraph(segments, renderInline) {
  const first = (segments && segments[0] && segments[0].text) || "";
  if (first.startsWith(SUBHEAD_PREFIX)) {
    const text = segments.map((s) => s.text).join("").slice(SUBHEAD_PREFIX.length);
    return `<h5 class="trend-head">${escapeHtml(text)}</h5>`;
  }
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

// ---------------------------------------------------------------- shared result rendering
function faqHtml(faq) {
  return faq.map((qa) => `
    <div class="faq-item">
      <p class="faq-question"><strong>${escapeHtml(qa.question)}</strong>${qa.intent
        ? `<span class="faq-intent" title="Every FAQ entry here is a Most Searched Question from the search-demand pass, answered from the article's own facts.">MSQ &middot; ${escapeHtml(qa.intent)}</span>` : ""}</p>
      <p class="faq-answer">${escapeHtml(qa.answer)}</p>
    </div>
  `).join("");
}

// The legend's count text for one result's keyword_counts. Tertiary (related) terms exist only on the
// market-first chain, so that row stays hidden when no terms were ever sent.
function keywordLegend(result) {
  const counts = result.keyword_counts || {};
  const sum = (o) => Object.values(o || {}).reduce((total, n) => total + n, 0);
  const tertiaryTerms = result.tertiary_terms || [];
  const longtails = result.long_tail_keywords || [];
  return {
    primary: `(${counts.primary || 0}x)`,
    secondary: `(${sum(counts.secondary)}x, ${Object.keys(counts.secondary || {}).length}/${(result.secondary_keywords || []).length} used)`,
    tertiary: tertiaryTerms.length ? `(${sum(counts.tertiary)}x, ${Object.keys(counts.tertiary || {}).length}/${tertiaryTerms.length} used)` : "",
    longtail: `(${sum(counts.longtail)}x, ${Object.keys(counts.longtail || {}).length}/${longtails.length} used)`,
    hasTertiary: tertiaryTerms.length > 0,
    hasLongtail: longtails.length > 0,
  };
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
  if (recheckEls.forResult !== result) { // a new draft makes an earlier recheck stale
    recheckEls.forResult = result;
    recheckEls.result.hidden = true;
    recheckEls.fileName.textContent = "";
  }
  const passed = result.checks.filter((c) => c.passed).length;
  document.getElementById("final-checks-passed").textContent = `${passed} / ${result.checks.length}`;
  document.getElementById("final-report-name").textContent = result.report.name;

  document.getElementById("final-title").textContent = result.title;
  document.getElementById("final-caption").innerHTML =
    `Revised from: <em>${escapeHtml(state.article.title)}</em> &middot; Report: <a href="${result.report.url}" target="_blank" rel="noopener">${escapeHtml(result.report.name)}</a>`;

  document.getElementById("final-article").innerHTML = renderSectionsHighlighted(result.sections, result);

  const faq = result.faq || [];
  document.getElementById("final-faq-block").hidden = faq.length === 0;
  document.getElementById("final-faq").innerHTML = faqHtml(faq);

  // The legend's numbers come from the backend's own highlighting pass (result.keyword_counts, filled by the
  // same spans that produce the marks in the text) so they always match what is highlighted. A literal phrase
  // search here cannot: it misses every secondary keyword the writer reordered or split across a sentence.
  const legend = keywordLegend(result);
  document.getElementById("legend-tertiary").hidden = !legend.hasTertiary;
  document.getElementById("legend-count-tertiary").textContent = legend.tertiary;
  document.getElementById("legend-longtail").hidden = !legend.hasLongtail;
  document.getElementById("legend-count-primary").textContent = legend.primary;
  document.getElementById("legend-count-secondary").textContent = legend.secondary;
  document.getElementById("legend-count-longtail").textContent = legend.longtail;
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

// ---------------------------------------------------------------- Recheck a humanized draft (final panel)
// The run's context lives on the server, so this works only while the run is still open in the app: the
// server maps the uploaded draft back onto the run's own sections and applies the run's checks to it.
const recheckEls = {
  input: document.getElementById("recheck-input"),
  spinner: document.getElementById("recheck-spinner"),
  result: document.getElementById("recheck-result"),
  fileName: document.getElementById("recheck-file-name"),
};

document.getElementById("recheck-btn").addEventListener("click", () => recheckEls.input.click());
recheckEls.input.addEventListener("change", () => {
  if (recheckEls.input.files.length) runRecheck(recheckEls.input.files[0]);
  recheckEls.input.value = ""; // the same file can be chosen again after edits
});

function canRecheck() {
  return !sopOpen && state.nav === "final" && !!state.run && !!state.result;
}

async function runRecheck(file) {
  hideError();
  recheckEls.fileName.textContent = file.name;
  recheckEls.result.hidden = true;
  recheckEls.spinner.hidden = false;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const data = await apiPost(`/recheck?run_token=${encodeURIComponent(state.run.token)}`, formData);
    renderRecheck(data.result);
  } catch (e) {
    showError("Could not recheck this file", e);
  } finally {
    recheckEls.spinner.hidden = true;
  }
}

function renderRecheck(result) {
  const passed = result.checks.filter((c) => c.passed).length;
  document.getElementById("recheck-passed").textContent = `${passed} / ${result.checks.length}`;
  const before = state.result.checks;
  document.getElementById("recheck-before").textContent =
    `${before.filter((c) => c.passed).length} / ${before.length}`;

  const notes = document.getElementById("recheck-notes");
  notes.hidden = !(result.notes || []).length;
  notes.innerHTML = (result.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");

  document.getElementById("recheck-title").textContent = result.title;
  document.getElementById("recheck-article").innerHTML = renderSectionsHighlighted(result.sections, result);
  const faq = result.faq || [];
  document.getElementById("recheck-faq-block").hidden = faq.length === 0;
  document.getElementById("recheck-faq").innerHTML = faqHtml(faq);

  const l = keywordLegend(result);
  document.getElementById("recheck-legend").innerHTML = `
    <span class="legend-item"><i class="legend-swatch legend-swatch--primary"></i> Primary keyword <span class="legend-count">${l.primary}</span></span>
    <span class="legend-item"><i class="legend-swatch legend-swatch--secondary"></i> Secondary keyword <span class="legend-count">${l.secondary}</span></span>
    ${l.hasTertiary ? `<span class="legend-item"><i class="legend-swatch legend-swatch--tertiary"></i> Related term <span class="legend-count">${l.tertiary}</span></span>` : ""}
    ${l.hasLongtail ? `<span class="legend-item"><i class="legend-swatch legend-swatch--longtail"></i> Long-tail keyword <span class="legend-count">${l.longtail}</span></span>` : ""}`;

  // Failures first, so what needs fixing is the first thing read.
  const rows = [...result.checks.filter((c) => !c.passed), ...result.checks.filter((c) => c.passed)];
  document.getElementById("recheck-checklist").innerHTML = rows.map((c) => `
    <li><i data-lucide="${c.passed ? "check" : "x"}" class="${c.passed ? "pass" : "fail"}"></i>
    <span>${escapeHtml(c.name)}${c.detail ? " — " + escapeHtml(c.detail) : ""}</span></li>
  `).join("");
  recheckEls.result.hidden = false;
  iconify();
}

// ---------------------------------------------------------------- SOP Check tool
const sopEls = {
  input: document.getElementById("sop-file-input"),
  text: document.getElementById("sop-file-text"),
  spinner: document.getElementById("sop-spinner"),
  result: document.getElementById("sop-result"),
};

function openSopCheck() {
  sopOpen = true;
  closeSidebar();
  render();
}

document.getElementById("sop-nav-btn").addEventListener("click", openSopCheck);
document.getElementById("sop-link").addEventListener("click", openSopCheck);
sopEls.input.addEventListener("change", () => {
  if (sopEls.input.files.length) runSopCheck(sopEls.input.files[0]);
  sopEls.input.value = ""; // the same file can be chosen again after edits
});

async function runSopCheck(file) {
  hideError();
  sopEls.text.textContent = file.name;
  sopEls.result.hidden = true;
  sopEls.spinner.hidden = false;
  const formData = new FormData();
  formData.append("file", file);
  try {
    renderSopResult(await apiPost("/sop-check", formData));
  } catch (e) {
    showError("Could not check this file", e);
  } finally {
    sopEls.spinner.hidden = true;
  }
}

function renderSopResult(data) {
  const hardFails = data.checks.filter((c) => !c.passed && !c.soft);
  const softFails = data.checks.filter((c) => !c.passed && c.soft);
  const passes = data.checks.filter((c) => c.passed);
  document.getElementById("sop-passed").textContent = `${passes.length} / ${data.checks.length}`;
  document.getElementById("sop-failed").textContent = String(hardFails.length + softFails.length);
  document.getElementById("sop-words").textContent = String(data.word_count);
  const row = (c, icon, cls) => `
    <li><i data-lucide="${icon}" class="${cls}"></i>
    <span>${escapeHtml(c.name)}${c.detail ? " — " + escapeHtml(c.detail) : ""}</span></li>`;
  document.getElementById("sop-checklist").innerHTML =
    hardFails.map((c) => row(c, "x", "fail")).join("")
    + softFails.map((c) => row(c, "alert-triangle", "warn")).join("")
    + passes.map((c) => row(c, "check", "pass")).join("");
  document.getElementById("sop-skipped").innerHTML = data.skipped.map((s) => `
    <li><i data-lucide="minus" class="skip"></i>
    <span>${escapeHtml(s.name)} — ${escapeHtml(s.reason)}</span></li>`).join("");
  sopEls.result.hidden = false;
  iconify();
}

// ---------------------------------------------------------------- master render
function render() {
  renderRunControls();
  renderStepNav();
  document.querySelectorAll(".step-item:not(.sop-item)").forEach((b) => { if (sopOpen) b.classList.remove("active"); });
  document.getElementById("sop-nav-btn").classList.toggle("active", sopOpen);
  showPanel(sopOpen ? "sopcheck" : state.article ? state.nav : "empty");
  if (state.article) { renderArticlePanel(); renderSponsorCandidates(); }
  renderStep1Panel();
  renderStep2Panel();
  renderStep3Panel();
  renderStep4Panel();
  renderFinalPanel();
  iconify();
}

// ---------------------------------------------------------------- display name
// The name the user types here is what the usage dashboard shows for this install. It is asked for on
// the very first launch (the dialog can't be dismissed until a valid name is saved) and can be
// changed afterwards from the sidebar. Validation mirrors telemetry.normalize_name on the server.
const NAME_MIN = 2;
const NAME_MAX = 60;
const nameEls = {
  modal: document.getElementById("name-modal"),
  form: document.getElementById("name-form"),
  input: document.getElementById("name-input"),
  error: document.getElementById("name-error"),
  save: document.getElementById("name-save"),
  saveLabel: document.getElementById("name-save-label"),
  cancel: document.getElementById("name-cancel"),
  title: document.getElementById("name-modal-title"),
  hint: document.getElementById("name-modal-hint"),
  chip: document.getElementById("user-chip"),
  chipName: document.getElementById("user-chip-name"),
};
let currentUserName = null;
let nameRequired = false;

function normalizeName(raw) {
  const cleaned = String(raw ?? "").replace(/\s+/g, " ").replace(/[\u0000-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim();
  return cleaned.length >= NAME_MIN && cleaned.length <= NAME_MAX ? cleaned : null;
}

function paintUserChip() {
  nameEls.chip.hidden = !currentUserName;
  nameEls.chipName.textContent = currentUserName || "";
  iconify();
}

function showNameError(message) {
  nameEls.error.textContent = message || "";
  nameEls.error.hidden = !message;
}

function openNameModal({ required }) {
  nameRequired = required;
  nameEls.title.textContent = required ? "Welcome! What should we call you?" : "Change your name";
  nameEls.hint.textContent = required
    ? "Enter your name. It is shown to the team that manages this tool in its usage dashboard, so your work can be attributed to you. You can change it any time from the sidebar."
    : "The dashboard keeps your new name as current and remembers the previous one.";
  nameEls.saveLabel.textContent = required ? "Continue" : "Save name";
  nameEls.cancel.hidden = required;
  nameEls.input.value = required ? "" : (currentUserName || "");
  showNameError("");
  nameEls.modal.hidden = false;
  nameEls.input.focus();
  nameEls.input.select();
}

function closeNameModal() {
  nameEls.modal.hidden = true;
  nameRequired = false;
  if (!nameEls.chip.hidden) nameEls.chip.focus();
}

async function submitName() {
  const name = normalizeName(nameEls.input.value);
  if (!name) {
    showNameError(`Enter a name of ${NAME_MIN}-${NAME_MAX} characters.`);
    nameEls.input.focus();
    return;
  }
  if (name === currentUserName) { closeNameModal(); return; }  // unchanged: nothing to record
  nameEls.save.disabled = true;
  showNameError("");
  try {
    const resp = await fetch(`${API_BASE}/app/user`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_name: name }),
    });
    if (!resp.ok) {
      showNameError((await resp.text().catch(() => "")) || "Could not save the name. Try again.");
      return;
    }
    currentUserName = (await resp.json()).user_name;
    paintUserChip();
    closeNameModal();
  } catch {
    showNameError("Could not reach the app. Check that it is still running, then try again.");
  } finally {
    nameEls.save.disabled = false;
  }
}

async function initUserName() {
  let name;
  try {
    const resp = await fetch(`${API_BASE}/app/user`);
    if (!resp.ok) return;                 // older/unreachable backend: never block the app over this
    name = (await resp.json()).user_name;
  } catch { return; }
  currentUserName = name || null;
  paintUserChip();
  if (!currentUserName) openNameModal({ required: true });
}

nameEls.form.addEventListener("submit", (e) => { e.preventDefault(); submitName(); });
nameEls.cancel.addEventListener("click", closeNameModal);
nameEls.chip.addEventListener("click", () => openNameModal({ required: false }));
nameEls.modal.addEventListener("mousedown", (e) => { if (e.target === nameEls.modal && !nameRequired) closeNameModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !nameEls.modal.hidden && !nameRequired) closeNameModal(); });

// ---------------------------------------------------------------- init
initTheme();
iconify();
initUserName();
render();
if (restoreResultIfAny()) {
  document.getElementById("sponsor-section").hidden = false;
  document.getElementById("run-controls").hidden = false;
  render();
} else {
  resumeRunIfAny().then(render);
}
