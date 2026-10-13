"""Pilot UX results must be qualified and must not retain submitted values."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from npi_integrity import api
from npi_integrity.ux import field_result, npi_valid, provider_assessment


def test_npi_checksum_and_comparison_states():
    assert npi_valid("1234567893")
    assert not npi_valid("1234567890")
    assert not npi_valid("123456789A")
    assert field_result("Dr. Jane Smith", "DR JANE SMITH") == "Match"
    assert field_result("Austin", "Dallas") == "Differs"
    assert field_result("TX123", None) == "Cannot check yet"
    assert field_result("", "Dallas") == "Not supplied"


def test_no_signal_does_not_become_unqualified_clear():
    result = provider_assessment({"score": 0, "tier": "Low"}, [], "Active")
    assert result["label"] == "No flagged signals"
    assert "checked sources" in result["sentence"]
    assert provider_assessment(None, [], "Deactivated")["label"] == "Needs attention"
    assert provider_assessment(None, [], "Active")["label"] == "Cannot check yet"
    assert provider_assessment({"tier": "Watch"}, [{"reason": "Pattern changed"}], "Active")["label"] == "Watching"


def test_verify_license_is_explicitly_unavailable(monkeypatch):
    monkeypatch.setattr(api, "lookup_nppes", lambda *_: {"npi": "1234567893", "name": "Jane Smith",
        "city": "Austin", "state": "TX", "taxonomy": "Family Medicine", "last_updated": "2026-10-01",
        "source_url": "https://npiregistry.cms.hhs.gov/provider-view/1234567893"})
    body = api.VerificationInput(npi="1234567893", name="JANE SMITH", city="Dallas", license="TX123")
    result = api.verify_provider(body)
    outcomes = {field["field"]: field["result"] for field in result["fields"]}
    assert outcomes == {"Name": "Match", "City": "Differs", "State": "Not supplied",
                        "Taxonomy": "Not supplied", "License": "Cannot check yet"}
    assert "saved" in result["note"]
    assert result["source"] == "Live NPPES Registry"


def test_roster_rejects_invalid_or_duplicate_npis(monkeypatch):
    monkeypatch.setenv("NPI_DATA_MODE", "synthetic")
    for npis in (["1234567890"], ["1234567893", "1234567893"]):
        with pytest.raises(HTTPException) as exc:
            api.check_roster(api.RosterInput(npis=npis))
        assert exc.value.status_code == 400


def test_government_leads_requires_server_role(monkeypatch):
    monkeypatch.setattr(api, "alerts", lambda: [{"alert_id": "A1"}])
    request = SimpleNamespace(state=SimpleNamespace(role="viewer"))
    with pytest.raises(HTTPException) as exc:
        api.government_leads(request)
    assert exc.value.status_code == 403
    request.state.role = "reviewer"
    assert api.government_leads(request) == [{"alert_id": "A1"}]
