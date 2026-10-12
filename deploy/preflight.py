"""Fail-closed deployment billing guard. Card 6022 cannot be overridden by env vars.

Cloud Billing APIs expose the billing account, not its cards. A recent payment
console inspection is mandatory; account names or an email are not card proof.
No full card numbers or credentials belong in this policy.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

class Refused(ValueError):
    pass


def validate_policy(p, now=None):
    now = now or datetime.now(timezone.utc)
    if p.get('expected_identity') != 'gowtham@texmed.us':
        raise Refused('deployment identity must be gowtham@texmed.us')
    if p.get('approved_primary_card_last4') != '8192':
        raise Refused('approved primary payment method must end in 8192')
    if '6022' not in p.get('forbidden_card_last4', []):
        raise Refused('permanent 6022 restriction is missing')
    required = ('approved_project', 'approved_billing_account', 'approved_organization',
                'approved_primary_card_last4', 'payment_methods_verified_at',
                'payment_methods_verified_by', 'payment_verification_source')
    if any(not p.get(k) for k in required):
        raise Refused('approved project, company billing account and verified payment methods are required')
    if not re.fullmatch(r'[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}', p['approved_billing_account']):
        raise Refused('invalid billing account ID')
    if not p.get('region', '').startswith('us-'):
        raise Refused('deployment must be in the US')
    cards = p.get('active_payment_method_last4', [])
    if not cards or any(not isinstance(c, str) or not re.fullmatch(r'\d{4}', c) for c in cards):
        raise Refused('all active payment method last-four digits must be recorded')
    if p['approved_primary_card_last4'] not in cards:
        raise Refused('approved primary card is not among verified active methods')
    if set(cards) & (set(p['forbidden_card_last4']) | {'6022'}):
        raise Refused('card ending 6022 or another blocked card is active; deployment refused')
    if p['payment_methods_verified_by'].lower() != 'gowtham@texmed.us':
        raise Refused('payment verification must be for the authorized company identity')
    if p['payment_verification_source'] != 'google_cloud_payment_methods_console':
        raise Refused('payment methods must be verified in the Google Cloud payment console')
    try:
        verified = datetime.fromisoformat(p['payment_methods_verified_at'].replace('Z', '+00:00'))
        max_age = min(float(p.get('max_verification_age_hours', 24)), 24)
        age = now - verified
    except (ValueError, TypeError):
        raise Refused('invalid payment verification timestamp') from None
    if max_age <= 0 or age < timedelta(0) or age > timedelta(hours=max_age):
        raise Refused('payment verification is expired or dated in the future; inspect payment methods again')
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
    parent = project.get('parent', {})
    if parent != {'type': 'organization', 'id': p['approved_organization']}:
        raise Refused('project is outside the approved organization')
    budgets = query(['billing', 'budgets', 'list', '--billing-account=' + p['approved_billing_account'],
                     '--project=' + p['approved_project']], account)
    if not any('projects/' + str(project['projectNumber']) in b.get('budgetFilter', {}).get('projects', [])
               and b.get('thresholdRules') for b in budgets):
        raise Refused('a budget with alerts filtered to this project must exist before deployment')
    return {'identity': account, 'project': p['approved_project'],
            'billing_account': p['approved_billing_account'], 'organization': p['approved_organization'],
            'region': p['region'], 'project_number': str(project['projectNumber']),
            'payment_verified_at': p['payment_methods_verified_at'], 'blocked_card': '6022'}


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
