"""Money gates use synthetic receipts only; no paid requests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from project_evidence.spending import SpendingLedger, SpendingError, YUAN
from project_evidence.storage import ProjectStore
from project_evidence.models import ProjectRegistration


def ledger(tmp_path, **limits):
    store = ProjectStore(tmp_path / "agent")
    result = SpendingLedger(store)
    result.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), **limits)
    return result


def test_no_permission_no_reservation_and_invalid_caps_rejected(tmp_path):
    result = SpendingLedger(ProjectStore(tmp_path / "agent"))
    with pytest.raises(SpendingError, match="gate"):
        result.authorize(confirmed=False)
    assert result.summary("first-trial")["status"] == "not_authorized"
    with pytest.raises(SpendingError):
        result.reserve("first-trial", "task", 100)
    for limits in ({"total_limit": 51 * YUAN}, {"trial_limit": 3 * YUAN, "task_limit": 4 * YUAN}, {"call_limit": True}):
        with pytest.raises(SpendingError):
            result.authorize(confirmed=True, **limits)


def test_reserve_then_receipt_releases_only_unused_upper_bound(tmp_path):
    result = ledger(tmp_path)
    request = result.reserve("first-trial", "task", 4000)
    assert result.summary("first-trial")["encumbered_microyuan"] == 2_232_000
    result.settle(request, 10000, 1000)
    summary = result.summary("first-trial")
    assert summary["accounted_upper_bound_microyuan"] == 28_000
    assert summary["encumbered_microyuan"] == 28_000
    assert result.settle(request, 10000, 1000) == 28_000
    with pytest.raises(SpendingError):
        result.settle(request, 10000, 999)


def test_unknown_response_keeps_reservation_and_blocks_restart(tmp_path):
    result = ledger(tmp_path)
    request = result.reserve("first-trial", "task", 4000)
    result.uncertain(request)
    restarted = SpendingLedger(ProjectStore(tmp_path / "agent"))
    assert restarted.summary("first-trial")["status"] == "cost_uncertain"
    assert restarted.summary("first-trial")["encumbered_microyuan"] == 2_232_000
    with pytest.raises(SpendingError):
        restarted.reserve("first-trial", "new-task", 1)


def test_project_delete_and_repeat_authorization_cannot_reset_spending(tmp_path):
    result = ledger(tmp_path)
    record = result.store.create(ProjectRegistration(name="Fixture", source_path="D:/never-read",
                                                    public_url="https://github.com/example/public", commit="a" * 40))
    request = result.reserve("first-trial", str(record.id), 4000)
    result.settle(request, 10000, 1000)
    result.store.delete(record.id)
    restarted = SpendingLedger(result.store)
    assert restarted.summary("first-trial")["accounted_upper_bound_microyuan"] == 28_000
    with pytest.raises(SpendingError):
        restarted.authorize(confirmed=True, trial_limit=6 * YUAN)


def test_concurrent_attempts_cannot_pass_pending_reservation(tmp_path):
    result = ledger(tmp_path)
    def reserve(number):
        try:
            return result.reserve("first-trial", str(number), 4000)
        except SpendingError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(reserve, range(8)))
    assert len([x for x in ids if x]) == 1
    assert result.summary("first-trial")["encumbered_microyuan"] == 2_232_000


def test_price_expiry_blocks_before_reservation(tmp_path):
    result = SpendingLedger(ProjectStore(tmp_path / "agent"))
    result.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    with pytest.raises(SpendingError) as error:
        result.reserve("first-trial", "task", 1)
    assert error.value.code == "price_expired"
    assert result.summary("first-trial")["encumbered_microyuan"] == 0


def test_excess_receipt_preserves_full_amount_and_stops_future_calls(tmp_path):
    result = ledger(tmp_path)
    request = result.reserve("first-trial", "task", 4000)
    with pytest.raises(SpendingError):
        result.settle(request, 1_500_000, 4000)
    summary = result.summary("first-trial")
    assert summary["accounted_upper_bound_microyuan"] == 3_032_000
    assert summary["status"] == "cost_uncertain"
    with pytest.raises(SpendingError):
        result.reserve("first-trial", "other", 1)


def test_task_trial_and_total_limits_use_all_prior_receipts(tmp_path):
    result = ledger(tmp_path, total_limit=3 * YUAN, trial_limit=3 * YUAN, task_limit=3 * YUAN)
    request = result.reserve("first-trial", "task", 4000)
    result.settle(request, 600_000, 1000)  # 1.208 yuan leaves < a new worst-case reserve.
    with pytest.raises(SpendingError):
        result.reserve("first-trial", "another-task", 4000)
    assert result.summary("first-trial")["trial_remaining_microyuan"] == 1_792_000
