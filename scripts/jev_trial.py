"""Build synthetic Jev shadow requests; optionally call TypeSafe after billing checks."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import httpx

from npi_integrity import config
from npi_integrity.jev_shadow import SyntheticAlertSummary, build_jev_request, parse_jev_suggestion

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_REPORT = config.ROOT / "evals" / "reports" / "jev_shadow_preview.json"
DEFAULT_BILLING = config.ROOT / "deploy" / "jev_billing_policy.json"


def verify_typesafe_billing(policy: dict, now: datetime | None = None) -> None:
    """Require a fresh TypeSafe-specific payment inspection before paid calls."""
    now = now or datetime.now(timezone.utc)
    if policy.get("verified_by") != "gowtham@texmed.us" or policy.get("source") != "typesafe_payment_console":
        raise ValueError("TypeSafe billing must be inspected by the approved TexMed identity")
    cards = policy.get("active_payment_method_last4")
    if not isinstance(cards, list) or not cards or any(
        not isinstance(c, str) or len(c) != 4 or not c.isdigit() for c in cards
    ):
        raise ValueError("all active TypeSafe payment methods must be recorded by last four digits")
    if policy.get("primary_card_last4") != "8192" or "8192" not in cards or "6022" in cards:
        raise ValueError("TypeSafe primary card must be 8192 and card 6022 must be absent")
    try:
        verified = datetime.fromisoformat(policy["verified_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("missing TypeSafe payment verification timestamp") from exc
    if verified.tzinfo is None or not timedelta(0) <= now - verified <= timedelta(hours=24):
        raise ValueError("TypeSafe payment verification must be within the last 24 hours")


def synthetic_alerts(warehouse: Path, limit: int) -> list[tuple[str, SyntheticAlertSummary]]:
    """Read only the latest completed synthetic run; never load raw case text."""
    conn = duckdb.connect(str(warehouse), read_only=True)
    try:
        row = conn.execute("""SELECT run_id FROM runs WHERE status = 'completed' AND mode = 'synthetic'
                              ORDER BY finished_at DESC LIMIT 1""").fetchone()
        if not row:
            raise ValueError("no completed synthetic run")
        run_id = row[0]
        alerts = conn.execute("""SELECT alert_id, npi, score, tier FROM alerts
                                 WHERE run_id = ? ORDER BY score DESC, alert_id LIMIT ?""",
                              [run_id, limit]).fetchall()
        output = []
        for alert_id, npi, score, tier in alerts:
            hits = conn.execute("""SELECT rule_id, hard, evidence_claims, evidence_sources
                                   FROM rule_hits WHERE run_id = ? AND npi = ?""",
                                [run_id, npi]).fetchall()
            claims = {claim for _, _, values, _ in hits for claim in (values or [])}
            source_count = sum(len(json.loads(sources or "[]")) for _, _, _, sources in hits)
            output.append((alert_id, SyntheticAlertSummary(
                score=score, tier=tier, hard_signal=any(hard for _, hard, _, _ in hits),
                rule_ids=tuple(rule for rule, _, _, _ in hits),
                cited_source_count=source_count, claim_line_count=len(claims),
            )))
        return output
    finally:
        conn.close()


def call_jev(request: dict, api_key: str) -> dict:
    """One bounded API call; never log credentials or response bodies on error."""
    response = httpx.post(ENDPOINT, json=request,
                          headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"TypeSafe returned HTTP {response.status_code}")
    try:
        return parse_jev_suggestion(response.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("TypeSafe returned an invalid Jev answer") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic-only Jev shadow evaluation")
    parser.add_argument("--warehouse", type=Path, default=config.WAREHOUSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=51)
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument("--live", action="store_true", help="send requests after key and TypeSafe billing checks")
    parser.add_argument("--billing-policy", type=Path, default=DEFAULT_BILLING)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("limit must be 1..100")
    api_key = None
    if args.live:
        try:
            verify_typesafe_billing(json.loads(args.billing_policy.read_text()))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.error(f"live Jev trial refused: {exc}")
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            parser.error("live Jev trial refused: TYPESAFE_API_KEY is missing")
    items = []
    for alert_id, summary in synthetic_alerts(args.warehouse, args.limit):
        request = build_jev_request(summary, model=args.model)
        item = {"alert_id": alert_id, "request": request}
        if args.live:
            item["suggestion"] = call_jev(request, api_key)
        items.append(item)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "mode": "live_synthetic_shadow" if args.live else "offline_preview",
              "count": len(items), "items": items}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['mode']}: {len(items)} synthetic alerts -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
