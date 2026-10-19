"""Dashboard coverage must not imply a live or complete source check."""
from datetime import date

from fastapi import HTTPException

from npi_integrity import api


def snapshot(updated="2026-10-01"):
    return {"source_updated_at": updated, "fetched_at": "2026-10-02T09:30:00Z",
            "rows_with_npi": 800, "total_rows": 10000}


def test_coverage_marks_dated_stale_and_unavailable_snapshots():
    assert api._public_coverage(snapshot(), date(2026, 10, 19)) == {
        "status": "dated_snapshot", "source_updated_at": "2026-10-01",
        "fetched_at": "2026-10-02T09:30:00+00:00", "age_days": 18,
        "exact_npi_rows": 800, "total_rows": 10000}
    assert api._public_coverage(snapshot(), date(2026, 11, 16))["status"] == "stale"
    assert api._public_coverage(snapshot("2026-11-01"), date(2026, 10, 19)) == {"status": "unavailable"}
    assert api._public_coverage({}, date(2026, 10, 19)) == {"status": "unavailable"}


def test_coverage_fails_closed_when_leie_index_cannot_load(monkeypatch):
    def unavailable():
        raise HTTPException(503, "snapshot unavailable")
    monkeypatch.setattr(api, "load_leie_snapshot", unavailable)
    result = api.coverage()
    assert result["leie"] == {"status": "unavailable"}
    assert result["nppes"]["status"] == "on_demand"
    assert result["claims"]["status"] == "synthetic"
    assert result["state_licensing"]["status"] == "not_connected"
