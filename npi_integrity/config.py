"""Paths and run-wide constants."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("NPI_HOME", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
INBOX = DATA / "inbox"            # files land here before the raw zone (downloads or synthetic)
RAW = DATA / "raw"                # write-once, version-stamped raw zone
RUNS = DATA / "runs"              # per-run manifest + rule snapshot (for as-of re-runs)
ANSWER_KEY = DATA / "answer_key"  # sealed synthetic answer key; the engine never reads it
WAREHOUSE = DATA / "warehouse.duckdb"
RULES_DIR = ROOT / "rules"

DEFAULT_AS_OF = "2026-10-06"

for p in (INBOX, RAW, RUNS, ANSWER_KEY):
    p.mkdir(parents=True, exist_ok=True)
