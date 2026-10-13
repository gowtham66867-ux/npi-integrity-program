"""Read-only public LEIE snapshot check by exact published NPI."""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from .ux import npi_valid

SNAPSHOT = Path(__file__).parent / "public_data/leie_npi.json.gz"
SEARCH_URL = "https://exclusions.oig.hhs.gov/"


@lru_cache(maxsize=1)
def load_snapshot() -> dict:
    try:
        with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as file:
            data = json.load(file)
        required = ("records", "fetched_at", "source_updated_at", "sha256", "source", "source_url", "rows_with_npi", "total_rows")
        if any(k not in data for k in required) or not isinstance(data["records"], dict):
            raise ValueError("incomplete snapshot")
        return data
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "LEIE snapshot unavailable; exclusion status cannot be checked") from exc


def check(npi: str, *, snapshot: dict | None = None, now: datetime | None = None) -> dict:
    if not npi_valid(npi):
        raise HTTPException(400, "Enter a valid 10-digit NPI")
    data = snapshot if snapshot is not None else load_snapshot()
    try:
        fetched = datetime.fromisoformat(data["fetched_at"].replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            raise ValueError("timestamp lacks timezone")
        source_date = date.fromisoformat(data["source_updated_at"])
        age_days = max(0, ((now or datetime.now(timezone.utc)).date() - source_date).days)
        matches = data["records"].get(npi, [])
        if not isinstance(matches, list):
            raise ValueError("malformed index")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(503, "LEIE snapshot unavailable; exclusion status cannot be checked") from exc
    return {
        "npi": npi, "source": data["source"], "source_url": data["source_url"],
        "search_url": SEARCH_URL, "fetched_at": data["fetched_at"],
        "source_updated_at": data["source_updated_at"],
        "source_sha256": data["sha256"], "snapshot_age_days": age_days,
        "stale": age_days > 45, "indexed_rows": data["rows_with_npi"],
        "total_rows": data["total_rows"], "matches": matches,
        "result": "Published NPI match — verify with OIG" if matches else "No exact NPI match in indexed LEIE rows",
        "note": "Most LEIE rows do not publish an NPI. This exact-NPI snapshot check cannot clear a provider, "
                "does not search name-only rows, and is not a fraud determination. Verify any possible match "
                "in the official OIG online search before taking action.",
    }
