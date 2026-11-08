"""Local opt-in startup; fake credentials and no actual server/model calls."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest
import uvicorn

import project_evidence.app as app_module
import project_evidence.spending as spending


def load_startup(tmp_path, monkeypatch, arguments):
    spec = importlib.util.spec_from_file_location("fixture_startup", Path(__file__).parents[1] / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    monkeypatch.setattr(sys, "argv", ["run.py", *arguments])
    captures = {}
    def factory(**kwargs):
        captures.update(kwargs)
        return "fixture-app"
    monkeypatch.setattr(app_module, "create_app", factory)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captures.update(server=kwargs, app=app))
    return module, captures


def test_default_does_not_enable_existing_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key-for-default-test")
    startup, captures = load_startup(tmp_path, monkeypatch, [])
    startup.main()
    assert captures["model_adapter"] is None
    assert captures["server"]["host"] == "127.0.0.1"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize('flags', [
    ['--followup-prose-flat'],
    ['--followup-prose-flat', '--followup-prose-kind'],
    ['--followup-prose-flat', '--followup-two-phase'],
])
def test_flat_prose_invalid_opt_ins_stop_before_key_grant_or_server(tmp_path, monkeypatch, flags):
    startup, captures = load_startup(tmp_path, monkeypatch, flags)
    with pytest.raises(SystemExit) as error:
        startup.main()
    assert error.value.code == 2 and captures == {} and not (tmp_path / 'data').exists()


def test_flat_prose_explicit_startup_exposes_selected_format_without_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-local-flat-key')
    prices = tmp_path / 'prices'
    prices.mkdir()
    (prices / 'deepseek-verified.json').write_text(json.dumps(price_proof()), encoding='utf-8')
    startup, captures = load_startup(tmp_path, monkeypatch, [
        '--enable-paid-model', '--enable-query-model', '--enable-followup-model', '--followup-two-phase',
        '--followup-selected-table', '--followup-prose-flat', '--followup-index-columns'])
    startup.main()
    adapter = captures['model_adapter']
    assert adapter.followup_prose_format == 'flat_prose_v1'
    assert adapter.followup_claim_format == 'selected_table_v1'
    assert adapter.followup_scope_view == 'java_scope_edge_unit_index_columns_v1'
    with adapter.ledger.store._connection() as db:
        assert db.execute('SELECT count(*) FROM model_spend').fetchone()[0] == 0
    # Inspect the real status endpoint without the startup factory patch.
    monkeypatch.undo()
    with TestClient(app_module.create_app(tmp_path / 'status-data', model_adapter=adapter),
                    base_url='http://localhost:8123') as client:
        status = client.get('/api/model/status').json()
    assert status['followup_prose_format'] == 'flat_prose_v1'
    assert 'synthetic-local-flat-key' not in json.dumps(status)


def test_opt_in_without_key_does_not_start_server_or_grant(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    startup, captures = load_startup(tmp_path, monkeypatch, ["--enable-paid-model"])
    with pytest.raises(SystemExit) as error:
        startup.main()
    assert error.value.code == 2 and captures == {}
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize('flag',['--enable-query-model','--enable-submission-model','--enable-consumption-model','--enable-judging-model','--enable-followup-model'])
def test_query_flag_cannot_enable_default_or_read_key_without_paid_opt_in(tmp_path, monkeypatch,flag):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-unused-key")
    startup, captures = load_startup(tmp_path, monkeypatch, [flag])
    with pytest.raises(SystemExit): startup.main()
    assert captures == {} and not (tmp_path / "data").exists()


def test_followup_flag_cannot_enable_topics_or_read_key_without_topic_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-unused-key")
    startup, captures = load_startup(tmp_path, monkeypatch, ["--enable-paid-model", "--enable-followup-model"])
    with pytest.raises(SystemExit): startup.main()
    assert captures == {} and not (tmp_path / "data").exists()


@pytest.mark.parametrize("topic_flags,topics", [([], set()), (["--enable-judging-model"], {"judging"})])
def test_explicit_local_opt_in_reuses_grant_without_any_api_request(tmp_path, monkeypatch, topic_flags, topics):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key-for-explicit-test")
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    class FixtureLedger(spending.SpendingLedger):
        def authorize(self, **kwargs):
            return super().authorize(**kwargs, expires_at=expiry)
    monkeypatch.setattr(spending, "SpendingLedger", FixtureLedger)
    startup, captures = load_startup(tmp_path, monkeypatch, ["--enable-paid-model", *topic_flags])
    for _ in range(2):
        startup.main()
        adapter = captures["model_adapter"]
        assert adapter.allowed_topics == topics
        assert adapter.public_status()["status"] == "ready"
        assert adapter.public_status()["encumbered_microyuan"] == 0
        assert adapter.public_status()["trial_limit_microyuan"] == 5_000_000
    with adapter.ledger.store._connection() as c:
        assert c.execute("SELECT count(*) FROM model_grants").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM model_spend").fetchone()[0] == 0


def test_http_cost_status_is_read_only_and_never_exposes_environment_key(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key-no-web-export")
    with TestClient(app_module.create_app(tmp_path / "data"), base_url="http://localhost:8123") as client:
        result = client.get("/api/model/status")
        assert result.json() == {"model_status": "not_configured", "spending": {"status": "not_enabled", "currency": "CNY"}}
        assert "synthetic-key" not in result.text
        assert client.post("/api/model/status", json={"confirmed": True}).status_code == 405
        assert client.get("/api/health").json()["capabilities"]["agent_generation"] is False


def price_proof():
    now = datetime.now(timezone.utc)
    return {"model": "deepseek-flash", "currency": "CNY",
            "input_microyuan_per_token": 2, "output_microyuan_per_token": 8,
            "source_url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
            "response_sha256": "a" * 64, "verified_at": now.isoformat(),
            "valid_until": (now + timedelta(hours=1)).isoformat()}


@pytest.mark.parametrize("problem,expected", [
    ("expired", "价格核验凭据已过期"),
    ("changed_price", "价格核验文件与已批准"),
    ("malformed", "价格核验文件或费用配置格式"),
])
def test_local_price_failure_identifies_cause_without_key_or_server(tmp_path, monkeypatch, capsys, problem, expected):
    marker = "synthetic-correct-format-never-export"
    monkeypatch.setenv("DEEPSEEK_API_KEY", marker)
    startup, captures = load_startup(tmp_path, monkeypatch, ["--enable-paid-model", "--enable-judging-model"])
    proof = price_proof()
    if problem == "expired":
        proof["verified_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        proof["valid_until"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    if problem == "changed_price":
        proof["output_microyuan_per_token"] = 9
    (tmp_path / "prices").mkdir()
    (tmp_path / "prices" / "deepseek-verified.json").write_text(
        "{" if problem == "malformed" else json.dumps(proof), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        startup.main()
    output = capsys.readouterr().err
    assert error.value.code == 2 and captures == {}
    assert expected in output and "未发送模型请求" in output and marker not in output
    ledger = spending.SpendingLedger(spending.ProjectStore(tmp_path / "data"))
    with ledger.store._connection() as c:
        assert c.execute("SELECT count(*) FROM model_spend").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM model_price_rechecks").fetchone()[0] == 0


def test_renewed_official_price_allows_restart_without_resetting_existing_spend(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-local-key-no-api")
    ledger = spending.SpendingLedger(spending.ProjectStore(tmp_path / "data"))
    grant = ledger.authorize(confirmed=True, grant_id=spending.PROFILE + "-initial")
    (tmp_path / "prices").mkdir()
    proof = price_proof()
    (tmp_path / "prices" / "deepseek-verified.json").write_text(json.dumps(proof), encoding="utf-8")
    ledger.refresh_verified_price(proof)
    spend_id = ledger.reserve(grant, "existing-task-before-reboot", 4000)
    ledger.settle(spend_id, 10000, 1000)
    before = ledger.summary(grant)
    arguments = ["--enable-paid-model", "--enable-query-model", "--enable-submission-model",
                 "--enable-consumption-model", "--enable-judging-model", "--enable-followup-model"]
    startup, captures = load_startup(tmp_path, monkeypatch, arguments)
    for _ in range(2):
        startup.main()
        adapter = captures["model_adapter"]
        assert adapter.allowed_topics == {"query", "submission", "consumption", "judging"}
        assert adapter.enable_followup
        assert adapter.public_status()["status"] == "ready"
        assert adapter.public_status()["accounted_upper_bound_microyuan"] == before["accounted_upper_bound_microyuan"] == 28000
        assert adapter.public_status()["trial_remaining_microyuan"] == before["trial_remaining_microyuan"]
    with ledger.store._connection() as c:
        assert c.execute("SELECT count(*) FROM model_grants").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM model_spend").fetchone()[0] == 1
