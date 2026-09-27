/* ===========================================================================
   Black Flag — single-page dashboard controller (vanilla JS, no framework)
   Talks only to the FastAPI backend. Never computes scores or compatibility
   in the browser — every result comes from the engine via /api.
   =========================================================================== */

const API = "/api";

// Default pipeline stages (mirrors backend PIPELINE_STAGES) for pre-job render.
const DEFAULT_STAGES = [
  { id: "analyze", label: "Analyze", state: "pending" },
  { id: "plan", label: "AI Plan", state: "pending" },
  { id: "adapt", label: "Adapt", state: "pending" },
  { id: "build", label: "Build", state: "pending" },
  { id: "test", label: "Test", state: "pending" },
  { id: "diagnose", label: "Diagnose", state: "pending" },
  { id: "repair", label: "Repair", state: "pending" },
  { id: "verify", label: "Verify", state: "pending" },
  { id: "package", label: "Package", state: "pending" },
];

const STAGE_ICON = { pending: "\u25CB", running: "\u25D0", complete: "\u2713", failed: "\u2715", skipped: "\u2013" };

let currentSource = null;   // "demo" or an upload_id
let currentJobId = null;
let pollTimer = null;
let infoCache = null;

// ------------------------------ helpers ---------------------------------
const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function distroName(id) {
  if (infoCache && infoCache.targets) {
    const t = infoCache.targets.find((x) => x.id === id);
    if (t) return t.display_name;
  }
  return id.charAt(0).toUpperCase() + id.slice(1);
}

let toastTimer = null;
function toast(msg, kind) {
  let t = $("toast");
  if (!t) { t = el("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.className = "show" + (kind ? " " + kind : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ""; }, 4000);
}

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function setStatus(text, cls) {
  const v = $("status-value");
  v.textContent = text;
  v.className = "chip-value" + (cls ? " " + cls : "");
}

function selectedTargets() {
  return Array.from(document.querySelectorAll("#targets input[type=checkbox]:checked")).map((c) => c.dataset.id);
}

// ------------------------------ info ------------------------------------
async function loadInfo() {
  try {
    const info = await api("/info");
    infoCache = info;
    $("version").textContent = info.version || "0.1.0";

    const p = info.provider || {};
    const pv = $("provider-value");
    pv.textContent = p.model ? `${p.name} \u00B7 ${p.model}` : p.name;
    pv.className = "chip-value " + (p.available ? "ok" : "warn");

    const d = info.docker || {};
    const dv = $("docker-value");
    dv.textContent = d.available ? (d.version ? `Connected ${d.version}` : "Connected") : "Unavailable";
    dv.className = "chip-value " + (d.available ? "ok" : "bad");

    const o = info.ollama || {};
    const ov = $("ollama-value");
    ov.textContent = o.available ? "Connected" : "Unavailable";
    ov.className = "chip-value " + (o.available ? "ok" : "bad");

    renderTargets(info.targets || []);

    // Default the provider selector to the server-active provider when valid.
    const sel = $("provider-select");
    if (p.name && Array.from(sel.options).some((op) => op.value === p.name)) {
      sel.value = p.name;
    }
  } catch (e) {
    toast("Could not load environment info: " + e.message, "error");
  }
}

function renderTargets(targets) {
  const wrap = $("targets");
  wrap.replaceChildren();
  targets.forEach((t) => {
    const label = el("label", "target checked");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.dataset.id = t.id;
    cb.addEventListener("change", () => label.classList.toggle("checked", cb.checked));
    const span = el("span");
    span.appendChild(el("span", "t-name", t.display_name));
    span.appendChild(el("span", "t-img", t.docker_image));
    label.appendChild(cb);
    label.appendChild(span);
    wrap.appendChild(label);
  });
}

// ------------------------------ project ---------------------------------
function renderProjectMeta(meta) {
  const box = $("project-meta");
  box.replaceChildren();
  const nameRow = el("div", "pm-row");
  nameRow.appendChild(el("span", "pm-name", meta.name));
  box.appendChild(nameRow);
  box.appendChild(el("div", "pm-row", `Files: ${meta.file_count}    Type: ${meta.project_type || "unknown"}`));
  $("btn-analyze").disabled = false;
}

async function launchDemo() {
  try {
    setStatus("Ready", "");
    const meta = await api("/projects/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "demo" }),
    });
    currentSource = "demo";
    renderProjectMeta(meta);
    resetResults();
    toast("Demo project loaded. Click Analyze Portability.", "ok");
  } catch (e) {
    toast("Failed to load demo: " + e.message, "error");
  }
}

async function uploadProject(file) {
  const form = new FormData();
  form.append("file", file);
  setStatus("Running", "run");
  try {
    const meta = await api("/projects/upload", { method: "POST", body: form });
    currentSource = meta.upload_id;
    renderProjectMeta(meta);
    resetResults();
    toast(`Uploaded ${meta.name} (${meta.file_count} files).`, "ok");
  } catch (e) {
    toast("Upload failed: " + e.message, "error");
  } finally {
    setStatus("Ready", "");
  }
}

// ------------------------------ analyze ---------------------------------
function scoreClass(s) { return s >= 0.7 ? "good" : (s >= 0.4 ? "mid" : "bad"); }

async function analyze() {
  if (!currentSource) { toast("Select a project first.", "error"); return; }
  setStatus("Running", "run");
  $("btn-analyze").disabled = true;
  try {
    const r = await api("/projects/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: currentSource }),
    });
    const sv = $("score-value");
    sv.textContent = r.score.toFixed(2);
    sv.className = "score-value " + scoreClass(r.score);
    $("issue-count").textContent = `${r.issue_count} portability issue(s) detected`;
    renderIssues(r.issues || []);
    $("btn-adapt").disabled = false;
    setStatus("Ready", "");
  } catch (e) {
    toast("Analysis failed: " + e.message, "error");
    setStatus("Failed", "bad");
  } finally {
    $("btn-analyze").disabled = false;
  }
}

function renderIssues(issues) {
  const tbody = $("issues-tbody");
  tbody.replaceChildren();
  const table = $("issues-table");
  if (!issues.length) {
    table.hidden = true;
    $("issue-count").textContent = "No portability issues detected — already portable.";
    return;
  }
  table.hidden = false;
  issues.forEach((i) => {
    const tr = el("tr");
    const tdSev = el("td"); tdSev.appendChild(el("span", "sev " + i.severity, i.severity.slice(0, 4)));
    tr.appendChild(tdSev);
    tr.appendChild(el("td", null, i.category));
    tr.appendChild(el("td", "file", `${i.source_file}:${i.line}`));
    tr.appendChild(el("td", null, (i.affected_targets || []).join(", ")));
    tr.appendChild(el("td", null, i.explanation));
    tr.appendChild(el("td", "prim", i.suggested_primitive || "\u2014"));
    tbody.appendChild(tr);
  });
}

// ------------------------------ pipeline --------------------------------
function renderPipeline(stages) {
  const ol = $("pipeline");
  ol.replaceChildren();
  (stages || DEFAULT_STAGES).forEach((s) => {
    const li = el("li", s.state);
    li.appendChild(el("span", "dot", STAGE_ICON[s.state] || STAGE_ICON.pending));
    li.appendChild(el("span", "stage-label", s.label));
    li.appendChild(el("span", "stage-state", s.state));
    ol.appendChild(li);
  });
}

// ------------------------------ console ---------------------------------
function renderLogs(logs) {
  const con = $("console");
  con.replaceChildren();
  if (!logs || !logs.length) {
    con.appendChild(el("span", "muted", "Waiting for a job\u2026"));
    return;
  }
  logs.forEach((l) => {
    const line = el("span", "ln");
    line.appendChild(el("span", "ts", `[${l.ts}] `));
    const cls = l.level === "error" ? "lv-error" : (l.level === "warn" ? "lv-warn" : "lv-info");
    line.appendChild(el("span", cls, l.message));
    con.appendChild(line);
  });
  con.scrollTop = con.scrollHeight;
}

// ------------------------------ adapt -----------------------------------
function resetResults() {
  $("results-card").hidden = true;
  $("package-card").hidden = true;
  $("score-value").textContent = "\u2014";
  $("score-value").className = "score-value";
  renderPipeline(DEFAULT_STAGES);
  renderLogs([]);
}

async function adapt() {
  if (!currentSource) { toast("Select a project first.", "error"); return; }
  const targets = selectedTargets();
  if (!targets.length) { toast("Select at least one target distribution.", "error"); return; }

  $("btn-adapt").disabled = true;
  $("btn-analyze").disabled = true;
  $("results-card").hidden = true;
  $("package-card").hidden = true;
  renderPipeline(DEFAULT_STAGES);
  setStatus("Running", "run");

  try {
    const r = await api("/projects/adapt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: currentSource,
        targets,
        provider: $("provider-select").value,
        max_iterations: 3,
        dry_run: false,
      }),
    });
    currentJobId = r.job_id;
    startPolling();
  } catch (e) {
    toast("Could not start adapt job: " + e.message, "error");
    setStatus("Failed", "bad");
    $("btn-adapt").disabled = false;
    $("btn-analyze").disabled = false;
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollJob, 1000);
  pollJob();
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function pollJob() {
  if (!currentJobId) return;
  let job;
  try {
    job = await api(`/jobs/${currentJobId}`);
  } catch (e) {
    // Transient — keep polling a few more times before giving up.
    return;
  }
  renderPipeline(job.stages);
  renderLogs(job.logs);

  if (job.status === "running" || job.status === "pending") {
    setStatus("Running", "run");
    return;
  }

  stopPolling();
  if (job.status === "complete") {
    setStatus("Complete", "ok");
    renderResults(job.result);
    toast("Adaptation complete.", "ok");
  } else {
    setStatus("Failed", "bad");
    toast("Job failed: " + (job.error || "unknown error"), "error");
  }
  $("btn-adapt").disabled = false;
  $("btn-analyze").disabled = false;
}

function renderResults(result) {
  if (!result) return;
  $("results-card").hidden = false;

  const sb = $("result-score-before");
  sb.textContent = Number(result.score_before).toFixed(2);
  sb.className = "score-num " + scoreClass(result.score_before);
  const sa = $("result-score-after");
  sa.textContent = Number(result.score_after).toFixed(2);
  sa.className = "score-num " + scoreClass(result.score_after);
  $("result-adaptations").textContent = result.adaptations_applied;

  const tbody = $("matrix-tbody");
  tbody.replaceChildren();
  const targets = result.targets || {};
  Object.keys(targets).forEach((id) => {
    const t = targets[id];
    const tr = el("tr");
    tr.appendChild(el("td", "distro", distroName(id)));
    tr.appendChild(markCell(t.prepare));
    tr.appendChild(markCell(t.build));
    tr.appendChild(markCell(t.test));
    const tdRes = el("td");
    tdRes.appendChild(el("span", "pill " + t.status, t.status));
    tr.appendChild(tdRes);
    tbody.appendChild(tr);
  });

  $("ai-summary").textContent = result.ai_summary || "";

  if (result.package) {
    $("package-card").hidden = false;
    $("package-name").textContent = result.package.name;
    $("package-size").textContent = `${result.package.size_kb} KB \u00B7 ${result.verified ? "verified" : "unverified"}`;
    $("package-download").href = result.package.download_url;
  }
}

function markCell(v) {
  const td = el("td");
  if (v === true) td.appendChild(el("span", "tick", "\u2713"));
  else if (v === false) td.appendChild(el("span", "cross", "\u2715"));
  else td.appendChild(el("span", "dash", "\u2014"));
  return td;
}

// ------------------------------ wiring ----------------------------------
document.addEventListener("DOMContentLoaded", () => {
  renderPipeline(DEFAULT_STAGES);
  loadInfo();

  $("btn-launch-demo").addEventListener("click", launchDemo);
  $("file-input").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) uploadProject(f);
    e.target.value = "";
  });
  $("btn-analyze").addEventListener("click", analyze);
  $("btn-adapt").addEventListener("click", adapt);
  $("btn-clear-log").addEventListener("click", () => renderLogs([]));
});
