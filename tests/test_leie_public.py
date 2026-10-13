"""Public exclusion checks must remain exact, dated, and never imply clearance."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from npi_integrity.api import app
from npi_integrity.leie_public import check, load_snapshot
from scripts.sync_leie import build_snapshot, source_update_date


def sample():
    return {"source": "HHS-OIG LEIE UPDATED.csv", "source_url": "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv",
            "fetched_at": "2026-10-13T00:00:00+00:00", "source_updated_at": "2026-10-13", "sha256": "a" * 64,
            "rows_with_npi": 1, "total_rows": 3,
            "records": {"1447268255": [{"name": "EXAMPLE ORGANIZATION", "city": "AUSTIN", "state": "TX",
                                        "exclusion_type": "1128a1", "exclusion_date": "20260101"}]}}


def test_exact_npi_match_requires_human_verification():
    result = check("1447268255", snapshot=sample(), now=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert result["matches"][0]["name"] == "EXAMPLE ORGANIZATION"
    assert result["result"] == "Published NPI match — verify with OIG"
    assert result["snapshot_age_days"] == 1 and result["stale"] is False
    assert "not a fraud determination" in result["note"]
    assert result["source_sha256"] == "a" * 64


def test_no_exact_npi_match_is_not_clearance_even_when_snapshot_is_stale():
    result = check("1234567893", snapshot=sample(), now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert result["matches"] == [] and result["stale"] is True
    assert "No exact NPI match" in result["result"]
    assert "cannot clear a provider" in result["note"]
    assert result["indexed_rows"] < result["total_rows"]


def test_invalid_or_unavailable_snapshot_fails_closed(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        check("1234567890", snapshot=sample())
    assert exc.value.status_code == 400
    monkeypatch.setattr("npi_integrity.leie_public.SNAPSHOT", __import__("pathlib").Path("/does-not-exist"))
    load_snapshot.cache_clear()
    with pytest.raises(HTTPException) as exc:
        load_snapshot()
    assert exc.value.status_code == 503
    load_snapshot.cache_clear()


def test_snapshot_builder_drops_name_only_and_sensitive_columns():
    content = ("NPI,LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,CITY,STATE,EXCLTYPE,EXCLDATE,DOB,ADDRESS\n"
               "1447268255,,,,EXAMPLE ORGANIZATION,AUSTIN,TX,1128a1,20260101,19700101,PRIVATE ADDRESS\n"
               "0,SMITH,JANE,, ,DALLAS,TX,1128b8,20260101,19800101,OTHER ADDRESS\n")
    result = build_snapshot(content.encode(), "2026-10-13T00:00:00+00:00", "2026-10-09", min_rows=2, min_npis=1)
    assert result["total_rows"] == 2 and result["rows_with_npi"] == 1
    assert list(result["records"]) == ["1447268255"]
    assert "DOB" not in str(result) and "PRIVATE ADDRESS" not in str(result)


def test_source_date_is_distinct_from_fetch_date_and_parser_fails_closed():
    assert source_update_date('<h3 class="usa-alert__heading">10-09-2026</h3><p>Last Update</p>') == "2026-10-09"
    with pytest.raises(ValueError):
        source_update_date("Last Update")
    data = sample()
    data["fetched_at"] = "2026-12-01T00:00:00+00:00"
    result = check("1234567893", snapshot=data, now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert result["stale"] is True  # a fresh download cannot reset source age


def test_duplicate_published_npi_rows_are_all_shown():
    data = sample()
    data["records"]["1447268255"].append({"name": "SECOND PUBLISHED ROW", "city": "AUSTIN",
                                             "state": "TX", "exclusion_type": "1128b8",
                                             "exclusion_date": "20260201"})
    result = check("1447268255", snapshot=data, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert [row["name"] for row in result["matches"]] == ["EXAMPLE ORGANIZATION", "SECOND PUBLISHED ROW"]


def test_changed_headers_or_short_download_cannot_replace_snapshot():
    with pytest.raises(ValueError, match="columns changed"):
        build_snapshot(b"NPI,BUSNAME\n1447268255,EXAMPLE\n", "2026-10-13T00:00:00+00:00", "2026-10-09",
                       min_rows=1, min_npis=1)
    content = ("NPI,LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,CITY,STATE,EXCLTYPE,EXCLDATE\n"
               "1447268255,,,,EXAMPLE,AUSTIN,TX,1128a1,20260101\n")
    with pytest.raises(ValueError, match="unexpectedly small"):
        build_snapshot(content.encode(), "2026-10-13T00:00:00+00:00", "2026-10-09")


def test_packaged_snapshot_is_valid_and_endpoint_does_not_join_synthetic_scores():
    snapshot = load_snapshot()
    assert snapshot["total_rows"] >= 50_000 and snapshot["rows_with_npi"] >= 5_000
    client = TestClient(app)
    response = client.get("/api/public-integrity/1447268255")
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["leie"]
    assert "score" not in str(body).lower() and "claims" not in str(body).lower()
    assert "DOB" not in str(body) and "ADDRESS" not in str(body)
