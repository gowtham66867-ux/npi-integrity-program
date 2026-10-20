# NPI Integrity console UX implementation status

The UX guide and NeXari call notes are design references. The pilot remains private and uses synthetic claim activity; a live NPPES check is a separate, single-NPI public lookup. No result establishes licensing, credentialing, fraud, or exclusion clearance.

| Request | Status | Evidence and boundary |
| --- | --- | --- |
| Question-led Home and three actions | Implemented | Home offers provider check, Verify, and My directory. |
| One-click sample provider | Implemented | Home opens synthetic NPI 1136934696. |
| Search | Partial | Pilot NPI/name search and exact-NPI live lookup. No national live name or organization search. |
| Answer-first provider page and tabs | Implemented for synthetic provider | Summary, Identity, Licenses, Exclusions, Activity, Evidence. Unavailable sources say so explicitly. |
| Provider report | Partial | Browser print/save-PDF of the current provider page. No separately versioned server-generated report. |
| Verify | Implemented for connected NPPES fields | Compares name, address, city, state, ZIP, taxonomy. Conservative Close match only for street suffix or ZIP+4. License is Cannot check yet. Print/save-PDF available. |
| Saved directory | Partial | User-scoped NPI-only synthetic directory, manual recheck, status changes, deletion, printable attestation. No schedule or notification path. |
| Clinic/Government views | Partial | Mode-specific navigation and Government Leads server role gate. Formal government jurisdiction and tenant entitlements are not implemented. |
| Case workspace | Existing | Six case tabs, audit history, disposition, referral HTML package. No jurisdiction, assignments, or PDF package. |
| Source/rule transparency | Existing | Methodology & Admin, dated lineage, versioned rules, scorecard, audit. |
| Additional live sources | Deferred | State license, PECOS, SAM, Medicare activity, and social feed are not connected. |
| Usability/accessibility validation | Partial | Keyboard and responsive CSS are present; moderated five-user test and complete 200% zoom/accessibility audit remain open. |

## Release acceptance

Run `python scripts/release_check.py`, `python deploy/preflight.py --policy deploy/billing_policy.json`, and authenticated post-deploy smoke checks. Keep Cloud Run private behind IAP, `NPI_DATA_MODE=synthetic`, and no PHI. A budget alert is not a spending cap.
