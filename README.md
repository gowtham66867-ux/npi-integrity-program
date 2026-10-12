# NPI Integrity Monitoring: prototype

TexMed × NeXari. A credit-bureau-style anomaly engine for clinician NPIs, built from
`NPI_Integrity_Program_Call_Summary_and_System_Design_2026-10-06.pdf` (Parts C and D).
Every output is an **anomaly for human review, never a fraud determination**.

## Quick start

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"
.venv/bin/python -m npi_integrity.cli demo      # synth data -> run -> scorecard with weekly replay
.venv/bin/python -m npi_integrity.cli serve     # reviewer console at http://127.0.0.1:8765
```

Other commands: `synth`, `run --as-of YYYY-MM-DD`, `scorecard --sweep`, `rerun <alert_id>`, `fetch <source>`.
Tests: `.venv/bin/python -m pytest -q tests` (end to end, about 20 s).
`scripts/release_check.py` runs the tests and offline evaluation gates and writes a reviewable report.
GitHub Actions regenerates the synthetic demo and runs that release harness on every push and pull request.

DuckDB allows one writer per file, so stop the console before running `run` or `demo`.

## Current result (synthetic run as of 10/06/2026)

| Metric | Result |
|---|---|
| Planted scenarios caught (design doc C16) | **12 / 12**, each with the expected reason code |
| False alerts | **0** (0 per 1,000 monitored NPIs) |
| Guardrail decoys not alerted | **10 / 10** |
| Alerts raised | 51 (40 of them are one virtual-office cluster, grouped in the queue) |
| Alert re-run as of creation date | Reproduces the identical fingerprint and score |

Inputs: 5,665 NPPES records, 732 billing NPIs, about 588k claim lines (01/2025 to 09/2026).

These numbers come from data we generated ourselves. They show the pipeline and rules work end to end.
They are **not** an accuracy estimate for real claims.
The queue uses compact cards on narrow screens and keyboard-accessible alert rows; the scorecard
explicitly labels synthetic metrics and allows horizontal scrolling for detailed scenario timing.

## How the nine layers map to code

| Layer | Where | Notes |
|---|---|---|
| L1 Ingestion & landing | `ingest.py`, `fetch.py` | Source registry; write-once raw zone `data/raw/<source>/<version>/` with SHA-256, read-only files, overwrite refused; schema, null and drift checks |
| L2 Normalize & resolve identity | `build.py` | USPS-style address normalization, taxonomy crosswalk; NPI is the hard key; LEIE/SAM name matching (last + first + state, ZIP/city bonus, middle-initial penalty) with confidence and basis recorded |
| L3 Provider graph | `build.py` (`graph_edges`) | Providers, addresses, phones, authorized officials, exclusions, reassignments, orders-for. Relational core in DuckDB, as the doc recommends |
| L4 Feature store | `build.py` | NPPES history kept SCD-2 style (every address/taxonomy/official change dated), monthly provider volume, peer groups |
| L5 Detection engine | `rules/*.yaml`, `engine.py` | 18 rules: D-01–05, I-01–03, C-01, G-01–03, V-01–02, RPM-01–03, N-01. Each has an ID, version, owner, parameters, SQL, and a reason template |
| L6 Score + reason codes | `engine.py`, `rules/scoring.yaml` | 0–1000; hard rules set a 900 floor; soft weights add with per-family caps; ×1.2 network multiplier; tiers Low/Watch/Elevated/High/Critical; alert at ≥ 550 |
| L7 Agent assist | `agents/` | Evidence-ledger agents, deterministic verifier, independent QA and offline fallback |
| L8 Alert & case management | `api.py`, `cases.py`, `static/` | Queue grouped by shared address; alert detail with evidence; New → In review / Monitoring / Dismissed (reason required) / Escalated (supervisor required) / Referred; coordination check |
| L9 Delivery | `static/` console, `/api/*` | Government face only. Referral package (HTML) can be exported only after escalation |
| Cross-cutting | `pipeline.py` | Append-only audit log; per-run rule snapshot + input manifest under `data/runs/`; as-of replay (`rerun`) verifies raw-file hashes before rebuilding |

Every build is a pure function of the raw files plus an as-of date. Snapshot sources use the latest version on or before that date; cumulative sources (weekly NPPES, claims) use every release up to it. This is what makes "re-run as of any date" and the weekly time-to-detect replay work.

## Agents, guardrails, and evals

**Agents (`npi_integrity/agents/`, model card in `MODEL_CARD.md`).** The console's "Run agents" button and `POST /api/alerts/{id}/draft` run this pipeline:

1. A **context agent** collects evidence with 6 read-only tools.
2. A **harness-owned evidence ledger** records each tool result. The harness, not the model, assigns the IDs (E1, E2, …), so the model can only cite things a tool actually returned.
3. A **required-evidence check** has the harness fill in anything the agent skipped.
4. A **narrative agent** writes the draft as structured JSON.
5. A **deterministic verifier** and an **independent QA agent** check every sentence. The QA agent can only add failures; it can never clear a deterministic one.
6. A **repair loop** (at most 2 rounds) sends the agent the exact failures to fix.
7. If it still fails, a **template draft** is used instead.

Agents have no write tools; only a human changes an alert. The model is Claude Opus 5.5 when an API key is present, otherwise an offline scripted model, and the draft is labelled with whichever was used.

**Guardrails (`agents/guardrails.py`)**, in three layers:
- **Input:** detects prompt injection, including base64-encoded, look-alike-character and zero-width-character variants. Wraps public-file text as untrusted data, replaces patient IDs with pseudonyms, and redacts SSN, date of birth, Medicare ID and email. A data-boundary check refuses to send anything other than public/synthetic data to an external model.
- **Tools:** a read-only allowlist with strict schemas. An agent may read only its assigned alert and NPI; NPIs are checksum-validated. Tool calls, per-tool repeats, model turns, tokens and cost are all budgeted.
- **Output:** ten checks, O1–O10. The draft must match the schema. Every sentence must cite existing evidence, and every number, date and NPI must appear in the cited evidence. The language must be neutral, with no decisions and no PHI. The disclaimer must be present, and no injected instruction may be followed.

The API also verifies signed IAP identity and reviewer roles when deployed, sets security headers, caps request size, rate-limits agent drafts across instances, and audits every draft.

**Eval harness (`evals/`).**

```bash
.venv/bin/python -m evals.run_evals
```

That runs offline, deterministically and CI-safe, writes `evals/reports/<time>/report.md`, and exits non-zero when a gate in `evals/gates.yaml` fails. The suites:

| Suite | What it proves | Current |
|---|---|---|
| detection | Blind scorecard against the sealed answer key | 12/12 caught, 0 false alerts, 10/10 decoys |
| metamorphic | "Monkey wrench" changes to the inputs: name and address formatting noise, initial-only name matches, shuffled row order, benign noise, a reactivated NPI, injection text in an org name, and removing evidence (ablation) | 8/8, plus 1 documented known limitation (splitting a 31-hour day across two NPIs drops the hard stop; both still land on the watch list) |
| guardrails | 18 injection attacks, 18 benign look-alike names, 14 bad and 7 good model outputs, grounding cases, tool-gate cases, data boundary | 100% caught, 0 false positives |
| agent_faults | 11 simulated misbehaving agents × 13 alerts = 143 runs. The faults: hallucinated number, accusation, missing or fake citation, a decision, PHI leak, missing disclaimer, tool abuse, lazy QA, refusal, a repairable draft | Output always safe; every fault caught |
| injection | Each attack planted in a provider field, run end to end, with both a good agent and one that obeys injected text | 36/36 flagged and safe |
| quality | Every frozen alert: first-try pass rate, coverage of every rule that fired, citations per sentence, tool calls, cost | 100% / 100% (scripted model) |
| redteam (`--live`) | Claude proposes new attacks; any misses are saved to grow the corpus | not run yet |

`--live` repeats the agent suites with real Claude, adds an LLM judge (scores faithfulness, neutrality and usefulness) and the red-team suite, and asks before spending money. The expanded pytest suite covers the pipeline, guardrails, agents, historical evidence, role checks, billing preflight, and durable local case state.

The evals found and fixed three real issues:
- **D-01 v1.1:** billing during a deactivation window was missed when the NPI was later reactivated.
- **Initial-only name matches:** they scored 0.95 confidence, enough for a hard stop. They now score 0.825, which routes to identity review.
- **Order-dependent evidence:** 9 rules built their evidence lists in an order that depended on input row order. They now sort deterministically, so alert fingerprints reproduce exactly.

## Deployment

The private console is deployed at [npi-console](https://npi-console-1056643263413.us-central1.run.app/) in GCP project `texmed-npi-integrity` for `gowtham@texmed.us`. Direct IAP protects the service; Firestore stores reviewer actions and agent drafts. The October 7 release passed 141 tests and all 16 offline evaluation gates, and a signed-in browser smoke test verified a review action and draft persisted in Firestore. Use `scripts/release_check.py` and `deploy/deploy_gcp.sh --dry-run` before subsequent releases. The [billing policy](deploy/billing_policy.json) pins primary card **8192** and forbids **6022**; card digits require a fresh manual check in Google Cloud before each release. See [deployment instructions](deploy/README.md). A passing synthetic run is not a real-world accuracy claim.

**PHI restriction:** This is a public/synthetic pilot and is not approved to receive PHI. The [HIPAA readiness assessment](deploy/HIPAA_READINESS.md) lists the verified technical controls and the contracts, risk analysis, operational controls, and production architecture required before any PHI use.

## What is real and what is synthetic

- **Real file formats.** The synthetic files use the real column headers of NPPES (full, weekly, practice locations, deactivation report), OIG LEIE, SAM, PECOS enrollment and reassignment, Order & Referring, opt-out affidavits, and Medicare by Provider. The same loaders read the real files.
- **Synthetic.** Claims come from our own "Synthea-style" generator (`synth.py`), not Synthea itself; swapping in Synthea or DE-SynPUF needs a claims adapter only. Also synthetic: the ZIP-centroid table (stands in for the Census ZCTA gazetteer) and the address-type table (stands in for a USPS CMRA / virtual-office feed, which needs a vendor).
- **Real-data connectors** (`npi fetch oig_leie nppes_deactivation nppes_full`) are written but **have not been run against the live sites yet**. The NPPES full file is about 1.1 GB zipped. Without claims, real public data feeds profiles, exclusion matching, address density (G-02), churn (I-03), network links (N-01), and peer outliers (V-02). D-01/D-02 and the other claims rules need claims keyed to real NPIs.

## Known gaps and next steps (toward v1, ~10/27)

1. Supervised first run of the live connectors, plus SAM (needs an API key), PECOS, O&R, opt-out, and Medicare by Provider from data.cms.gov.
2. Synthea RPM claims keyed to real NPIs, so the planted scenarios sit inside real provider data.
3. Live-model evaluations and model-quality review on approved public/synthetic data; the offline scripted model has passed.
4. Repeat authenticated IAP and Firestore smoke tests on later releases, then migrate the detection warehouse to the agreed production infrastructure.
5. Rule weights are placeholders for NeXari to set (open question 3 in the doc). The dispositions table already records the labels for tuning.
6. Broader adversarial and independent real-data validation beyond the current synthetic tests.
7. React front end if wanted; the current console is plain JS with no build step.
