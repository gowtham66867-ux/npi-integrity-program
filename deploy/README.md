# Guarded Cloud Run deployment

The private console is deployed at [npi-console](https://npi-console-1056643263413.us-central1.run.app/) in project `texmed-npi-integrity`, region `us-central1`. The active `gcloud` identity must be `gowtham@texmed.us`. Every later deployment runs the billing preflight before cloud changes.

## Required billing evidence

The user approved billing account `01E4B3-EA09E1-258E11` with primary card ending **8192**. Google Cloud labels its organization `wanologicalsolution.com` (number `267783416603`); this is the actual project parent and billing-account organization, even though the project and service are named for TexMed. Inspect **Payment method** for this exact account before every deployment and record only the last four digits of every active method, including backup methods. Card **6022** is forbidden by the policy validator. Never store a full card number.

[billing_policy.json](billing_policy.json) records the approved project, account, organization number, primary card last four, active method last fours, inspection timestamp in UTC, and inspector identity. Set `payment_verification_source` to `google_cloud_payment_methods_console`. The attestation expires after 24 hours; refresh it after any payment-method change or before a later deployment.

The preflight queries the live project billing link, account status and organization, project parent, and a monthly USD 25 budget with alerts scoped to this project. A budget alert does not cap spending. Cloud Billing's API does **not** expose card digits. The payment console inspection is a human attestation; the code cannot independently prove which card Google will charge at billing time. Do not set an approval from an account name alone.

```bash
.venv/bin/python deploy/preflight.py --policy-only
.venv/bin/python deploy/preflight.py
.venv/bin/python scripts/release_check.py
./deploy/deploy_gcp.sh --dry-run
./deploy/deploy_gcp.sh
```

`--dry-run` performs the billing preflight without cloud changes. A normal run repeats the preflight after the release checks and before enabling services or creating resources. There are no environment overrides for the forbidden card or approved account. The script uses the pinned identity and project on every `gcloud` command.

## Runtime

Cloud Run uses direct IAP, a verified IAP JWT in the application, a dedicated service account, and the delete-protected Firestore database `npi-cases` in `us-central1` for case state, drafts, and review events. An External Google Auth Platform app in Testing mode has only `gowtham@texmed.us` as a test user; the IAP web app user grant is also limited to that address. `NPI_REVIEWERS` contains that address. `NPI_SUPERVISORS` is empty: a TexMed administrator must designate verified supervisor email addresses before escalation. The API rejects a typed supervisor name when deployed. The container holds only public and synthetic detection data. No Anthropic secret is mounted, so agents use the offline model.

The Cloud Run service is private. On October 7, signed-in browser access with `gowtham@texmed.us` rendered the alert queue, accepted a synthetic `Start review` action, and generated a scripted draft. A direct Firestore read confirmed one case document, one draft document, and `disposition:review` and `agent_draft` events. Revision `npi-console-00004-xk4` serves 100% of traffic with IAP enabled; the prior case and draft survived that revision change. The new UI shows a no-PHI warning and fixed reason choices. A scripted draft write also succeeded through Cloud Run's second official `a.run.app` URL. The CSRF check accepts same-origin HTTPS writes on Cloud Run URLs and rejects cross-origin writes. Future releases should repeat these checks and verify `/api/session`.

The release harness passed 141 tests and 16/16 offline evaluation gates on October 7. The synthetic scorecard does not estimate real-world accuracy. Separate live-data validation, rule calibration, and live-model evaluation are required before a production accuracy claim.

On October 13, the task-focused console update passed 159 tests and the offline evaluation gates. Billing preflight confirmed project `texmed-npi-integrity`, account `01E4B3-EA09E1-258E11`, and the current 8192 payment attestation with 6022 blocked. Cloud Build `c1c1ffc1-632a-4cbf-b53a-1d5184d860dd` produced image digest `sha256:4772cc77f4d99aa636750e0c6eb1bf871c16ba2b495467a0e0e880a3f917ad6c`. A temporary local DNS failure interrupted polling after the build succeeded, so that exact image was deployed by digest. Revision `npi-console-00006-dgf` serves 100% of traffic with IAP enabled and synthetic mode retained. Signed-in browser smoke checks rendered Home, the case workspace, field comparison (including “Cannot check yet” for license), and Government leads. An unauthenticated request to `/` was redirected to Google sign-in. The one-time roster check has not been exercised with an uploaded CSV in the live service; it remains restricted to synthetic input and does not persist or monitor rosters.

## PHI boundary

The current deployment is a synthetic pilot and must not receive PHI. It pins `NPI_DATA_MODE=synthetic`, rejects free-text review reasons, and has no external model credentials. Data Access audit logs for Firestore, IAP, and Cloud Run and Firestore point-in-time recovery are enabled by [harden_gcp.sh](harden_gcp.sh), which the deployment script reruns on each release. See [HIPAA_READINESS.md](HIPAA_READINESS.md) for verified controls and requirements before any PHI use; these settings alone do not establish HIPAA compliance.
