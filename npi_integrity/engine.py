"""L5 detection engine and L6 score + reason codes.

Rules are YAML files (rules/*.yaml) with an ID, version, owner, parameters, a
SQL query, and a plain-language reason template. The engine is deterministic:
the same staged inputs, as-of date, and rule files always give the same hits,
scores, and alert fingerprints.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import yaml
from jinja2 import Environment, StrictUndefined


def _mdy(v):
    if v is None:
        return "n/a"
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    return v.strftime("%m/%d/%Y")


def _mmyyyy(v):
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    return v.strftime("%m/%Y")


SQL_ENV = Environment(undefined=StrictUndefined, autoescape=False)
TEXT_ENV = Environment(undefined=StrictUndefined, autoescape=False)
TEXT_ENV.filters["mdy"] = _mdy
TEXT_ENV.filters["mmyyyy"] = _mmyyyy


def load_rules(rules_dir: Path) -> tuple[list[dict], dict]:
    rules = []
    for path in sorted(rules_dir.glob("*.yaml")):
        text = path.read_text()
        doc = yaml.safe_load(text)
        if path.name == "scoring.yaml":
            continue
        doc["_file"] = path.name
        doc["_sha"] = hashlib.sha256(text.encode()).hexdigest()
        doc.setdefault("params", {})
        doc.setdefault("weight", 0)
        rules.append(doc)
    scoring = yaml.safe_load((rules_dir / "scoring.yaml").read_text())
    return rules, scoring


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (float, Decimal)):
        return round(float(v), 4)
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


def run_rules(conn: duckdb.DuckDBPyConnection, rules: list[dict], scoring: dict, as_of: str) -> list[dict]:
    hits = []
    for rule in rules:
        sql = SQL_ENV.from_string(rule["sql"]).render(p=rule["params"], AS_OF=f"DATE '{as_of}'")
        try:
            cur = conn.execute(sql)
        except duckdb.Error as e:
            raise RuntimeError(f"rule {rule['id']} failed: {e}") from e
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            hits.append(_make_hit(rule, r, scoring))
    return hits


def _make_hit(rule: dict, r: dict, scoring: dict) -> dict:
    strength = float(r.get("strength") or 1.0)
    confidence = r.get("confidence")
    suffix = ""
    if rule["type"] == "hard":
        hard = confidence is None or confidence >= scoring["hard_min_confidence"]
    elif rule["type"] == "mixed":
        hard = bool(r.get("hard"))
    else:
        hard = False
    weight = 0.0
    if not hard:
        if rule["type"] == "hard":
            weight = float(scoring["identity_review_weight"])
            suffix = " Identity match below the hard-stop threshold: routed to identity review, not a hard stop."
        else:
            weight = float(rule["weight"]) * strength
    facts = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in r.items()
             if k not in ("evidence_claims", "evidence_sources", "npi")}
    reason = " ".join(TEXT_ENV.from_string(rule["reason"]).render(p=rule["params"], **facts).split()) + suffix
    return {
        "npi": r["npi"], "rule_id": rule["id"], "version": rule["version"], "reason_code": rule["reason_code"],
        "family": rule["family"], "hard": hard, "weight": round(weight, 2), "strength": strength,
        "confidence": confidence, "reason": reason, "facts": _jsonable(facts),
        "evidence_claims": list(r.get("evidence_claims") or []),
        "evidence_sources": _jsonable(list(r.get("evidence_sources") or [])),
        "event_date": r.get("event_date"),
    }


def tier_for(score: float, scoring: dict) -> str:
    name = scoring["tiers"][0]["name"]
    for t in scoring["tiers"]:
        if score >= t["min"]:
            name = t["name"]
    return name


def fingerprint(hits: list[dict]) -> str:
    """Stable hash of everything a reviewer sees for one NPI: rules, versions, reasons, evidence."""
    body = sorted((h["rule_id"], h["version"], h["reason"], tuple(h["evidence_claims"]),
                   json.dumps(h["evidence_sources"], sort_keys=True)) for h in hits)
    return hashlib.sha256(json.dumps(body).encode()).hexdigest()[:16]


def score(hits: list[dict], scoring: dict) -> list[dict]:
    by_npi: dict[str, list[dict]] = {}
    for h in hits:
        by_npi.setdefault(h["npi"], []).append(h)
    out = []
    caps = scoring["family_caps"]
    for npi, hs in by_npi.items():
        hard = any(h["hard"] for h in hs)
        fam: dict[str, float] = {}
        for h in hs:
            fam[h["family"]] = fam.get(h["family"], 0) + h["weight"]
        soft = sum(min(v, caps.get(f, 1000)) for f, v in fam.items())
        network = any(h["rule_id"] == "N-01" and h["strength"] >= 1.0 for h in hs)
        if network:
            soft *= scoring["network_multiplier"]
        s = min(1000, round((scoring["hard_floor"] if hard else 0) + soft))
        out.append({"npi": npi, "score": s, "tier": tier_for(s, scoring), "hard": hard,
                    "rules": sorted({h["rule_id"] for h in hs}), "fingerprint": fingerprint(hs),
                    "first_event": min((h["event_date"] for h in hs if h["event_date"]), default=None)})
    out.sort(key=lambda x: (-x["score"], x["npi"]))
    return out
