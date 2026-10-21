"""Billing, case persistence, role and concurrent update regression tests."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import importlib.util
from pathlib import Path
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from npi_integrity.cases import SQLiteCases, case_key, event
from npi_integrity import security

spec = importlib.util.spec_from_file_location('preflight', Path(__file__).parents[1] / 'deploy' / 'preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)
def policy():
    return {'expected_identity':'gowtham@texmed.us',
            'approved_project':'texmed-test', 'approved_billing_account':'AAAAAA-BBBBBB-CCCCCC',
            'approved_organization':'123', 'region':'us-central1'}


def test_verified_policy_passes():
    preflight.validate_policy(policy())


@pytest.mark.parametrize('changes', [
    {'expected_identity':'personal@gmail.com'},
    {'approved_project':None}, {'approved_billing_account':None}, {'approved_organization':None},
    {'approved_billing_account':'invalid'}, {'region':'asia-south1'},
])
def test_billing_policy_fails_closed(changes):
    with pytest.raises(preflight.Refused):
        preflight.validate_policy({**policy(), **changes})


def cloud_query(args, account):
    assert account == 'gowtham@texmed.us'
    if args[:3] == ['billing','projects','describe']:
        return {'billingEnabled':True, 'billingAccountName':'billingAccounts/AAAAAA-BBBBBB-CCCCCC'}
    if args[:3] == ['billing','accounts','describe']:
        return {'open':True, 'parent':'organizations/123'}
    if args[:2] == ['projects','describe']:
        return {'projectNumber':'999', 'parent':{'type':'organization','id':'123'}}
    if args[:2] == ['projects','get-ancestors']:
        return [{'type':'project','id':'texmed-test'}, {'type':'organization','id':'123'}]
    return [{'budgetFilter':{'projects':['projects/999']}, 'thresholdRules':[{'thresholdPercent':0.5}]}]


def test_cloud_preflight_requires_matching_company_billing():
    assert preflight.check_cloud(policy(), cloud_query)['project_number'] == '999'
    def wrong(args, account):
        result = cloud_query(args, account)
        if args[:3] == ['billing','projects','describe']:
            result['billingAccountName'] = 'billingAccounts/PERSONAL'
        return result
    with pytest.raises(preflight.Refused):
        preflight.check_cloud(policy(), wrong)


def test_budget_must_cover_selected_project():
    def wrong(args, account):
        return [] if args[:3] == ['billing','budgets','list'] else cloud_query(args, account)
    with pytest.raises(preflight.Refused):
        preflight.check_cloud(policy(), wrong)


def test_project_folder_ancestry_must_end_at_texmed_organization():
    def with_folder(args, account):
        if args[:2] == ['projects','describe']:
            return {'projectNumber':'999', 'parent':{'type':'folder','id':'456'}}
        if args[:2] == ['projects','get-ancestors']:
            return [{'type':'project','id':'texmed-test'}, {'type':'folder','id':'456'},
                    {'type':'organization','id':'123'}]
        return cloud_query(args, account)
    assert preflight.check_cloud(policy(), with_folder)['project_number'] == '999'
    def wrong_org(args, account):
        if args[:2] == ['projects','get-ancestors']:
            return [{'type':'project','id':'texmed-test'}, {'type':'folder','id':'456'},
                    {'type':'organization','id':'personal'}]
        return with_folder(args, account)
    with pytest.raises(preflight.Refused):
        preflight.check_cloud(policy(), wrong_org)


@pytest.fixture
def cases(tmp_path):
    return SQLiteCases(tmp_path/'cases.sqlite')


def disposition(action, actor='reviewer', reason='reviewed evidence', supervisor=''):
    return SimpleNamespace(action=action, actor=actor, reason=reason, supervisor=supervisor)


def test_cases_survive_new_store_and_drafts_survive_restart(cases):
    cases.transition('key','New','A1',disposition('review'))
    cases.save_draft('key',{'status':'passed'},event('r','agent_draft','A1'))
    reopened = SQLiteCases(cases.path)
    assert reopened.states(['key']) == {'key':'In review'}
    assert reopened.latest_draft('key') == {'status':'passed'}
    assert len(reopened.audit(20)) == 2


def test_case_identity_survives_new_run_id():
    a = {'npi':'123', 'fingerprint':'abc', 'run_id':'run1', 'alert_id':'A1'}
    assert case_key(a) == case_key({**a,'run_id':'run2','alert_id':'A2'})
    assert case_key(a) != case_key({**a,'fingerprint':'changed'})


def test_transition_and_audit_commit_together(cases):
    with pytest.raises(HTTPException):
        cases.transition('key','New','A1',disposition('refer'))
    assert cases.states(['key']) == {} and cases.audit(20) == []
    cases.transition('key','New','A1',disposition('review'))
    cases.transition('key','New','A1',disposition('escalate',supervisor='s'))
    cases.transition('key','New','A1',disposition('refer'))
    assert cases.states(['key'])['key'] == 'Referred'
    assert len(cases.history('key')) == 3
    with pytest.raises(HTTPException):
        cases.transition('key','New','A1',disposition('reopen'))


def test_concurrent_reviewers_cannot_overwrite_closed_case(cases):
    def apply():
        try:
            cases.transition('key','New','A1',disposition('dismiss'))
            return True
        except HTTPException:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:apply(),range(4)))
    assert sum(results) == 1 and len(cases.history('key')) == 1


def test_rate_budget_is_shared_across_instances(cases):
    cases.reserve_draft(1,now=100)
    with pytest.raises(HTTPException) as exc:
        SQLiteCases(cases.path).reserve_draft(1,now=110)
    assert exc.value.status_code == 429
    cases.reserve_draft(1,now=161)


def test_audit_events_cannot_be_rewritten_or_deleted(cases):
    import sqlite3
    cases.record('key',event('reviewer','package_export','A1'))
    with cases.connect() as conn:
        for sql in ('DELETE FROM events', "UPDATE events SET body='{}'"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def req(headers=None):
    return Request({'type':'http','headers':[(k.encode(),v.encode()) for k,v in (headers or {}).items()]})


def test_plain_email_header_is_not_identity(monkeypatch):
    monkeypatch.setenv('NPI_IAP_AUDIENCE','audience')
    with pytest.raises(HTTPException):
        security.authenticate(req({'x-goog-authenticated-user-email':'gowtham@texmed.us'}),'texmed.us')


def test_verified_identity_roles_and_supervisor_signoff(monkeypatch):
    monkeypatch.setenv('NPI_IAP_AUDIENCE','audience')
    monkeypatch.setenv('NPI_REVIEWERS','reviewer@texmed.us')
    monkeypatch.setenv('NPI_SUPERVISORS','supervisor@texmed.us')
    monkeypatch.setattr(security,'verify_iap',lambda token,aud:{'email':token,'sub':'subject'})
    request = req({'x-goog-iap-jwt-assertion':'reviewer@texmed.us'})
    security.authenticate(request,'texmed.us')
    assert security.actor_for(request,'forged-name') == 'reviewer@texmed.us'
    with pytest.raises(HTTPException):
        security.actor_for(request,'supervisor@texmed.us',supervisor=True)
    request = req({'x-goog-iap-jwt-assertion':'supervisor@texmed.us'})
    security.authenticate(request,'texmed.us')
    assert security.actor_for(request,'forged',supervisor=True) == 'supervisor@texmed.us'


def test_deployed_instance_cannot_fall_back_to_local_auth(monkeypatch):
    monkeypatch.setenv('K_SERVICE','npi-console')
    with pytest.raises(HTTPException):
        security.authenticate(req(),'')


def test_invalid_signed_token_fails_closed(monkeypatch):
    monkeypatch.setenv('NPI_IAP_AUDIENCE','audience')
    def invalid(*args):
        raise ValueError('invalid signature')
    monkeypatch.setattr(security,'verify_iap',invalid)
    with pytest.raises(HTTPException):
        security.authenticate(req({'x-goog-iap-jwt-assertion':'invalid'}),'texmed.us')
