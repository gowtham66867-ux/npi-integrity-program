# Model card: L7 agent assist (prototype)

Per the CMS draft SOO, AI use in contract work needs CMS approval; each agent ships with a card.

| | |
|---|---|
| Purpose | Speed up human review of an alert: gather evidence, draft a cited plain-language summary, QA it |
| Not for | Deciding anything. Agents have no write tools and cannot change alert status, scores, or rules |
| Model | Claude Opus 5.5 (`claude-opus-5-5`), adaptive thinking, effort `medium` (`high` for judge / red team); server-side refusal fallback enabled. Offline: deterministic scripted model |
| Inputs | Alert, rule hits, source citations, NPPES profile, peer stats, graph summary, up to 20 claim lines; patients pseudonymized, SSN/DOB/MBI/email redacted, provider text wrapped as untrusted data |
| Outputs | JSON draft: sentences, each with evidence IDs; open questions; fixed disclaimer |
| Data boundary | Synthetic + public data only. Other data modes are refused even if `NPI_PHI_BOUNDARY_APPROVED=1` is set; PHI needs a separately designed and approved workflow. |
| Agents | Context (read-only tool loop) → Narrative (structured output) → QA (independent prompt) ; harness assigns evidence IDs |
| Guardrails | Input: injection detection (incl. base64, homoglyph, zero-width), PHI redaction, boundary gate. Tool: allowlist, strict schemas, assigned alert/NPI only, Luhn-checked NPIs, per-tool and total call budgets, cost/token budget. Output: O1-O10 (schema, citations exist and cover every sentence, numbers/dates/NPIs grounded in cited evidence, neutral language, no decisions, no PHI, disclaimer, no injected instructions). QA can only add failures |
| Failure handling | Up to 2 repair rounds with the exact violations; then deterministic template draft. Refusals and budget exhaustion fall back the same way |
| Versioning | Prompts in `prompts/*.md`, hash recorded on every draft and in the audit log |
| Evals | `python -m evals.run_evals` (offline gates) and `--live` (Claude + LLM judge + red-team). See `evals/gates.yaml` |
| Known limits | Scripted-model evals prove the guardrails, not Claude's writing quality; live evals have not been run yet (no API key on this machine). Numeric grounding is lexical (a correct paraphrase like "two" for "2" is rejected) |
