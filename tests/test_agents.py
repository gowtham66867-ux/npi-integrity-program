"""Agent orchestration with scripted models over the frozen eval dataset (no API calls)."""
from pathlib import Path

import pytest

from npi_integrity.agents.guardrails import DISCLAIMER, DataBoundaryError
from npi_integrity.agents.models import ScriptedModel
from npi_integrity.agents.orchestrator import draft_case_summary
from npi_integrity.agents.tools import TOOL_DEFS, FixtureSource

DATA = Path(__file__).resolve().parent.parent / "evals" / "datasets" / "alerts.json"
pytestmark = pytest.mark.skipif(not DATA.exists(), reason="run python -m evals.build_dataset first")


@pytest.fixture(scope="module")
def src():
    return FixtureSource.from_file(DATA)


def first(src):
    return next(iter(src.cases))


def test_good_model_passes_first_try_with_citations(src):
    for aid in src.cases:
        r = draft_case_summary(aid, src, ScriptedModel())
        assert r["status"] == "passed" and r["repairs"] == 0, (aid, r["attempts"])
        assert r["draft"]["sentences"][-1]["text"] == DISCLAIMER
        ids = {e["id"] for e in r["ledger"]}
        assert all(c in ids for s in r["draft"]["sentences"] for c in s["citations"])


@pytest.mark.parametrize("fault,check", [("hallucinate_number", "O4"), ("accusatory", "O6"), ("decides", "O7"),
                                         ("leak_phi", "O8"), ("fake_citation", "O3"), ("missing_citation", "O2"),
                                         ("drop_disclaimer", "O9")])
def test_faulty_narrative_never_reaches_reviewer(src, fault, check):
    r = draft_case_summary(first(src), src, ScriptedModel(fault))
    assert check in r["attempts"][0]["deterministic"]["failed"]
    assert r["status"] == "fallback_template" and r["final_checks"]["passed"]


def test_lazy_qa_cannot_override_deterministic_checks(src):
    r = draft_case_summary(first(src), src, ScriptedModel("hallucinate_number"), qa_model=ScriptedModel("lazy_qa"))
    assert r["status"] == "fallback_template"


def test_repair_loop_fixes_a_correctable_draft(src):
    r = draft_case_summary(first(src), src, ScriptedModel("repairable"))
    assert r["status"] == "passed" and r["repairs"] == 1


def test_tool_abuse_is_denied_and_no_write_tools_exist(src):
    r = draft_case_summary(first(src), src, ScriptedModel("tool_abuse"))
    assert any("allowlist" in v for v in r["tool_violations"])
    assert any("assigned" in v for v in r["tool_violations"])
    assert all(c["name"].startswith("get_") for c in r["tool_calls"])
    assert all(t["name"].startswith("get_") and t["strict"] for t in TOOL_DEFS)
    assert r["final_checks"]["passed"]


def test_refusal_falls_back_safely(src):
    r = draft_case_summary(first(src), src, ScriptedModel("refuse"))
    assert r["status"] == "fallback_template" and any("refused" in e for e in r["events"])


def test_budget_stops_runaway_context_loop(src):
    from npi_integrity.agents.guardrails import Budget
    r = draft_case_summary(first(src), src, ScriptedModel("tool_abuse"), budget=Budget(max_model_turns=4))
    assert len(r["trace"]) <= 4 + 3 + 3  # context turns + narrative/QA attempts


def test_data_boundary_blocks_external_model(src):
    class FakeExternal(ScriptedModel):
        external = True
    with pytest.raises(DataBoundaryError):
        draft_case_summary(first(src), src, FakeExternal(), data_mode="claims_837")


def test_patients_are_pseudonymized_in_ledger(src):
    aid = next(a for a, c in src.cases.items() if any(h.get("claims") for h in c["detail"]["hits"]))
    r = draft_case_summary(aid, src, ScriptedModel())
    txt = " ".join(e["text"] for e in r["ledger"])
    assert "BENE" not in txt and "patient P1" in txt
