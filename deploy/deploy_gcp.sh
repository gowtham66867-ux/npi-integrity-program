#!/usr/bin/env bash
# Every paid mutation is downstream of billing verification and release checks.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
SERVICE="npi-console"
POLICY="deploy/billing_policy.json"
CHECK="$(mktemp)"
ENV_FILE="$(mktemp)"
trap 'rm -f "$CHECK" "$ENV_FILE"' EXIT
"$PYTHON" deploy/preflight.py --policy "$POLICY" --output "$CHECK"
read_policy() { "$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$CHECK" "$1"; }
PROJECT="$(read_policy project)"
ACCOUNT="$(read_policy identity)"
REGION="$(read_policy region)"
NUMBER="$(read_policy project_number)"
SA="npi-console@$PROJECT.iam.gserviceaccount.com"
BUILD_SA="npi-build@$PROJECT.iam.gserviceaccount.com"
if [[ "$DRY_RUN" == 1 ]]; then
  echo "Preflight passed. Dry run: no cloud resources changed."
  exit 0
fi
"$PYTHON" scripts/release_check.py
# Recheck immediately before paid mutation (billing may have changed during tests).
"$PYTHON" deploy/preflight.py --policy "$POLICY" --output "$CHECK"
gc() { gcloud "$@" --project "$PROJECT" --account "$ACCOUNT" --quiet; }
gc services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com iap.googleapis.com cloudresourcemanager.googleapis.com
if ! gc iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gc iam service-accounts create npi-console --display-name='NPI console runtime'
fi
gc projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role=roles/datastore.user --condition=None
if ! gc iam service-accounts describe "$BUILD_SA" >/dev/null 2>&1; then
  gc iam service-accounts create npi-build --display-name='NPI source build'
fi
gc projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$BUILD_SA" --role=roles/run.builder --condition=None
if ! gc firestore databases describe --database=npi-cases >/dev/null 2>&1; then
  gc firestore databases create --database=npi-cases --location="$REGION" --type=firestore-native --delete-protection
fi
# Reassert project audit logging and Firestore recovery on every release.
./deploy/harden_gcp.sh
"$PYTHON" - "$ENV_FILE" "$PROJECT" "$NUMBER" "$REGION" "$SERVICE" <<'PY'
import json,sys
path,project,number,region,service=sys.argv[1:]
json.dump({'GOOGLE_CLOUD_PROJECT':project, 'NPI_CASE_BACKEND':'firestore', 'NPI_FIRESTORE_DATABASE':'npi-cases',
           'NPI_ALLOWED_DOMAIN':'texmed.us', 'NPI_DATA_MODE':'synthetic',
           'NPI_IAP_AUDIENCE':f'/projects/{number}/locations/{region}/services/{service}',
           'NPI_PUBLIC_ORIGIN':f'https://{service}-{number}.{region}.run.app',
           'NPI_REVIEWERS':'gowtham@texmed.us', 'NPI_SUPERVISORS':'', 'NPI_DRAFTS_PER_MINUTE':'6'},open(path,'w'))
PY
# Ship scripted agents initially. Enabling a paid model requires its own verified company billing.
gc beta run deploy "$SERVICE" --region "$REGION" --source . --no-allow-unauthenticated --iap \
  --service-account "$SA" --build-service-account "projects/$PROJECT/serviceAccounts/$BUILD_SA" --cpu 2 --memory 2Gi --min-instances 0 --max-instances 2 \
  --concurrency 10 --timeout 300 --env-vars-file "$ENV_FILE" \
  --labels='app=npi-integrity,owner=gowtham,cost-center=texmed,data=synthetic'
# Direct IAP needs its own service agent to invoke the private Cloud Run service.
gc beta services identity create --service=iap.googleapis.com
gc run services add-iam-policy-binding "$SERVICE" --region "$REGION" \
  --member="serviceAccount:service-$NUMBER@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker
gc beta iap web add-iam-policy-binding --region "$REGION" --resource-type=cloud-run --service "$SERVICE" \
  --member="user:$ACCOUNT" --role=roles/iap.httpsResourceAccessor
for VIEWER in lester@texmed.us kashif@texmed.us; do
  gc beta iap web add-iam-policy-binding --region "$REGION" --resource-type=cloud-run --service "$SERVICE" \
    --member="user:$VIEWER" --role=roles/iap.httpsResourceAccessor
done
gc run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
echo "Service deployed privately. Authenticated smoke tests and Firestore persistence verification are required before acceptance."
