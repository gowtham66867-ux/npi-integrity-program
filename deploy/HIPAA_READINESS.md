# HIPAA readiness assessment — synthetic pilot

**Status: not approved for PHI.** As of October 7, 2026, the deployed NPI console uses public and synthetic data only. No HIPAA compliance certification is claimed. HHS does not certify software as HIPAA compliant; the covered entity or business associate must assess and operate the complete workflow under the Privacy, Security, and Breach Notification Rules. This document is a technical inventory for that assessment, not a legal determination.

## Verified pilot controls

| Area | Evidence |
|---|---|
| Access | Private Cloud Run service with direct IAP; one `gowtham@texmed.us` IAP web-app-user binding and External OAuth test user; application verifies the signed IAP JWT and limits reviewer actions to the configured role. |
| Data scope | Deployment pins `NPI_DATA_MODE=synthetic`, and the API refuses any other deployed mode. The container holds public and synthetic inputs. No Anthropic secret is mounted. Agent processing refuses non-public data modes even if the old `NPI_PHI_BOUNDARY_APPROVED` variable is set. A mode label does not inspect or prove the contents of an input file. |
| Review text | Deployed review actions accept only fixed, non-patient reason choices; the UI warns against entering PHI. This reduces accidental entry but cannot classify every possible PHI disclosure. |
| Storage | Firestore database `npi-cases` is delete-protected, in `us-central1`, with point-in-time recovery enabled. Case state, drafts, and disposition events survive revision changes. |
| Audit | Project Data Access audit logging is enabled for `datastore.googleapis.com` (which configures Firestore), `iap.googleapis.com`, and `run.googleapis.com`, for Admin Read, Data Read, and Data Write. Subsequent Firestore read and IAP authorization entries were observed in Cloud Logging. The application also records reviewer dispositions and drafts. Logging was enabled after the initial pilot activity; it is not retroactive. The `_Default` log bucket currently retains entries for 30 days. |
| Browser/API | HTTPS, IAP protection, signed user identity, same-origin write checks, response security headers, no-store responses, request-size limits, and no public Cloud Run invocation. |

## Required before any real PHI

1. **Contracts and ownership:** Verify that the legal entity controlling the GCP organization/billing account (`wanologicalsolution.com`, organization `267783416603`) has an executed Google Cloud BAA covering this project, and document TexMed's contractual relationship and any required BAA with that entity. Confirm all other vendors and subprocessors that could receive PHI are covered. Do not infer a BAA from the billing account or Google Cloud's HIPAA product listing.
2. **Risk analysis and policies:** The regulated entity must document data flows, assets, threat and vulnerability analysis, access authorization and termination, incident response and breach notification, workforce training, contingency planning, and periodic evaluation. Assign a security official and record approval for the specific use case.
3. **PHI architecture:** Design a separate production ingestion, warehouse, storage, model, backup, and export boundary. This pilot bakes a synthetic DuckDB warehouse into its image and has no validated PHI ingestion path, data minimization rules, retention/deletion schedule, or approved external-model path. Never put PHI in source/build artifacts, Cloud resource metadata, URLs, support tickets, or logs.
4. **Access and audit operations:** Review least-privilege IAM and Google account MFA; define supervisor identities; set log retention/export, monitoring and alerts, and a documented review cadence. Test that access and Firestore audit events are captured and investigated. The current project-level `roles/owner` user grant and absence of operational log review need assessment.
5. **Recovery and lifecycle:** Set recovery objectives, backup schedule and retention, test restores, and document data disposal. Firestore point-in-time recovery alone is not a complete backup strategy.
6. **Validation:** Perform security testing, configuration review, real-data accuracy/clinical workflow validation, and a formal go-live sign-off. Keep real PHI out of this deployment until these items are complete and the fail-closed synthetic boundary is deliberately replaced through a reviewed release.

Enabling Data Access logs and Firestore point-in-time recovery can incur charges. The project's USD 25 monthly budget is an alert, not a spending cap.

## Primary references

- [HHS Security Rule summary](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html)
- [HHS cloud-service/BAA guidance](https://www.hhs.gov/hipaa/for-professionals/faq/may-a-hipaa-covered-entity-or-business-associate-use-cloud-service-to-store-or-process-ephi/index.html)
- [Google Cloud HIPAA guidance and shared responsibility](https://docs.cloud.google.com/docs/security/compliance/hipaa)
- [Google Cloud Data Access audit logging](https://docs.cloud.google.com/logging/docs/audit/configure-data-access)
- [Firestore audit logging](https://docs.cloud.google.com/firestore/native/docs/audit-logging)
