/* NPI Integrity reviewer console. Plain JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtN = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
const mdy = (d) => { if (!d) return "–"; const s = String(d).slice(0, 10).split("-"); return s.length === 3 ? `${s[1]}/${s[2]}/${s[0]}` : d; };
const title = (s) => String(s ?? "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const scoreBadge = (score, tier) => `<span class="score tier-${esc(tier)}">${esc(score)}</span><span class="tiertxt">${esc(tier)}</span>`;
const statusTag = (s) => `<span class="status ${esc(String(s).replace(" ", "-"))}">${esc(s)}</span>`;
const chips = (ids, hl, cls = "hard") => (ids || []).map((r) => `<span class="chip ${hl && hl.has(r) ? cls : ""}">${esc(r)}</span>`).join("");
const HARD_RULES = new Set(["D-01", "D-02", "D-03", "D-04", "D-05", "D-06", "D-07"]);

let SESSION = {actor: null, role: "local_demo", modes: ["clinic", "government"]};
let MODE = "clinic";
function reviewer() {
  if (SESSION.actor) return SESSION.actor;
  try { return localStorage.getItem("npi.reviewer") || ""; } catch { return ""; }
}
$("#reviewer").value = reviewer();
$("#reviewer").addEventListener("change", (e) => { try { localStorage.setItem("npi.reviewer", e.target.value.trim()); } catch {} });

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 3800);
}

let SUMMARY = null;
let COVERAGE = null;
const OPEN = new Set(["New", "In review", "Monitoring", "Escalated"]);
function nextReviewStep(a) {
  return ({"New": "Start review", "In review": "Verify evidence", "Monitoring": "Recheck next run",
    "Escalated": "Consider referral", "Referred": "Referral recorded", "Dismissed": "Review closed"})[a.status] || "Inspect case";
}
function coveragePanel() {
  const leie = COVERAGE?.leie || {status: "unavailable"};
  const leieText = leie.status === "unavailable" ? "Snapshot unavailable; no exclusion conclusion" :
    `${leie.status === "stale" ? "Outdated snapshot" : "Dated snapshot"} · source ${mdy(leie.source_updated_at)} · ${fmtN(leie.exact_npi_rows)} of ${fmtN(leie.total_rows)} rows have an NPI`;
  return `<section class="card coverage-card" aria-label="Data coverage and availability"><div class="section-head"><div><div class="eyebrow">Data coverage</div><h2>What can be checked now</h2></div><a href="#/sources">Pilot file lineage →</a></div>
    <div class="coverage-grid"><div><span class="coverage-dot live"></span><strong>CMS NPPES</strong><p>Live, one exact NPI on demand. Identity, taxonomy and published address only.</p></div>
    <div><span class="coverage-dot ${leie.status === "stale" || leie.status === "unavailable" ? "warn" : "dated"}"></span><strong>OIG LEIE</strong><p>${esc(leieText)}. Name-only rows are not checked.</p></div>
    <div><span class="coverage-dot demo"></span><strong>Activity and scoring</strong><p>Synthetic claims through ${mdy(SUMMARY.claims.hi)}. No live claim-level feed or fraud finding.</p></div>
    <div><span class="coverage-dot off"></span><strong>Additional live sources</strong><p>Medicare activity, state licenses and social signals are not connected.</p></div></div></section>`;
}
async function loadSummary() {
  SESSION = await api("/api/session");
  try { MODE = sessionStorage.getItem("npi.mode") || MODE; } catch {}
  if (location.hash === "#/leads" || location.hash === "#/watch") MODE = "government";
  MODE = SESSION.modes.includes(MODE) ? MODE : SESSION.modes[0];
  $("#mode-switch").innerHTML = SESSION.modes.length > 1
    ? SESSION.modes.map((m) => `<button class="mode-button ${MODE === m ? "active" : ""}" data-mode="${m}" aria-pressed="${MODE === m}">${m === "clinic" ? "Clinic / Facility" : "Government"}</button>`).join("")
    : `<span class="small muted">Clinic / Facility</span>`;
  $("#mode-switch").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    MODE = b.dataset.mode;
    try { sessionStorage.setItem("npi.mode", MODE); } catch {}
    $("#mode-switch").querySelectorAll("button").forEach((button) => { button.classList.toggle("active", button.dataset.mode === MODE); button.setAttribute("aria-pressed", String(button.dataset.mode === MODE)); });
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("hidden", !!a.dataset.modes && !a.dataset.modes.split(" ").includes(MODE)));
    location.hash = MODE === "clinic" ? "#/home" : "#/leads";
  }));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("hidden", !!a.dataset.modes && !a.dataset.modes.split(" ").includes(MODE)));
  if (SESSION.actor) {
    $("#reviewer").value = SESSION.actor;
    $("#reviewer").readOnly = true;
  }
  SUMMARY = await api("/api/summary");
  try { COVERAGE = await api("/api/coverage"); } catch { COVERAGE = null; }
  const r = SUMMARY.run, c = SUMMARY.claims;
  $("#runbar").innerHTML = `<span>Run <b class="mono">${esc(r.run_id)}</b></span>
    <span>Inputs as of <b>${mdy(r.as_of)}</b></span>
    <span><b>${fmtN(SUMMARY.monitored.active)}</b> active NPIs monitored</span>
    <span><b>${fmtN(c.n)}</b> claim lines (${mdy(c.lo)} – ${mdy(c.hi)}, synthetic)</span>
    <span>Public + synthetic pilot · do not enter patient data or PHI</span>`;
}

/* ---------------------------------------------------------------- router */
const routes = { home: viewHome, live: viewLiveNppes, verify: viewVerify, roster: viewRoster, leads: viewLeads, methodology: viewMethodology,
  queue: viewQueue, watch: viewWatch, alert: viewAlert, provider: viewProvider, scorecard: viewScorecard, rules: viewRules, sources: viewSources, audit: viewAudit };
async function route() {
  const [, requested, arg] = location.hash.split("/");
  app.innerHTML = `<p class="muted pad">Loading…</p>`;
  try {
    if (!SUMMARY) await loadSummary();
    const view = requested || (MODE === "clinic" ? "home" : "leads");
    if ((view === "leads" || view === "watch") && !SESSION.modes.includes("government")) throw new Error("This view is not enabled for your account.");
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.v === view));
    await (routes[view] || viewHome)(arg && decodeURIComponent(arg));
  } catch (e) {
    app.innerHTML = `<div class="card"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`;
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
async function runSearch(q) {
  if (!q) return;
  if (/^\d{10}$/.test(q)) { location.hash = `#/live/${q}`; return; }
  const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
  if (res.length === 1) { location.hash = `#/provider/${res[0].npi}`; return; }
  app.innerHTML = `<h1>Synthetic demo search: ${esc(q)}</h1><p class="small muted">Name search covers only the synthetic pilot. For a real provider, enter the exact 10-digit NPI in Live NPPES.</p><div class="tablewrap"><table class="t"><tr><th>NPI</th><th>Name</th><th>Taxonomy</th><th>Location</th><th>Status</th></tr>
    ${res.map((p) => `<tr class="click" data-href="#/provider/${p.npi}"><td class="mono">${p.npi}</td><td>${esc(p.display_name)}</td><td>${esc(p.taxonomy_desc)}</td><td>${esc(title(p.city))}, ${esc(p.state)}</td><td>${esc(p.status)}</td></tr>`).join("") || `<tr><td colspan=5 class="muted">No matches</td></tr>`}</table></div>`;
}
$("#search").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await runSearch($("#q").value.trim()); } catch (err) { toast(err.message); }
});

async function viewHome() {
  const items = await api("/api/alerts");
  const first = items.find((a) => OPEN.has(a.status));
  app.innerHTML = `<section class="hero card"><div class="eyebrow">Clinic / Facility</div><h1>What would you like to check?</h1>
    <p class="muted">Enter an exact NPI to check the live CMS record. The alert demo and claims remain synthetic.</p>
    <form id="home-search" class="hero-search"><label for="home-q">10-digit NPI</label><div><input id="home-q" required inputmode="numeric" pattern="[0-9]{10}" maxlength="10" autocomplete="off" placeholder="Enter a 10-digit NPI"><button class="b primary">Check live NPPES</button></div></form>
    <p class="small muted">Do not enter patient data. NPPES does not verify licensing or credentialing.</p></section>
    <div class="journeys"><a class="journey card" href="#/home" id="focus-search"><strong>Check a provider →</strong><span>See current signals and the dated evidence.</span></a>
      <a class="journey card" href="#/verify"><strong>Verify our information →</strong><span>Compare your fields with a live NPPES record.</span></a>
      <a class="journey card" href="#/roster"><strong>Check a directory →</strong><span>Run a one-time check on up to 100 synthetic NPIs.</span></a></div>
    ${coveragePanel()}
    <section class="card home-review"><div class="section-head"><div><div class="eyebrow">Synthetic review workbench</div><h2>${fmtN(items.filter((a) => OPEN.has(a.status)).length)} open cases</h2></div><a href="#/queue">Open case queue →</a></div>
      ${first ? `<a class="featured-case" href="#/alert/${esc(first.alert_id)}"><span><b>${esc(first.display_name || first.npi)}</b><span class="small muted">${esc(first.top_reason || "View evidence")}</span></span><span>${statusTag(first.status)}<small>${esc(nextReviewStep(first))} →</small></span></a>` : `<p class="muted">No current alerts.</p>`}
      <p class="small muted">These cases use synthetic claims and dated pilot source files. Review the source record before any real-world decision.</p></section>`;
  $("#home-search").addEventListener("submit", async (e) => { e.preventDefault(); try { await runSearch($("#home-q").value.trim()); } catch (err) { toast(err.message); } });
  $("#focus-search").addEventListener("click", (e) => { e.preventDefault(); $("#home-q").focus(); });
}

async function viewLiveNppes(npi) {
  if (!npi) {
    app.innerHTML = `<h1>Public provider check</h1><p class="muted">Check one live NPPES profile and a dated OIG exclusion snapshot by exact 10-digit NPI. This does not run synthetic alerts or verify licensing, enrollment, or claims.</p>
      <form id="live-search" class="card hero-search"><label for="live-npi">10-digit NPI</label><div><input id="live-npi" required inputmode="numeric" pattern="[0-9]{10}" maxlength="10" autocomplete="off"><button class="b primary">Look up NPI</button></div></form>`;
    $("#live-search").addEventListener("submit", (e) => { e.preventDefault(); location.hash = `#/live/${$("#live-npi").value.trim()}`; });
    return;
  }
  const [profileResponse, leieResponse] = await Promise.allSettled([
    api(`/api/nppes/${encodeURIComponent(npi)}`),
    api(`/api/public-integrity/${encodeURIComponent(npi)}`),
  ]);
  const p = profileResponse.status === "fulfilled" ? profileResponse.value : null;
  const profileCard = p ? `<div class="card alerthead"><div>
      <div class="eyebrow">LIVE CMS NPPES REGISTRY · retrieved ${esc(p.retrieved_at.slice(0, 16).replace("T", " "))} UTC</div>
      <h1>${esc(p.name)}</h1><p class="muted"><span class="mono">${esc(p.npi)}</span> · ${esc(p.entity_type || "Type unavailable")} · ${esc(p.taxonomy || "Taxonomy unavailable")}</p>
      <div class="finding"><strong>${esc(p.status)} NPPES record</strong><p>${p.status === "Active" ? "This NPI appears as active in the Registry. That does not establish licensing, credentialing, Medicare enrollment, or absence of exclusion." : "Review the Registry record and dates directly; no eligibility conclusion is made here."}</p></div>
      <p class="small muted">Record last updated ${mdy(p.last_updated)} · enumerated ${mdy(p.enumeration_date)}. Live source data; no synthetic score is assigned.</p></div></div>
      <div class="card" style="margin-top:16px"><h2>Published provider details</h2><dl class="kv">
        <dt>Primary taxonomy</dt><dd>${esc(p.taxonomy || "Unavailable")} ${p.taxonomy_code ? `<span class="mono small">${esc(p.taxonomy_code)}</span>` : ""}</dd>
        <dt>Practice location</dt><dd>${esc(p.city || "Unavailable")}, ${esc(p.state || "")} ${esc(p.postal_code || "")}</dd>
        <dt>Source</dt><dd><a href="${esc(p.source_url)}" target="_blank" rel="noopener noreferrer">Open this record in the CMS NPI Registry ↗</a></dd>
      </dl><p class="small muted">This is a single live lookup. Enrollment, licensing, and claim-level checks require separate verified sources.</p></div>`
    : `<div class="card"><h1>NPI ${esc(npi)}</h1><div class="banner" role="alert">Live NPPES profile unavailable: ${esc(profileResponse.reason?.message || "Lookup failed")}. A missing Registry result is not a clearance or deactivation finding; check the official deactivation file.</div></div>`;
  app.innerHTML = `<p class="small"><a href="#/live">← Check another NPI</a></p>${profileCard}
    <div class="card" style="margin-top:16px" id="public-integrity" aria-live="polite"><h2>Public exclusion check</h2><p class="muted">Checking the dated OIG snapshot…</p></div>
    <div class="card" style="margin-top:16px"><h2>Data coverage for this provider check</h2>
    <ul><li><strong>NPPES identity:</strong> ${p ? "live lookup above" : "unavailable; no profile conclusion"}.</li>
      <li><strong>OIG LEIE:</strong> dated exact-NPI snapshot above; name-only rows are not checked.</li>
      <li><strong>Medicare annual activity:</strong> not connected yet; no utilization conclusion is available.</li>
      <li><strong>Claim-level activity:</strong> not connected. The separate potential-anomaly demo uses synthetic claims.</li></ul></div>`;
  if (leieResponse.status === "fulfilled") {
    const {leie} = leieResponse.value;
    const matchRows = leie.matches.map((m) => `<li><strong>${esc(m.name || "Name unavailable")}</strong> · ${esc(m.city || "City unavailable")}, ${esc(m.state || "State unavailable")} · exclusion type ${esc(m.exclusion_type || "unavailable")} · effective ${esc(mdy(String(m.exclusion_date || "").replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3")))}</li>`).join("");
    $("#public-integrity").innerHTML = `<h2>OIG LEIE exclusion snapshot</h2><div class="finding ${leie.matches.length ? "attention" : ""}"><strong>${esc(leie.result)}</strong>
      <p>${leie.matches.length ? "A published LEIE row contains this exact NPI. Confirm the person or organization in OIG's official search before taking action." : "No row publishing this exact NPI was found in this snapshot. This is not exclusion clearance: most LEIE rows do not publish an NPI."}</p></div>
      ${leie.stale ? `<div class="banner" role="alert">Snapshot is over 45 days old. Refresh the source before relying on it.</div>` : ""}
      ${matchRows ? `<ul>${matchRows}</ul>` : ""}
      <p class="small muted">OIG source updated ${mdy(leie.source_updated_at)} · downloaded ${esc(leie.fetched_at.slice(0, 10))} · ${fmtN(leie.indexed_rows)} of ${fmtN(leie.total_rows)} rows publish a 10-digit NPI.</p>
      <details><summary>Source checksum</summary><span class="mono source-hash">SHA-256: ${esc(leie.source_sha256)}</span></details>
      <p class="small muted">${esc(leie.note)} <a href="${esc(leie.search_url)}" target="_blank" rel="noopener noreferrer">Verify in OIG search ↗</a></p>`;
  } else {
    $("#public-integrity").innerHTML = `<h2>OIG LEIE exclusion snapshot</h2><div class="banner" role="alert">Cannot check yet: ${esc(leieResponse.reason?.message || "Lookup failed")}. No exclusion conclusion is available.</div>`;
  }
}

async function viewVerify() {
  app.innerHTML = `<h1>Verify our information</h1><p class="muted">Compare your fields with a live CMS NPPES lookup for one exact NPI. Values entered are not saved; do not enter patient data.</p>
    <form id="verify-form" class="card field-form"><label>NPI <input name="npi" inputmode="numeric" pattern="[0-9]{10}" maxlength="10" required></label>
      <label>Name <input name="name" maxlength="120"></label><label>City <input name="city" maxlength="80"></label>
      <label>State <input name="state" maxlength="2"></label><label>Taxonomy <input name="taxonomy" maxlength="120"></label>
      <label>License number <input name="license" maxlength="80" aria-describedby="license-note"></label>
      <p id="license-note" class="small muted">License source is not connected; this field will say “Cannot check yet.”</p>
      <button class="b primary">Compare with source</button></form><div id="verify-results" aria-live="polite"></div>`;
  $("#verify-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    try {
      const result = await api("/api/verify", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
      $("#verify-results").innerHTML = `<div class="card result-card"><h2>Comparison for NPI ${esc(result.npi)}</h2><p class="small muted">${esc(result.source)} · record updated ${mdy(result.last_updated)}. ${esc(result.note)} <a href="${esc(result.source_url)}" target="_blank" rel="noopener noreferrer">Open CMS record ↗</a></p>
        <div class="tablewrap"><table class="t"><thead><tr><th>Field</th><th>You entered</th><th>Source record</th><th>Result</th></tr></thead><tbody>${result.fields.map((f) => `<tr><td>${esc(f.field)}</td><td>${esc(f.entered || "—")}</td><td>${esc(f.official || "Unavailable")}</td><td><strong>${esc(f.result)}</strong></td></tr>`).join("")}</tbody></table></div></div>`;
    } catch (err) { $("#verify-results").innerHTML = `<div class="banner" role="alert">${esc(err.message)}</div>`; }
  });
}

async function viewRoster() {
  app.innerHTML = `<h1>One-time directory check</h1><div class="banner">Synthetic pilot only. This check does not save or continuously monitor a directory. Upload a CSV with exactly one column named <b>npi</b>; do not include patient or clinic data.</div>
    <form id="roster-form" class="card"><label for="roster-file">Synthetic NPI directory CSV (up to 100 rows)</label><input id="roster-file" type="file" accept=".csv,text/csv" required><button class="b primary">Check directory</button></form>
    <div id="roster-results" aria-live="polite"></div>`;
  $("#roster-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const file = $("#roster-file").files[0];
      if (!file || file.size > 4096) throw new Error("CSV must be 4 KB or smaller.");
      const lines = (await file.text()).trim().split(/\r?\n/);
      if (lines.shift()?.trim().toLowerCase() !== "npi" || lines.length < 1 || lines.length > 100 || lines.some((line) => !/^\d{10}$/.test(line.trim())))
        throw new Error("Use exactly one npi column with 1–100 ten-digit rows.");
      const result = await api("/api/roster/check", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({npis:lines.map((line) => line.trim())})});
      const counts = result.results.reduce((out, row) => { out[row.label] = (out[row.label] || 0) + 1; return out; }, {});
      $("#roster-results").innerHTML = `<div class="card result-card"><h2>Directory results</h2><p>${Object.entries(counts).map(([label, n]) => `${esc(label)}: ${n}`).join(" · ")}</p><p class="small muted">${esc(result.note)}</p>
        <div class="tablewrap"><table class="t"><thead><tr><th>NPI</th><th>Provider</th><th>Result</th><th>Why</th></tr></thead><tbody>${result.results.map((row) => `<tr><td><a href="#/provider/${esc(row.npi)}">${esc(row.npi)}</a></td><td>${esc(row.name)}</td><td>${esc(row.label)}</td><td>${esc(row.reason)}</td></tr>`).join("")}</tbody></table></div></div>`;
    } catch (err) { $("#roster-results").innerHTML = `<div class="banner" role="alert">${esc(err.message)}</div>`; }
  });
}

async function viewLeads() {
  const items = await api("/api/government/leads");
  app.innerHTML = `<div class="eyebrow">Government · synthetic pilot</div><h1>Review workbench</h1><p class="muted">Work through potential anomalies, verify cited evidence, then record a disposition. Scores prioritize review; they are not fraud probabilities.</p>
    <div class="kpis"><div class="kpi"><div class="v">${fmtN(items.filter((a) => OPEN.has(a.status)).length)}</div><div class="l">Open leads</div></div>
      <div class="kpi"><div class="v">${fmtN(items.filter((a) => a.status === "New").length)}</div><div class="l">Awaiting first review</div></div>
      <div class="kpi"><div class="v">${fmtN(items.filter((a) => a.status === "In review").length)}</div><div class="l">Evidence review underway</div></div>
      <div class="kpi"><div class="v">${fmtN(items.filter((a) => a.status === "Monitoring").length)}</div><div class="l">Awaiting next run</div></div>
      <div class="kpi"><div class="v">${fmtN(items.filter((a) => a.status === "Escalated").length)}</div><div class="l">Escalated</div></div></div>
    ${coveragePanel()}
    <section class="card worklist-card"><div class="section-head"><div><div class="eyebrow">Action queue</div><h2>Cases needing attention</h2></div><span class="small muted">Select a case to inspect its source evidence</span></div>
    <div class="filters"><label for="lead-query">Find</label><input id="lead-query" type="search" placeholder="Provider, NPI or reason" aria-label="Find a lead">
      <label for="lead-state">State</label><select id="lead-state"><option value="">All states</option>${[...new Set(items.map((a) => a.state).filter(Boolean))].sort().map((s) => `<option>${esc(s)}</option>`).join("")}</select>
      <label for="lead-status">Status</label><select id="lead-status"><option value="Open">Open</option><option value="">All statuses</option>${[...new Set(items.map((a) => a.status))].sort().map((s) => `<option>${esc(s)}</option>`).join("")}</select></div>
    <div class="tablewrap"><table class="t"><thead><tr><th>Score</th><th>Provider</th><th>State</th><th>Top reason</th><th>Review stage</th><th>Next step</th></tr></thead><tbody id="lead-body"></tbody></table></div>
    <div class="pager"><span id="lead-count" class="small muted"></span><button class="b" id="lead-prev">Previous</button><button class="b" id="lead-next">Next</button></div></section>`;
  let page = 0;
  const render = () => {
    const query = $("#lead-query").value.trim().toLowerCase();
    const filtered = items.filter((a) => (!$("#lead-state").value || a.state === $("#lead-state").value) &&
      (!$("#lead-status").value || ($("#lead-status").value === "Open" ? OPEN.has(a.status) : a.status === $("#lead-status").value)) &&
      (!query || [a.display_name, a.npi, a.top_reason].some((value) => String(value || "").toLowerCase().includes(query))));
    page = Math.min(page, Math.max(0, Math.ceil(filtered.length / 10) - 1));
    $("#lead-body").innerHTML = filtered.slice(page * 10, page * 10 + 10)
      .map((a) => `<tr class="click" data-href="#/alert/${esc(a.alert_id)}" tabindex="0" role="link" aria-label="Open lead for ${esc(a.display_name || a.npi)}"><td>${scoreBadge(a.score,a.tier)}</td><td><b>${esc(a.display_name || a.npi)}</b><div class="small muted">${esc(a.npi)}</div></td><td>${esc(a.state)}</td><td>${esc(a.top_reason)}</td><td>${statusTag(a.status)}</td><td><a href="#/alert/${esc(a.alert_id)}">${esc(nextReviewStep(a))} →</a></td></tr>`).join("") || `<tr><td colspan="6">No leads match these filters.</td></tr>`;
    $("#lead-count").textContent = filtered.length ? `${page * 10 + 1}–${Math.min(page * 10 + 10, filtered.length)} of ${filtered.length}` : "0 leads";
    $("#lead-prev").disabled = page === 0; $("#lead-next").disabled = (page + 1) * 10 >= filtered.length;
  };
  $("#lead-query").addEventListener("input", () => { page = 0; render(); });
  $("#lead-state").addEventListener("change", () => { page = 0; render(); }); $("#lead-status").addEventListener("change", () => { page = 0; render(); });
  $("#lead-prev").addEventListener("click", () => { page--; render(); }); $("#lead-next").addEventListener("click", () => { page++; render(); }); render();
}

async function viewMethodology() {
  app.innerHTML = `<h1>Methodology &amp; Admin</h1><p class="muted">Inspect how results were produced, evaluated, and reviewed.</p>
    <div class="journeys"><a class="journey card" href="#/rules"><strong>Rules →</strong><span>Versioned signals and scoring.</span></a><a class="journey card" href="#/sources"><strong>Sources →</strong><span>Source files and freshness.</span></a>
    <a class="journey card" href="#/scorecard"><strong>Scorecard →</strong><span>Synthetic pilot evaluation.</span></a><a class="journey card" href="#/audit"><strong>Audit log →</strong><span>Recorded review activity.</span></a></div>`;
}

/* ---------------------------------------------------------------- queue */
let QUEUE_FILTER = { tier: "All", status: "Open" };
async function viewQueue() {
  const items = await api("/api/alerts");
  const sc = SUMMARY.scorecard, t = SUMMARY.tiers;
  const open = items.filter((a) => OPEN.has(a.status)).length;
  const featured = items.find((a) => a.tier === "Critical");
  const kpi = (v, l, cls = "") => `<div class="kpi ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`;
  const tiers = ["All", "Critical", "High", "Elevated"];
  const statuses = ["Open", "All", "New", "In review", "Monitoring", "Escalated", "Referred", "Dismissed"];
  const render = () => {
    let rows = items.filter((a) => (QUEUE_FILTER.tier === "All" || a.tier === QUEUE_FILTER.tier) &&
      (QUEUE_FILTER.status === "All" || (QUEUE_FILTER.status === "Open" ? OPEN.has(a.status) : a.status === QUEUE_FILTER.status)));
    const seen = new Set(); const out = [];
    for (const a of rows) {
      if (a.cluster_key) {
        if (seen.has(a.cluster_key)) continue;
        seen.add(a.cluster_key);
        const members = rows.filter((x) => x.cluster_key === a.cluster_key);
        if (members.length > 1) {
          const gid = "g" + out.length;
          out.push(`<tr class="group click" data-toggle="${gid}" tabindex="0" role="button" aria-label="Expand ${members.length} alerts at shared practice address">
            <td data-label="Score">${scoreBadge(a.score, a.tier)}</td><td data-label="Practice" colspan="2"><b>${members.length} alerts share one practice address</b><div class="small muted">${esc(a.cluster_key.replace("|", ", "))} · click to expand</div></td>
            <td data-label="Reasons">${chips([...new Set(members.flatMap((m) => m.rules))])}</td><td data-label="Top reason" class="small">${esc(a.top_reason)}</td><td data-label="Status">${statusTag(a.status)}</td></tr>`);
          members.forEach((m) => out.push(row(m, `${gid} hidden`)));
          continue;
        }
      }
      out.push(row(a));
    }
    $("#qbody").innerHTML = out.join("") || `<tr><td colspan="6" class="muted">No alerts match these filters.</td></tr>`;
    document.querySelectorAll(".filters button").forEach((b) => b.classList.toggle("on", QUEUE_FILTER[b.dataset.k] === b.dataset.v));
  };
  const row = (a, cls = "") => `<tr class="click ${cls}" data-href="#/alert/${a.alert_id}" tabindex="0" role="link" aria-label="Open alert ${esc(a.alert_id)} for ${esc(a.display_name || a.npi)}">
      <td data-label="Score">${scoreBadge(a.score, a.tier)}</td>
      <td data-label="Alert" class="mono">${esc(a.alert_id)}</td>
      <td data-label="Provider"><b>${esc(a.display_name || a.npi)}</b><div class="small muted"><span class="mono">${esc(a.npi)}</span> · ${esc(a.taxonomy_desc || "")} · ${esc(title(a.city))}, ${esc(a.state)}${a.npi_status === "Deactivated" ? " · <b>deactivated</b>" : ""}</div></td>
      <td data-label="Reason codes">${chips(a.rules, HARD_RULES)}</td>
      <td data-label="Top reason" class="small">${esc(a.top_reason)}</td>
      <td data-label="Status">${statusTag(a.status)}</td></tr>`;
  app.innerHTML = `
    <div class="kpis">
      ${kpi(fmtN(open), "Open alerts")}
      ${kpi(fmtN(t.Critical || 0), "Critical (hard rules)")}
      ${kpi(fmtN((t.High || 0) + (t.Elevated || 0)), "High + Elevated")}
      ${kpi(fmtN(t.Watch || 0), "Watch list (no alert)")}
      ${sc ? kpi(`${sc.caught} / ${sc.total}`, "Synthetic planted scenarios caught", sc.caught === sc.total ? "good" : "") : ""}
      ${sc ? kpi(sc.false_alerts_per_1000, "Synthetic false alerts per 1,000 NPIs", sc.false_alerts === 0 ? "good" : "") : ""}
    </div>
    <section class="demo-guide" aria-label="Demo walkthrough">
      <div><b>Explore the synthetic demo</b><div class="small muted">Follow an alert from the queue to its source evidence, then inspect the evaluation and lineage.</div></div>
      <div class="demo-steps">
        ${featured ? `<a href="#/alert/${esc(featured.alert_id)}">1 · Review a critical alert</a>` : ""}
        <a href="#/scorecard">2 · Check the scorecard</a>
        <a href="#/sources">3 · Trace source files</a>
      </div>
    </section>
    <div class="filters">
      ${tiers.map((v) => `<button data-k="tier" data-v="${v}">${v}</button>`).join("")}
      <span class="muted" style="margin:0 6px">|</span>
      ${statuses.map((v) => `<button data-k="status" data-v="${v}">${v}</button>`).join("")}
    </div>
    <div class="tablewrap queue-table"><table class="t"><thead><tr><th>Score</th><th>Alert</th><th>Provider</th><th>Reason codes</th><th>Top reason</th><th>Status</th></tr></thead><tbody id="qbody"></tbody></table></div>
    <p class="small muted">Potential anomalies require human verification. A red rule indicator means the rule met its hard-evidence conditions and sets a 900-point floor; it is not a verdict. <a href="#/rules">How scores and bands work</a>. Synthetic results do not establish real-world accuracy.</p>`;
  document.querySelectorAll(".filters button").forEach((b) => b.addEventListener("click", () => { QUEUE_FILTER[b.dataset.k] = b.dataset.v; render(); }));
  render();
}

async function viewWatch() {
  const items = await api("/api/watchlist");
  app.innerHTML = `<h1>Watch list</h1><p class="muted">Scored but below the alert threshold (550). Re-scored every run; no reviewer action needed. Weak or low-confidence signals land here by design.</p>
    <div class="tablewrap"><table class="t"><tr><th>Score</th><th>Provider</th><th>Signals</th><th>Top reason</th></tr>
    ${items.map((s) => `<tr class="click" data-href="#/provider/${s.npi}"><td>${scoreBadge(s.score, s.tier)}</td>
      <td><b>${esc(s.display_name || s.npi)}</b><div class="small muted"><span class="mono">${s.npi}</span> · ${esc(s.taxonomy_desc || "")} · ${esc(title(s.city))}, ${esc(s.state)}</div></td>
      <td>${chips(s.rules)}</td><td class="small">${esc(s.top_reason)}</td></tr>`).join("")}</table></div>`;
}

/* ---------------------------------------------------------------- alert + provider */
function reasonCard(h) {
  const facts = h.facts || {};
  const src = (h.sources || []).map((s) => `<li><span class="eid">${s.eid}</span> ${esc(s.label)}${s.version ? ` · version ${mdy(s.version)}` : ""}${s.row ? ` · row ${fmtN(s.row)}` : ""}${s.file ? ` · <span class="mono">${esc(s.file)}</span>` : ""}${s.sha256 ? ` · sha256 <span class="mono">${esc(s.sha256)}…</span>` : ""}${s.note ? ` — ${esc(s.note)}` : ""}</li>`).join("");
  const claims = h.claims && h.claims.length ? `<details><summary><span class="eid">${h.claims_eid}</span> ${h.claims.length} claim lines${h.evidence_claims.length > h.claims.length ? "" : ""}</summary>
      <div class="tablewrap" style="margin-top:6px;max-height:320px;overflow:auto"><table class="t"><tr><th>Line</th><th>Date</th><th>Code</th><th>Units</th><th>POS</th><th>Place</th><th>Billing</th><th>Rendering</th><th>Ordering</th><th>Patient</th><th>Source row</th></tr>
      ${h.claims.map((c) => `<tr><td class="mono">${esc(c.line_id)}</td><td>${mdy(c.service_date)}</td><td><span class="mono">${esc(c.hcpcs)}</span> <span class="small muted">${esc(c.descr || "")}</span></td><td>${c.units}</td><td>${esc(c.pos)}</td><td>${esc(title(c.svc_city))}</td><td class="mono">${esc(c.billing_npi)}</td><td class="mono">${esc(c.rendering_npi)}</td><td class="mono">${esc(c.ordering_npi || "")}</td><td class="mono">${esc(c.patient_id)}</td><td class="mono small">${esc(c._file)} r${c._row}</td></tr>`).join("")}
      </table></div></details>` : "";
  const conf = h.confidence != null ? ` · identity confidence ${Number(h.confidence).toFixed(2)}` : "";
  return `<div class="reason ${h.hard ? "hard" : ""}">
    <div class="rh"><span class="code">${esc(h.reason_code)}</span><span class="chip ${h.hard ? "hard" : "soft"}">${esc(h.rule_id)} v${esc(h.version)}</span>
      <span class="small muted">${h.hard ? "hard rule · sets floor" : `soft signal · +${Math.round(h.weight)} pts`}${conf}</span></div>
    <div class="txt">${esc(h.reason)}</div>
    ${src ? `<ul class="evid">${src}</ul>` : ""}${claims}</div>`;
}
function plainReasonCard(h) {
  return `<div class="plain-reason"><strong>${esc(h.reason)}</strong><details><summary>View source evidence and rule details</summary>${reasonCard(h)}</details></div>`;
}

function scoreChart(info) {
  if (!info) return `<div class="card"><h2>Why this score?</h2><p class="small muted">A verified breakdown is unavailable for this run. See the rule evidence before interpreting the score.</p></div>`;
  const bar = (label, value, detail) => `<div class="score-row"><div><strong>${esc(label)}</strong><span>${esc(detail)}</span></div><div class="score-track" role="img" aria-label="${esc(label)}: ${fmtN(value)} of 1000 points"><i style="width:${Math.min(100, 100 * value / 1000)}%"></i></div><b>${fmtN(value)}</b></div>`;
  const familyRows = info.families.map((f) => bar(`${f.family} signal family`, f.points, f.raw > f.points ? `Capped from ${fmtN(f.raw)}` : "After family cap")).join("");
  return `<div class="card"><h2>Why this score?</h2><p class="small muted">Synthetic rule contributions for this run. Bars show points before the network multiplier; they are not probabilities.</p>
    ${info.hard_floor ? bar("Hard-evidence floor", info.hard_floor, "At least one hard rule met its evidence conditions") : ""}${familyRows || `<p class="small muted">No weighted signal points.</p>`}
    <p class="small muted">${info.network_multiplier > 1 ? `Weighted signal points × ${esc(info.network_multiplier)} for a network link. ` : ""}Final score ${fmtN(info.score)} / ${fmtN(info.ceiling)}${info.score === info.ceiling ? " (capped at 1000)" : ""}; alert setting ${fmtN(info.alert_threshold)}. <a href="#/rules">Band definitions and rules</a>.</p></div>`;
}

function profileCard(p) {
  const kv = (k, v) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : "");
  const loc = (p.locations || []).map((l) => `${esc(l.address_key.split("|")[0])}, ${esc(title(l.city))} ${esc(l.state)} ${esc(l.zip5)} <span class="small muted">(${l.kind})</span>`).join("<br>");
  const ex = (p.exclusions || []).map((e) => `${e.source === "oig_leie" ? "OIG LEIE" : "SAM"} ${mdy(e.excl_date)} ${esc(e.excl_type)}${e.rein_date ? `, reinstated ${mdy(e.rein_date)}` : ""} · ${esc(e.basis)} (${Number(e.confidence).toFixed(2)})`).join("<br>");
  const om = (p.official_matches || []).map((e) => `${esc(title(e.official))} ↔ ${e.source === "oig_leie" ? "OIG" : "SAM"} exclusion ${mdy(e.excl_date)} in ${esc(title(e.city))}, ${esc(e.state)} (${Number(e.confidence).toFixed(2)})`).join("<br>");
  const oo = (p.optouts || []).map((o) => `${mdy(o.eff_date)} – ${mdy(o.end_date)}`).join("<br>");
  const ra = (p.reassignments || []).map((r) => esc(title(r.group_name))).join("<br>");
  const orf = p.order_referring ? `In file · Part B ${p.order_referring.partb} · DME ${p.order_referring.dme} · HHA ${p.order_referring.hha}` : (p.entity_type === "1" ? "<b>Not in file</b>" : "");
  const hist = (p.history || []).map((h) => `${mdy(h.change_date)} · ${esc(h.field)}: <span class="small muted">${esc(h.old_value || "")} → </span>${esc(h.new_value || "")}`).join("<br>");
  return `<div class="card"><h2>Source profile</h2><dl class="kv">
    ${kv("Name", `<b>${esc(p.display_name)}</b> ${esc(p.credential || "")}`)}
    ${kv("NPI", `<span class="mono">${esc(p.npi)}</span> · type ${p.entity_type === "2" ? "2 (organization)" : "1 (individual)"}`)}
    ${kv("NPPES status", p.status === "Deactivated" ? `<b style="color:var(--red)">Deactivated ${mdy(p.deact_date)}</b>` : `Active${p.react_date ? ` (reactivated ${mdy(p.react_date)})` : ""}`)}
    ${kv("Taxonomy", `${esc(p.taxonomy_desc)} <span class="mono small">${esc(p.taxonomy)}</span>`)}
    ${kv("Enumerated", mdy(p.enum_date))}
    ${kv("Practice locations", loc)}
    ${kv("Authorized official", p.auth_last ? `${esc(title(p.auth_first + " " + p.auth_last))} · ${esc(p.auth_title || "")}` : "")}
    ${kv("Official ↔ exclusion", om)}
    ${kv("Exclusion matches", ex)}
    ${kv("Opt-out", oo)}
    ${kv("Order & Referring", orf)}
    ${kv("Reassigns to", ra)}
    ${kv("PECOS enrollment", p.enrollment ? `${esc(p.enrollment.enrollment_id)} · ${esc(p.enrollment.provider_type)}` : "")}
    ${kv("Medicare 2024", p.medicare ? `${fmtN(p.medicare.services)} services · ${fmtN(p.medicare.benes)} beneficiaries` : "")}
    ${kv("NPPES changes", hist)}
  </dl></div>`;
}

function timelineSvg(p, hits) {
  const m = p.monthly || [];
  if (!m.length) return `<p class="muted small">No claims for this NPI.</p>`;
  const W = 760, H = 170, L = 40, B = 24, T = 26;
  const max = Math.max(...m.map((x) => x.lines), 1);
  const first = new Date(m[0].month), last = new Date(m[m.length - 1].month);
  const months = (last.getFullYear() - first.getFullYear()) * 12 + last.getMonth() - first.getMonth() + 1;
  const bw = (W - L - 8) / months;
  const xOf = (d) => { const dt = new Date(String(d).slice(0, 10)); return L + ((dt.getFullYear() - first.getFullYear()) * 12 + dt.getMonth() - first.getMonth() + (dt.getDate() - 1) / 30) * bw; };
  const bars = m.map((x) => { const h = ((H - B - T) * x.lines) / max; return `<rect x="${xOf(x.month) + 1}" y="${H - B - h}" width="${Math.max(bw - 2, 1)}" height="${h}" fill="#1f6fb2" opacity=".85"><title>${mdy(x.month)}: ${x.lines} lines, ${x.patients} patients</title></rect>`; }).join("");
  const ev = [];
  if (p.deact_date && p.status === "Deactivated") ev.push([p.deact_date, "Deactivated", "#c92a2a"]);
  (p.exclusions || []).forEach((e) => ev.push([e.excl_date, "Excluded", "#c92a2a"]));
  (p.optouts || []).forEach((o) => ev.push([o.eff_date, "Opt-out", "#b7791f"]));
  (p.history || []).forEach((h) => ev.push([h.change_date, h.field.split(" ").pop(), "#6741d9"]));
  const lo = m[0].month, hi = m[m.length - 1].month;
  const marks = ev.filter(([d]) => d >= lo.slice(0, 7) && d <= hi.slice(0, 7) + "-31").map(([d, label, c], i) => { const x = xOf(d); return `<line x1="${x}" x2="${x}" y1="${T - 6}" y2="${H - B}" stroke="${c}" stroke-dasharray="3 3"/><text x="${x + 3}" y="${T - 10 + (i % 2) * 11}" font-size="10" fill="${c}">${esc(label)} ${mdy(d)}</text>`; }).join("");
  const axis = m.filter((x, i) => i % 3 === 0).map((x) => `<text x="${xOf(x.month) + bw / 2}" y="${H - 8}" font-size="10" text-anchor="middle" fill="#5b6b82">${x.month.slice(5, 7)}/${x.month.slice(2, 4)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Monthly claim lines">
    <text x="4" y="${T + 4}" font-size="10" fill="#5b6b82">${fmtN(max)}</text><line x1="${L}" x2="${W}" y1="${H - B}" y2="${H - B}" stroke="#dbe3ee"/>${bars}${marks}${axis}</svg>
    <div class="small muted">Monthly claim lines (billing or rendering). Dashed lines: profile, exclusion, and enrollment events.</div>`;
}

function peersCard(p) {
  const pe = p.peers;
  if (!pe || !pe.n || pe.mine == null) return "";
  const max = Math.max(pe.p99, pe.mine, 1);
  const bar = (v, c) => `<div class="bar"><span style="width:${(100 * v) / max}%;background:${c}"></span></div>`;
  return `<div class="card"><h2>Peer comparison</h2><p class="small muted">Claim lines in the last 90 days vs ${fmtN(pe.n)} rendering NPIs in the same taxonomy group.</p>
    <div class="kv"><dt>This NPI</dt><dd>${fmtN(pe.mine)} lines · ${pe.pct}th percentile ${bar(pe.mine, "#e8590c")}</dd>
    <dt>Peer median</dt><dd>${fmtN(Math.round(pe.median))} ${bar(pe.median, "#8a99ad")}</dd>
    <dt>Peer 90th pct</dt><dd>${fmtN(Math.round(pe.p90))} ${bar(pe.p90, "#8a99ad")}</dd>
    <dt>Peer 99th pct</dt><dd>${fmtN(Math.round(pe.p99))} ${bar(pe.p99, "#8a99ad")}</dd></div></div>`;
}

async function drawGraph(npi, el) {
  const g = await api(`/api/providers/${npi}/graph`);
  const W = el.clientWidth || 600, H = 420;
  const nodes = g.nodes.map((n, i) => ({ ...n, x: W / 2 + Math.cos(i) * 150 * Math.random(), y: H / 2 + Math.sin(i) * 120 * Math.random(), vx: 0, vy: 0 }));
  const idx = Object.fromEntries(nodes.map((n, i) => [n.id, i]));
  const links = g.edges.filter((e) => e.src in idx && e.dst in idx).map((e) => ({ s: idx[e.src], t: idx[e.dst], rel: e.rel, detail: e.detail }));
  const center = nodes.find((n) => n.center);
  if (center) { center.x = W / 2; center.y = H / 2; }
  for (let it = 0; it < 320; it++) {
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j]; let dx = a.x - b.x, dy = a.y - b.y; let d2 = dx * dx + dy * dy + 0.01; const f = (nodes.length > 30 ? 2600 : 7000) / d2;
      const d = Math.sqrt(d2); dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
    }
    for (const l of links) { const a = nodes[l.s], b = nodes[l.t]; const dx = b.x - a.x, dy = b.y - a.y; const d = Math.sqrt(dx * dx + dy * dy) + 0.01; const f = (d - (nodes.length > 30 ? 70 : 130)) * 0.03; a.vx += (dx / d) * f; a.vy += (dy / d) * f; b.vx -= (dx / d) * f; b.vy -= (dy / d) * f; }
    for (const n of nodes) { n.vx += (W / 2 - n.x) * 0.002; n.vy += (H / 2 - n.y) * 0.003; if (n.center) { n.vx = n.vy = 0; continue; } n.x += Math.max(-8, Math.min(8, n.vx)); n.y += Math.max(-8, Math.min(8, n.vy)); n.vx *= 0.6; n.vy *= 0.6; n.x = Math.max(20, Math.min(W - 20, n.x)); n.y = Math.max(20, Math.min(H - 20, n.y)); }
  }
  const color = (n) => n.type === "exclusion" ? "#c92a2a" : n.type === "address" ? "#b7791f" : n.type === "person" ? "#6741d9" : n.type === "enrollment" ? "#8a99ad" : n.alert_id ? "#e8590c" : "#1f6fb2";
  const shape = (n) => {
    const r = n.center ? 13 : 8, c = color(n);
    if (n.type === "address") return `<rect x="${n.x - r}" y="${n.y - r * 0.7}" width="${2 * r}" height="${1.4 * r}" rx="2" fill="#fff6e0" stroke="${c}" stroke-width="2"/>`;
    if (n.type === "exclusion") return `<polygon points="${n.x},${n.y - r} ${n.x + r},${n.y + r * 0.8} ${n.x - r},${n.y + r * 0.8}" fill="#fdecec" stroke="${c}" stroke-width="2"/>`;
    if (n.org) return `<rect x="${n.x - r}" y="${n.y - r}" width="${2 * r}" height="${2 * r}" rx="3" fill="#fff" stroke="${c}" stroke-width="${n.center ? 3 : 2}"/>`;
    return `<circle cx="${n.x}" cy="${n.y}" r="${r}" fill="${n.center ? c : "#fff"}" stroke="${c}" stroke-width="2"/>`;
  };
  const showLabels = nodes.length <= 26;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    ${links.map((l) => { const a = nodes[l.s], b = nodes[l.t]; return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${l.rel === "excluded_as" ? "#c92a2a" : "#b8c4d4"}" stroke-width="${l.rel === "excluded_as" ? 2 : 1}"><title>${esc(l.rel)} ${esc(l.detail || "")}</title></line>`; }).join("")}
    ${nodes.map((n) => `<g class="${n.type === "provider" ? "click" : ""}" data-npi="${n.type === "provider" ? esc(n.id) : ""}" style="cursor:${n.type === "provider" ? "pointer" : "default"}">${shape(n)}
      <title>${esc(n.label)}${n.sub ? " · " + esc(n.sub) : ""}${n.score != null ? ` · score ${n.score} ${n.tier}` : ""}</title>
      ${showLabels || n.center || n.type !== "provider" ? `<text x="${n.x + 12}" y="${n.y + 4}" font-size="10.5" fill="#14213d">${esc(String(n.label).slice(0, 28))}</text>` : ""}</g>`).join("")}
  </svg>`;
  el.querySelectorAll("g[data-npi]").forEach((gEl) => gEl.dataset.npi && gEl.addEventListener("click", () => { location.hash = `#/provider/${gEl.dataset.npi}`; }));
}
const graphLegend = `<div class="legend"><span><i style="background:#1f6fb2"></i>provider</span><span><i style="background:#e8590c"></i>alerted provider</span><span><i style="background:#b7791f"></i>address</span><span><i style="background:#6741d9"></i>person (official)</span><span><i style="background:#c92a2a"></i>exclusion</span><span>squares = organizations</span></div>`;

async function viewAlert(id) {
  const d = await api(`/api/alerts/${id}`);
  const a = d.alert, p = d.provider;
  const assessment = p.status === "Deactivated" ? "Needs attention" : a.tier === "Critical" || a.tier === "High" ? "Needs attention" : "To review";
  const canWrite = ["reviewer", "supervisor", "local_demo"].includes(SESSION.role);
  const canEscalate = ["supervisor", "local_demo"].includes(SESSION.role);
  const closed = ["Dismissed", "Referred"].includes(a.status);
  const disp = d.dispositions.map((x) => `<tr><td>${esc(String(x.ts).slice(0, 16).replace("T", " "))}</td><td>${esc(x.actor)}</td><td>${esc(x.action)}</td><td>${esc(x.supervisor || "")}</td><td>${esc(x.reason || "")}</td></tr>`).join("");
  const related = d.related.filter((r) => r.alert_id !== a.alert_id);
  app.innerHTML = `
    <p class="small"><a href="${MODE === "government" ? "#/leads" : "#/queue"}">← ${MODE === "government" ? "Leads" : "Alerts"}</a></p>
    <div class="banner"><b>Potential anomaly for human review.</b> Verify the evidence before making a decision. Reasons below are tied to the exact source file, version, and row.</div>
    <div class="review-path card" aria-label="Case review steps"><div><span>1</span><strong>Check identity</strong><small>Confirm the NPI in <a href="#/live/${esc(a.npi)}">live NPPES</a>.</small></div>
      <div><span>2</span><strong>Review evidence</strong><small>Inspect the dated files and synthetic activity in the Evidence tab.</small></div>
      <div><span>3</span><strong>Record a decision</strong><small>${esc(nextReviewStep(a))}. Case status: ${esc(a.status)}.</small></div></div>
    <div class="card alerthead">
      <div><div class="small muted mono">${esc(a.alert_id)} · checked as of ${mdy(a.created_as_of)}</div>
        <h1>${esc(p.display_name || a.npi)}</h1>
        <div class="muted"><span class="mono">${esc(a.npi)}</span> · ${esc(p.taxonomy_desc || "")} · ${esc(title(p.city))}, ${esc(p.state)}</div>
        <div class="finding"><strong>${esc(assessment)}</strong><p>${esc(d.hits[0]?.reason || "A signal needs human review.")}</p><div class="small muted">Case: ${statusTag(a.status)} · Source snapshot ${mdy(a.created_as_of)}</div></div></div>
      <div style="text-align:center"><div class="big" style="color:var(--t-${a.tier === "Critical" ? "crit" : a.tier === "High" ? "high" : "elev"})">${a.score}</div><div class="small muted">${esc(a.tier)} · 0–1000</div></div>
      <div class="actions">
        ${["New", "Monitoring"].includes(a.status) ? `<button class="b primary" data-act="review" ${!canWrite ? "disabled" : ""}>Start review</button>` : `<button class="b primary" id="primary-evidence">Review evidence</button>`}
        <details class="more-actions"><summary>More case actions</summary><div class="more-action-list">
        <button class="b" data-act="monitor" ${!canWrite || !["New", "In review", "Escalated"].includes(a.status) ? "disabled" : ""}>Monitor</button>
        <button class="b danger" data-act="dismiss" ${!canWrite || closed || a.status === "Escalated" ? "disabled" : ""}>Dismiss</button>
        <button class="b primary" data-act="escalate" ${!canEscalate || !["In review", "Monitoring"].includes(a.status) ? "disabled" : ""}>Escalate</button>
        <button class="b" data-act="reopen" ${!canWrite || a.status !== "Dismissed" ? "disabled" : ""}>Reopen</button>
        <button class="b primary" data-act="refer" ${!canWrite || a.status !== "Escalated" ? "disabled" : ""}>Refer</button>
        <button class="b" id="rerun" ${!canWrite ? "disabled" : ""}>Re-run as of ${mdy(a.created_as_of)}</button>
        <button class="b" id="pkg" ${canWrite && ["Escalated", "Referred"].includes(a.status) ? "" : "disabled title='Escalate with supervisor sign-off first'"}>Referral package</button></div></details>
      </div>
    </div>
    <nav class="case-tabs" aria-label="Case sections">${["Why flagged", "Timeline", "Relationships", "Evidence", "Peers", "History"].map((label, i) => `<button class="case-tab ${i === 0 ? "active" : ""}" type="button" data-case-tab="${i}" aria-pressed="${i === 0}">${label}</button>`).join("")}</nav>
    <div id="rerunres"></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="stack">
        <div class="card" data-case-panel="0"><h2>Why flagged (${d.hits.length})</h2>${d.hits.map((h) => `<div class="plain-reason"><strong>${esc(h.reason)}</strong></div>`).join("")}
          <p class="small muted">Open Evidence for the dated source records and rule details.</p></div>
        <div data-case-panel="0">${scoreChart(d.score_explanation)}</div>
        <div class="card hidden" data-case-panel="3"><h2>Source evidence</h2>${d.hits.map(reasonCard).join("")}</div>
        <div class="card hidden" data-case-panel="3"><h2 style="display:flex;align-items:center;gap:8px">Evidence-linked draft <button class="b" id="agentrun" style="margin-left:auto" ${!canWrite ? "disabled" : ""}>Run agents</button></h2><div id="agent"></div></div>
        <div class="card hidden" data-case-panel="3"><h2>Template case summary</h2><p class="small muted">Assembled from the reasons above. Use Run agents for an evidence-linked draft and QA result.</p>
          <ol class="summary">${d.draft_summary.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>
        <div class="card hidden" data-case-panel="1"><h2>Activity timeline</h2>${timelineSvg(p, d.hits)}</div>
        <div class="card hidden" data-case-panel="2"><h2>Relationship map</h2><div id="graph" class="graph"></div>${graphLegend}</div>
      </div>
      <div class="stack">
        <div class="hidden" data-case-panel="3">${profileCard(p)}</div>
        <div class="hidden" data-case-panel="4">${peersCard(p) || `<div class="card">Peer data unavailable.</div>`}</div>
        <div class="card hidden" data-case-panel="2"><h2>Coordination check</h2>${related.length ? `<p class="small">${related.length} other alerts share an address, official, or phone with this provider. Review together to avoid duplicate or conflicting actions.</p>
          <table class="t"><tr><th>Alert</th><th>Score</th><th>Status</th><th>Shared</th></tr>${related.slice(0, 12).map((r) => `<tr class="click" data-href="#/alert/${r.alert_id}"><td class="mono">${r.alert_id}</td><td>${scoreBadge(r.score, r.tier)}</td><td>${statusTag(r.status)}</td><td class="small">${esc(String(r.via).split(":")[0].toLowerCase())}</td></tr>`).join("")}</table>${related.length > 12 ? `<p class="small muted">+${related.length - 12} more</p>` : ""}` : `<p class="small muted">No other open alerts share an address, official, or phone with this provider.</p>`}</div>
        <div class="card hidden" data-case-panel="3"><h2>Top billed codes</h2><table class="t">${(p.top_codes || []).map((c) => `<tr><td class="mono">${esc(c.hcpcs)}</td><td class="small">${esc(c.descr || "")}</td><td><span class="chip">${esc(c.family || "")}</span></td><td style="text-align:right">${fmtN(c.lines)}</td></tr>`).join("")}</table></div>
        <div class="card hidden" data-case-panel="5"><h2>Review history</h2>${disp ? `<table class="t"><tr><th>When (UTC)</th><th>Reviewer</th><th>Action</th><th>Supervisor</th><th>Reason</th></tr>${disp}</table>` : `<p class="small muted">No actions yet.</p>`}</div>
      </div>
    </div>`;
  document.querySelectorAll(".case-tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".case-tab").forEach((button) => { button.classList.toggle("active", button === tab); button.setAttribute("aria-pressed", String(button === tab)); });
    document.querySelectorAll("[data-case-panel]").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.casePanel !== tab.dataset.caseTab));
    if (tab.dataset.caseTab === "2") drawGraph(a.npi, $("#graph"));
  }));
  $("#primary-evidence")?.addEventListener("click", () => document.querySelector('[data-case-tab="3"]').click());
  loadAgent(a.alert_id);
  document.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => actionModal(a, b.dataset.act)));
  $("#rerun").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Re-running from archived inputs…";
    try {
      const r = await api(`/api/alerts/${a.alert_id}/rerun?actor=${encodeURIComponent(reviewer() || "reviewer")}`, { method: "POST" });
      $("#rerunres").innerHTML = `<div class="card" style="margin-top:12px;border-color:${r.reproduced ? "#9fd4ad" : "#f1c4c4"}"><b class="${r.reproduced ? "pass" : "fail"}">${r.reproduced ? "Reproduced exactly" : "Did not reproduce"}</b>
        <span class="small"> · replayed run <span class="mono">${esc(r.run_id)}</span> as of ${mdy(r.as_of)} from the write-once raw zone and the archived rule snapshot · score ${r.original_score} → ${r.replayed_score} · fingerprint <span class="mono">${esc(r.original_fingerprint)}</span> → <span class="mono">${esc(r.replayed_fingerprint)}</span></span></div>`;
    } catch (err) { toast(err.message); }
    e.target.disabled = false; e.target.textContent = `Re-run as of ${mdy(a.created_as_of)}`;
  });
  $("#pkg").addEventListener("click", () => window.open(`/api/alerts/${a.alert_id}/package?actor=${encodeURIComponent(reviewer() || "reviewer")}`, "_blank"));
}

function actionModal(a, act) {
  const labels = { review: "Start review", monitor: "Monitor: raise watch level and re-check next cycle", dismiss: "Dismiss (reason required)", escalate: "Escalate (supervisor sign-off required)", reopen: "Reopen", refer: "Refer" };
  const quick = { dismiss: ["Data lag: NPPES not yet updated", "Legitimate locum / coverage", "Identity mismatch: different person", "Known group practice"], monitor: ["Re-check after next claims feed", "Waiting on state board data"], escalate: ["Evidence supports referral", "Pre-payment hold recommended"], review: [], reopen: [], refer: [] }[act];
  const pilot = SESSION.data_mode === "synthetic";
  const reasonInput = pilot
    ? `<label>Standard reason (no patient information)</label><select id="m-reason">${["", ...quick].map((q) => `<option value="${esc(q)}">${esc(q || "Select a reason")}</option>`).join("")}</select>`
    : `<label>Reason</label><textarea id="m-reason" rows="3"></textarea>
      <div style="margin-top:6px">${quick.map((q) => `<button class="b small" style="padding:3px 8px;margin:2px" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
  const m = $("#modal");
  m.innerHTML = `<div class="box"><h2>${esc(labels[act])}</h2><div class="small muted">${esc(a.alert_id)} · NPI ${esc(a.npi)}</div>
    <label>Reviewer</label><input id="m-actor" value="${esc(reviewer())}" placeholder="your name" ${SESSION.actor ? "readonly" : ""}>
    ${act === "review" ? "" : reasonInput}
    ${act === "escalate" && !SESSION.actor ? `<label>Supervisor sign-off</label><input id="m-sup" placeholder="supervisor name">` : ""}
    <div class="row"><button class="b" id="m-cancel">Cancel</button><button class="b primary" id="m-ok">Confirm</button></div></div>`;
  m.classList.remove("hidden");
  m.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { $("#m-reason").value = b.dataset.q; }));
  $("#m-cancel").onclick = () => m.classList.add("hidden");
  $("#m-ok").onclick = async () => {
    const body = { actor: $("#m-actor").value.trim(), action: act, reason: $("#m-reason")?.value.trim() || "", supervisor: $("#m-sup")?.value.trim() || "" };
    try {
      try { localStorage.setItem("npi.reviewer", body.actor); } catch {}
      $("#reviewer").value = body.actor;
      const r = await api(`/api/alerts/${a.alert_id}/disposition`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      m.classList.add("hidden"); toast(`${a.alert_id} → ${r.status}`); viewAlert(a.alert_id);
    } catch (e) { toast(e.message); }
  };
}

async function viewProvider(npi) {
  const d = await api(`/api/providers/${npi}`);
  const p = d.provider;
  app.innerHTML = `<p class="small"><a href="#" data-back="1">← Back</a></p>
    <div class="card alerthead"><div><div class="small muted">Synthetic demo provider check · source snapshot ${mdy(d.as_of)}</div><h1>${esc(p.display_name || npi)}</h1>
      <div class="muted"><span class="mono">${esc(npi)}</span> · ${esc(p.taxonomy_desc || "")} · ${esc(title(p.city))}, ${esc(p.state)}</div>
      <div class="finding"><strong>${esc(d.assessment.label)}</strong><p>${esc(d.assessment.sentence)}</p></div>
      <p class="small muted">This synthetic result covers demo sources as of ${mdy(d.as_of)}. It does not verify credentials or establish wrongdoing. <a href="#/live/${esc(npi)}">Check this NPI in live NPPES ↗</a></p></div>
      <div style="margin-left:auto;text-align:right">${d.score ? scoreBadge(d.score.score, d.score.tier) : `<span class="score tier-Low">0</span><span class="tiertxt">Low</span>`}
      ${d.alert ? `<div style="margin-top:8px"><a href="#/alert/${d.alert.alert_id}">Open alert ${esc(d.alert.alert_id)} →</a></div>` : `<div class="small muted" style="margin-top:8px">No alert this run</div>`}</div></div>
    <div class="grid g2" style="margin-top:16px"><div class="stack">
      <div class="card"><h2>What we found (${d.hits.length})</h2>${d.hits.length ? d.hits.map(plainReasonCard).join("") : `<p class="muted small">No rule fired for this NPI in the latest run.</p>`}</div>
      ${d.score ? scoreChart(d.score_explanation) : ""}
      <details class="card"><summary>Activity timeline</summary>${timelineSvg(p, d.hits)}</details>
      <details class="card" id="provider-graph"><summary>Relationship map</summary><div id="graph" class="graph"></div>${graphLegend}</details>
    </div><div class="stack"><details class="card"><summary>Source profile</summary>${profileCard(p)}</details>${peersCard(p)}</div></div>`;
  $("#provider-graph").addEventListener("toggle", (e) => { if (e.target.open) drawGraph(npi, $("#graph")); });
}

/* ---------------------------------------------------------------- scorecard, rules, sources, audit */
async function viewScorecard() {
  const s = await api("/api/scorecard");
  const sweep = s.sweep || [];
  const W = 760, H = 150, L = 30;
  const xs = (i) => L + (i * (W - L - 20)) / Math.max(sweep.length - 1, 1);
  const ys = (v) => H - 24 - ((H - 44) * v) / s.total;
  const chart = sweep.length ? `<svg viewBox="0 0 ${W} ${H}" width="100%"><line x1="${L}" x2="${W - 20}" y1="${ys(s.total)}" y2="${ys(s.total)}" stroke="#dbe3ee" stroke-dasharray="3 3"/>
    <polyline fill="none" stroke="#2f9e44" stroke-width="2.5" points="${sweep.map((p, i) => `${xs(i)},${ys(p.caught)}`).join(" ")}"/>
    ${sweep.map((p, i) => `<circle cx="${xs(i)}" cy="${ys(p.caught)}" r="3.5" fill="#2f9e44"><title>as of ${mdy(p.as_of)}: ${p.caught} caught, ${p.false_alerts} false alerts</title></circle><text x="${xs(i)}" y="${H - 6}" font-size="10" text-anchor="middle" fill="#5b6b82">${p.as_of.slice(5)}</text>`).join("")}
    <text x="2" y="${ys(s.total) + 4}" font-size="10" fill="#5b6b82">${s.total}</text><text x="2" y="${ys(0) + 4}" font-size="10" fill="#5b6b82">0</text></svg>` : `<p class="muted small">Run <span class="mono">npi scorecard --sweep</span> to replay weekly as-of dates.</p>`;
  const firstSweep = sweep.length ? sweep[0].as_of : null;
  app.innerHTML = `<h1>Prototype scorecard</h1>
    <p class="muted">Synthetic claims with 12 planted scenarios; the engine ran blind to the sealed answer key (sha256 <span class="mono">${esc(s.answer_key_sha256.slice(0, 16))}…</span>). Run <span class="mono">${esc(s.run_id)}</span>, as of ${mdy(s.as_of)}. These results measure this synthetic dataset only; real-world accuracy and PHI readiness remain unvalidated.</p>
    <div class="kpis">
      <div class="kpi ${s.caught === s.total ? "good" : ""}"><div class="v">${s.caught} / ${s.total}</div><div class="l">Planted scenarios caught</div></div>
      <div class="kpi ${s.false_alerts === 0 ? "good" : ""}"><div class="v">${s.false_alerts}</div><div class="l">Synthetic false alerts (${s.false_alerts_per_1000} per 1,000 NPIs)</div></div>
      <div class="kpi"><div class="v">${s.alerts}</div><div class="l">Alerts raised</div></div>
      <div class="kpi ${s.decoys.every((d) => d.passed) ? "good" : ""}"><div class="v">${s.decoys.filter((d) => d.passed).length} / ${s.decoys.length}</div><div class="l">Guardrail decoys not alerted</div></div>
    </div>
    <div class="card"><h2>Planted scenarios</h2><p class="small muted">Scroll the table horizontally to see detection timing on a narrow screen.</p><div class="tablewrap scorecard-table"><table class="t"><tr><th></th><th>#</th><th>Scenario</th><th>Should trigger</th><th>Fired</th><th>Score</th><th>First bad event</th><th>First detected (weekly as-of)</th></tr>
      ${s.scenarios.map((x) => `<tr class="${x.npi ? "click" : ""}" data-href="#/provider/${x.npi}"><td class="${x.caught ? "pass" : "fail"}">${x.caught ? "PASS" : "MISS"}</td><td>${x.no}</td><td>${esc(x.title)}${x.targets > 1 ? ` <span class="small muted">(${x.targets_caught}/${x.targets} NPIs)</span>` : ""}</td>
        <td>${chips(x.expected_rules)}</td><td>${chips(x.rules_fired, new Set(x.expected_rules), "ok")}</td><td>${x.score ? scoreBadge(x.score, x.tier) : "–"}</td>
        <td>${mdy(x.first_bad_event)}</td><td>${x.first_detected_as_of ? `${x.first_detected_as_of === firstSweep ? "≤ " : ""}${mdy(x.first_detected_as_of)}${x.days_to_detect != null && x.first_detected_as_of !== firstSweep ? ` <span class="small muted">(${x.days_to_detect} d)</span>` : ""}` : "–"}</td></tr>`).join("")}</table></div>
      <p class="small muted">Detection timing is bounded by publication lag: claims feeds land 5 days after month end; the opt-out file and LEIE are monthly. "≤" means already detected at the first replayed date.</p></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>Guardrail decoys (should not alert)</h2><table class="t"><tr><th></th><th>Decoy</th><th>Max score</th><th>Signals</th></tr>
        ${s.decoys.map((d) => `<tr class="click" data-href="#/provider/${d.npi}"><td class="${d.passed ? "pass" : "fail"}">${d.passed ? "PASS" : "FAIL"}</td><td>${esc(d.title)}${d.note ? `<div class="small muted">${esc(d.note)}</div>` : ""}</td><td>${d.max_score}</td><td>${chips(d.rules_fired)}</td></tr>`).join("")}</table></div>
      <div class="stack"><div class="card"><h2>Caught over time (weekly replay)</h2>${chart}</div>
        <div class="card"><h2>Precision by rule</h2><table class="t"><tr><th>Rule</th><th>Hits</th><th>On planted</th><th>Alerts</th><th>Precision</th></tr>
        ${Object.entries(s.rule_stats).map(([r, st]) => `<tr><td><span class="chip">${r}</span></td><td>${st.hits}</td><td>${st.on_planted}</td><td>${st.alerts}</td><td>${st.precision == null ? "–" : (st.precision * 100).toFixed(0) + "%"}</td></tr>`).join("")}</table>
        <p class="small muted">Hits below the alert threshold (e.g. V-02 peer outliers) stay on the watch list by design.</p></div></div></div>`;
}

async function viewRules() {
  const { rules, scoring } = await api("/api/rules");
  const meanings = {Low:"No alert under the current synthetic rules; checked coverage still matters.", Watch:"A weaker signal to revisit as new data arrives.", Elevated:"Meets the current alert setting; review the evidence.", High:"Stronger combined signals; prioritize human review.", Critical:"At least 900 points, often from a hard-evidence rule; verify before acting."};
  const bandRows = scoring.tiers.map((t, i) => `<tr><td>${esc(t.name)}</td><td>${fmtN(t.min)}–${fmtN((scoring.tiers[i + 1]?.min ?? 1001) - 1)}</td><td>${esc(meanings[t.name] || "Human review required")}</td></tr>`).join("");
  app.innerHTML = `<h1>Score bands and rule catalog</h1><p class="muted">Draft definitions for NeXari review. This pilot currently has ${rules.length} implemented rules. A score orders synthetic signals for review; it does not measure the probability of wrongdoing.</p>
    <div class="card" style="margin-bottom:16px"><h2>What the bands mean</h2><div class="tablewrap"><table class="t"><thead><tr><th>Band</th><th>Points</th><th>Plain-language meaning</th></tr></thead><tbody>${bandRows}</tbody></table></div>
      <p class="small muted">A red rule indicator means its hard-evidence conditions were met and sets a ${scoring.hard_floor}-point floor. High and Critical score colors are prioritization cues, not judgments. The alert setting is ${scoring.alert_threshold}; changing <span class="mono">rules/scoring.yaml</span> requires a reviewed new run and does not rewrite earlier results.</p></div>
    <p class="muted">Each rule below has a version, owner, parameters, and a plain-language reason. Reviewer decisions provide labels for later tuning.</p>
    <div class="tablewrap"><table class="t"><tr><th>ID</th><th>Signal</th><th>Type</th><th>Weight</th><th>Version</th><th>Phase</th><th>Hits</th><th>Alerts</th><th>Labels</th><th>Parameters</th></tr>
    ${rules.map((r) => `<tr><td><span class="chip ${r.type === "hard" ? "hard" : "soft"}">${esc(r.id)}</span><div class="small mono muted">${esc(r.reason_code)}</div></td>
      <td><b>${esc(r.name)}</b><div class="small muted">${esc(r.description)}</div></td><td>${esc(r.type)}</td><td>${r.type === "hard" ? "floor 900" : r.weight}</td>
      <td>${esc(r.version)}</td><td class="small">${esc(r.phase)}</td><td>${r.hits}</td><td>${r.alerts}</td>
      <td class="small">${r.dismissed || r.escalated ? `${r.escalated || 0} escalated · ${r.dismissed || 0} dismissed` : "–"}</td>
      <td class="small mono">${esc(Object.entries(r.params || {}).map(([k, v]) => `${k}=${v}`).join(" "))}</td></tr>`).join("")}</table></div>
    <div class="card" style="margin-top:16px"><h2>Score assembly</h2><dl class="kv">
      <dt>Hard rules</dt><dd>Any confirmed hard rule sets a floor of ${scoring.hard_floor}. Identity matches below ${scoring.hard_min_confidence} confidence never hard-stop; they add ${scoring.identity_review_weight} as "identity review".</dd>
      <dt>Soft signals</dt><dd>Weighted points add up; each family is capped (${Object.entries(scoring.family_caps).map(([k, v]) => `${k} ${v}`).join(", ")}).</dd>
      <dt>Network</dt><dd>Soft points × ${scoring.network_multiplier} when linked to a confirmed exclusion (N-01).</dd>
      <dt>Tiers</dt><dd>${scoring.tiers.map((t) => `${t.name} ≥ ${t.min}`).join(" · ")} · alert at ≥ ${scoring.alert_threshold}</dd></dl></div>`;
}

async function viewSources() {
  const d = await api("/api/sources");
  const byKey = Object.fromEntries(d.sources.map((s) => [s.key, s]));
  app.innerHTML = `<h1>Sources and lineage</h1><p class="muted">Files land write-once in the raw zone, stamped with SHA-256 and version date. Every evidence citation points back to a file, version, and row here.</p>
    <div class="tablewrap"><table class="t"><tr><th>Source</th><th>Version</th><th>File</th><th>Rows</th><th>Checks</th><th>SHA-256</th><th>Origin</th></tr>
    ${d.loads.map((l) => `<tr><td><b>${esc(byKey[l.source]?.label || l.source)}</b><div class="small muted">${esc(byKey[l.source]?.kind || "")}</div></td><td>${mdy(l.version)}</td><td class="mono small">${esc(l.file)}</td><td>${fmtN(l.rows)}</td>
      <td><span class="chip ${l.status === "ok" ? "ok" : "warn"}">${esc(l.status)}</span> <span class="small muted">null key ${((l.checks.null_key_rate || 0) * 100).toFixed(1)}%${l.checks.missing_columns?.length ? " · missing " + esc(l.checks.missing_columns.join(", ")) : ""}</span></td>
      <td class="mono small">${esc(l.sha256.slice(0, 16))}…</td><td class="small">${esc(byKey[l.source]?.origin || "")}</td></tr>`).join("")}</table></div>`;
}

async function viewAudit() {
  const rows = await api("/api/audit");
  app.innerHTML = `<h1>Audit log</h1><p class="muted">Append-only: runs, re-runs, dispositions, and package exports. The API exposes no update or delete.</p>
    <div class="tablewrap"><table class="t"><tr><th>#</th><th>When (UTC)</th><th>Actor</th><th>Action</th><th>Object</th><th>Details</th></tr>
    ${rows.map((r) => `<tr><td>${r.seq}</td><td class="small">${esc(String(r.ts).slice(0, 19).replace("T", " "))}</td><td>${esc(r.actor)}</td><td><span class="chip">${esc(r.action)}</span></td><td class="mono small">${esc(r.object)}</td><td class="mono small" style="max-width:520px;word-break:break-all">${esc(r.details)}</td></tr>`).join("")}</table></div>`;
}

document.addEventListener("click", (e) => {
  const back = e.target.closest("[data-back]");
  if (back) { e.preventDefault(); history.back(); return; }
  const tog = e.target.closest("[data-toggle]");
  if (tog) { document.querySelectorAll("." + tog.dataset.toggle).forEach((r) => r.classList.toggle("hidden")); return; }
  const el = e.target.closest("[data-href]");
  if (el && !e.target.closest("a,button")) location.hash = el.dataset.href;
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const row = e.target.closest('tr[data-href], tr[data-toggle]');
  if (!row || e.target !== row) return;
  e.preventDefault();
  row.click();
});

/* ---------------------------------------------------------------- L7 agent draft */
function agentCard(r) {
  if (!r) return `<p class="small muted">No agent draft yet. The context agent reads this alert with read-only tools, the narrative agent drafts, and an independent QA agent plus deterministic guardrails check every sentence before you see it.</p>`;
  const badge = r.status === "passed" ? `<span class="chip ok">passed guardrails</span>` : r.status === "fallback_template" ? `<span class="chip warn">fallback: template draft</span>` : `<span class="chip hard">blocked</span>`;
  const checks = (r.final_checks?.checks || []).map((c) => `<span class="chip ${c.passed ? "ok" : "hard"}" title="${esc(c.details.join("; "))}">${esc(c.id)} ${esc(c.name)}</span>`).join(" ");
  const ledger = Object.fromEntries((r.ledger || []).map((e) => [e.id, e]));
  return `<div style="margin-bottom:8px">${badge} <span class="small muted">model ${esc(r.models.agent)} · QA ${esc(r.models.qa)} · ${r.tool_calls.length} tool calls${r.tool_violations.length ? ` · <b>${r.tool_violations.length} tool calls denied</b>` : ""} · ${r.repairs} repair round${r.repairs === 1 ? "" : "s"} · $${r.cost_usd} · prompts ${esc(Object.entries(r.prompt_versions).map(([k, v]) => k + "@" + v).join(", "))}</span></div>
    ${r.injection_flags?.length ? `<div class="banner">Instruction-like text was found in provider data and treated as data only: ${esc(r.injection_flags.slice(0, 3).join(" | "))}</div>` : ""}
    <ol class="summary">${r.draft.sentences.map((s) => `<li>${esc(s.text)} ${s.citations.map((c) => `<span class="eid" title="${esc(ledger[c]?.text || "")}">[${esc(c)}]</span>`).join(" ")}</li>`).join("")}</ol>
    ${r.draft.open_questions?.length ? `<h3>Open questions</h3><ul class="small">${r.draft.open_questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
    <h3>Guardrail checks</h3><div>${checks}</div>
    ${r.events.length ? `<p class="small muted">${esc(r.events.join(" · "))}</p>` : ""}
    <details><summary>Evidence ledger (${r.ledger.length} items) and agent trace</summary><ul class="evid">${r.ledger.map((e) => `<li><span class="eid">${e.id}</span> <span class="chip">${esc(e.kind)}</span> ${esc(e.text)}</li>`).join("")}</ul>
      <pre class="small mono" style="white-space:pre-wrap">${esc(JSON.stringify(r.trace, null, 1))}</pre></details>
    <p class="small muted">Draft only. Agents cannot change an alert's status; a reviewer decides and signs.</p>`;
}
async function loadAgent(alertId) {
  const box = $("#agent");
  if (!box) return;
  try { box.innerHTML = agentCard(await api(`/api/alerts/${alertId}/draft`)); } catch (e) { box.innerHTML = agentCard(null); }
  $("#agentrun").onclick = async (ev) => {
    ev.target.disabled = true; ev.target.textContent = "Agents working…";
    try { box.innerHTML = agentCard(await api(`/api/alerts/${alertId}/draft?actor=${encodeURIComponent(reviewer() || "reviewer")}`, { method: "POST" })); }
    catch (e) { toast(e.message); }
    ev.target.disabled = false; ev.target.textContent = "Run agents";
  };
}

route();
