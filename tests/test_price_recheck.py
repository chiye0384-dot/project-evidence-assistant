"""Synthetic price proof, no HTTP, renewal cannot renew a spending allowance."""
from datetime import datetime,timedelta,timezone
import pytest
from project_evidence.storage import ProjectStore
from project_evidence.spending import SpendingLedger,SpendingError,YUAN


def receipt():
    now=datetime.now(timezone.utc)
    return {'model':'deepseek-flash','currency':'CNY','input_microyuan_per_token':2,'output_microyuan_per_token':8,
            'source_url':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/','response_sha256':'a'*64,
            'verified_at':now.isoformat(),'valid_until':(now+timedelta(hours=1)).isoformat()}


def test_same_price_refresh_preserves_grant_cost_and_restart_identity(tmp_path):
    ledger=SpendingLedger(ProjectStore(tmp_path/'agent'))
    grant=ledger.authorize(confirmed=True,expires_at=(datetime.now(timezone.utc)+timedelta(seconds=10)).isoformat())
    reserved=ledger.reserve(grant,'old-task',4000); ledger.settle(reserved,10000,1000)
    before=ledger.summary(grant); proof=receipt()
    ledger.refresh_verified_price(proof); ledger.refresh_verified_price(proof)
    after=ledger.summary(grant)
    assert before['accounted_upper_bound_microyuan']==after['accounted_upper_bound_microyuan']==28000
    assert after['trial_remaining_microyuan']==5000000-28000 and after['trial_limit_microyuan']==5*YUAN
    with ledger.store._connection() as c:
        assert c.execute('SELECT count(*) FROM model_grants').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM model_price_rechecks').fetchone()[0]==1
        old_expiry=c.execute('SELECT expires_at FROM model_grants').fetchone()[0]
    assert ledger.authorize(confirmed=True,expires_at=old_expiry)==grant


@pytest.mark.parametrize('problem',['changed_price','provider','expired','future','too_long','extra'])
def test_untrusted_changed_or_stale_proof_cannot_renew_price(tmp_path,problem):
    ledger=SpendingLedger(ProjectStore(tmp_path/'agent')); value=receipt()
    if problem=='changed_price': value['output_microyuan_per_token']=9
    if problem=='provider': value['source_url']='https://example.invalid/pricing/'
    if problem=='expired': value['verified_at']=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat();value['valid_until']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    if problem=='future': value['verified_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    if problem=='too_long': value['valid_until']=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    if problem=='extra': value['trial_limit']=50*YUAN
    with pytest.raises(SpendingError): ledger.refresh_verified_price(value)
    with ledger.store._connection() as c: assert c.execute('SELECT count(*) FROM model_price_rechecks').fetchone()[0]==0


def test_price_refresh_cannot_release_uncertain_cost(tmp_path):
    ledger=SpendingLedger(ProjectStore(tmp_path/'agent'))
    grant=ledger.authorize(confirmed=True,expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    hold=ledger.reserve(grant,'unknown',4000); ledger.uncertain(hold)
    with pytest.raises(SpendingError): ledger.refresh_verified_price(receipt())
    assert ledger.summary(grant)['status']=='cost_uncertain'
