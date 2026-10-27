"""Fail-closed deployment guard for the TexMed project and billing account.

Cloud Billing APIs do not expose payment methods. The account owner controls
the method associated with this TexMed account; no card details are stored here.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path

class Refused(ValueError):
    pass


def validate_policy(p):
    if p.get('expected_identity') != 'gowtham@texmed.us':
        raise Refused('deployment identity must be gowtham@texmed.us')
    required = ('approved_project', 'approved_billing_account', 'approved_organization')
    if any(not p.get(k) for k in required):
        raise Refused('approved project, company billing account and organization are required')
    if not re.fullmatch(r'[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}', p['approved_billing_account']):
        raise Refused('invalid billing account ID')
    if not p.get('region', '').startswith('us-'):
        raise Refused('deployment must be in the US')
    return p


def gcloud_json(args, account):
    cmd = ['gcloud', *args, '--account', account, '--format=json', '--quiet']
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=60)
        return json.loads(r.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise Refused('unable to verify GCP configuration; no deployment is allowed') from exc


def check_cloud(p, query=gcloud_json):
    account = p['expected_identity']
    billing = query(['billing', 'projects', 'describe', p['approved_project']], account)
    if billing.get('billingEnabled') is not True or billing.get('billingAccountName') != 'billingAccounts/' + p['approved_billing_account']:
        raise Refused('project billing does not match the approved company account')
    details = query(['billing', 'accounts', 'describe', p['approved_billing_account']], account)
    if details.get('open') is not True or details.get('parent') != 'organizations/' + p['approved_organization']:
        raise Refused('billing account is closed or is outside the approved organization')
    project = query(['projects', 'describe', p['approved_project']], account)
    ancestors = query(['projects', 'get-ancestors', p['approved_project']], account)
    if not isinstance(ancestors, list) or not ancestors or ancestors[0] != {'type': 'project', 'id': p['approved_project']}:
        raise Refused('project ancestry could not be verified')
    if ancestors[-1] != {'type': 'organization', 'id': p['approved_organization']}:
        raise Refused('project is outside the approved organization')
    parent = project.get('parent', {})
    if len(ancestors) < 2 or parent != ancestors[1]:
        raise Refused('project parent does not match its ancestry')
    budgets = query(['billing', 'budgets', 'list', '--billing-account=' + p['approved_billing_account'],
                     '--project=' + p['approved_project']], account)
    if not any(any(ref in b.get('budgetFilter', {}).get('projects', []) for ref in
                   ('projects/' + str(project['projectNumber']), 'projects/' + p['approved_project']))
               and b.get('thresholdRules') for b in budgets):
        raise Refused('a budget with alerts filtered to this project must exist before deployment')
    return {'identity': account, 'project': p['approved_project'],
            'billing_account': p['approved_billing_account'], 'organization': p['approved_organization'],
            'region': p['region'], 'project_number': str(project['projectNumber'])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--policy', type=Path, default=Path(__file__).with_name('billing_policy.json'))
    ap.add_argument('--policy-only', action='store_true')
    ap.add_argument('--output', type=Path)
    a = ap.parse_args()
    try:
        p = validate_policy(json.loads(a.policy.read_text()))
        result = {'policy_valid': True} if a.policy_only else check_cloud(p)
    except (Refused, OSError, json.JSONDecodeError) as exc:
        ap.exit(1, f'REFUSED: {exc}\n')
    body = json.dumps(result, indent=2)
    if a.output:
        a.output.write_text(body + '\n')
    print(body)

if __name__ == '__main__':
    main()
