# NeXari feedback: pilot response

Status: draft for NeXari review, 15 October 2026. This document distinguishes deployed pilot behavior from proposed product work. The source is Lester Steptoe Jr.'s October 13 call notes.

## Dashboard update, 19 October 2026

The Clinic Home and Government Leads screens now show public-source availability alongside the synthetic review run. They distinguish live, per-NPI NPPES lookups; a dated OIG exact-NPI index with its source date and NPI coverage; synthetic claim activity; and live feeds that are not connected. The Government screen adds stage counts, an open-case default, provider/NPI/reason search, and a status-dependent next step. Case pages start with a three-step review path linking to the exact NPI lookup and dated evidence. These changes organize existing checks and cases; they do not add continuous monitoring, live claims, licensing, social feeds, or a fraud determination. The coverage endpoint fails closed when the LEIE snapshot is unavailable. See `tests/test_dashboard.py` and cases A15–A16, M17–M18 in `tests/CONSOLE_TEST_PLAN.md`.

## Implemented in the pilot update

- **Directory language:** clinic navigation, home action, upload form, and result heading use Directory. The current check accepts only a one-column `npi` CSV with 1–100 distinct, valid synthetic NPIs. It does not save the file or monitor it later.
- **Neutral conclusions:** console copy, the generated case summary, the agent's required closing sentence, the OIG note, and referral package instruct a human to verify before deciding. Internal attack fixtures still contain prohibited words for guardrail testing.
- **Five score bands:** Low 0–299, Watch 300–549, Elevated 550–749, High 750–899, Critical 900–1000. These ranges are read from the versioned scoring file. The current alert setting is 550. The red hard-rule indicator means that a rule met its evidence conditions and sets a 900-point floor; it is not a finding about intent. Changing the alert setting requires a reviewed scoring-file change and a new run; historical results are preserved.
- **Rule list:** the Rules page lists all **18 implemented** rules, their versions, descriptions, parameters, and synthetic hit counts. The call notes' “about 50” is a proposed larger catalog, not current coverage.
- **Score explanation chart:** provider and case pages show the archived run's hard floor, weighted families after caps, network multiplier, final ceiling and alert setting. If the archived configuration is missing or fails to reproduce the stored score, the UI withholds the chart and tells the reviewer to inspect evidence.

## Source cadence and coverage

| Source or output | Current pilot behavior | What a production promise would require |
|---|---|---|
| NPPES Registry | One live exact-NPI request on demand | Rate limits, outages, monitoring intervals, and the separate monthly/weekly/deactivation files |
| OIG LEIE | Dated exact-NPI snapshot in the image, refreshed before a release | Monthly refresh controls, name-based matching with human verification, source-lag handling |
| Synthetic claims and alerts | Generated demo dataset, updated only by a new run | Licensed claim-level feed and measured ingestion latency |
| State boards, SAM, social media, medical directorships | No connected feed | Access rights, source policy, identity resolution, freshness, false-lead review, and operating cost |

Per-check and notification costs have not been measured. The pilot cannot promise continuous or real-time monitoring from monthly or annual files.

## Proposed next phase, not yet offered

An Individual provider account with paid notifications requires verified provider enrollment, consent, account isolation, billing, notification delivery, appeal/correction workflows, and source rights. A suspicious-activity feed requires named sources, dated links, removal/correction handling, and a human-review workflow. Federal, state, social, and primary-source agents can be built as separate read-only tasks behind an orchestrator, but should not scrape restricted sites or emit allegations. Leslie's regional keyword library and medical-director watch need source-by-source validation and careful identity matching before alerts.

The current deployment remains an internal public/synthetic pilot. It is not approved for PHI or for external user onboarding. Access for NeXari reviewers should follow a separate review of account permissions and tenant boundaries.
