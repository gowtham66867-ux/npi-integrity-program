"""Agent orchestration for one alert.

    context agent (tool loop, gated)  ->  evidence ledger (harness-assigned IDs)
        -> required-evidence check (harness fills gaps deterministically)
        -> narrative agent (structured output)
        -> deterministic verifier + independent QA agent
        -> repair loop with the exact violations (bounded)
        -> deterministic template fallback if still failing
    Result is a DRAFT: agents never change alert state; a human signs before export.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .guardrails import Budget, ToolGate, check_data_boundary, check_output, summarize
from .models import ModelClient, ScriptedModel, good_draft
from .tools import TOOL_DEFS, DataSource, Ledger, Toolbox

PROMPTS = Path(__file__).parent / "prompts"
DRAFT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sentences", "open_questions"],
    "properties": {
        "sentences": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "citations"],
            "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}},
        "open_questions": {"type": "array", "items": {"type": "string"}}},
}
QA_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["index", "supported", "issue"],
        "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"}, "issue": {"type": "string"}}}}},
}
REQUIRED_KINDS = ("alert", "rule_hit", "profile")


def prompt(name: str) -> tuple[str, str]:
    text = (PROMPTS / f"{name}.md").read_text()
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def draft_case_summary(alert_id: str, source: DataSource, model: ModelClient, qa_model: ModelClient | None = None,
                       budget: Budget | None = None, data_mode: str = "synthetic", max_repairs: int = 2) -> dict:
    budget = budget or Budget()
    qa_model = qa_model or model
    t0 = time.time()
    check_data_boundary(data_mode, model.external or qa_model.external)
    detail = source.alert(alert_id)
    npi = detail["alert"]["npi"]
    ledger = Ledger()
    toolbox = Toolbox(source, ledger)
    toolbox._detail[alert_id] = detail
    gate = ToolGate(alert_id, npi, budget)
    first_rule = next((h["rule_id"] for h in detail["hits"] if h.get("claims")), None)
    ctx = {"alert_id": alert_id, "npi": npi, "first_rule_with_claims": first_rule, "ledger": ledger}
    trace, cost, out_tokens, events = [], 0.0, 0, []
    prompts = {n: prompt(n) for n in ("context", "narrative", "qa")}

    def account(turn, agent):
        nonlocal cost, out_tokens
        cost += turn.cost_usd
        out_tokens += turn.usage.get("output_tokens", 0)
        trace.append({"agent": agent, "stop_reason": turn.stop_reason, "tool_calls": [c.name for c in turn.tool_calls],
                      "usage": turn.usage, "model": turn.model})

    def over_budget():
        return cost > budget.max_cost_usd or out_tokens > budget.max_output_tokens

    # ---- 1. context agent: model-driven tool loop behind the tool gate
    messages = [{"role": "user", "content": f"Gather the evidence for alert {alert_id} (NPI {npi})."}]
    for _ in range(budget.max_model_turns):
        turn = model.step("context", prompts["context"][0], messages, TOOL_DEFS, None, ctx, effort="medium")
        account(turn, "context")
        if turn.stop_reason == "refusal":
            events.append("context agent refused; harness gathered required evidence")
            break
        messages.append({"role": "assistant", "content": turn.raw_content})
        if not turn.tool_calls:
            break
        results = []
        for call in turn.tool_calls:
            denied = gate.check(call.name, call.input)
            if denied:
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": f"Denied: {denied}",
                                "is_error": True})
            else:
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": toolbox.run(call.name, call.input)})
        messages.append({"role": "user", "content": results})
        if over_budget():
            events.append("budget exhausted during context gathering")
            break

    # ---- 2. required evidence: the harness, not the model, guarantees coverage
    have = {e["kind"] for e in ledger.items.values()}
    for kind, (tool, args) in {"alert": ("get_alert", {"alert_id": alert_id}),
                               "rule_hit": ("get_rule_evidence", {"alert_id": alert_id}),
                               "profile": ("get_profile", {"npi": npi})}.items():
        if kind not in have:
            toolbox.run(tool, args)
            events.append(f"harness added missing {kind} evidence")

    # ---- 3. narrative agent + verification + bounded repair
    status, draft, attempts = "blocked", {}, []
    if over_budget():
        events.append("budget exhausted before drafting")
    else:
        nmsgs = [{"role": "user", "content": "Evidence ledger:\n" + ledger.render() +
                  "\n\nDraft the case summary as JSON matching the schema."}]
        for attempt in range(max_repairs + 1):
            turn = model.step("narrative", prompts["narrative"][0], nmsgs, None, DRAFT_SCHEMA, ctx, effort="medium")
            account(turn, "narrative")
            if turn.stop_reason == "refusal":
                attempts.append({"attempt": attempt, "refusal": True})
                break
            cand = _parse_json(turn.text)
            checks = check_output(cand, ledger.items, ledger.tainted)
            det = summarize(checks)
            qa = {"verdicts": []}
            if det["passed"]:
                qctx = {**ctx, "draft": cand}
                qturn = qa_model.step("qa", prompts["qa"][0], [{"role": "user", "content":
                                      "Evidence ledger:\n" + ledger.render() + "\n\nDraft:\n" + json.dumps(cand)}],
                                      None, QA_SCHEMA, qctx, effort="medium")
                account(qturn, "qa")
                qa = _parse_json(qturn.text) or {"verdicts": []}
            qa_fail = [v for v in qa.get("verdicts", []) if not v.get("supported")]
            attempts.append({"attempt": attempt, "deterministic": det, "qa_unsupported": qa_fail})
            if det["passed"] and not qa_fail:
                status, draft = "passed", cand
                break
            problems = [d for c in det["checks"] if not c["passed"] for d in c["details"] or [c["name"]]]
            problems += [f"sentence {v['index'] + 1}: {v['issue']}" for v in qa_fail]
            nmsgs += [{"role": "assistant", "content": turn.raw_content},
                      {"role": "user", "content": "Your draft failed these checks:\n- " + "\n- ".join(problems) +
                       "\nRewrite the full draft so that every check passes."}]
            if over_budget():
                events.append("budget exhausted during repair")
                break

    if status != "passed":
        draft = good_draft(ledger)
        fallback = summarize(check_output(draft, ledger.items, ledger.tainted))
        status = "fallback_template" if fallback["passed"] else "blocked"
        events.append("narrative agent did not pass guardrails; deterministic template draft used")

    final = summarize(check_output(draft, ledger.items, ledger.tainted))
    return {
        "alert_id": alert_id, "npi": npi, "status": status, "draft": draft, "final_checks": final,
        "attempts": attempts, "repairs": max(0, len(attempts) - 1), "events": events,
        "tool_calls": gate.calls, "tool_violations": gate.violations, "trace": trace,
        "ledger": list(ledger.items.values()), "injection_flags": ledger.tainted,
        "models": {"agent": model.name, "qa": qa_model.name},
        "prompt_versions": {k: v[1] for k, v in prompts.items()},
        "cost_usd": round(cost, 4), "output_tokens": out_tokens, "seconds": round(time.time() - t0, 2),
        "requires_human_signoff": True,
    }


def default_model() -> ModelClient:
    """Claude when credentials exist, otherwise the scripted offline model (clearly labelled)."""
    import os
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"):
        from .models import ClaudeModel
        return ClaudeModel()
    return ScriptedModel()
