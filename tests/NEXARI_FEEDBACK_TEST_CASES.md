# NeXari feedback acceptance cases (pilot)

Scope: internal, IAP-protected, public/synthetic pilot. These cases do not validate production accuracy, licensing, exclusion clearance, PHI handling, or a paid notification service. Test with synthetic fixtures and public NPIs only.

| ID | Priority | Action | Expected result |
|---|---|---|---|
| NX-F01 | P0 | Open Clinic home and navigation. | User-facing action says **Directory**, not Roster; one-time and 100-NPI synthetic limit are visible. |
| NX-F02 | P0 | Upload a one-column `npi` CSV with one known and one unknown synthetic NPI. | Row order is preserved, unknown says Cannot check yet, results are not saved, and no ongoing monitoring is promised. |
| NX-F03 | P0 | Upload duplicate, invalid checksum, empty, or 101-row directory. | Request is rejected with a clear input error; no partial results are shown. |
| NX-F04 | P0 | Open alert queue, case, provider, and referral package. | Results use neutral anomaly language and tell a human to verify before deciding; no accusation appears. |
| NX-F05 | P0 | Open a critical hard-rule case. | Red rule meaning and 900-point floor are explained; score chart uses the archived run configuration. |
| NX-F06 | P0 | Open a case with capped soft signals and a network link. | Chart shows raw vs capped family points, multiplier, final score, ceiling, and alert setting; final score equals API score. |
| NX-F07 | P0 | Reopen an older run after changing current rule configuration in a test fixture. | Historical chart still uses that run's archived scoring file; if unavailable or inconsistent, the UI shows no verified breakdown rather than an invented one. |
| NX-F08 | P1 | Open an unalerted provider. | No fabricated chart appears when no score exists; status remains qualified by checked source coverage. |
| NX-F09 | P0 | Open Methodology & Admin > Rules. | Five bands show numeric ranges and plain meanings; all 18 implemented rules are listed; guide is marked draft for NeXari review. |
| NX-F10 | P0 | Change alert threshold in a test copy of `rules/scoring.yaml`, then run a new synthetic detection. | New alerts use the new threshold. Earlier run scores/alerts and archived explanations remain unchanged. No live case is retroactively relabeled. |
| NX-F11 | P0 | Search code and exported content for accusatory product wording. | UI, agent closing sentence, OIG note, and referral package use neutral language. Internal adversarial test strings may still contain forbidden terms. |
| NX-F12 | P0 | Use the live exact-NPI lookup and OIG panel. | Source dates and limitations are visible; no exact OIG NPI match is never called clearance. |
| NX-F13 | P0 | Sign in as an unauthorized user and request a case or rules API. | IAP/authentication rejects access; this release adds no public individual account or external NeXari entitlement. |
| NX-F14 | P0 | Attempt to submit PHI or activate non-synthetic Cloud Run mode. | Existing data boundary blocks non-synthetic mode; pilot instructions prohibit patient data. |
| NX-F15 | P1 | Review the source cadence and future-feature notes. | Public/synthetic status, source freshness, and missing source rights are explicit; no real-time activity feed or paid physician notifications are implied. |
| NX-F16 | P0 | Run `scripts/release_check.py`, billing preflight, and authenticated deployed smoke. | Automated gates pass; the project is linked to its approved TexMed billing account and budget; IAP and synthetic mode remain enabled. |

Automated coverage: `tests/test_ux.py` checks score math; `tests/test_pipeline.py` checks archived API breakdown against stored scores, directory input behavior, and case language; `tests/test_leie_public.py` checks public-source boundaries. `scripts/release_check.py` runs the full suite and offline evaluations. Browser cases NX-F01, F04-F09, F12-F13 and the cloud portion of F16 need a signed-in manual smoke after deployment.
