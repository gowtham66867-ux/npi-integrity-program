"""Offline Jev request/response boundary for synthetic reviewer-assistance trials.

This module does not call the TypeSafe API or modify alerts/cases. Only its
allowlisted aggregate fields can enter the external request if a separate
integration is approved later.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from npi_integrity.agents.guardrails import DataBoundaryError, RULE_ID


EVIDENCE_STEPS = {
    "identity_sources": "Inspect the cited provider identity and exclusion sources.",
    "claim_lines": "Inspect the cited claim-line evidence.",
    "relationships": "Inspect the cited provider relationship evidence.",
    "human_review": "Begin human review of the evidence already cited.",
}


@dataclass(frozen=True)
class SyntheticAlertSummary:
    score: int
    tier: str
    hard_signal: bool
    rule_ids: tuple[str, ...]
    cited_source_count: int
    claim_line_count: int
    data_mode: str = "synthetic"


def build_jev_request(summary: SyntheticAlertSummary, *, model: str = "jev-latest") -> dict:
    """Create a typed Choice request from aggregates, never raw case records."""
    if summary.data_mode != "synthetic":
        raise DataBoundaryError("Jev shadow evaluation accepts synthetic data only")
    if type(summary.score) is not int or not 0 <= summary.score <= 1000:
        raise ValueError("score must be an integer in 0..1000")
    if summary.tier not in {"Critical", "High", "Elevated", "Watch", "Low"}:
        raise ValueError("unknown tier")
    if type(summary.hard_signal) is not bool:
        raise ValueError("hard_signal must be boolean")
    if not isinstance(summary.rule_ids, tuple) or len(summary.rule_ids) > 30 or any(
        not isinstance(rule, str) or not RULE_ID.fullmatch(rule) for rule in summary.rule_ids
    ):
        raise ValueError("invalid rule IDs")
    for count in (summary.cited_source_count, summary.claim_line_count):
        if type(count) is not int or not 0 <= count <= 10000:
            raise ValueError("counts must be integers in 0..10000")
    if not isinstance(model, str) or not re.fullmatch(r"jev-[a-z0-9][a-z0-9.-]{0,35}", model):
        raise ValueError("invalid Jev model")
    # json.dumps serializes only fixed keys and validated values: no free text,
    # NPI, patient identifier, name, claim detail, or citation body can leak.
    state = json.dumps({
        "data_mode": "synthetic",
        "score": summary.score,
        "tier": summary.tier,
        "hard_signal": summary.hard_signal,
        "rule_ids": sorted(set(summary.rule_ids)),
        "cited_source_count": summary.cited_source_count,
        "claim_line_count": summary.claim_line_count,
    }, sort_keys=True, separators=(",", ":"))
    return {
        "model": model,
        "state": state,
        "questions": {
            "next_evidence_step": {
                "type": "choice",
                "instructions": "Which evidence category should a human reviewer inspect next? Suggest only; do not decide alert disposition or fraud.",
                "criteria": EVIDENCE_STEPS.copy(),
            }
        },
    }


def parse_jev_suggestion(response: dict) -> dict:
    """Validate a Jev answer; return an advisory value with no write action."""
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise ValueError("missing Jev answers")
    answer = response["answers"].get("next_evidence_step")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("missing choice answer")
    choice, confidence = answer.get("choice"), answer.get("confidence")
    if choice not in EVIDENCE_STEPS:
        raise ValueError("unrecognized evidence step")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("invalid confidence")
    return {
        "suggested_evidence_step": choice,
        "confidence": float(confidence),
        "model": response.get("model") if isinstance(response.get("model"), str) else None,
        "advisory_only": True,
        "requires_human_review": True,
        "applied": False,
    }
