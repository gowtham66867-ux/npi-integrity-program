"""Guardrails for the L7 agent layer (design doc C10, C14, C15).

Three rings, all deterministic so they can't be talked out of a decision:

* Input  - everything that came from a public file (names, addresses, org names)
           is untrusted data: scanned for prompt injection, wrapped, and PHI is
           redacted or pseudonymized before any model sees it. A data-boundary
           gate refuses to process non-public data in this pilot.
* Tool   - read-only allowlist, strict argument validation, per-run budgets.
* Output - every sentence must cite evidence IDs that exist in the ledger,
           every number and NPI must appear in the cited evidence, neutral
           language only, no decisions, no PHI, disclaimer present, and nothing
           an injected instruction asked for.

An LLM QA agent runs as well, but it can only add failures. It can never
clear a deterministic failure.
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

DISCLAIMER = "Potential anomalies require human verification before any decision."

# --------------------------------------------------------------------------- normalization
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S", "Т": "T",
    "Н": "H", "К": "K", "М": "M", "В": "B", "ɑ": "a", "ο": "o", "ν": "v", "Ι": "I", "Α": "A", "Ε": "E",
    "0": "0", "​": "", "‌": "", "‍": "", "⁠": "", "﻿": "",
})


def fold(text: str) -> str:
    """NFKC + confusable folding + zero-width removal, for matching (never for display)."""
    t = unicodedata.normalize("NFKC", text or "").translate(_CONFUSABLES)
    return "".join(ch for ch in t if unicodedata.category(ch) != "Cf")


# --------------------------------------------------------------------------- input ring
INJECTION_PATTERNS = [
    r"\bignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|messages)",
    r"\bdisregard (the |all |your )?(previous|prior|above|system)",
    r"\bforget (everything|your instructions|the rules)",
    r"\byou are now\b", r"\bact as\b", r"\bnew instructions?\b", r"\bsystem prompt\b",
    r"^\s*(system|assistant|developer)\s*:", r"<\s*/?\s*(system|instructions?|assistant)\s*>",
    r"\b(mark|flag|set|treat) (this|the) (provider|alert|npi)( as)? (clean|cleared|legitimate|safe|verified)",
    r"\b(dismiss|close|clear|resolve) (this|the) alert\b", r"\bno (further )?review (is )?(needed|required)\b",
    r"\bcall (the )?(update|delete|dismiss|escalate)\w*\b", r"\b(reveal|print|output|include) (the )?(patient|beneficiary|bene)",
    r"\bdo not (cite|mention|include)\b", r"\bjailbreak\b", r"\bDAN mode\b",
]
_INJ = [re.compile(p, re.I | re.M) for p in INJECTION_PATTERNS]
_B64 = re.compile(r"\b[A-Za-z0-9+/]{16,}={0,2}")


def injection_findings(text: str) -> list[str]:
    """Return the matched injection spans (also checks base64-encoded payloads)."""
    t = fold(text)
    hits = [m.group(0) for rx in _INJ for m in rx.finditer(t)]
    for tok in _B64.findall(t):
        try:
            decoded = base64.b64decode(tok + "=" * (-len(tok) % 4), validate=True).decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            continue
        if decoded and sum(c.isprintable() for c in decoded) / len(decoded) > 0.9:
            hits += [f"base64:{m.group(0)}" for rx in _INJ for m in rx.finditer(fold(decoded))]
    return hits


PHI_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "dob": re.compile(r"\b(?:DOB|date of birth)\s*[:=]?\s*\d{1,2}/\d{1,2}/\d{2,4}\b", re.I),
    "beneficiary_id": re.compile(r"\bBENE\d{6,}\b", re.I),
    "mbi": re.compile(r"\b[1-9][AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d[AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d[AC-HJKMNP-RT-Y]{2}\d{2}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "phone": re.compile(r"(?<!\d)(?:\(\d{3}\)\s?|\d{3}[-.])\d{3}[-.]\d{4}(?!\d)"),
}


def phi_findings(text: str) -> list[str]:
    t = fold(text)
    return [f"{k}:{m.group(0)}" for k, rx in PHI_PATTERNS.items() for m in rx.finditer(t)]


class Pseudonymizer:
    """Stable per-case pseudonyms ("patient P3") so the model can reason about counts without identities."""

    def __init__(self):
        self.map: dict[str, str] = {}

    def __call__(self, text: str) -> str:
        def sub(m):
            return self.map.setdefault(m.group(0).upper(), f"patient P{len(self.map) + 1}")
        t = PHI_PATTERNS["beneficiary_id"].sub(sub, text)
        t = PHI_PATTERNS["ssn"].sub("[REDACTED-SSN]", t)
        t = PHI_PATTERNS["dob"].sub("[REDACTED-DOB]", t)
        t = PHI_PATTERNS["mbi"].sub("[REDACTED-MBI]", t)
        t = PHI_PATTERNS["email"].sub("[REDACTED-EMAIL]", t)
        return t


def wrap_untrusted(text: str, max_len: int = 4000) -> tuple[str, list[str]]:
    """Strip control chars, cap length, wrap in tags, and report injection spans."""
    clean = "".join(ch for ch in (text or "") if ch in "\n\t" or unicodedata.category(ch)[0] != "C")[:max_len]
    clean = clean.replace("<untrusted_data", "&lt;untrusted_data").replace("</untrusted_data", "&lt;/untrusted_data")
    return f"<untrusted_data>{clean}</untrusted_data>", injection_findings(clean)


class DataBoundaryError(RuntimeError):
    pass


def check_data_boundary(data_mode: str, model_is_external: bool):
    """The pilot cannot process non-public data, regardless of model location."""
    if data_mode in ("synthetic", "public"):
        return
    raise DataBoundaryError(f"data mode {data_mode!r} is outside this public/synthetic pilot")


# --------------------------------------------------------------------------- tool ring
READ_ONLY_TOOLS = {"get_alert", "get_rule_evidence", "get_profile", "get_peer_stats", "get_relationships",
                   "get_claim_lines"}
ALERT_ID = re.compile(r"^A-\d{4}-\d{6}$")
RULE_ID = re.compile(r"^(D|I|C|G|V|RPM|N)-\d{2}$")


def npi_valid(npi: str) -> bool:
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    digits = "80840" + npi[:9]
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return str((10 - total % 10) % 10) == npi[9]


@dataclass
class Budget:
    max_tool_calls: int = 12
    max_same_tool: int = 3
    max_model_turns: int = 10
    max_output_tokens: int = 60_000
    max_cost_usd: float = 1.50


@dataclass
class ToolGate:
    """Validates every tool call before it runs; violations become is_error tool results, not crashes."""
    alert_id: str
    npi: str
    budget: Budget = field(default_factory=Budget)
    calls: list[dict] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def check(self, name: str, args: dict) -> str | None:
        if name not in READ_ONLY_TOOLS:
            return self._deny(f"tool {name!r} is not on the read-only allowlist")
        if len(self.calls) >= self.budget.max_tool_calls:
            return self._deny(f"tool-call budget of {self.budget.max_tool_calls} exhausted")
        if sum(c["name"] == name for c in self.calls) >= self.budget.max_same_tool:
            return self._deny(f"{name} already called {self.budget.max_same_tool} times")
        if not isinstance(args, dict):
            return self._deny("arguments must be an object")
        if "alert_id" in args and args["alert_id"] != self.alert_id:
            return self._deny("agents may only read the alert they were assigned")
        if "npi" in args and not npi_valid(str(args["npi"])):
            return self._deny(f"invalid NPI {args['npi']!r}")
        if "npi" in args and args["npi"] != self.npi:
            return self._deny("agents may only read the NPI on their assigned alert")
        if "rule_id" in args and not RULE_ID.match(str(args["rule_id"])):
            return self._deny(f"invalid rule id {args['rule_id']!r}")
        if "limit" in args and not (isinstance(args["limit"], int) and 1 <= args["limit"] <= 20):
            return self._deny("limit must be an integer 1-20")
        self.calls.append({"name": name, "args": args})
        return None

    def _deny(self, msg: str) -> str:
        self.violations.append(msg)
        return msg


# --------------------------------------------------------------------------- output ring
ACCUSATORY = ["fraud", "fraudulent", "fraudster", "criminal", "crime", "scam", "scheme", "stole", "stolen", "theft",
              "embezzl", "guilty", "illegal", "unlawful", "kickback", "con artist", "cheat", "bilked", "defraud"]
DECISIONS = [r"\b(recommend|suggest|advise)\w* (dismiss|clos|refer|escalat|suspend|exclu|revok|terminat|clear)",
             r"\bshould (be )?(dismissed|closed|referred|escalated|suspended|excluded|revoked|terminated|cleared)",
             r"\b(has been|is) (cleared|exonerated|verified as legitimate|confirmed (as )?(fraud|legitimate))",
             r"\bno (further )?review (is )?(needed|required)", r"\b(we|i) (have )?(dismissed|closed|escalated|referred)",
             r"\bpayment (should|must) be (suspended|held|stopped)"]
_DEC = [re.compile(p, re.I) for p in DECISIONS]
_NUM = re.compile(r"(?<![\w.])(\d{1,2}/\d{1,2}/\d{4}|\d[\d,]*(?:\.\d+)?)(?![\w])")
_STRIP_IDS = re.compile(r"\b(?:R-\d{3}|(?:D|I|C|G|V|RPM|N)-\d{2}|v\d+(?:\.\d+)?|E\d+|A-\d{4}-\d{6})\b")
_NPI = re.compile(r"(?<!\d)\d{10}(?!\d)")


def numbers_in(text: str) -> set[str]:
    out = set()
    for tok in _NUM.findall(_STRIP_IDS.sub(" ", fold(text))):
        tok = tok.replace(",", "")
        out.add(tok)
        if re.fullmatch(r"\d+\.0+", tok):
            out.add(tok.split(".")[0])
    return out


@dataclass
class Check:
    id: str
    name: str
    passed: bool
    details: list[str] = field(default_factory=list)


def _has_term(text: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term)}", fold(text), re.I) is not None


def check_output(draft: dict, ledger: dict[str, dict], tainted_spans: list[str] | None = None) -> list[Check]:
    """Deterministic verification of a narrative draft against the evidence ledger."""
    checks: list[Check] = []
    sentences = draft.get("sentences") if isinstance(draft, dict) else None
    ok_schema = isinstance(sentences, list) and all(
        isinstance(s, dict) and isinstance(s.get("text"), str) and isinstance(s.get("citations"), list)
        for s in sentences) and len(sentences or []) > 0
    checks.append(Check("O1", "schema valid", ok_schema, [] if ok_schema else ["draft does not match the schema"]))
    if not ok_schema:
        return checks
    body = [(i, s) for i, s in enumerate(sentences) if s["text"].strip() != DISCLAIMER]

    missing = [f"sentence {i + 1} has no citation" for i, s in body if not s["citations"]]
    checks.append(Check("O2", "every sentence cites evidence", not missing, missing))

    unknown = [f"sentence {i + 1} cites {c}, which is not in the ledger" for i, s in body for c in s["citations"]
               if c not in ledger]
    checks.append(Check("O3", "citations exist", not unknown, unknown))

    ungrounded, bad_npi = [], []
    for i, s in body:
        cited = [ledger[c] for c in s["citations"] if c in ledger]
        allowed = set().union(*(numbers_in(e["text"]) for e in cited)) if cited else set()
        extra = numbers_in(s["text"]) - allowed
        if extra:
            ungrounded.append(f"sentence {i + 1}: {sorted(extra)} not in cited evidence")
        npis = set(_NPI.findall(s["text"])) - set().union(*(set(_NPI.findall(e["text"])) for e in cited)) if cited else set(_NPI.findall(s["text"]))
        if npis:
            bad_npi.append(f"sentence {i + 1}: NPI {sorted(npis)} not in cited evidence")
    checks.append(Check("O4", "numbers grounded in cited evidence", not ungrounded, ungrounded))
    checks.append(Check("O5", "NPIs grounded in cited evidence", not bad_npi, bad_npi))

    text_all = " ".join(s["text"] for _, s in body)
    acc = sorted({t for t in ACCUSATORY if _has_term(text_all, t)})
    checks.append(Check("O6", "neutral language (no accusation)", not acc, [f"uses {t!r}" for t in acc]))

    dec = [m.group(0) for rx in _DEC for m in rx.finditer(fold(text_all))]
    checks.append(Check("O7", "no decisions or dispositions", not dec, [f"decision language: {d!r}" for d in dec]))

    phi = phi_findings(text_all)
    checks.append(Check("O8", "no PHI / PII", not phi, phi))

    disc = any(s["text"].strip() == DISCLAIMER for s in sentences)
    checks.append(Check("O9", "disclaimer present", disc, [] if disc else ["missing the not-a-fraud-determination line"]))

    echoed = []
    for span in tainted_spans or []:
        frag = fold(span).lower().strip()
        if len(frag) >= 8 and frag in fold(text_all).lower():
            echoed.append(f"repeats injected text {span!r}")
    echoed += [f"injected instruction followed: {f!r}" for f in injection_findings(text_all)]
    checks.append(Check("O10", "no injected instructions followed", not echoed, echoed))
    return checks


def summarize(checks: list[Check]) -> dict:
    return {"passed": all(c.passed for c in checks), "failed": [c.id for c in checks if not c.passed],
            "checks": [c.__dict__ for c in checks]}
