"""Command line: npi synth | run | scorecard | rerun | serve | fetch."""
from __future__ import annotations

import argparse
import json
import sys

from . import config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="npi", description="NPI Integrity prototype")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synth", help="generate synthetic sources + sealed answer key into data/inbox/synthetic")
    s.add_argument("--seed", type=int, default=None)
    r = sub.add_parser("run", help="land inbox files, build, detect, score, raise alerts")
    r.add_argument("--as-of", default=config.DEFAULT_AS_OF)
    r.add_argument("--inbox", default=str(config.INBOX / "synthetic"))
    r.add_argument("--mode", default="synthetic")
    c = sub.add_parser("scorecard", help="score a run against the sealed answer key")
    c.add_argument("--run-id")
    c.add_argument("--sweep", action="store_true", help="also replay weekly as-of dates for time-to-detect")
    rr = sub.add_parser("rerun", help="replay an alert exactly as of its creation date")
    rr.add_argument("alert_id")
    sv = sub.add_parser("serve", help="start the reviewer console")
    sv.add_argument("--port", type=int, default=8765)
    d = sub.add_parser("demo", help="synth + run + scorecard --sweep")
    d.add_argument("--as-of", default=config.DEFAULT_AS_OF)
    f = sub.add_parser("fetch", help="download real public sources into data/inbox/public")
    f.add_argument("sources", nargs="*", default=["oig_leie", "nppes_deactivation", "nppes_full"])
    a = ap.parse_args(argv)

    if a.cmd in ("synth", "demo"):
        from .synth import SEED, generate
        key = generate(seed=getattr(a, "seed", None) or SEED)
        print(f"synthetic sources written: {key['nppes_records']} NPPES records, {key['claim_lines']} claim lines, "
              f"{len(key['scenarios'])} planted scenarios, {len(key['decoys'])} decoys")
    if a.cmd in ("run", "demo"):
        from pathlib import Path
        from .pipeline import run_pipeline
        inbox = Path(getattr(a, "inbox", config.INBOX / "synthetic"))
        run_id = run_pipeline(a.as_of, inbox=inbox, mode=getattr(a, "mode", "synthetic"))
        print(f"run {run_id} complete")
    if a.cmd in ("scorecard", "demo"):
        from .pipeline import scorecard
        sc = scorecard(getattr(a, "run_id", None), sweep=getattr(a, "sweep", True) if a.cmd == "scorecard" else True)
        print_scorecard(sc)
    if a.cmd == "rerun":
        from .pipeline import rerun_alert
        print(json.dumps(rerun_alert(a.alert_id, actor="cli"), indent=2))
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("npi_integrity.api:app", host="127.0.0.1", port=a.port)
    if a.cmd == "fetch":
        from .fetch import fetch
        for src in a.sources:
            print(fetch(src))


def print_scorecard(sc: dict):
    print(f"\nScorecard for {sc['run_id']} (as of {sc['as_of']})")
    print(f"  planted scenarios caught: {sc['caught']} / {sc['total']}")
    print(f"  alerts: {sc['alerts']}  false alerts: {sc['false_alerts']}  "
          f"({sc['false_alerts_per_1000']} per 1,000 monitored NPIs)")
    for s in sc["scenarios"]:
        ttd = f"  detected {s.get('first_detected_as_of')} ({s.get('days_to_detect')} d)" if s.get("first_detected_as_of") else ""
        print(f"  {'PASS' if s['caught'] else 'MISS'} #{s['no']:>2} {s['title'][:58]:<58} "
              f"{','.join(s['expected_rules']):<10} score={s['score']} fired={','.join(s['rules_fired'])}{ttd}")
    for d in sc["decoys"]:
        print(f"  {'PASS' if d['passed'] else 'FAIL'} decoy: {d['title'][:70]:<70} max={d['max_score']} "
              f"fired={','.join(d['rules_fired'])}")
    if sc["false_alert_npis"]:
        print("  false alert NPIs:", ", ".join(sc["false_alert_npis"][:20]))


if __name__ == "__main__":
    sys.exit(main())
