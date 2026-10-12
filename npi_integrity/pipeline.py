"""Orchestration: land -> stage -> build -> detect -> score -> alerts, plus as-of re-runs and the scorecard.

The warehouse keeps run history (runs, rule_hits, scores, alerts, dispositions,
audit_log). Working tables (providers, claims, graph_edges, ...) reflect the
latest run and back the reviewer UI.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from . import config
from .build import build
from .engine import load_rules, run_rules, score
from .ingest import SOURCES, land, raw_manifest, select_files, sha256, stage

PERSISTENT_DDL = """
CREATE TABLE IF NOT EXISTS runs (run_id VARCHAR PRIMARY KEY, as_of DATE, started_at TIMESTAMP, finished_at TIMESTAMP,
    mode VARCHAR, n_files INT, n_hits INT, n_scored INT, n_alerts INT, rules_json VARCHAR, manifest_json VARCHAR,
    status VARCHAR);
CREATE TABLE IF NOT EXISTS source_loads (run_id VARCHAR, source VARCHAR, version DATE, file VARCHAR, sha256 VARCHAR,
    rows BIGINT, status VARCHAR, checks_json VARCHAR);
CREATE TABLE IF NOT EXISTS rule_hits (run_id VARCHAR, npi VARCHAR, rule_id VARCHAR, version VARCHAR,
    reason_code VARCHAR, family VARCHAR, hard BOOLEAN, weight DOUBLE, strength DOUBLE, confidence DOUBLE,
    reason VARCHAR, facts VARCHAR, evidence_claims VARCHAR[], evidence_sources VARCHAR, event_date DATE);
CREATE TABLE IF NOT EXISTS scores (run_id VARCHAR, npi VARCHAR, score INT, tier VARCHAR, hard BOOLEAN,
    rules VARCHAR[], fingerprint VARCHAR, first_event DATE);
CREATE TABLE IF NOT EXISTS alerts (alert_id VARCHAR PRIMARY KEY, run_id VARCHAR, npi VARCHAR, score INT, tier VARCHAR,
    status VARCHAR, created_as_of DATE, fingerprint VARCHAR);
CREATE TABLE IF NOT EXISTS dispositions (alert_id VARCHAR, ts TIMESTAMP, actor VARCHAR, action VARCHAR,
    reason VARCHAR, supervisor VARCHAR);
CREATE SEQUENCE IF NOT EXISTS audit_seq;
CREATE TABLE IF NOT EXISTS audit_log (seq BIGINT DEFAULT nextval('audit_seq'), ts TIMESTAMP, actor VARCHAR,
    action VARCHAR, object VARCHAR, details VARCHAR);
CREATE TABLE IF NOT EXISTS scorecards (run_id VARCHAR, created_at TIMESTAMP, body VARCHAR);
"""


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def connect(path: Path = config.WAREHOUSE) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    conn.execute(PERSISTENT_DDL)
    return conn


def audit(conn, actor: str, action: str, obj: str, details: dict | None = None):
    conn.execute("INSERT INTO audit_log (ts, actor, action, object, details) VALUES (?, ?, ?, ?, ?)",
                 [now(), actor, action, obj, json.dumps(details or {}, default=str)])


def detect(conn, as_of: str, rules_dir: Path):
    rules, scoring = load_rules(rules_dir)
    hits = run_rules(conn, rules, scoring, as_of)
    scored = score(hits, scoring)
    return rules, scoring, hits, scored


def run_pipeline(as_of: str = config.DEFAULT_AS_OF, inbox: Path | None = None, rules_dir: Path = config.RULES_DIR,
                 warehouse: Path = config.WAREHOUSE, actor: str = "pipeline", mode: str = "synthetic") -> str:
    conn = connect(warehouse)
    started = now()
    run_id = f"RUN-{as_of}-{started.strftime('%H%M%S')}"
    audit(conn, actor, "run_started", run_id, {"as_of": as_of, "mode": mode})
    if inbox:
        landed = land(inbox)
        audit(conn, actor, "files_landed", run_id,
              {"landed": sum(1 for x in landed if x["status"] == "landed"),
               "unchanged": sum(1 for x in landed if x["status"] == "unchanged")})
    manifest = [m for m in raw_manifest(as_of=as_of)]
    loads = stage(conn, manifest)
    build(conn, as_of)
    rules, scoring, hits, scored = detect(conn, as_of, rules_dir)

    # snapshot the exact rule files used so this run can be replayed later
    snap = config.RUNS / run_id
    (snap / "rules").mkdir(parents=True, exist_ok=True)
    for f in rules_dir.glob("*.yaml"):
        shutil.copy2(f, snap / "rules" / f.name)
    (snap / "manifest.json").write_text(json.dumps(manifest, indent=1))

    for l in loads:
        conn.execute("INSERT INTO source_loads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     [run_id, l["source"], l["version"], l["file"], l["sha256"], l["rows"], l["status"],
                      json.dumps(l["checks"])])
    if hits:
        conn.executemany(
            "INSERT INTO rule_hits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [[run_id, h["npi"], h["rule_id"], h["version"], h["reason_code"], h["family"], h["hard"], h["weight"],
              h["strength"], h["confidence"], h["reason"], json.dumps(h["facts"]), h["evidence_claims"],
              json.dumps(h["evidence_sources"]), h["event_date"]] for h in hits])
    if scored:
        conn.executemany("INSERT INTO scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         [[run_id, s["npi"], s["score"], s["tier"], s["hard"], s["rules"], s["fingerprint"],
                           s["first_event"]] for s in scored])
    year = as_of[:4]
    seq = conn.execute("SELECT coalesce(max(try_cast(split_part(alert_id, '-', 3) AS INT)), 0) FROM alerts "
                       "WHERE alert_id LIKE ?", [f"A-{year}-%"]).fetchone()[0]
    alerts = []
    for s in scored:
        if s["score"] >= scoring["alert_threshold"]:
            seq += 1
            alerts.append([f"A-{year}-{seq:06d}", run_id, s["npi"], s["score"], s["tier"], "New", as_of,
                           s["fingerprint"]])
    if alerts:
        conn.executemany("INSERT INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", alerts)
    rules_meta = [{"id": r["id"], "version": r["version"], "sha": r["_sha"][:16]} for r in rules]
    conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 [run_id, as_of, started, now(), mode, len(manifest), len(hits), len(scored), len(alerts),
                  json.dumps(rules_meta), json.dumps(manifest), "completed"])
    audit(conn, actor, "run_completed", run_id, {"hits": len(hits), "scored": len(scored), "alerts": len(alerts)})
    conn.close()
    return run_id


def latest_run(conn) -> str | None:
    r = conn.execute("SELECT run_id FROM runs WHERE status = 'completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
    return r[0] if r else None


def replay(run_id: str, conn) -> tuple[str, list[dict], list[dict]]:
    """Rebuild a past run in memory from its archived raw files and rule snapshot."""
    as_of, manifest_json = conn.execute("SELECT as_of, manifest_json FROM runs WHERE run_id = ?", [run_id]).fetchone()
    as_of = str(as_of)
    manifest = json.loads(manifest_json)
    for m in manifest:
        actual = sha256(config.RAW / m["path"])
        if actual != m["sha256"]:
            raise RuntimeError(f"raw file changed since the run: {m['path']}")
    mem = duckdb.connect()
    stage(mem, manifest)
    build(mem, as_of)
    _, _, hits, scored = detect(mem, as_of, config.RUNS / run_id / "rules")
    mem.close()
    return as_of, hits, scored


def rerun_alert(alert_id: str, actor: str = "reviewer") -> dict:
    conn = connect()
    row = conn.execute("SELECT run_id, npi, fingerprint, score FROM alerts WHERE alert_id = ?", [alert_id]).fetchone()
    if not row:
        conn.close()
        raise KeyError(alert_id)
    run_id, npi, fp, original_score = row
    conn.close()
    as_of, hits, scored = replay(run_id, connect())
    s = next((x for x in scored if x["npi"] == npi), None)
    result = {"alert_id": alert_id, "run_id": run_id, "as_of": as_of, "original_fingerprint": fp,
              "replayed_fingerprint": s["fingerprint"] if s else None, "original_score": original_score,
              "replayed_score": s["score"] if s else None,
              "reproduced": bool(s and s["fingerprint"] == fp and s["score"] == original_score),
              "replayed_at": now().isoformat(timespec="seconds")}
    conn = connect()
    audit(conn, actor, "alert_rerun", alert_id, result)
    conn.close()
    return result


# ------------------------------------------------------------------------------------------- scorecard
def evaluate(scored: list[dict], hits: list[dict], key: dict, threshold: int, n_monitored: int) -> dict:
    alerted = {s["npi"]: s for s in scored if s["score"] >= threshold}
    by_npi: dict[str, list[dict]] = {}
    for h in hits:
        by_npi.setdefault(h["npi"], []).append(h)
    scenarios = []
    targets_all = set()
    for sc in key["scenarios"]:
        targets_all.update(sc["targets"])
        ok_targets = []
        for t in sc["targets"]:
            rules = {h["rule_id"] for h in by_npi.get(t, [])}
            ok_targets.append(t in alerted and set(sc["expected_rules"]) <= rules)
        frac = sum(ok_targets) / len(ok_targets)
        caught = frac >= (0.9 if len(ok_targets) > 1 else 1.0)
        first = sc["targets"][0]
        scenarios.append({
            "no": sc["no"], "title": sc["title"], "expected_rules": sc["expected_rules"], "targets": len(sc["targets"]),
            "targets_caught": sum(ok_targets), "caught": caught, "npi": first,
            "score": alerted.get(first, {}).get("score"), "tier": alerted.get(first, {}).get("tier"),
            "rules_fired": sorted({h["rule_id"] for h in by_npi.get(first, [])}),
        })
    decoys = []
    for d in key["decoys"]:
        flagged = [n for n in d["npis"] if n in alerted]
        rules = sorted({h["rule_id"] for n in d["npis"] for h in by_npi.get(n, [])})
        best = max((s["score"] for s in scored if s["npi"] in d["npis"]), default=0)
        decoys.append({"title": d["title"], "npi": d["npi"], "passed": not flagged, "max_score": best,
                       "rules_fired": rules, "note": d.get("note", "")})
    false_alerts = [n for n in alerted if n not in targets_all]
    rule_stats: dict[str, dict] = {}
    for h in hits:
        st = rule_stats.setdefault(h["rule_id"], {"hits": 0, "on_planted": 0, "alerts": 0, "alerts_on_planted": 0})
        st["hits"] += 1
        st["on_planted"] += h["npi"] in targets_all
        if h["npi"] in alerted:
            st["alerts"] += 1
            st["alerts_on_planted"] += h["npi"] in targets_all
    for st in rule_stats.values():
        st["precision"] = round(st["alerts_on_planted"] / st["alerts"], 3) if st["alerts"] else None
    return {
        "scenarios": scenarios, "decoys": decoys,
        "caught": sum(s["caught"] for s in scenarios), "total": len(scenarios),
        "alerts": len(alerted), "false_alerts": len(false_alerts), "false_alert_npis": false_alerts[:50],
        "monitored_npis": n_monitored,
        "false_alerts_per_1000": round(1000 * len(false_alerts) / n_monitored, 2) if n_monitored else None,
        "rule_stats": dict(sorted(rule_stats.items())),
    }


def scorecard(run_id: str | None = None, sweep: bool = False, actor: str = "pipeline") -> dict:
    key_path = config.ANSWER_KEY / "sealed_answer_key.json"
    key = json.loads(key_path.read_text())
    conn = connect()
    run_id = run_id or latest_run(conn)
    as_of, threshold_hits = conn.execute("SELECT as_of, n_hits FROM runs WHERE run_id = ?", [run_id]).fetchone()
    hits = [{"npi": r[0], "rule_id": r[1], "event_date": r[2]} for r in conn.execute(
        "SELECT npi, rule_id, event_date FROM rule_hits WHERE run_id = ?", [run_id]).fetchall()]
    scored = [{"npi": r[0], "score": r[1], "tier": r[2]} for r in conn.execute(
        "SELECT npi, score, tier FROM scores WHERE run_id = ?", [run_id]).fetchall()]
    _, scoring = load_rules(config.RUNS / run_id / "rules")
    n_monitored = conn.execute("SELECT count(*) FROM providers WHERE status = 'Active'").fetchone()[0]
    conn.close()
    result = evaluate(scored, hits, key, scoring["alert_threshold"], n_monitored)
    result.update({"run_id": run_id, "as_of": str(as_of), "answer_key_sha256":
                   (config.ANSWER_KEY / "sealed_answer_key.sha256").read_text().strip()})
    # time-to-detect: first bad event in the data vs. first weekly as-of run that raises the alert
    for s in result["scenarios"]:
        ev = [h["event_date"] for h in hits
              if h["npi"] == s["npi"] and h["rule_id"] in s["expected_rules"] and h["event_date"]]
        s["first_bad_event"] = str(min(ev)) if ev else None
    if sweep:
        result["sweep"] = time_to_detect(run_id, key, scoring["alert_threshold"], result)
    conn = connect()
    conn.execute("INSERT INTO scorecards VALUES (?, ?, ?)", [run_id, now(), json.dumps(result, default=str)])
    audit(conn, actor, "scorecard", run_id, {"caught": result["caught"], "false_alerts": result["false_alerts"]})
    conn.close()
    return result


def time_to_detect(run_id: str, key: dict, threshold: int, result: dict) -> list[dict]:
    conn = connect()
    as_of, manifest_json = conn.execute("SELECT as_of, manifest_json FROM runs WHERE run_id = ?", [run_id]).fetchone()
    conn.close()
    manifest = json.loads(manifest_json)
    mem = duckdb.connect()
    stage(mem, manifest)
    end = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
    dates = [end - timedelta(days=7 * k) for k in range(13, -1, -1)]
    first_seen: dict[int, str] = {}
    steps = []
    first_nppes = min(m["version"] for m in manifest if m["source"] == "nppes_full")
    for d in dates:
        if d.isoformat() < first_nppes:
            continue
        build(mem, d.isoformat())
        _, _, hits, scored = detect(mem, d.isoformat(), config.RUNS / run_id / "rules")
        ev = evaluate(scored, hits, key, threshold, 1)
        steps.append({"as_of": d.isoformat(), "caught": ev["caught"], "false_alerts": ev["false_alerts"]})
        for s in ev["scenarios"]:
            if s["caught"] and s["no"] not in first_seen:
                first_seen[s["no"]] = d.isoformat()
    mem.close()
    for s in result["scenarios"]:
        s["first_detected_as_of"] = first_seen.get(s["no"])
        if s.get("first_bad_event") and s["first_detected_as_of"]:
            s["days_to_detect"] = (date.fromisoformat(s["first_detected_as_of"]) -
                                   date.fromisoformat(s["first_bad_event"])).days
    return steps


def snapshot_run(conn, run_id: str):
    """Return an isolated historical warehouse, verified against the archived manifest.

    The caller owns and must close the returned connection. Historical UI and
    agent evidence must never use the current working tables.
    """
    row = conn.execute("SELECT as_of, manifest_json FROM runs WHERE run_id = ?", [run_id]).fetchone()
    if row is None:
        raise KeyError(run_id)
    as_of, manifest_json = row
    manifest = json.loads(manifest_json)
    for entry in manifest:
        if sha256(config.RAW / entry["path"]) != entry["sha256"]:
            raise RuntimeError("historical source integrity check failed")
    mem = duckdb.connect()
    try:
        stage(mem, manifest)
        build(mem, str(as_of))
        mem.execute(PERSISTENT_DDL)
        for table in ("runs", "source_loads", "rule_hits", "scores", "alerts", "scorecards"):
            result = conn.execute(f"SELECT * FROM {table} WHERE run_id = ?", [run_id])
            records = result.fetchall()
            if records:
                placeholders = ",".join("?" for _ in result.description)
                mem.executemany(f"INSERT INTO {table} VALUES ({placeholders})", records)
        return mem
    except Exception:
        mem.close()
        raise
