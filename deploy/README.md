# Guarded Cloud Run deployment

The private console is deployed at [npi-console](https://npi-console-1056643263413.us-central1.run.app/) in project `texmed-npi-integrity`, region `us-central1`. The active `gcloud` identity must be `gowtham@texmed.us`. Every later deployment runs the billing preflight before cloud changes.

## Required billing evidence

The project is under the `texmed.pro` organization (`878199198137`) and is linked to TexMed Cloud Billing account `01B53D-1B9A75-6E585F`. The October 19 user instruction authorizes using whichever payment method is associated with this TexMed account. The deployment guard checks the live billing link and organization; it does not pin or record card digits. Never store a full card number.

[billing_policy.json](billing_policy.json) records the approved deployment identity, project, billing account, organization, and region.

The preflight queries the live project billing link, account status and organization, complete project ancestry, and a monthly USD 25 budget with alerts scoped to this project. A budget alert does not cap spending. Cloud Billing's API does **not** expose card digits; the account owner manages its payment method.

```bash
.venv/bin/python deploy/preflight.py --policy-only
.venv/bin/python deploy/preflight.py
.venv/bin/python scripts/release_check.py
./deploy/deploy_gcp.sh --dry-run
./deploy/deploy_gcp.sh
```

`--dry-run` performs the billing preflight without cloud changes. A normal run repeats the preflight after the release checks and before enabling services or creating resources. The script uses the pinned identity and project on every `gcloud` command.

## Runtime

Cloud Run uses direct IAP, a verified IAP JWT in the application, a dedicated service account, and the delete-protected Firestore database `npi-cases` in `us-central1` for case state, drafts, and review events. An External Google Auth Platform app in Testing mode has only `gowtham@texmed.us` as a test user; the IAP web app user grant is also limited to that address. `NPI_REVIEWERS` contains that address. `NPI_SUPERVISORS` is empty: a TexMed administrator must designate verified supervisor email addresses before escalation. The API rejects a typed supervisor name when deployed. The container holds only public and synthetic detection data. No Anthropic secret is mounted, so agents use the offline model.

The Cloud Run service is private. On October 7, signed-in browser access with `gowtham@texmed.us` rendered the alert queue, accepted a synthetic `Start review` action, and generated a scripted draft. A direct Firestore read confirmed one case document, one draft document, and `disposition:review` and `agent_draft` events. Revision `npi-console-00004-xk4` serves 100% of traffic with IAP enabled; the prior case and draft survived that revision change. The new UI shows a no-PHI warning and fixed reason choices. A scripted draft write also succeeded through Cloud Run's second official `a.run.app` URL. The CSRF check accepts same-origin HTTPS writes on Cloud Run URLs and rejects cross-origin writes. Future releases should repeat these checks and verify `/api/session`.

The release harness passed 141 tests and 16/16 offline evaluation gates on October 7. The synthetic scorecard does not estimate real-world accuracy. Separate live-data validation, rule calibration, and live-model evaluation are required before a production accuracy claim.

On October 13, the task-focused console update passed 159 tests and the offline evaluation gates. Billing preflight confirmed project `texmed-npi-integrity`, account `01E4B3-EA09E1-258E11`, and the current 8192 payment attestation with 6022 blocked. Cloud Build `c1c1ffc1-632a-4cbf-b53a-1d5184d860dd` produced image digest `sha256:4772cc77f4d99aa636750e0c6eb1bf871c16ba2b495467a0e0e880a3f917ad6c`. A temporary local DNS failure interrupted polling after the build succeeded, so that exact image was deployed by digest. Revision `npi-console-00006-dgf` serves 100% of traffic with IAP enabled and synthetic mode retained. Signed-in browser smoke checks rendered Home, the case workspace, field comparison (including “Cannot check yet” for license), and Government leads. An unauthenticated request to `/` was redirected to Google sign-in. The one-time roster check has not been exercised with an uploaded CSV in the live service; it remains restricted to synthetic input and does not persist or monitor rosters.

## PHI boundary

The current deployment is a synthetic pilot and must not receive PHI. It pins `NPI_DATA_MODE=synthetic`, rejects free-text review reasons, and has no external model credentials. Data Access audit logs for Firestore, IAP, and Cloud Run and Firestore point-in-time recovery are enabled by [harden_gcp.sh](harden_gcp.sh), which the deployment script reruns on each release. See [HIPAA_READINESS.md](HIPAA_READINESS.md) for verified controls and requirements before any PHI use; these settings alone do not establish HIPAA compliance.

The optional exact-NPI lookup and verification form call the public NPPES API
v2.1 server-side. They return a single public Registry record without storing
the response or joining it to synthetic scores. Bulk monitoring still requires
an independently validated monthly/weekly/deactivation ingestion pipeline.
On October 13, revision `npi-console-00007-gws` passed 176 tests and the offline
evaluation gates and serves 100% of traffic with IAP enabled and
`NPI_DATA_MODE=synthetic`. A signed-in browser check retrieved NPI 1447268255
from CMS through Cloud Run and confirmed the live comparison showed a matching
name and differing city. The existing synthetic alert demo remained labeled.

The October 13 public-data release adds a minimal exact-NPI index of the
HHS-OIG LEIE active-exclusion download to the provider page. The OIG source
reported an update on 2026-10-09; the index was downloaded on 2026-10-13 from
84,093 rows, of which 8,924 published a 10-digit NPI. The source CSV SHA-256
is `7483d47bc48734f7955095d51d13dd1111b11cc8a60f54ebe894806e6c293c03`.
The index excludes DOB and street address and never treats an absent exact-NPI
match as exclusion clearance. A published match requires verification in the
official OIG search. Its source-update date, download date, coverage, and hash
are visible in the UI. This snapshot is not a real-time fraud feed.

Revision `npi-console-00009-d2x` serves 100% of traffic from image digest
`sha256:4d899cb895d29b5ff8036a147a6a379b6c9568b6f8a6b8f0890ba5616a789777`.
The release harness passed 184 tests and 16/16 offline evaluation gates.
Deployment preflight confirmed the approved project, account, recent 8192
payment-method attestation, and 6022 block. Cloud Run still has IAP enabled and
`NPI_DATA_MODE=synthetic`; an unauthenticated request redirected to Google
sign-in. Signed-in browser checks showed both an absent exact-NPI match and a
published LEIE NPI match, and confirmed the narrow-screen layout after a
checksum overflow fix. Firestore case persistence was not retested on this
revision; the case storage backend and database configuration were unchanged.
See [public-data acceptance cases](../tests/PUBLIC_DATA_TEST_CASES.md) for
remaining source-specific and browser checks.

On October 19, the project was confirmed under `texmed.pro` and linked to
TexMed billing account `01B53D-1B9A75-6E585F`. A USD 25 monthly alert budget
was created for project `1056643263413` with 50%, 90%, and 100% thresholds.
The user approved billing through whichever method is associated with this
TexMed account, and the deployment policy now verifies the account rather than
card digits. The release passed 180 tests and 16/16 offline evaluation gates.
Revision `npi-console-00010-q2r` serves 100% of traffic from image digest
`sha256:a8e1c7fb5c76b34b9730bd8f7970c03a907263380c92330625de3cfedd2d1bca`.
IAP and synthetic mode remain enabled. An unauthenticated request redirects to
Google sign-in; the TexMed Chrome profile loaded the dashboard, `/api/session`
returned `gowtham@texmed.us` as reviewer with synthetic mode, and exact NPI
`1447268255` returned live NPPES data and the dated LEIE snapshot. A separate
in-app browser session received an IAP access denial; access was verified in
the signed-in TexMed Chrome profile. Firestore case writes were not retested
on this revision.
