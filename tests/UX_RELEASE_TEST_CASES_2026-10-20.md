# UX release test cases

Each case records run ID, source dates, actor, expected and actual result. Use only synthetic NPIs and public provider fields; no patient or claim upload.

| ID | Steps | Expected result |
| --- | --- | --- |
| H-01 | Open Home and select the sample provider. | NPI 1136934696 opens with synthetic label, status, one sentence, source date, and evidence tabs. |
| H-02 | Search a known pilot NPI and name; search an unknown name. | Known records are selectable; unknown query shows an empty state. No national coverage claim. |
| P-01 | Open each Provider Check tab. | Identity, license limitation, exclusion caveat, synthetic activity, and source evidence appear in their respective tabs. |
| P-02 | Print/save the provider page. | Generated document includes NPI, source date, status, limitation, and the visible evidence; no fraud or clearance claim. |
| V-01 | Enter exact official values for a public NPI. | NPPES fields say Match; license says Cannot check yet. |
| V-02 | Enter a five-digit ZIP for a ZIP+4 record and an equivalent street suffix. | Both say Close match, with normalization explained. |
| V-03 | Change house number or provider name by one character. | Says Differs; identity is never fuzzy-matched. |
| V-04 | Submit invalid, missing, or deactivated NPI, or simulate upstream timeout. | Explicit validation/not-found/unavailable state; no clearance claim. |
| V-05 | Print/save Verify comparison. | Entered and source values, outcomes, source/retrieval date, and limitations are present. |
| D-01 | Upload NPI-only CSV with 1–100 unique valid NPIs. | Saves user-scoped directory and shows checked date, source date, status, reasons. |
| D-02 | Upload duplicate, invalid, extra-column, >100-row, or >4-KB CSV. | Rejected; no directory replacement. |
| D-03 | Recheck after a new synthetic run changes a result. | Only changed statuses appear in change list; current results and time update. |
| D-04 | Sign in as another authorized user. | Cannot read, replace, recheck, or delete the first user’s directory. |
| D-05 | Delete a directory; then recheck. | Directory is absent and recheck returns 404; audit event remains. |
| D-06 | Print/save attestation. | States it is a completed synthetic pilot check and does not imply continuous monitoring or clearance. |
| G-01 | Enter Government Leads as a viewer. | Server returns 403 even if endpoint is called directly. |
| G-02 | Complete case review, escalation, referral and reopen paths with appropriate roles. | Transitions follow server rules, audit records persist, unauthorized actions fail. |
| S-01 | Access the deployment without IAP; inspect `/api/session` after sign-in. | Unauthorized access denied; authorized domain actor/role returned. |
| S-02 | Inspect deployed service settings and data mode. | IAP enabled, no unauthenticated ingress, synthetic mode, project/region match policy. |
| S-03 | Review evidence freshness, NPPES live lookup and OIG snapshot. | Dates and coverage limits displayed; unavailable/stale sources never become negative findings. |

Automation covers comparison normalization, directory owner isolation/recheck, roles, source failures, and existing case/pipeline behavior. Browser print output, 200% zoom, keyboard-only paths, and five-user usability require human signoff.
