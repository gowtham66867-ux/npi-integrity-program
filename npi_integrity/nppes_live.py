"""Read-only, single-NPI lookups against the official NPPES Registry API.

This is separate from the synthetic detection warehouse and never persists data.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from fastapi import HTTPException

from .ux import npi_valid

API_URL = "https://npiregistry.cms.hhs.gov/api/"
REGISTRY_URL = "https://npiregistry.cms.hhs.gov/provider-view/"


def lookup(npi: str) -> dict:
    if not npi_valid(npi):
        raise HTTPException(400, "Enter a valid 10-digit NPI")
    try:
        response = httpx.get(API_URL, params={"version": "2.1", "number": npi},
                             timeout=httpx.Timeout(10.0, connect=3.0), follow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 200_000:
            raise ValueError("unexpectedly large NPPES response")
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "NPPES is unavailable; please try again later") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise HTTPException(502, "NPPES returned an unexpected response")
    if not results:
        raise HTTPException(404, "NPI not found in the live NPPES Registry")
    record = next((r for r in results if isinstance(r, dict) and str(r.get("number")) == npi), None)
    if record is None:
        raise HTTPException(502, "NPPES returned an unexpected NPI")
    basic = record.get("basic") or {}
    addresses = record.get("addresses") or []
    taxonomies = record.get("taxonomies") or []
    if not isinstance(basic, dict) or not isinstance(addresses, list) or not isinstance(taxonomies, list):
        raise HTTPException(502, "NPPES returned an unexpected record")
    location = next((a for a in addresses if isinstance(a, dict) and a.get("address_purpose") == "LOCATION"), {})
    taxonomies = [t for t in taxonomies if isinstance(t, dict)]
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else {})
    entity_type = record.get("enumeration_type")
    name = (basic.get("organization_name") if entity_type == "NPI-2" else
            " ".join(filter(None, [basic.get("first_name"), basic.get("middle_name"), basic.get("last_name")])))
    status = {"A": "Active", "D": "Deactivated"}.get(basic.get("status"), "Unknown")
    return {
        "npi": npi, "name": name or "Name unavailable", "entity_type": entity_type,
        "status": status, "taxonomy": primary.get("desc"), "taxonomy_code": primary.get("code"),
        "city": location.get("city"), "state": location.get("state"),
        "postal_code": location.get("postal_code"),
        "enumeration_date": basic.get("enumeration_date"),
        "last_updated": basic.get("last_updated"),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_url": REGISTRY_URL + npi,
    }
