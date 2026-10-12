"""Eval harness: run suites, apply release gates, write a JSON + Markdown report.

    python -m evals.run_evals                      # offline: no API calls, deterministic, CI-safe
    python -m evals.run_evals --suites guardrails,agent_faults
    python -m evals.run_evals --live               # real Claude for agents, LLM judge, red-team (costs money)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import suites as S

HERE = Path(__file__).parent
ALL = ["detection", "metamorphic", "guardrails", "agent_faults", "injection", "quality"]


def _metric(results, dotted):
    suite, name = dotted.split(".", 1)
    return results.get(suite, {}).get("metrics", {}).get(name)


def apply_gates(results: dict, live: bool) -> list[dict]:
    spec = yaml.safe_load((HERE / "gates.yaml").read_text())
    out = []
    for dotted, rule in spec["live" if live else "offline"].items():
        if dotted.split(".")[0] not in results:
            continue
        v = _metric(results, dotted)
        if "eq" in rule:
            ok, want = v == rule["eq"], f"== {rule['eq']}"
        elif "gte" in rule:
            ok, want = v is not None and v >= rule["gte"], f">= {rule['gte']}"
        else:
            target = _metric(results, rule["eq_metric"])
            ok, want = v == target, f"== {rule['eq_metric']} ({target})"
        out.append({"gate": dotted, "value": v, "want": want, "passed": bool(ok)})
    return out


def report_md(results, gates, meta) -> str:
    lines = [f"# Eval report: {meta['started']}", "",
             f"Mode: **{'live (Claude)' if meta['live'] else 'offline (scripted models)'}** · "
             f"suites: {', '.join(results)} · {meta['seconds']} s", "",
             f"## Gates: {'PASS' if all(g['passed'] for g in gates) else 'FAIL'} "
             f"({sum(g['passed'] for g in gates)}/{len(gates)})", "", "| Gate | Value | Required | |", "|---|---|---|---|"]
    lines += [f"| {g['gate']} | {g['value']} | {g['want']} | {'✅' if g['passed'] else '❌'} |" for g in gates]
    for name, r in results.items():
        lines += ["", f"## {name}", "", "| Metric | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in r["metrics"].items()]
        failed = [c for c in r["cases"] if not c["passed"]]
        known = [c for c in r["cases"] if c.get("known_limitation")]
        if failed:
            lines += ["", f"**Failing cases ({len(failed)})**", ""]
            lines += [f"- `{c['id']}` {c['name']}: {c['observed']}" for c in failed[:40]]
        if known:
            lines += ["", "**Known limitations (reported, not gated)**", ""]
            lines += [f"- `{c['id']}` {c['name']}: {c['observed']}" for c in known]
        if name == "metamorphic":
            lines += ["", "| Case | Perturbation | Result | Observed |", "|---|---|---|---|"]
            lines += [f"| {c['id']} | {c['name']} | {'pass' if c['passed'] else 'FAIL'} | {c['observed']} |" for c in r["cases"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", default=",".join(ALL))
    ap.add_argument("--live", action="store_true", help="use Claude (requires credentials; spends money)")
    ap.add_argument("--yes", action="store_true", help="skip the live-run cost confirmation")
    a = ap.parse_args(argv)
    names = [s.strip() for s in a.suites.split(",") if s.strip()]
    claude = None
    if a.live:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            sys.exit("--live needs ANTHROPIC_API_KEY (or an `ant auth login` profile)")
        if not a.yes and input("Live evals call Claude Opus 5.5 (roughly $1-5 per full run). Continue? [y/N] ").lower() != "y":
            sys.exit("cancelled")
        from npi_integrity.agents.models import ClaudeModel
        claude = ClaudeModel
        names = names + ([] if "redteam" in names else ["redteam"])
    t0 = time.time()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    results = {}
    engine = None
    for n in names:
        print(f"running {n} ...", flush=True)
        if n in ("detection", "metamorphic"):
            engine = engine or S.Engine()
            results[n] = getattr(S, f"suite_{n}")(engine)
        elif n == "injection":
            results[n] = S.suite_injection(claude)
        elif n == "quality":
            results[n] = S.suite_quality(claude, judge=claude() if claude else None)
        elif n == "redteam":
            if claude:
                results[n] = S.suite_redteam(claude())
        else:
            results[n] = getattr(S, f"suite_{n}")()
        print(f"  {results.get(n, {}).get('metrics')}", flush=True)
    gates = apply_gates(results, a.live)
    meta = {"started": started, "live": a.live, "seconds": round(time.time() - t0, 1)}
    out = HERE / "reports" / started
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"meta": meta, "gates": gates, "results": results}, indent=1, default=str))
    (out / "report.md").write_text(report_md(results, gates, meta))
    print(f"\ngates: {sum(g['passed'] for g in gates)}/{len(gates)} passed -> {out / 'report.md'}")
    for g in gates:
        if not g["passed"]:
            print(f"  FAIL {g['gate']} = {g['value']} (want {g['want']})")
    return 0 if all(g["passed"] for g in gates) else 1


if __name__ == "__main__":
    sys.exit(main())
