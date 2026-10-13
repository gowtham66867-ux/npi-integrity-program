"""Create a reviewable, minimal exact-NPI index from the public OIG LEIE CSV.

The downloadable LEIE is a snapshot, not a live clearance service. Most rows
do not publish an NPI; this index intentionally makes no name-based matches.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
PAGE_URL = "https://oig.hhs.gov/exclusions/leie-database-supplement-downloads/"
OUTPUT = Path(__file__).resolve().parent.parent / "npi_integrity/public_data/leie_npi.json.gz"
FIELDS = {"NPI", "LASTNAME", "FIRSTNAME", "MIDNAME", "BUSNAME", "CITY", "STATE", "EXCLTYPE", "EXCLDATE"}


def source_update_date(page: str) -> str:
    match = re.search(r'<h3[^>]*>\s*(\d{2}-\d{2}-\d{4})\s*</h3>\s*<p[^>]*>\s*Last Update\s*</p>',
                      page, flags=re.I)
    if not match:
        raise ValueError("OIG source update date not found; refusing to publish an index")
    return datetime.strptime(match.group(1), "%m-%d-%Y").date().isoformat()


def build_snapshot(payload: bytes, fetched_at: str, source_updated_at: str, *, min_rows: int = 50_000,
                   min_npis: int = 5_000) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
    if not reader.fieldnames or not FIELDS.issubset(reader.fieldnames):
        raise ValueError("LEIE CSV columns changed; refusing to publish an index")
    by_npi: dict[str, list[dict]] = {}
    count = 0
    for row in reader:
        count += 1
        npi = (row["NPI"] or "").strip()
        if len(npi) != 10 or not npi.isascii() or not npi.isdigit():
            continue
        name = (row["BUSNAME"] or "").strip() or " ".join(
            v.strip() for v in (row["FIRSTNAME"] or "", row["MIDNAME"] or "", row["LASTNAME"] or "") if v.strip()
        )
        by_npi.setdefault(npi, []).append({
            "name": name, "city": (row["CITY"] or "").strip(),
            "state": (row["STATE"] or "").strip(),
            "exclusion_type": (row["EXCLTYPE"] or "").strip(),
            "exclusion_date": (row["EXCLDATE"] or "").strip(),
        })
    if count < min_rows or len(by_npi) < min_npis:
        raise ValueError("LEIE download is unexpectedly small; refusing to publish an index")
    return {"source": "HHS-OIG LEIE UPDATED.csv", "source_url": URL,
            "source_page_url": PAGE_URL, "source_updated_at": source_updated_at,
            "fetched_at": fetched_at, "sha256": digest, "total_rows": count,
            "rows_with_npi": sum(map(len, by_npi.values())),
            "records": dict(sorted(by_npi.items()))}


def main() -> None:
    page = httpx.get(PAGE_URL, timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
    page.raise_for_status()
    updated = source_update_date(page.text)
    response = httpx.get(URL, timeout=httpx.Timeout(90.0, connect=10.0), follow_redirects=True)
    response.raise_for_status()
    if len(response.content) > 50_000_000:
        raise ValueError("LEIE download exceeds expected size")
    snapshot = build_snapshot(response.content, datetime.now(timezone.utc).isoformat(), updated)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".tmp")
    with tmp.open("wb") as file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=file, mtime=0) as zipped:
            zipped.write(json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode())
    tmp.replace(OUTPUT)
    print(f"LEIE indexed: {snapshot['rows_with_npi']} rows with NPI / {snapshot['total_rows']} total; "
          f"sha256={snapshot['sha256']}; source_updated={updated}; fetched={snapshot['fetched_at']}")


if __name__ == "__main__":
    main()
