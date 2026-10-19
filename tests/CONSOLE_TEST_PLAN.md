# NPI Integrity console test plan — synthetic pilot

For the live NPPES and dated OIG exact-NPI source checks, use the
[public-data acceptance plan](PUBLIC_DATA_TEST_CASES.md) alongside this plan.

This plan verifies the deployed task-focused console without using patient data. It does not certify HIPAA compliance, real-world detection accuracy, or production readiness. Run automated checks with `.venv/bin/python -m pytest -q tests`, then execute the browser cases against the authenticated demo at `https://npi-console-1056643263413.us-central1.run.app/`. Use only generated NPIs and fictitious comparison values. Record the revision, tester, date, browser, observed result, and any defect for each manual case.

## Test data and setup

- Regenerate the synthetic fixture when testing locally with `.venv/bin/python -m npi_integrity.cli demo`. The isolated `tests/test_pipeline.py` fixture generates its own data automatically.
- Select one alerted NPI and one unalerted NPI from the current synthetic run. Do not hard-code their identity across runs.
- Use `1234567893` as a checksum-valid NPI absent from the current fixture, and `1234567890` as an invalid-checksum NPI.
- For deployed authorization checks, use separate test identities assigned the `viewer`, `reviewer`, and `supervisor` roles. Do not test role denial by changing a production user's permissions mid-demo.
- A pass requires the specified result **and** no unexpected state change, data leak, or server error. A browser case remains unverified until a person executes and records it.

## Automated cases

| ID | Area | Steps / input | Expected result | Automated coverage |
|---|---|---|---|---|
| A01 | Search | Query an existing NPI, its lowercase full name, and an impossible name. | The first two include the same provider; the impossible name returns no rows. | `test_search_opens_the_same_provider_by_npi_or_name` |
| A02 | Provider Check | Open an alerted NPI and a missing NPI. | Alerted provider has a dated, qualified assessment and linked case; missing NPI returns 404. | `test_provider_check_qualifies_status_and_links_to_case` |
| A03 | Verification | Submit matching name/taxonomy, different city, blank state, and a fictitious license. | Match, Differs, Not supplied, and Cannot check yet appear in the respective fields; no license authority is implied. | `test_verification_reports_each_field_and_does_not_audit_submitted_values` |
| A04 | Verification validation | Submit invalid checksum and valid but absent NPI. | Invalid NPI returns 400; absent NPI returns 404. | Same test as A03 |
| A05 | Verification retention | Compare a fictitious value; inspect audit count before and after. | Comparison does not create an audit entry or claim values were saved. | Same test as A03; deployed storage review remains manual |
| A06 | Roster | Check one known and one valid unknown synthetic NPI. | Response preserves row order; unknown NPI is Cannot check yet; `saved=false`. | `test_roster_preserves_row_order_and_never_claims_unknown_npi_is_clear` |
| A07 | Roster validation | Submit empty, duplicate, invalid-checksum, and 101-row rosters. | Empty and oversized are 422; duplicate and invalid are 400. | `test_roster_rejects_empty_duplicate_invalid_and_oversized_inputs` |
| A08 | Pilot boundary | Set data mode to `real`, then call roster endpoint. | HTTP 403; no results returned. | `test_roster_is_blocked_outside_pilot_mode` |
| A09 | Role gating | Query session and Government leads as a signed viewer and reviewer. | Viewer has Clinic only and gets 403 for leads; reviewer sees Government and receives ranked leads. | `test_government_leads_and_mode_are_role_gated` |
| A10 | Case evidence | Open an alert detail. | Dated alert, provider, reasons, cited source or claims evidence, non-fraud disclaimer, and history array exist. | `test_case_workspace_has_dated_reasons_evidence_and_review_history` |
| A11 | Case transitions | Try action without reason, supervisor escalation without sign-off, and referral before escalation. | Invalid actions fail; valid transitions persist with audit events; concurrent closure cannot overwrite. | Existing `test_disposition_controls`, `test_transition_and_audit_commit_together`, `test_concurrent_reviewers_cannot_overwrite_closed_case` |
| A12 | Authentication and request guard | Omit IAP JWT, use wrong domain, send cross-origin write and oversized body. | 403/413 as appropriate; no route side effect. | Existing `test_domain_gate`, `test_cross_origin_write_is_refused`, `test_actual_body_size_is_limited_without_content_length` |
| A13 | Billing deployment guard | Change project, linked billing account, organization ancestry, or remove the project budget. | Preflight refuses deployment. | `test_billing_policy_fails_closed`, `test_cloud_preflight_requires_matching_company_billing`, `test_project_folder_ancestry_must_end_at_texmed_organization` |
| A14 | Detection and model guardrails | Run full release harness. | All synthetic detection, decoy, evidence, replay, agent-safety and evaluation gates pass. | `scripts/release_check.py` |
| A15 | Public-source coverage | Evaluate a dated LEIE snapshot, one older than 45 days, a future date, and a missing index. | Current is labeled dated, old is stale, and future/missing are unavailable; neither state claims exclusion clearance. | `tests/test_dashboard.py` |
| A16 | Coverage endpoint fallback | Make the LEIE index fail to load. | The dashboard reports LEIE unavailable while still marking NPPES as on-demand, claims as synthetic, and unconnected sources explicitly. | `test_coverage_fails_closed_when_leie_index_cannot_load` |

## Browser and deployment acceptance cases

| ID | Priority | Steps | Expected result / evidence to capture |
|---|---|---|---|
| M01 | Critical | Open the demo in a private window while signed out. | Google IAP redirects to sign-in; no app data is visible before authentication. Capture HTTP status and destination, without recording tokens. |
| M02 | Critical | Sign in as an authorized TexMed reviewer; load `/`. | Clinic / Facility Home appears, run date and synthetic-data warning are visible, and there are no console or network errors. Record revision and screenshot. |
| M03 | High | Use Home search with an exact NPI, a partial provider name, and no match. | Correct provider opens for exact NPI; multiple name matches are selectable; no-match state is clear and does not imply the provider is safe. |
| M04 | High | Open an alerted and an unalerted provider. Expand activity, map, and source profile. | Status and plain-language sentence lead; score/evidence are secondary; source date and qualified scope remain visible. No fraud or credential-clearance claim. |
| M05 | High | On Verify, enter a matching name, changed city, blank state, and fictitious license. Submit, then refresh. | Side-by-side field results are accurate; license reads Cannot check yet; values disappear on refresh; no submitted values appear in audit or server logs. |
| M06 | High | Upload a one-column `npi` CSV with one alerted, one unalerted, and one unknown synthetic NPI. | Counts and per-row results match the individual Provider Check pages; unknown is Cannot check yet; screen says one-time and unsaved. |
| M07 | High | Upload CSVs with wrong header, extra column, duplicate NPI, invalid checksum, 101 rows, and >4 KB file. | Each is rejected with a clear error; no partial results are displayed. |
| M08 | High | Switch Clinic → Government → Clinic; refresh each mode. | Navigation and wording change consistently; no stale panels remain; selected mode persists only for that browser session. |
| M09 | High | In Government, filter leads by state/status, paginate, and open a lead. | Counts, rows, previous/next controls, and case link stay consistent; no lead disappears solely from pagination. |
| M10 | Critical | As a viewer, try `#/leads` and `/api/government/leads`; as reviewer, retry. | Viewer is blocked by both UI and API; reviewer can open leads. Also inspect direct case URLs and record whether they expose Government-only information. |
| M11 | High | Open all six case tabs: Why flagged, Timeline, Relationships, Evidence, Peers, History. | Only selected panel is visible; evidence has source/version/row; graph links resolve; historical actions appear with actor/time; keyboard activation works. |
| M12 | Critical | Attempt referral package before supervisor escalation, then perform approved escalation and export. | Pre-escalation export is blocked; post-escalation package includes dated evidence, disclaimer, sign-offs, and an audit event. Use a disposable synthetic case. |
| M13 | High | Open Methodology & Admin, then Rules, Sources, Scorecard, Audit. | Rule versions, input hashes/freshness, explicitly synthetic scorecard, and append-only actions are inspectable. |
| M14 | High | Repeat M02–M06 and M11 at 390 px viewport, 200% zoom, keyboard-only, and with a screen reader. | No clipped primary action, trapped focus, unlabeled input, or inaccessible tab/row; record any usability defects. |
| M15 | Critical | Compare deployed Cloud Run revision and image digest with release artifact; run billing preflight immediately before next deployment. | 100% traffic goes to intended image; service remains IAP protected and synthetic-only; project, linked TexMed billing account, organization, and budget match. Cloud APIs do not expose card digits. |
| M16 | Critical | Review GCP BAA, service/data inventory, access logs, retention, incident response, backup restore, and threat model before any real data onboarding. | All required contractual, operational, and technical controls have documented owner and evidence. Until then, no PHI or real patient data is used. |
| M17 | High | On Clinic Home and Government Leads, inspect the coverage panel. Then search Government leads by NPI/name/reason, filter Open/All and state, paginate, and open a result. | NPPES is labeled on-demand; OIG date/row coverage and stale state are accurate; claims are synthetic; unavailable feeds are explicit. Counts and next steps follow case status, including after a saved disposition. |
| M18 | High | Open a case from the workbench and follow the three review steps at desktop and 390 px width. | The NPPES link opens the exact NPI, Evidence tab retains dated citations, and the decision guidance matches the saved case status. No step implies a verified fraud finding. |

## Release decision

For the **synthetic pilot**, require A01–A16, M01–M13, M15, M17, and M18 to pass; record M14 accessibility findings before a broad user rollout. M16 is a hard gate for any PHI/production use. Persistent roster monitoring, tenant isolation, jurisdiction-scoped access, license-source verification, and production attestation exports remain unimplemented and must be tested after they are built.
