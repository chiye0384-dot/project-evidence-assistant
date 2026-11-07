"""Audited cumulative authorization; synthetic ledgers, no network or real keys."""
import copy
from datetime import datetime, timedelta, timezone
import json

import pytest

from project_evidence.spending import PROFILE, SpendingError, SpendingLedger
from project_evidence.storage import ProjectStore
from test_paid_startup import load_startup, price_proof


GRANT = PROFILE + "-initial"


def authorization():
    return {"grant_id": GRANT, "profile": PROFILE, "currency": "CNY",
            "total_limit_microyuan": 50_000_000, "authorized_cumulative_limit_microyuan": 50_000_000,
            "user_confirmation_sha256": "a" * 64, "confirmed_at": datetime.now(timezone.utc).isoformat()}


def fixture_ledger(tmp_path):
    ledger = SpendingLedger(ProjectStore(tmp_path / "data"))
    ledger.authorize(confirmed=True, grant_id=GRANT)
    ledger.refresh_verified_price(price_proof())
    return ledger


def rows(ledger, table):
    with ledger.store._connection() as c:
        return [tuple(row) for row in c.execute("SELECT * FROM " + table + " ORDER BY id")]


def test_authorized_increase_retains_all_costs_caps_and_identity_across_restart(tmp_path):
    ledger = fixture_ledger(tmp_path)
    receipt = ledger.reserve(GRANT, "previous", 4000)
    ledger.settle(receipt, 10_000, 1_000)
    before_grant = rows(ledger, "model_grants")[0]
    before_spend = rows(ledger, "model_spend")
    proof = authorization()
    identifier = ledger.apply_project_budget_authorization(proof, confirmed=True)
    restarted = SpendingLedger(ledger.store)
    assert restarted.apply_project_budget_authorization(proof, confirmed=True) == identifier
    assert rows(ledger, "model_spend") == before_spend
    after_grant = rows(ledger, "model_grants")[0]
    assert after_grant[:3] == before_grant[:3]
    assert after_grant[3] == 50_000_000 and after_grant[4:] == before_grant[4:]
    assert len(rows(ledger, "model_budget_authorizations")) == 1
    assert rows(ledger, "model_budget_authorizations")[0][2] == 5_000_000
    assert restarted.summary(GRANT)["trial_remaining_microyuan"] == 49_972_000
    restarted.authorize(confirmed=True, grant_id=GRANT, trial_limit=50_000_000)
    next_receipt = restarted.reserve(GRANT, "new-task", 4000)
    assert restarted.summary(GRANT)["encumbered_microyuan"] == 28_000 + 2_232_000
    restarted.settle(next_receipt, 10_000, 1_000)


@pytest.mark.parametrize("problem", ["unconfirmed", "missing", "extra", "wrong_grant", "over50", "under50",
                                     "boolean", "bad_hash", "future", "naive", "no_grant"])
def test_invalid_authorization_cannot_change_grant_or_history(tmp_path, problem):
    ledger = fixture_ledger(tmp_path); proof = authorization(); confirmed = True
    if problem == "unconfirmed": confirmed = False
    if problem == "missing": proof.pop("confirmed_at")
    if problem == "extra": proof["reset_spend"] = True
    if problem == "wrong_grant": proof["grant_id"] = "new-budget"
    if problem == "over50": proof["authorized_cumulative_limit_microyuan"] = 50_000_001
    if problem == "under50": proof["authorized_cumulative_limit_microyuan"] = 8_000_000
    if problem == "boolean": proof["total_limit_microyuan"] = True
    if problem == "bad_hash": proof["user_confirmation_sha256"] = "invalid"
    if problem == "future": proof["confirmed_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    if problem == "naive": proof["confirmed_at"] = "2026-10-16T00:00:00"
    if problem == "no_grant":
        ledger = SpendingLedger(ProjectStore(tmp_path / "empty"))
    before = rows(ledger, "model_grants")
    with pytest.raises(SpendingError): ledger.apply_project_budget_authorization(proof, confirmed=confirmed)
    assert rows(ledger, "model_grants") == before
    assert rows(ledger, "model_budget_authorizations") == []


@pytest.mark.parametrize("state", ["pending", "uncertain", "violation"])
def test_unresolved_costs_still_stop_authorization_atomically(tmp_path, state):
    ledger = fixture_ledger(tmp_path); receipt = ledger.reserve(GRANT, "unresolved", 4000)
    with ledger.store._connection() as c:
        c.execute("UPDATE model_spend SET state=? WHERE id=?", (state, receipt))
    before = rows(ledger, "model_grants"); spend = rows(ledger, "model_spend")
    with pytest.raises(SpendingError) as error: ledger.apply_project_budget_authorization(authorization(), confirmed=True)
    assert error.value.code == "cost_uncertain"
    assert rows(ledger, "model_grants") == before and rows(ledger, "model_spend") == spend
    assert rows(ledger, "model_budget_authorizations") == []


def test_total_and_task_caps_remain_enforced_at_full_authorized_budget(tmp_path):
    ledger = fixture_ledger(tmp_path); ledger.apply_project_budget_authorization(authorization(), confirmed=True)
    receipt = ledger.reserve(GRANT, "same-task", 4000); ledger.settle(receipt, 600_000, 1000)
    with pytest.raises(SpendingError) as error: ledger.reserve(GRANT, "same-task", 4000)
    assert error.value.code == "spending_limit"
    for index in range(39):
        receipt = ledger.reserve(GRANT, f"previous-{index}", 4000)
        ledger.settle(receipt, 600_000, 1000)
    assert ledger.summary(GRANT)["accounted_upper_bound_microyuan"] == 48_320_000
    with pytest.raises(SpendingError) as error: ledger.reserve(GRANT, "new-task", 4000)
    assert error.value.code == "spending_limit"
    assert ledger.summary(GRANT)["unresolved_requests"] == 0


def test_restart_reads_same_authorization_without_reset_or_model_dispatch(tmp_path, monkeypatch):
    ledger = fixture_ledger(tmp_path); receipt = ledger.reserve(GRANT, "previous", 4000)
    ledger.settle(receipt, 10_000, 1_000)
    (tmp_path / "prices").mkdir()
    (tmp_path / "prices" / "project-budget-authorization.json").write_text(json.dumps(authorization()), encoding="utf-8")
    (tmp_path / "prices" / "deepseek-verified.json").write_text(json.dumps(price_proof()), encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key-no-network")
    startup, captures = load_startup(tmp_path, monkeypatch, ["--enable-paid-model", "--enable-judging-model"])
    before = copy.deepcopy(rows(ledger, "model_spend"))
    for _ in range(2):
        startup.main()
        status = captures["model_adapter"].public_status()
        assert status["status"] == "ready" and status["trial_limit_microyuan"] == 50_000_000
        assert status["accounted_upper_bound_microyuan"] == 28_000
    assert rows(ledger, "model_spend") == before
    assert len(rows(ledger, "model_grants")) == len(rows(ledger, "model_budget_authorizations")) == 1
