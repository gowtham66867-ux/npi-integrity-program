"""Official Registry adapter: exact NPI only, no synthetic score or PHI."""
import httpx
import pytest
from fastapi import HTTPException

from npi_integrity import nppes_live


def response(body, status=200):
    return httpx.Response(status, json=body, request=httpx.Request("GET", nppes_live.API_URL))


def test_exact_public_npi_lookup(monkeypatch):
    seen = {}
    def get(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return response({"result_count": 1, "results": [{"number": "1447268255", "enumeration_type": "NPI-2",
            "basic": {"organization_name": "APRIA HEALTHCARE LLC", "status": "A", "last_updated": "2024-04-19"},
            "addresses": [{"address_purpose": "MAILING", "city": "INDIANAPOLIS", "state": "IN"},
                          {"address_purpose": "LOCATION", "address_1": "100 MAIN STREET", "address_2": "SUITE 2", "city": "BATON ROUGE", "state": "LA", "postal_code": "70801-1234"}],
            "taxonomies": [{"desc": "Supplies", "code": "332B00000X", "primary": True}]}]})
    monkeypatch.setattr(nppes_live.httpx, "get", get)
    result = nppes_live.lookup("1447268255")
    assert seen["url"] == "https://npiregistry.cms.hhs.gov/api/"
    assert seen["params"] == {"version": "2.1", "number": "1447268255"}
    assert result["name"] == "APRIA HEALTHCARE LLC"
    assert (result["city"], result["state"], result["status"]) == ("BATON ROUGE", "LA", "Active")
    assert result["street_address"] == "100 MAIN STREET SUITE 2"
    assert result["postal_code"] == "70801-1234"
    assert "score" not in result and "claims" not in result


def test_invalid_npi_does_not_call_cms(monkeypatch):
    monkeypatch.setattr(nppes_live.httpx, "get", lambda *_a, **_k: pytest.fail("network called"))
    with pytest.raises(HTTPException) as exc:
        nppes_live.lookup("1234567890")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("body,status", [({"results": []}, 404), ({"oops": 1}, 502),
                                              ({"results": [{"number": "1234567893"}]}, 502)])
def test_missing_or_unexpected_record_fails_closed(monkeypatch, body, status):
    monkeypatch.setattr(nppes_live.httpx, "get", lambda *_a, **_k: response(body))
    with pytest.raises(HTTPException) as exc:
        nppes_live.lookup("1447268255")
    assert exc.value.status_code == status


def test_cms_failure_is_not_a_clear_result(monkeypatch):
    monkeypatch.setattr(nppes_live.httpx, "get", lambda *_a, **_k: response({}, 429))
    with pytest.raises(HTTPException) as exc:
        nppes_live.lookup("1447268255")
    assert exc.value.status_code == 503
