"""Connectors for the real public sources (L1). Downloads land in data/inbox/public/<source>/<version>/.

Run `npi fetch oig_leie nppes_deactivation nppes_full`, then
`npi run --inbox data/inbox/public --mode public`. The NPPES full file is
about 1 GB zipped; only npidata_pfile*.csv and pl_pfile*.csv are kept.

Not yet exercised against the live sites: URLs and page layouts below were
taken from the design doc's source list and need a first supervised run.
"""
from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import Path

import httpx

from . import config

NPPES_PAGE = "https://download.cms.gov/nppes/NPI_Files.html"
NPPES_BASE = "https://download.cms.gov/nppes/"
LEIE_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
OUT = config.INBOX / "public"


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest


def _extract_flat(z: zipfile.ZipFile, member: str, dest_dir: Path):
    """The raw zone expects files directly under <source>/<version>/."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with z.open(member) as src, open(dest_dir / Path(member).name, "wb") as out:
        while chunk := src.read(1 << 20):
            out.write(chunk)


def _nppes_links() -> list[str]:
    html = httpx.get(NPPES_PAGE, follow_redirects=True, timeout=60).text
    return re.findall(r'href="([^"]+\.(?:zip|xlsx))"', html, flags=re.I)


def _abs(link: str) -> str:
    return link if link.startswith("http") else NPPES_BASE + link.lstrip("./")


def fetch(source: str) -> str:
    today = date.today().isoformat()
    if source == "oig_leie":
        dest = _download(LEIE_URL, OUT / "oig_leie" / today / "UPDATED.csv")
        return f"oig_leie -> {dest}"
    if source == "nppes_deactivation":
        link = next(l for l in _nppes_links() if "deactivat" in l.lower())
        name = Path(link).name
        zpath = _download(_abs(link), config.DATA / "downloads" / name)
        version = _version_from_name(name) or today
        dest_dir = OUT / "nppes_deactivation" / version
        dest_dir.mkdir(parents=True, exist_ok=True)
        if zpath.suffix.lower() == ".zip":
            with zipfile.ZipFile(zpath) as z:
                for m in z.namelist():
                    if m.lower().endswith((".xlsx", ".csv")):
                        _extract_flat(z, m, dest_dir)
        else:
            zpath.rename(dest_dir / name)
        return f"nppes_deactivation -> {dest_dir}"
    if source == "nppes_full":
        link = next(l for l in _nppes_links()
                    if "dissemination" in l.lower() and "weekly" not in l.lower() and "_v2" in l.lower())
        name = Path(link).name
        zpath = _download(_abs(link), config.DATA / "downloads" / name)
        version = _version_from_name(name) or today
        with zipfile.ZipFile(zpath) as z:
            for m in z.namelist():
                low = Path(m).name.lower()
                if low.startswith("npidata_pfile") and not low.endswith("fileheader.csv"):
                    _extract_flat(z, m, OUT / "nppes_full" / version)
                elif low.startswith("pl_pfile") and not low.endswith("fileheader.csv"):
                    _extract_flat(z, m, OUT / "nppes_practice_locations" / version)
        return f"nppes_full -> {OUT / 'nppes_full' / version}"
    raise SystemExit(f"no connector yet for {source}; place files in {OUT}/{source}/<YYYY-MM-DD>/ manually")


def _version_from_name(name: str) -> str | None:
    m = re.search(r"(20\d{2})(\d{2})(\d{2})(?!.*20\d{6})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    months = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"], start=1)}
    m = re.search(r"_([A-Za-z]+)_(20\d{2})", name)
    if m and m.group(1).lower() in months:
        return f"{m.group(2)}-{months[m.group(1).lower()]:02d}-01"
    return None
