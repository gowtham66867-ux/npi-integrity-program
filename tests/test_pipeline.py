"""End-to-end: synthetic sources -> run -> scorecard -> as-of replay, in an isolated NPI_HOME."""
import importlib
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("npi_home")
    shutil.copytree(ROOT / "rules", home / "rules")
    os.environ["NPI_HOME"] = str(home)
    import npi_integrity.config as config
    importlib.reload(config)
    import npi_integrity.ingest, npi_integrity.pipeline, npi_integrity.synth  # noqa: E401
    for m in ("npi_integrity.ingest", "npi_integrity.synth", "npi_integrity.pipeline"):
        importlib.reload(importlib.import_module(m))
    from npi_integrity import pipeline, synth
    synth.generate()
    run_id = pipeline.run_pipeline(config.DEFAULT_AS_OF, inbox=config.INBOX / "synthetic", rules_dir=config.RULES_DIR,
                                   warehouse=config.WAREHOUSE)
    return {"config": config, "pipeline": pipeline, "run_id": run_id}


def test_all_planted_scenarios_caught(env):
    sc = env["pipeline"].scorecard(env["run_id"])
    missed = [s["title"] for s in sc["scenarios"] if not s["caught"]]
    assert sc["caught"] == 12, missed


def test_no_false_alerts_and_decoys_pass(env):
    sc = env["pipeline"].scorecard(env["run_id"])
    assert sc["false_alerts"] == 0, sc["false_alert_npis"]
    failed = [d["title"] for d in sc["decoys"] if not d["passed"]]
    assert not failed, failed


def test_low_confidence_identity_never_hard_stops(env):
    import duckdb
    c = duckdb.connect(str(env["config"].WAREHOUSE), read_only=True)
    rows = c.execute("SELECT hard, confidence FROM rule_hits WHERE rule_id = 'D-02' AND run_id = ?",
                     [env["run_id"]]).fetchall()
    c.close()
    assert rows and all(conf >= 0.9 for hard, conf in rows if hard)
    assert any(not hard for hard, _ in rows), "expected the name-alike decoy as an identity-review hit"


def test_every_alert_has_evidence(env):
    import duckdb
    c = duckdb.connect(str(env["config"].WAREHOUSE), read_only=True)
    bare = c.execute("""SELECT a.alert_id FROM alerts a JOIN rule_hits h ON h.npi = a.npi AND h.run_id = a.run_id
                        WHERE a.run_id = ? GROUP BY a.alert_id
                        HAVING sum(len(h.evidence_claims)) = 0 AND bool_and(h.evidence_sources = '[]')""",
                     [env["run_id"]]).fetchall()
    c.close()
    assert not bare


def test_alert_replays_exactly(env):
    import duckdb
    c = duckdb.connect(str(env["config"].WAREHOUSE), read_only=True)
    alert_id = c.execute("SELECT alert_id FROM alerts WHERE run_id = ? ORDER BY alert_id LIMIT 1",
                         [env["run_id"]]).fetchone()[0]
    c.close()
    res = env["pipeline"].rerun_alert(alert_id, actor="test")
    assert res["reproduced"], res


def test_raw_zone_is_write_once(env):
    from npi_integrity.ingest import WriteOnceViolation, land
    cfg = env["config"]
    raw_file = next((cfg.RAW / "optout").rglob("*.csv"))
    inbox_file = cfg.INBOX / "synthetic" / raw_file.relative_to(cfg.RAW)
    inbox_file.write_text(inbox_file.read_text() + "\n")
    with pytest.raises(WriteOnceViolation):
        land(cfg.INBOX / "synthetic")


# ------------------------------------------------------------------------------- API controls
@pytest.fixture(scope="module")
def client(env):
    from fastapi.testclient import TestClient
    import npi_integrity.api as api
    importlib.reload(api)
    return TestClient(api.app), api


def _an_alert(tc):
    return tc.get("/api/alerts").json()[0]["alert_id"]


def test_security_headers(client):
    tc, _ = client
    r = tc.get("/healthz")
    assert r.status_code == 200 and "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_disposition_controls(client, env):
    tc, _ = client
    aid = _an_alert(tc)
    assert tc.post(f"/api/alerts/{aid}/disposition", json={"actor": "t", "action": "dismiss"}).status_code == 400
    assert tc.post(f"/api/alerts/{aid}/disposition", json={"actor": "t", "action": "escalate", "reason": "x"}).status_code == 400
    assert tc.post(f"/api/alerts/{aid}/disposition", json={"actor": "", "action": "review"}).status_code == 400
    assert tc.post(f"/api/alerts/{aid}/disposition", json={"actor": "t", "action": "delete"}).status_code == 400
    assert tc.get(f"/api/alerts/{aid}/package").status_code == 409


def test_agent_draft_is_read_only_and_audited(client, env):
    tc, api = client
    aid = _an_alert(tc)
    before = tc.get(f"/api/alerts/{aid}").json()["alert"]["status"]
    r = tc.post(f"/api/alerts/{aid}/draft")
    assert r.status_code == 200 and r.json()["final_checks"]["passed"]
    assert tc.get(f"/api/alerts/{aid}").json()["alert"]["status"] == before
    assert any(x["action"] == "agent_draft" for x in tc.get("/api/audit").json())


def test_agent_draft_rate_limit(client, env, monkeypatch):
    tc, api = client
    monkeypatch.setenv("NPI_DRAFTS_PER_MINUTE", "1")
    # Budget is durable and shared; isolate this test from earlier reservations.
    from npi_integrity.cases import store
    with store().connect() as conn:
        conn.execute("DELETE FROM budget")
    aid = _an_alert(tc)
    assert tc.post(f"/api/alerts/{aid}/draft").status_code == 200
    assert tc.post(f"/api/alerts/{aid}/draft").status_code == 429


def test_domain_gate(client, monkeypatch):
    tc, api = client
    monkeypatch.setattr(api, "ALLOWED_DOMAIN", "texmed.us")
    monkeypatch.setenv("NPI_IAP_AUDIENCE", "test-audience")
    from npi_integrity import security
    monkeypatch.setattr(security, "verify_iap", lambda token, audience: {"email": token, "sub": "test"})
    assert tc.get("/api/summary").status_code == 403
    assert tc.get("/api/summary", headers={"x-goog-iap-jwt-assertion": "x@gmail.com"}).status_code == 403
    assert tc.get("/api/summary", headers={"x-goog-iap-jwt-assertion": "a@texmed.us"}).status_code == 200
    assert tc.get("/healthz").status_code == 200


def test_deployed_pilot_rejects_missing_mode_and_free_text_reason(client, monkeypatch):
    tc, api = client
    monkeypatch.setenv("K_SERVICE", "npi-console")
    assert tc.get("/healthz").status_code == 503
    monkeypatch.setenv("NPI_DATA_MODE", "synthetic")
    monkeypatch.setattr(api, "ALLOWED_DOMAIN", "texmed.us")
    monkeypatch.setenv("NPI_IAP_AUDIENCE", "test-audience")
    monkeypatch.setenv("NPI_REVIEWERS", "reviewer@texmed.us")
    from npi_integrity import security
    monkeypatch.setattr(security, "verify_iap", lambda token, audience: {"email": token, "sub": "test"})
    r = tc.post("/api/alerts/A-2026-000001/disposition",
                json={"actor": "reviewer@texmed.us", "action": "monitor", "reason": "Patient Jane Doe"},
                headers={"x-goog-iap-jwt-assertion": "reviewer@texmed.us"})
    assert r.status_code == 400
    assert "free text is disabled" in r.json()["detail"]


def test_actual_body_size_is_limited_without_content_length(client):
    tc, _ = client
    r = tc.post("/api/alerts/unknown/disposition", content=(b"x" * 65001 for _ in range(1)))
    assert r.status_code == 413


def test_cross_origin_write_is_refused(client):
    tc, _ = client
    r = tc.post("/api/alerts/unknown/disposition", json={"actor":"r","action":"review"},
                headers={"origin":"https://attacker.example"})
    assert r.status_code == 403


def test_deployed_public_origin_allows_both_run_urls_but_rejects_other_origins(client, monkeypatch):
    tc, _ = client
    monkeypatch.setenv("NPI_PUBLIC_ORIGIN", "https://npi-console.example.run.app")
    payload = {"actor": "r", "action": "review"}
    same = tc.post("/api/alerts/unknown/disposition", json=payload,
                   headers={"origin": "https://npi-console.example.run.app", "host": "npi-console.example.run.app"})
    assert same.status_code == 404  # Reached the route; only the unknown alert failed.
    alternate = tc.post("/api/alerts/unknown/disposition", json=payload,
                        headers={"origin": "https://npi-console-alt.a.run.app", "host": "npi-console-alt.a.run.app"})
    assert alternate.status_code == 404
    other = tc.post("/api/alerts/unknown/disposition", json=payload,
                    headers={"origin": "https://attacker.example", "host": "npi-console.example.run.app"})
    assert other.status_code == 403
    wrong_scheme = tc.post("/api/alerts/unknown/disposition", json=payload,
                           headers={"origin": "http://npi-console.example.run.app", "host": "npi-console.example.run.app"})
    assert wrong_scheme.status_code == 403


def test_no_alert_run_still_returns_watchlist(client, env):
    tc, api = client
    cur = api.db()
    cur.execute("BEGIN TRANSACTION")
    try:
        cur.execute("DELETE FROM alerts WHERE run_id = ?", [env["run_id"]])
        items = api._watchlist(cur, env["run_id"])
        assert items
        # Every scored provider remains visible when no alert rows exist.
        assert len(items) == cur.execute("SELECT count(*) FROM scores WHERE run_id = ?", [env["run_id"]]).fetchone()[0]
    finally:
        cur.execute("ROLLBACK")


def test_historical_detail_rebuilds_original_provider(client, env, monkeypatch):
    tc, api = client
    aid = _an_alert(tc)
    before = api.alert_detail(aid)
    cur = api.db()
    npi = before["alert"]["npi"]
    original = before["provider"]["display_name"]
    cur.execute("UPDATE providers SET display_name='LATER CHANGED NAME' WHERE npi=?", [npi])
    monkeypatch.setattr(api, "latest_run", lambda conn: "later-run")
    try:
        after = api.alert_detail(aid)
        assert after["provider"]["display_name"] == original
        assert after["hits"] == before["hits"]
    finally:
        cur.execute("UPDATE providers SET display_name=? WHERE npi=?", [original,npi])


def test_source_metadata_uses_alert_run_not_latest(client, env):
    _, api = client
    cur = api.db()
    hit = api.rows(cur, "SELECT * FROM rule_hits WHERE run_id=? LIMIT 1", [env["run_id"]])
    enriched = api._evidence(cur,hit,env["run_id"])
    for source in enriched[0]["sources"]:
        expected = api.one(cur,"SELECT sha256 FROM source_loads WHERE run_id=? AND source=? AND version=?",
                           [env["run_id"],source["source"],source["version"]])
        if expected:
            assert source["sha256"] == expected["sha256"][:16]


def test_invalid_run_returns_not_found(client):
    tc, _ = client
    assert tc.get("/api/summary?run_id=missing").status_code == 404


def test_signed_viewer_cannot_change_case(client, monkeypatch):
    tc, api = client
    aid = _an_alert(tc)
    monkeypatch.setattr(api,"ALLOWED_DOMAIN","texmed.us")
    monkeypatch.setenv("NPI_IAP_AUDIENCE","test-audience")
    from npi_integrity import security
    monkeypatch.setattr(security,"verify_iap",lambda *args:{"email":"viewer@texmed.us","sub":"test"})
    r = tc.post(f"/api/alerts/{aid}/disposition",json={"actor":"gowtham@texmed.us","action":"review"},
                headers={"x-goog-iap-jwt-assertion":"signed"})
    assert r.status_code == 403


def test_historical_summary_keeps_run_selector(client, env):
    _, api = client
    cur = api.db()
    fake = "RUN-FUTURE-SELECTOR-TEST"
    cur.execute("INSERT INTO runs SELECT ? AS run_id, as_of, started_at, finished_at + INTERVAL 1 DAY, "
                "mode, n_files, n_hits, n_scored, n_alerts, rules_json, manifest_json, status "
                "FROM runs WHERE run_id = ?", [fake, env["run_id"]])
    try:
        result = api.summary(run_id=env["run_id"])
        assert {x["run_id"] for x in result["runs"]} >= {fake, env["run_id"]}
    finally:
        cur.execute("DELETE FROM runs WHERE run_id = ?", [fake])


# --------------------------------------------------------- Task-focused console journeys
def test_search_opens_the_same_provider_by_npi_or_name(client):
    tc, api = client
    p = api.one(api.db(), "SELECT npi, display_name FROM providers WHERE length(display_name) > 8 ORDER BY npi LIMIT 1")
    by_npi = tc.get("/api/search", params={"q": p["npi"]})
    by_name = tc.get("/api/search", params={"q": p["display_name"].lower()})
    assert by_npi.status_code == by_name.status_code == 200
    assert [x["npi"] for x in by_npi.json()] == [p["npi"]]
    assert p["npi"] in {x["npi"] for x in by_name.json()}
    assert tc.get("/api/search", params={"q": "NO_SUCH_PROVIDER_987654"}).json() == []


def test_provider_check_qualifies_status_and_links_to_case(client):
    tc, _ = client
    alert = tc.get("/api/alerts").json()[0]
    r = tc.get(f"/api/providers/{alert['npi']}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["provider"]["npi"] == alert["npi"]
    assert detail["alert"]["alert_id"] == alert["alert_id"]
    assert detail["assessment"]["label"] in {"Needs attention", "Watching", "To review"}
    assert detail["assessment"]["sentence"]
    assert detail["as_of"]
    assert tc.get("/api/providers/0000000000").status_code == 404


def test_verification_reports_each_field_and_does_not_audit_submitted_values(client, monkeypatch):
    tc, api = client
    p = api.one(api.db(), "SELECT npi, display_name, city, state, taxonomy_desc FROM providers "
                              "WHERE city IS NOT NULL AND taxonomy_desc IS NOT NULL ORDER BY npi LIMIT 1")
    def live_record(npi):
        if npi != p["npi"]:
            raise HTTPException(404, "NPI not found in the live NPPES Registry")
        return {"npi": npi, "name": p["display_name"], "city": p["city"], "state": p["state"],
                "taxonomy": p["taxonomy_desc"], "last_updated": "2026-10-01",
                "source_url": f"https://npiregistry.cms.hhs.gov/provider-view/{npi}"}
    monkeypatch.setattr(api, "lookup_nppes", live_record)
    audit_before = len(tc.get("/api/audit", params={"limit": 1000}).json())
    r = tc.post("/api/verify", json={"npi": p["npi"], "name": p["display_name"].lower(),
                                     "city": "NOT THE CITY", "taxonomy": p["taxonomy_desc"],
                                     "license": "SYNTHETIC-LICENSE-ONLY"})
    assert r.status_code == 200
    fields = {f["field"]: f for f in r.json()["fields"]}
    assert fields["Name"]["result"] == "Match"
    assert fields["City"]["result"] == "Differs"
    assert fields["State"]["result"] == "Not supplied"
    assert fields["Taxonomy"]["result"] == "Match"
    assert fields["License"]["result"] == "Cannot check yet"
    assert fields["License"]["official"] is None
    assert "not saved" in r.json()["note"].lower()
    assert len(tc.get("/api/audit", params={"limit": 1000}).json()) == audit_before
    assert tc.post("/api/verify", json={"npi": "1234567890"}).status_code == 400
    assert tc.post("/api/verify", json={"npi": "0000000006"}).status_code == 404


def test_roster_preserves_row_order_and_never_claims_unknown_npi_is_clear(client, monkeypatch):
    tc, api = client
    monkeypatch.setenv("NPI_DATA_MODE", "synthetic")
    known = api.one(api.db(), "SELECT npi FROM scores ORDER BY npi LIMIT 1")["npi"]
    unknown = "1234567893"
    assert api.npi_valid(unknown) and not api.one(api.db(), "SELECT npi FROM providers WHERE npi=?", [unknown])
    r = tc.post("/api/roster/check", json={"npis": [known, unknown]})
    assert r.status_code == 200
    body = r.json()
    assert [x["npi"] for x in body["results"]] == [known, unknown]
    assert body["results"][0]["label"] == tc.get(f"/api/providers/{known}").json()["assessment"]["label"]
    assert body["results"][1]["label"] == "Cannot check yet"
    assert body["saved"] is False and "No roster is stored" in body["note"]


@pytest.mark.parametrize("npis,expected", [
    ([], 422), (["1234567893"] * 2, 400), (["1234567890"], 400),
    (["1234567893"] * 101, 422),
])
def test_roster_rejects_empty_duplicate_invalid_and_oversized_inputs(client, monkeypatch, npis, expected):
    tc, _ = client
    monkeypatch.setenv("NPI_DATA_MODE", "synthetic")
    assert tc.post("/api/roster/check", json={"npis": npis}).status_code == expected


def test_roster_is_blocked_outside_pilot_mode(client, monkeypatch):
    tc, _ = client
    monkeypatch.setenv("NPI_DATA_MODE", "real")
    r = tc.post("/api/roster/check", json={"npis": ["1234567893"]})
    assert r.status_code == 403


def test_government_leads_and_mode_are_role_gated(client, monkeypatch):
    tc, api = client
    monkeypatch.setattr(api, "ALLOWED_DOMAIN", "texmed.us")
    monkeypatch.setenv("NPI_IAP_AUDIENCE", "test-audience")
    monkeypatch.setenv("NPI_REVIEWERS", "reviewer@texmed.us")
    from npi_integrity import security
    monkeypatch.setattr(security, "verify_iap", lambda token, audience: {"email": token, "sub": "test"})
    viewer = {"x-goog-iap-jwt-assertion": "viewer@texmed.us"}
    reviewer = {"x-goog-iap-jwt-assertion": "reviewer@texmed.us"}
    assert tc.get("/api/session", headers=viewer).json()["modes"] == ["clinic"]
    assert tc.get("/api/government/leads", headers=viewer).status_code == 403
    assert tc.get("/api/session", headers=reviewer).json()["modes"] == ["clinic", "government"]
    leads = tc.get("/api/government/leads", headers=reviewer)
    assert leads.status_code == 200 and leads.json()
    assert all(x["alert_id"] and x["top_reason"] for x in leads.json())


def test_case_workspace_has_dated_reasons_evidence_and_review_history(client):
    tc, _ = client
    aid = _an_alert(tc)
    r = tc.get(f"/api/alerts/{aid}")
    assert r.status_code == 200
    case = r.json()
    assert case["alert"]["alert_id"] == aid
    assert case["provider"]["npi"] == case["alert"]["npi"]
    assert case["alert"]["created_as_of"]
    assert case["hits"] and case["draft_summary"]
    assert all(h["reason"] and (h["sources"] or h["claims"]) for h in case["hits"])
    assert "fraud determination" in " ".join(case["draft_summary"]).lower()
    assert isinstance(case["dispositions"], list)
