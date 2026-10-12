"""Freeze alert snapshots (detail + graph) into evals/datasets/alerts.json.

Reads from the running console (http://127.0.0.1:8765) so it does not fight the
DuckDB writer lock; falls back to in-process reads when the console is down.
Keeps every alert outside the shared-address cluster plus 2 cluster members.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "datasets" / "alerts.json"


def fetch(base="http://127.0.0.1:8765"):
    try:
        alerts = httpx.get(f"{base}/api/alerts", timeout=10).json()
        get = lambda p: httpx.get(base + p, timeout=30).json()
    except httpx.HTTPError:
        from npi_integrity import api
        alerts = api.alerts()
        get = lambda p: (api.alert_detail(p.split("/")[3]) if "/graph" not in p else api.graph(p.split("/")[3]))
    picked, seen_cluster = [], {}
    for a in alerts:
        k = a.get("cluster_key")
        if k:
            seen_cluster[k] = seen_cluster.get(k, 0) + 1
            if seen_cluster[k] > 2:
                continue
        picked.append(a)
    cases = {}
    for a in picked:
        cases[a["alert_id"]] = {"detail": get(f"/api/alerts/{a['alert_id']}"),
                                "graph": get(f"/api/providers/{a['npi']}/graph")}
    OUT.write_text(json.dumps(cases, indent=1, default=str))
    return cases


if __name__ == "__main__":
    c = fetch(*(sys.argv[1:2]))
    print(f"wrote {len(c)} alert snapshots to {OUT}")
