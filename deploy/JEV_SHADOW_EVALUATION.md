# Jev for reviewer assistance: synthetic shadow trial

Jev can be evaluated as a **typed suggestion for the next evidence category a reviewer should inspect**. It must not replace the rule engine, set alert thresholds, determine fraud, change a case disposition, or initiate a payment or clinical action. The adapter in `npi_integrity/jev_shadow.py` builds a request and validates an answer. `scripts/jev_trial.py` previews requests locally by default; its `--live` mode is an explicit, synthetic-only API trial gated on a TypeSafe API key and fresh TypeSafe-specific card inspection. Nothing runs in the deployed service.

TypeSafe's [official quick start](https://docs.typesafe.ai/introduction/quickstart) documents `POST /v1/systemone`, a `state` string, `choice` questions, and an answer with `choice` and `confidence`. The adapter follows that shape. It accepts only synthetic aggregate fields; it cannot serialize source narratives, provider or patient identifiers, names, claim details, or free text.

## Evaluation gate

1. Build a blinded synthetic set of alert summaries with human labels for the most useful next evidence category. Include critical/hard-signal, sparse-evidence, decoy, and conflicting-rule examples.
2. Compare Jev to a deterministic baseline. Report per-category accuracy, confusion matrix, confidence calibration, latency, and cost; investigate every suggestion that could divert a reviewer from a hard signal. The model's confidence is a model output, not evidence of correctness.
3. Keep every suggestion advisory and behind human review. Record the request schema version, exact model version, response, and reviewer's override in a local audit trail. Never use `jev-latest` for a production evaluation without resolving it to a pinned version.
4. Consider a live synthetic-only trial only after API billing and an approved payment method are verified. **8192 must be the sole active payment method**; **6022** is forbidden. A separate TypeSafe subscription may have its own payment method and must be checked before paid calls.
5. Do not send PHI or non-public case data to TypeSafe. Before any future PHI proposal, complete TexMed's HIPAA review and obtain a signed BAA covering the specific service and data flow; TypeSafe's enterprise page mentions [zero-data-retention availability](https://typesafe.ai/enterprise), which alone does not establish HIPAA eligibility.

Run `.venv/bin/python -m scripts.jev_trial` to create `evals/reports/jev_shadow_preview.json` from the current synthetic alerts. No TypeSafe request or charge occurs. For a bounded live trial, inspect all active cards in the TypeSafe payment console, record only the last four digits and inspection timestamp in `deploy/jev_billing_policy.json`, provide `TYPESAFE_API_KEY` through the process environment or a secret manager, and run `.venv/bin/python -m scripts.jev_trial --live --limit 3`. Never put the key in the repository or report.

The tests check the data boundary, request allowlist, billing gate, typed response validation, and inability to apply a suggestion. They do not establish Jev's accuracy or HIPAA compliance.
