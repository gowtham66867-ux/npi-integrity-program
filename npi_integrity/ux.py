"""Plain-language pilot views over existing, versioned detection results.

No submitted verification values or roster rows are persisted here.
"""
import re


def npi_valid(value: str) -> bool:
    """NPI uses the Luhn check digit with the 80840 prefix."""
    if not re.fullmatch(r"\d{10}", value):
        return False
    digits = [int(c) for c in "80840" + value]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def field_result(submitted: str, official: str | None) -> str:
    if not submitted:
        return "Not supplied"
    if not official:
        return "Cannot check yet"
    return "Match" if norm(submitted) == norm(official) else "Differs"


def provider_assessment(score: dict | None, hits: list[dict], status: str) -> dict:
    """Describe observed signals without implying credential or billing clearance."""
    if status == "Deactivated":
        return {"label": "Needs attention", "sentence": "The NPPES record is deactivated. Review the dated source evidence."}
    if score is None:
        return {"label": "Cannot check yet", "sentence": "This NPI has no score in the current pilot run."}
    if hits:
        tier = score.get("tier")
        label = "Needs attention" if tier in ("Critical", "High") else "Watching" if tier == "Watch" else "To review"
        return {"label": label, "sentence": hits[0].get("reason") or "A signal needs human review."}
    return {"label": "No flagged signals", "sentence": "No rule fired for this NPI in the latest run of the checked sources."}
