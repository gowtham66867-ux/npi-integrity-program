#!/usr/bin/env bash
# Additional pilot audit and recovery controls. This is not HIPAA certification.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
POLICY="$(mktemp)"
CHECK="$(mktemp)"
trap 'rm -f "$POLICY" "$CHECK"' EXIT
"$PYTHON" deploy/preflight.py --output "$CHECK"
read_check() { "$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$CHECK" "$1"; }
PROJECT="$(read_check project)"
ACCOUNT="$(read_check identity)"
gcloud projects get-iam-policy "$PROJECT" --account="$ACCOUNT" --format=json > "$POLICY"
"$PYTHON" - "$POLICY" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as handle:
    policy = json.load(handle)
services = ('datastore.googleapis.com', 'iap.googleapis.com', 'run.googleapis.com')
configs = policy.setdefault('auditConfigs', [])
for service in services:
    config = next((item for item in configs if item['service'] == service), None)
    if config is None:
        config = {'service': service, 'auditLogConfigs': []}
        configs.append(config)
    existing = {item['logType'] for item in config['auditLogConfigs']}
    for kind in ('ADMIN_READ', 'DATA_READ', 'DATA_WRITE'):
        if kind not in existing:
            config['auditLogConfigs'].append({'logType': kind})
with open(path, 'w') as handle:
    json.dump(policy, handle)
PY
gcloud projects set-iam-policy "$PROJECT" "$POLICY" --account="$ACCOUNT" --quiet >/dev/null
gcloud firestore databases update --database=npi-cases --enable-pitr --project="$PROJECT" --account="$ACCOUNT" --quiet >/dev/null
echo 'Project Data Access audit logs enabled for Firestore, IAP, and Cloud Run; Firestore PITR enabled.'
