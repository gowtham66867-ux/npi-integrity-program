"""L8/L9: alert & case management API and the reviewer console (government face).

No PHI: prototype runs on public + synthetic data only. Deployed identity
comes from a verified IAP JWT; local demo runs allow a typed reviewer name.
"""
from __future__ import annotations

import html
from contextlib import contextmanager
from collections import Counter
import json
import os
import threading
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from .security import authenticate, actor_for
from .cases import store, case_key, event

from . import config
from .ingest import SOURCES
from .pipeline import audit, connect, latest_run, now, rerun_alert
from .ux import field_result, npi_valid, provider_assessment
from .nppes_live import lookup as lookup_nppes

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="NPI Integrity - reviewer console", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_conn = None
_lock = threading.Lock()
ALLOWED_DOMAIN = os.environ.get("NPI_ALLOWED_DOMAIN", "")  # e.g. "texmed.us" behind IAP / Cloud Run auth


@app.middleware("http")
async def guard(request: Request, call_next):
    """Deployment guardrails: identity-aware proxy domain check, body-size cap, security headers."""
    if os.environ.get("K_SERVICE") and os.environ.get("NPI_DATA_MODE") != "synthetic":
        return JSONResponse({"detail": "deployment is restricted to synthetic data"}, status_code=503)
    if request.url.path != "/healthz":
        try:
            await run_in_threadpool(authenticate, request, ALLOWED_DOMAIN)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if request.method in ("POST", "PUT", "PATCH"):
        origin = request.headers.get("origin")
        # Cloud Run terminates HTTPS before forwarding to the container, so
        # request.base_url can be http:// for either official HTTPS service URL.
        if origin:
            parsed = urlsplit(origin)
            same_host = parsed.netloc.lower() == request.headers.get("host", "").lower()
            expected_scheme = "https" if os.environ.get("NPI_PUBLIC_ORIGIN") else request.url.scheme
            allowed_host = (not os.environ.get("NPI_PUBLIC_ORIGIN") or
                            (parsed.hostname or "").endswith(".run.app"))
            if not (same_host and parsed.scheme == expected_scheme and allowed_host and
                    not parsed.path and not parsed.query and not parsed.fragment):
                return JSONResponse({"detail": "cross-origin write refused"}, status_code=403)
    # Read the actual body: Content-Length can be absent, incorrect, or chunked.
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > 64_000:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        chunks.append(chunk)
    request._body = b"".join(chunks)
    resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                         "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                         "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                    "script-src 'self'; img-src 'self' data:; frame-ancestors 'none'"})
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/session")
def session(request: Request):
    role = getattr(request.state, "role", "local_demo")
    return {"actor": getattr(request.state, "actor", None),
            "role": role,
            "modes": ["clinic", "government"] if role in ("local_demo", "reviewer", "supervisor") else ["clinic"],
            "data_mode": os.environ.get("NPI_DATA_MODE", "local_demo")}


@contextmanager
def historical(cur, run_id):
    if run_id == latest_run(cur):
        yield cur
        return
    from .pipeline import snapshot_run
    mem = snapshot_run(cur, run_id)
    try:
        yield mem
    finally:
        mem.close()

STATUS_FOR_ACTION = {"review": "In review", "monitor": "Monitoring", "dismiss": "Dismissed", "escalate": "Escalated",
                     "refer": "Referred", "reopen": "In review"}


def db():
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect()
        return _conn.cursor()


def rows(cur, sql, params=()):
    res = cur.execute(sql, list(params))
    cols = [d[0] for d in res.description]
    return [dict(zip(cols, r)) for r in res.fetchall()]


def one(cur, sql, params=()):
    r = rows(cur, sql, params)
    return r[0] if r else None


def jd(v):
    if isinstance(v, (date,)):
        return v.isoformat()
    return v


def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, tuple):
        return [clean(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def current_run(cur, run_id=None):
    rid = run_id or latest_run(cur)
    if rid and not one(cur, "SELECT run_id FROM runs WHERE run_id = ? AND status = 'completed'", [rid]):
        raise HTTPException(404, "run not found")
    if not rid:
        raise HTTPException(404, "no completed run yet: run `npi demo` first")
    return rid


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/summary")
def summary(run_id: str | None = None):
    cur = db()
    rid = current_run(cur, run_id)
    with historical(cur, rid) as snapshot:
        result = _summary(snapshot, rid)
    result["runs"] = clean(rows(cur, "SELECT run_id, as_of, finished_at, n_alerts FROM runs ORDER BY finished_at DESC LIMIT 10"))
    return result


def _summary(cur, rid):
    run = one(cur, "SELECT * EXCLUDE (manifest_json, rules_json) FROM runs WHERE run_id = ?", [rid])
    tiers = rows(cur, "SELECT tier, count(*) n FROM scores WHERE run_id = ? GROUP BY 1", [rid])
    counts = Counter(a["status"] for a in overlay(rows(cur, "SELECT * FROM alerts WHERE run_id = ?", [rid])))
    status = [{"status": k, "n": v} for k, v in counts.items()]
    monitored = one(cur, "SELECT count(*) n, count(*) FILTER (status = 'Active') active FROM providers")
    claims = one(cur, "SELECT count(*) n, min(service_date) lo, max(service_date) hi, "
                      "count(DISTINCT rendering_npi) npis FROM claims")
    sc = one(cur, "SELECT body FROM scorecards WHERE run_id = ? ORDER BY created_at DESC LIMIT 1", [rid])
    sc = json.loads(sc["body"]) if sc else None
    return clean({"run": run, "tiers": {t["tier"]: t["n"] for t in tiers}, "status": {s["status"]: s["n"] for s in status},
                  "monitored": monitored, "claims": claims,
                  "scorecard": {k: sc[k] for k in ("caught", "total", "false_alerts", "false_alerts_per_1000", "alerts")}
                  if sc else None,
                  "runs": []})



def overlay(items):
    keys = [case_key(a) for a in items]
    states = store().states(keys)
    for a, key in zip(items, keys):
        a["status"] = states.get(key, a["status"])
    return items


def case_alert(alert_id):
    a = one(db(), "SELECT * FROM alerts WHERE alert_id = ?", [alert_id])
    if not a:
        raise HTTPException(404, alert_id)
    return a


def case_history(a):
    return [{"alert_id": a["alert_id"], "ts": e["ts"], "actor": e["actor"],
             "action": e["action"].split(":", 1)[1], "reason": e["details"].get("reason", ""),
             "supervisor": e["details"].get("supervisor", "")} for e in reversed(store().history(case_key(a)))]

def _alert_rows(cur, rid, where="", params=()):
    return rows(cur, f"""
        SELECT a.alert_id, a.fingerprint, a.npi, a.score, a.tier, a.status, a.created_as_of, p.display_name, p.entity_type,
               p.taxonomy_desc, p.city, p.state, p.status AS npi_status, p.address_key,
               (SELECT list(h.rule_id ORDER BY h.hard DESC, h.weight DESC) FROM rule_hits h
                 WHERE h.run_id = a.run_id AND h.npi = a.npi) AS rules,
               (SELECT h.reason FROM rule_hits h WHERE h.run_id = a.run_id AND h.npi = a.npi
                 ORDER BY h.hard DESC, h.weight DESC LIMIT 1) AS top_reason
        FROM alerts a LEFT JOIN providers p ON p.npi = a.npi
        WHERE a.run_id = ? {where}
        ORDER BY a.score DESC, a.alert_id""", [rid, *params])


@app.get("/api/alerts")
def alerts(run_id: str | None = None):
    cur = db()
    rid = current_run(cur, run_id)
    with historical(cur, rid) as snapshot:
        return _alerts(snapshot, rid)


def _alerts(cur, rid):
    items = overlay(_alert_rows(cur, rid))
    # cluster alerts that share a practice address so one scheme reads as one item in the queue
    by_addr: dict[str, list] = {}
    for a in items:
        if a["address_key"]:
            by_addr.setdefault(a["address_key"], []).append(a["alert_id"])
    for a in items:
        peers = by_addr.get(a["address_key"], [])
        a["cluster_size"] = len(peers) if len(peers) > 1 else 0
        a["cluster_key"] = a["address_key"] if len(peers) > 1 else None
    return clean(items)


@app.get("/api/watchlist")
def watchlist(run_id: str | None = None):
    cur = db()
    rid = current_run(cur, run_id)
    with historical(cur, rid) as snapshot:
        return _watchlist(snapshot, rid)


def _watchlist(cur, rid):
    return clean(rows(cur, """
        SELECT s.npi, s.score, s.tier, s.rules, p.display_name, p.taxonomy_desc, p.city, p.state,
               (SELECT h.reason FROM rule_hits h WHERE h.run_id = s.run_id AND h.npi = s.npi
                 ORDER BY h.weight DESC LIMIT 1) AS top_reason
        FROM scores s LEFT JOIN providers p ON p.npi = s.npi
        WHERE s.run_id = ? AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.run_id = s.run_id AND a.npi = s.npi)
        ORDER BY s.score DESC, s.npi""", [rid]))


def _source_label(key):
    s = SOURCES.get(key)
    return s.label if s else {"nppes_rows": "NPPES"}.get(key, key)


def _evidence(cur, hits, run_id):
    claim_ids = sorted({c for h in hits for c in (h["evidence_claims"] or [])})
    claims = {}
    if claim_ids:
        for r in rows(cur, """SELECT c.*, rc.descr FROM claims c LEFT JOIN ref_codes rc USING (hcpcs)
                              WHERE line_id IN (SELECT unnest(?::VARCHAR[]))""", [claim_ids]):
            claims[r["line_id"]] = r
    shas = {(r["source"], str(r["version"])): r for r in rows(
        cur, "SELECT source, version, file, sha256 FROM source_loads WHERE run_id = ?", [run_id])}
    n = 0
    for h in hits:
        h["facts"] = json.loads(h["facts"]) if isinstance(h["facts"], str) else h["facts"]
        srcs = json.loads(h["evidence_sources"]) if isinstance(h["evidence_sources"], str) else h["evidence_sources"]
        h["sources"] = []
        for s in srcs:
            n += 1
            meta = shas.get((s.get("source"), str(s.get("version"))), {})
            h["sources"].append({**s, "eid": f"E{n}", "label": _source_label(s.get("source")),
                                 "file": meta.get("file"), "sha256": (meta.get("sha256") or "")[:16]})
        h["claims"] = [claims[c] for c in (h["evidence_claims"] or []) if c in claims]
        if h["claims"]:
            n += 1
            h["claims_eid"] = f"E{n}"
    return hits


def _profile(cur, npi):
    p = one(cur, "SELECT * FROM providers WHERE npi = ?", [npi])
    if not p:
        raise HTTPException(404, f"NPI {npi} not found")
    p["history"] = rows(cur, "SELECT * FROM nppes_history WHERE npi = ? ORDER BY change_date", [npi])
    p["locations"] = rows(cur, "SELECT address_key, kind, city, state, zip5 FROM provider_locations WHERE npi = ?", [npi])
    p["exclusions"] = rows(cur, """SELECT m.method, m.confidence, m.basis, e.* FROM exclusion_matches m
                                   JOIN exclusions e USING (excl_id) WHERE m.npi = ?""", [npi])
    p["official_matches"] = rows(cur, """SELECT m.official, m.confidence, m.basis, e.source, e.excl_date, e.excl_type,
                                         e.city, e.state FROM official_matches m JOIN exclusions e USING (excl_id)
                                         WHERE m.org_npi = ?""", [npi])
    p["optouts"] = rows(cur, "SELECT * FROM optouts WHERE npi = ?", [npi])
    p["order_referring"] = one(cur, "SELECT * FROM order_referring WHERE npi = ?", [npi])
    p["reassignments"] = rows(cur, "SELECT * FROM reassignments WHERE npi = ?", [npi])
    p["enrollment"] = one(cur, "SELECT * FROM enrollments WHERE npi = ?", [npi])
    p["deactivation"] = one(cur, "SELECT * FROM deactivations WHERE npi = ?", [npi])
    p["medicare"] = one(cur, "SELECT * FROM medicare_by_provider WHERE npi = ?", [npi])
    p["monthly"] = rows(cur, """SELECT date_trunc('month', service_date)::DATE AS month, count(*) AS lines,
                                count(DISTINCT patient_id) AS patients, round(sum(amount)) AS amount
                                FROM claims WHERE rendering_npi = ? OR billing_npi = ? GROUP BY 1 ORDER BY 1""",
                         [npi, npi])
    p["top_codes"] = rows(cur, """SELECT c.hcpcs, rc.descr, rc.family, count(*) AS lines FROM claims c
                                  LEFT JOIN ref_codes rc USING (hcpcs) WHERE rendering_npi = ? OR billing_npi = ?
                                  GROUP BY ALL ORDER BY lines DESC LIMIT 8""", [npi, npi])
    grp = p.get("taxonomy_group")
    p["peers"] = one(cur, """
        WITH v AS (SELECT c.rendering_npi AS npi, count(*) AS lines FROM claims c
                   JOIN providers pp ON pp.npi = c.rendering_npi AND pp.taxonomy_group = ?
                   WHERE c.service_date > (SELECT max(service_date) FROM claims) - INTERVAL 90 DAY GROUP BY 1)
        SELECT count(*) AS n, median(lines) AS median, quantile_cont(lines, 0.9) AS p90,
               quantile_cont(lines, 0.99) AS p99, max(lines) FILTER (npi = ?) AS mine,
               round(100 * avg(CASE WHEN lines < (SELECT lines FROM v WHERE npi = ?) THEN 1 ELSE 0 END)) AS pct
        FROM v""", [grp, npi, npi]) if grp else None
    return p


def _draft_summary(p, hits):
    """Narrative-agent stand-in: deterministic, and every sentence cites an evidence ID."""
    name = p.get("display_name") or "This provider"
    kind = "organization" if p.get("entity_type") == "2" else p.get("taxonomy_desc") or "provider"
    art = "an" if kind[:1].upper() in "AEIOU" else "a"
    out = [f"{name} (NPI {p['npi']}) is {art} {kind} located in {(p.get('city') or '').title()}, {p.get('state') or ''}; "
           f"NPPES status is {p.get('status')}."]
    for h in hits:
        cite = ", ".join([s["eid"] for s in h["sources"]] + ([h["claims_eid"]] if h.get("claims_eid") else []))
        out.append(f"{h['reason_code']} ({h['rule_id']} v{h['version']}): {h['reason']}" + (f" [{cite}]" if cite else ""))
    out.append("These are anomalies for human review, not a fraud determination.")
    return out


@app.get("/api/alerts/{alert_id}")
def alert_detail(alert_id: str):
    cur = db()
    a = one(cur, "SELECT * FROM alerts WHERE alert_id = ?", [alert_id])
    if not a:
        raise HTTPException(404, alert_id)
    with historical(cur, a["run_id"]) as snapshot:
        result = _alert_detail(snapshot, alert_id)
    overlay([result["alert"]])
    result["dispositions"] = case_history(result["alert"])
    overlay(result["related"])
    return result


def _alert_detail(cur, alert_id):
    a = one(cur, "SELECT * FROM alerts WHERE alert_id = ?", [alert_id])
    if not a:
        raise HTTPException(404, alert_id)
    hits = rows(cur, """SELECT * FROM rule_hits WHERE run_id = ? AND npi = ?
                        ORDER BY hard DESC, weight DESC, rule_id""", [a["run_id"], a["npi"]])
    hits = _evidence(cur, hits, a["run_id"])
    p = _profile(cur, a["npi"])
    run = one(cur, "SELECT rules_json FROM runs WHERE run_id = ?", [a["run_id"]])
    related = rows(cur, """
        WITH mine AS (SELECT dst FROM graph_edges WHERE src = ? AND dst_type IN ('address', 'person', 'phone'))
        SELECT DISTINCT a2.alert_id, a2.fingerprint, a2.npi, a2.score, a2.tier, a2.status, g.dst AS via
        FROM graph_edges g JOIN mine USING (dst) JOIN alerts a2 ON a2.npi = g.src AND a2.run_id = ?
        WHERE g.src <> ? ORDER BY a2.score DESC LIMIT 60""", [a["npi"], a["run_id"], a["npi"]])
    return clean({"alert": a, "hits": hits, "provider": p, "related": related, "dispositions": [],
                  "draft_summary": _draft_summary(p, hits), "rules_meta": json.loads(run["rules_json"])})


@app.get("/api/providers/{npi}")
def provider(npi: str):
    cur = db()
    rid = current_run(cur)
    p = _profile(cur, npi)
    hits = _evidence(cur, rows(cur, "SELECT * FROM rule_hits WHERE run_id = ? AND npi = ? ORDER BY weight DESC",
                               [rid, npi]), rid)
    s = one(cur, "SELECT score, tier FROM scores WHERE run_id = ? AND npi = ?", [rid, npi])
    a = one(cur, "SELECT alert_id FROM alerts WHERE run_id = ? AND npi = ?", [rid, npi])
    return clean({"provider": p, "hits": hits, "score": s, "alert": a,
                  "assessment": provider_assessment(s, hits, p["status"]),
                  "as_of": one(cur, "SELECT as_of FROM runs WHERE run_id = ?", [rid])["as_of"]})


@app.get("/api/nppes/{npi}")
def live_nppes(npi: str):
    """Read one public NPPES record without joining synthetic signals."""
    return lookup_nppes(npi)


class VerificationInput(BaseModel):
    npi: str = Field(min_length=10, max_length=10)
    name: str = Field(default="", max_length=120)
    city: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=2)
    taxonomy: str = Field(default="", max_length=120)
    license: str = Field(default="", max_length=80)


@app.post("/api/verify")
def verify_provider(body: VerificationInput):
    if not npi_valid(body.npi):
        raise HTTPException(400, "Enter a valid 10-digit NPI")
    p = lookup_nppes(body.npi)
    fields = [("Name", body.name, p["name"]), ("City", body.city, p["city"]),
              ("State", body.state, p["state"]), ("Taxonomy", body.taxonomy, p["taxonomy"]),
              ("License", body.license, None)]
    return {"npi": body.npi, "source": "Live NPPES Registry", "last_updated": p["last_updated"],
            "source_url": p["source_url"], "fields": [{"field": label, "entered": entered,
            "official": official, "result": field_result(entered, official)}
            for label, entered, official in fields],
            "note": "Comparison uses a live NPPES lookup. A match does not establish licensing or credentialing. License verification is not connected. Values entered are not saved."}


class RosterInput(BaseModel):
    npis: list[str] = Field(min_length=1, max_length=100)


@app.post("/api/roster/check")
def check_roster(body: RosterInput):
    if os.environ.get("NPI_DATA_MODE", "local_demo") not in ("local_demo", "synthetic"):
        raise HTTPException(403, "roster checking is limited to the synthetic pilot")
    if len(set(body.npis)) != len(body.npis) or any(not npi_valid(n) for n in body.npis):
        raise HTTPException(400, "Roster requires unique, valid 10-digit NPIs")
    cur = db()
    rid = current_run(cur)
    results = []
    for npi in body.npis:
        p = one(cur, "SELECT npi, display_name, status FROM providers WHERE npi = ?", [npi])
        if not p:
            results.append({"npi": npi, "name": "Not in pilot snapshot", "label": "Cannot check yet", "reason": "No source record in this pilot"})
            continue
        hits = rows(cur, "SELECT reason FROM rule_hits WHERE run_id = ? AND npi = ? ORDER BY hard DESC, weight DESC LIMIT 1", [rid, npi])
        s = one(cur, "SELECT score, tier FROM scores WHERE run_id = ? AND npi = ?", [rid, npi])
        assessment = provider_assessment(s, hits, p["status"])
        results.append({"npi": npi, "name": p["display_name"], "label": assessment["label"],
                        "reason": assessment["sentence"]})
    return {"results": results, "saved": False, "note": "One-time synthetic pilot check. No roster is stored or monitored."}


@app.get("/api/government/leads")
def government_leads(request: Request):
    if getattr(request.state, "role", "local_demo") not in ("local_demo", "reviewer", "supervisor"):
        raise HTTPException(403, "government view is not enabled for this account")
    return alerts()


@app.get("/api/providers/{npi}/graph")
def graph(npi: str, run_id: str | None = None):
    cur = db()
    rid = current_run(cur, run_id)
    with historical(cur, rid) as snapshot:
        return _graph(snapshot, rid, npi)


def _graph(cur, rid, npi):
    hop1 = rows(cur, "SELECT * FROM graph_edges WHERE (src = ? OR dst = ?) AND rel <> 'phone'", [npi, npi])
    hubs = {e["dst"] for e in hop1 if e["dst_type"] in ("address", "person", "provider", "enrollment")} | \
           {e["src"] for e in hop1 if e["src_type"] in ("person",)}
    hop2 = rows(cur, """SELECT * FROM graph_edges WHERE (dst IN (SELECT unnest(?::VARCHAR[]))
                        OR src IN (SELECT unnest(?::VARCHAR[]))) AND rel <> 'phone' LIMIT 160""",
                [list(hubs), list(hubs)]) if hubs else []
    edges = {(e["src"], e["rel"], e["dst"]): e for e in hop1 + hop2}
    ids = {e["src"] for e in edges.values()} | {e["dst"] for e in edges.values()}
    types = {}
    for e in edges.values():
        types[e["src"]] = e["src_type"]
        types[e["dst"]] = e["dst_type"]
    provs = {r["npi"]: r for r in rows(cur, """SELECT p.npi, p.display_name, p.taxonomy_desc, p.entity_type, p.status,
                     s.score, s.tier, a.alert_id FROM providers p
                     LEFT JOIN scores s ON s.npi = p.npi AND s.run_id = ?
                     LEFT JOIN alerts a ON a.npi = p.npi AND a.run_id = ?
                     WHERE p.npi IN (SELECT unnest(?::VARCHAR[]))""", [rid, rid, [i for i in ids if i.isdigit()]])}
    excl = {r["excl_id"]: r for r in rows(cur, "SELECT excl_id, source, excl_date, excl_type FROM exclusions "
                                                "WHERE excl_id IN (SELECT unnest(?::VARCHAR[]))", [list(ids)])}
    nodes = []
    for i in ids:
        t = types.get(i)
        n = {"id": i, "type": t, "center": i == npi}
        if t == "provider" and i in provs:
            pr = provs[i]
            n.update(label=pr["display_name"] or i, sub=pr["taxonomy_desc"], score=pr["score"], tier=pr["tier"],
                     alert_id=pr["alert_id"], org=pr["entity_type"] == "2", status=pr["status"])
        elif t == "address":
            n.update(label=i[5:].split("|")[0].title(), sub=i.split("|")[-1])
        elif t == "person":
            n.update(label=i[7:].split("|")[0].title(), sub="person")
        elif t == "exclusion" and i in excl:
            e = excl[i]
            n.update(label=("OIG LEIE" if e["source"] == "oig_leie" else "SAM") + " exclusion",
                     sub=f"{jd(e['excl_date'])} {e['excl_type'] or ''}")
        else:
            n.update(label=i, sub=t)
        nodes.append(n)
    return clean({"nodes": nodes, "edges": list(edges.values())})


class Disposition(BaseModel):
    actor: str = Field(max_length=254)
    action: str = Field(min_length=1, max_length=20)
    reason: str = Field(default="", max_length=4000)
    supervisor: str = Field(default="", max_length=254)


# The deployed pilot stores only predefined review reasons. Free text could
# accidentally introduce PHI into Firestore or the audit trail.
PILOT_REASONS = {
    "review": {""},
    "monitor": {"Re-check after next claims feed", "Waiting on state board data"},
    "dismiss": {"Data lag: NPPES not yet updated", "Legitimate locum / coverage",
                "Identity mismatch: different person", "Known group practice"},
    "escalate": {"Evidence supports referral", "Pre-payment hold recommended"},
    "reopen": {""},
    "refer": {""},
}


@app.post("/api/alerts/{alert_id}/disposition")
def disposition(alert_id: str, d: Disposition, request: Request):
    d.actor = actor_for(request, d.actor, supervisor=d.action == "escalate")
    if getattr(request.state, "actor", None) and d.action == "escalate":
        d.supervisor = d.actor  # sign-off belongs to the authenticated supervisor
    if d.action not in STATUS_FOR_ACTION:
        raise HTTPException(400, f"unknown action {d.action}")
    if os.environ.get("K_SERVICE") and d.reason not in PILOT_REASONS[d.action]:
        raise HTTPException(400, "select a standard reason; free text is disabled in the synthetic pilot")
    if not d.actor.strip():
        raise HTTPException(400, "reviewer name required")
    if d.action in ("dismiss", "monitor", "escalate") and not d.reason.strip():
        raise HTTPException(400, "a reason is required")
    if d.action == "escalate" and not d.supervisor.strip():
        raise HTTPException(400, "escalation requires supervisor sign-off")
    a = case_alert(alert_id)
    status = store().transition(case_key(a), a["status"], alert_id, d)
    return {"status": status}



@app.post("/api/alerts/{alert_id}/rerun")
def rerun(alert_id: str, request: Request, actor: str = "reviewer"):
    actor = actor_for(request, actor)
    try:
        a = case_alert(alert_id)
        result = rerun_alert(alert_id, actor=actor)
        store().record(case_key(a), event(actor, "rerun", alert_id,
            {"reproduced": result.get("reproduced"), "fingerprint": a["fingerprint"]}))
        return result
    except KeyError:
        raise HTTPException(404, alert_id)


@app.get("/api/alerts/{alert_id}/package", response_class=HTMLResponse)
def package(alert_id: str, request: Request, actor: str = "reviewer"):
    actor = actor_for(request, actor)
    d = alert_detail(alert_id)
    a = d["alert"]
    if a["status"] not in ("Escalated", "Referred"):
        raise HTTPException(409, "referral packages are built only after a supervisor signs the escalation")
    p = d["provider"]
    e = html.escape
    parts = [f"""<!doctype html><html><head><meta charset="utf-8"><title>Referral package {e(a['alert_id'])}</title>
    <style>body{{font:14px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:900px;margin:32px auto;color:#14213d;padding:0 16px}}
    h1{{font-size:22px}}h2{{font-size:16px;border-bottom:2px solid #2f9e44;padding-bottom:4px;margin-top:28px}}
    table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{border:1px solid #cbd5e1;padding:4px 6px;text-align:left;vertical-align:top}}
    th{{background:#0f2340;color:#fff}}.note{{background:#fff7e6;border:1px solid #f0b429;padding:8px 12px}}</style></head><body>
    <p class="note"><b>Anomaly referral, not a fraud determination.</b> Prepared from public and synthetic data
    (prototype). Generated {e(now().isoformat(timespec='seconds'))} UTC by {e(actor)}.</p>
    <h1>Referral package: {e(a['alert_id'])}</h1>
    <table><tr><th>NPI</th><th>Name</th><th>Taxonomy</th><th>Location</th><th>Score</th><th>Status</th></tr>
    <tr><td>{e(a['npi'])}</td><td>{e(p.get('display_name') or '')}</td><td>{e(p.get('taxonomy_desc') or '')}</td>
    <td>{e((p.get('city') or '').title())}, {e(p.get('state') or '')}</td><td>{a['score']} {e(a['tier'])}</td>
    <td>{e(a['status'])}</td></tr></table>
    <h2>Summary</h2><ol>""" + "".join(f"<li>{e(s)}</li>" for s in d["draft_summary"]) + "</ol><h2>Reasons</h2><table>"
             "<tr><th>Code</th><th>Rule</th><th>Reason</th><th>Evidence</th></tr>"]
    for h in d["hits"]:
        ev = "; ".join(f"{s['eid']}: {e(s['label'])} {e(str(s.get('version') or ''))}"
                       f"{' row ' + str(s['row']) if s.get('row') else ''} ({e(s.get('note') or '')})" for s in h["sources"])
        if h.get("claims_eid"):
            ev += f"; {h['claims_eid']}: {len(h['claims'])} claim lines (listed below)"
        parts.append(f"<tr><td>{e(h['reason_code'])}</td><td>{e(h['rule_id'])} v{e(h['version'])}</td>"
                     f"<td>{e(h['reason'])}</td><td>{ev}</td></tr>")
    parts.append("</table><h2>Evidence index: claim lines</h2><table><tr><th>Line</th><th>Date</th><th>Billing</th>"
                 "<th>Rendering</th><th>Ordering</th><th>Code</th><th>Units</th><th>POS</th><th>Place</th><th>Source</th></tr>")
    seen = set()
    for h in d["hits"]:
        for c in h["claims"]:
            if c["line_id"] in seen:
                continue
            seen.add(c["line_id"])
            parts.append(f"<tr><td>{e(c['line_id'])}</td><td>{e(str(c['service_date']))}</td><td>{e(c['billing_npi'])}</td>"
                         f"<td>{e(c['rendering_npi'])}</td><td>{e(c['ordering_npi'] or '')}</td>"
                         f"<td>{e(c['hcpcs'])} {e(c.get('descr') or '')}</td><td>{c['units']}</td><td>{e(c['pos'])}</td>"
                         f"<td>{e((c.get('svc_city') or '').title())}</td><td>{e(c['_file'])} row {c['_row']}</td></tr>")
    parts.append("</table><h2>Timeline</h2><table><tr><th>Date</th><th>Event</th></tr>")
    tl = [(str(p.get("enum_date")), "NPI enumerated")]
    tl += [(str(x["change_date"]), f"NPPES {x['field']} changed") for x in p["history"]]
    if p.get("deactivation"):
        tl.append((str(p["deactivation"]["deact_date"]), "NPI deactivated"))
    tl += [(str(x["excl_date"]), f"Exclusion ({x['source']})") for x in p["exclusions"]]
    tl += [(str(h["event_date"]), f"First event for {h['rule_id']}") for h in d["hits"] if h.get("event_date")]
    tl.append((str(a["created_as_of"]), f"Alert {a['alert_id']} raised"))
    tl += [(str(x["ts"])[:19], f"{x['action']} by {x['actor']}" + (f" (supervisor {x['supervisor']})" if x["supervisor"] else "")
            + (f": {x['reason']}" if x["reason"] else "")) for x in d["dispositions"]]
    parts += [f"<tr><td>{e(t)}</td><td>{e(v)}</td></tr>" for t, v in sorted(tl)]
    parts.append("</table><h2>Rule and data versions</h2><table><tr><th>Rule</th><th>Version</th><th>File SHA-256</th></tr>")
    used = {h["rule_id"] for h in d["hits"]}
    parts += [f"<tr><td>{e(r['id'])}</td><td>{e(r['version'])}</td><td>{e(r['sha'])}</td></tr>"
              for r in d["rules_meta"] if r["id"] in used]
    parts.append(f"</table><p>Alert fingerprint {e(a['fingerprint'])}; run {e(a['run_id'])}; inputs as of "
                 f"{e(str(a['created_as_of']))}. Re-runnable from the archived raw zone.</p>"
                 "<h2>Reviewer sign-offs</h2><table><tr><th>When</th><th>Reviewer</th><th>Action</th><th>Supervisor</th>"
                 "<th>Reason</th></tr>")
    parts += [f"<tr><td>{e(str(x['ts'])[:19])}</td><td>{e(x['actor'])}</td><td>{e(x['action'])}</td>"
              f"<td>{e(x['supervisor'] or '')}</td><td>{e(x['reason'] or '')}</td></tr>" for x in d["dispositions"]]
    parts.append("</table></body></html>")
    store().record(case_key(a), event(actor, "package_export", alert_id, {"fingerprint": a["fingerprint"]}))
    return HTMLResponse("".join(parts))


@app.get("/api/scorecard")
def get_scorecard(run_id: str | None = None):
    cur = db()
    rid = current_run(cur, run_id)
    r = one(cur, "SELECT body FROM scorecards WHERE run_id = ? ORDER BY created_at DESC LIMIT 1", [rid])
    if not r:
        raise HTTPException(404, "no scorecard for this run: run `npi scorecard --sweep`")
    return json.loads(r["body"])


@app.get("/api/rules")
def rules_catalog():
    import yaml
    cur = db()
    rid = current_run(cur)
    stats = {r["rule_id"]: r for r in rows(cur, """
        SELECT h.rule_id, count(*) AS hits, count(a.alert_id) AS alerts,
               count(*) FILTER (h.hard) AS hard_hits FROM rule_hits h
        LEFT JOIN alerts a ON a.npi = h.npi AND a.run_id = h.run_id WHERE h.run_id = ? GROUP BY 1""", [rid])}
    labels = {r["rule_id"]: r for r in rows(cur, """
        SELECT h.rule_id, count(*) FILTER (d.action = 'dismiss') AS dismissed,
               count(*) FILTER (d.action IN ('escalate', 'refer')) AS escalated
        FROM dispositions d JOIN alerts a USING (alert_id)
        JOIN rule_hits h ON h.npi = a.npi AND h.run_id = a.run_id GROUP BY 1""")}
    out = []
    for path in sorted(config.RULES_DIR.glob("*.yaml")):
        if path.name == "scoring.yaml":
            continue
        doc = yaml.safe_load(path.read_text())
        doc.pop("sql", None)
        doc.update(stats.get(doc["id"], {"hits": 0, "alerts": 0, "hard_hits": 0}))
        doc.update({k: v for k, v in labels.get(doc["id"], {}).items() if k != "rule_id"})
        out.append(doc)
    scoring = yaml.safe_load((config.RULES_DIR / "scoring.yaml").read_text())
    return clean({"rules": out, "scoring": scoring})


@app.get("/api/sources")
def sources():
    cur = db()
    rid = current_run(cur)
    loads = rows(cur, "SELECT * FROM source_loads WHERE run_id = ? ORDER BY source, version", [rid])
    for l in loads:
        l["checks"] = json.loads(l.pop("checks_json"))
    meta = [{"key": s.key, "label": s.label, "kind": s.kind, "phase": s.phase, "origin": s.origin}
            for s in SOURCES.values()]
    return clean({"loads": loads, "sources": meta})


@app.get("/api/audit")
def audit_log(limit: int = 200):
    if not 1 <= limit <= 1000:
        raise HTTPException(400, "limit must be between 1 and 1000")
    cur = db()
    combined = clean(rows(cur, "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", [limit])) + store().audit(limit)
    for e in combined:
        if isinstance(e["details"], dict):
            e["details"] = json.dumps(e["details"])
    return sorted(combined, key=lambda e: str(e["ts"]), reverse=True)[:limit]


@app.get("/api/search")
def search(q: str):
    cur = db()
    q = q.strip().upper()
    return clean(rows(cur, """SELECT npi, display_name, taxonomy_desc, city, state, status FROM providers
                              WHERE npi = ? OR display_name LIKE '%' || ? || '%' LIMIT 25""", [q, q]))


# ------------------------------------------------------------------------------------------- L7 agents
@app.post("/api/alerts/{alert_id}/draft")
def agent_draft(alert_id: str, request: Request, actor: str = "reviewer"):
    """Run context -> narrative -> QA agents. Read-only: agents cannot change alert state."""
    from .agents.guardrails import DataBoundaryError
    from .agents.orchestrator import default_model, draft_case_summary
    from .agents.tools import WarehouseSource
    actor = actor_for(request, actor)
    a = case_alert(alert_id)
    store().reserve_draft(int(os.environ.get("NPI_DRAFTS_PER_MINUTE", "6")))
    cur = db()
    run = one(cur, "SELECT r.mode FROM alerts a JOIN runs r USING (run_id) WHERE a.alert_id = ?", [alert_id])
    if not run:
        raise HTTPException(404, alert_id)
    try:
        result = draft_case_summary(alert_id, WarehouseSource(), default_model(), data_mode=run["mode"])
    except DataBoundaryError as e:
        raise HTTPException(403, str(e))
    store().save_draft(case_key(a), result, event(actor, "agent_draft", alert_id,
        {"status": result["status"], "model": result["models"]["agent"],
         "repairs": result["repairs"], "cost_usd": result["cost_usd"],
         "tool_violations": len(result["tool_violations"]), "prompt_versions": result["prompt_versions"]}))
    return clean(result)


@app.get("/api/alerts/{alert_id}/draft")
def latest_draft(alert_id: str):
    a = case_alert(alert_id)
    return store().latest_draft(case_key(a))
