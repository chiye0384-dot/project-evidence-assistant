"""Official HTTP contract against mock transport, not real model acceptance."""

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from project_evidence.agent_protocol import ModelContext, ModelError
from project_evidence.deepseek_adapter import DeepSeekAdapter, ENDPOINT, MODEL
from project_evidence.spending import SpendingLedger
from project_evidence.storage import ProjectStore
from project_evidence.agent_engine import run_agent
import project_evidence.deepseek_adapter as adapter_module

KEY = "synthetic-key-never-a-real-credential"


def setup(tmp_path, responder, authorized=True):
    ledger = SpendingLedger(ProjectStore(tmp_path / "agent"))
    if authorized:
        ledger.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    adapter = DeepSeekAdapter(KEY, ledger, "first-trial", transport=httpx.MockTransport(responder)).for_task("fixture-task")
    return adapter, ledger


def envelope(content=None, **updates):
    content = content if content is not None else json.dumps({"kind": "final", "final_output": {
        "kind": "gap", "message": "Only fixture evidence is available.", "gaps": ["No verified code was read."]}})
    result = {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {"content": content}}],
              "usage": {"prompt_tokens": 10000, "completion_tokens": 1000}}
    result.update(updates)
    return result


def context():
    return ModelContext(commit="a" * 40, public_url="https://github.com/example/public-java")


def test_fixed_endpoint_non_thinking_json_and_provider_receipt(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT and request.method == "POST"
        assert request.headers["authorization"] == "Bearer " + KEY
        payload = json.loads(request.content)
        assert payload["model"] == MODEL and payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"} and payload["stream"] is False
        assert payload["temperature"] == 0 and payload["max_tokens"] == 4000
        assert [m["role"] for m in payload["messages"]] == ["system", "user"]
        assert "JSON" in payload["messages"][0]["content"]
        assert KEY not in request.content.decode() and "source_path" not in request.content.decode()
        return httpx.Response(200, json=envelope())
    adapter, ledger = setup(tmp_path, respond)
    turn = adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert len(calls) == 1 and turn.usage.mode == "real" and turn.usage.input_tokens == 10000
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 28_000


def test_no_authorization_never_sends_a_request(tmp_path):
    def never(request):
        pytest.fail("An unauthorized network request was attempted")
    adapter, ledger = setup(tmp_path, never, authorized=False)
    with pytest.raises(ModelError) as error:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "not_authorized"
    result = run_agent(adapter, lambda *x: None, lambda: None, lambda: False, initial_context=context())
    assert result.counters.model_calls == 1  # Conservative local attempt, not a HTTP request.
    assert result.counters.input_tokens == 0 and result.counters.output_tokens == 0


def test_directory_permission_cannot_dispatch_query_or_reserve_cost(tmp_path):
    from project_evidence.agent_protocol import OUTLINE_GOAL
    from test_learning_engine import anchor
    adapter, ledger = setup(tmp_path, lambda request: pytest.fail("New-purpose request was sent"))
    query = ModelContext(commit="a"*40, public_url="https://github.com/example/public-java", goal=OUTLINE_GOAL, anchor=anchor())
    with pytest.raises(ModelError) as error:
        adapter.generate(query, timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "not_authorized" and error.value.request_sent is False
    with pytest.raises(ValueError): adapter.for_task("new-task", goal=OUTLINE_GOAL)
    assert ledger.summary("first-trial")["encumbered_microyuan"] == 0
    with ledger.store._connection() as c: assert c.execute("SELECT count(*) FROM model_spend").fetchone()[0] == 0


def test_query_opt_in_uses_only_selected_goal_schema_and_same_ledger(tmp_path):
    from project_evidence.agent_protocol import OUTLINE_GOAL
    from test_learning_engine import anchor
    directory, ledger = setup(tmp_path, lambda request: httpx.Response(200, json=envelope()))
    adapter = DeepSeekAdapter(KEY, ledger, "first-trial", transport=directory._transport, enable_query=True).for_task("query-task", goal=OUTLINE_GOAL)
    query = ModelContext(commit="a"*40, public_url="https://github.com/example/public-java", goal=OUTLINE_GOAL, anchor=anchor())
    messages = adapter._messages(query)
    assert '"chapter_outline"' in messages[0]["content"] and '"SectionExplanation"' not in messages[0]["content"]
    assert '"DirectoryOutput"' not in messages[0]["content"]
    adapter.generate(query, timeout_seconds=5, max_output_tokens=4000)
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 28000
    assert ledger.summary("first-trial")["trial_limit_microyuan"] == 5000000
    with ledger.store._connection() as c: assert c.execute("SELECT count(*) FROM model_grants").fetchone()[0] == 1


@pytest.mark.parametrize("mode", ["timeout", "redirect", "server_error", "bad_envelope", "wrong_model", "missing_usage", "oversize"])
def test_unpriced_response_keeps_hold_and_prevents_retry(tmp_path, mode):
    calls = []
    def respond(request):
        calls.append(request)
        if mode == "timeout": raise httpx.ReadTimeout("synthetic secret-bearing error", request=request)
        if mode == "redirect": return httpx.Response(302, headers={"Location": "https://example.invalid/collect"})
        if mode == "server_error": return httpx.Response(503, text=KEY)
        if mode == "bad_envelope": return httpx.Response(200, text="invalid JSON " + KEY)
        if mode == "oversize": return httpx.Response(200, content=b"x" * 262145)
        data = envelope()
        if mode == "wrong_model": data["model"] = "other-price-model"
        if mode == "missing_usage": data.pop("usage")
        return httpx.Response(200, json=data)
    adapter, ledger = setup(tmp_path, respond)
    for _ in range(2):
        with pytest.raises(ModelError) as error:
            adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
        assert error.value.code == "cost_uncertain" and KEY not in str(error.value)
        assert error.value.retryable is False
    assert len(calls) == 1 and ledger.summary("first-trial")["encumbered_microyuan"] == 2_232_000


@pytest.mark.parametrize("mode", ["empty", "bad_json", "model_usage", "length", "thoughts", "invalid_tool"])
def test_bad_action_still_accounts_known_provider_usage(tmp_path, mode):
    def respond(request):
        data = envelope()
        message = data["choices"][0]["message"]
        if mode == "empty": message["content"] = ""
        if mode == "bad_json": message["content"] = "not JSON " + KEY
        if mode == "model_usage": message["content"] = json.dumps({"kind": "final", "usage": {"input_tokens": 0}})
        if mode == "length": data["choices"][0]["finish_reason"] = "length"
        if mode == "thoughts": message["reasoning_content"] = "private internal reasoning"
        if mode == "invalid_tool": message["content"] = json.dumps({"kind": "tools", "tool_calls": [{"id": "bad", "name": "shell", "arguments": {}}]})
        return httpx.Response(200, json=data)
    adapter, ledger = setup(tmp_path, respond)
    result = run_agent(adapter, lambda *x: pytest.fail("No tools should run"), lambda: None, lambda: False,
                       initial_context=context())
    assert result.status in {"failed", "budget_exhausted"}
    assert result.counters.reported_input_tokens == 10000 and result.counters.reported_output_tokens == 1000
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 28_000
    assert KEY not in result.model_dump_json() and "private internal reasoning" not in result.model_dump_json()


def test_cancel_after_response_keeps_usage_but_never_publishes(tmp_path):
    cancelled = [False]
    def respond(request):
        cancelled[0] = True
        return httpx.Response(200, json=envelope())
    adapter, ledger = setup(tmp_path, respond)
    result = run_agent(adapter, lambda *x: None, lambda: None, lambda: cancelled[0], initial_context=context())
    assert result.status == "cancelled" and result.final_output is None
    assert result.counters.reported_output_tokens == 1000
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 28_000


def test_schema_rejection_records_only_fixed_labels_and_keeps_receipt(tmp_path):
    private_value = "synthetic-rejected-content-never-log"
    data = {"kind": "final", "final_output": {"kind": "directory", "overview": "fixture",
        "chapters": [{"title": "fixture", "summary": "fixture", "kind": "backend",
                      "support": private_value, "evidence_ids": []}], "gaps": ["fixture gap"]}}
    def respond(request):
        return httpx.Response(200, json=envelope(json.dumps(data)))
    adapter, ledger = setup(tmp_path, respond)
    result = run_agent(adapter, lambda *x: pytest.fail("No tool should run"), lambda: None,
                       lambda: False, initial_context=context())
    logged = result.model_dump_json()
    assert result.status == "failed" and result.final_output is None
    assert "support.literal_error" in logged and private_value not in logged
    assert "模型响应停止：invalid_response" in logged
    assert result.counters.reported_output_tokens == 1000
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 28_000


def test_diagnostic_labels_cannot_carry_arbitrary_keys_or_error_messages():
    error = ModelError("invalid_response", diagnostics=("support.literal_error", KEY, "unknown.value_error"))
    assert error.diagnostics == ("support.literal_error",)
    assert KEY not in str(error)


@pytest.mark.parametrize("status", [302, 400, 401, 402, 403, 418, 422, 429, 500, 503])
def test_non_200_retains_only_http_status_without_reading_body_releasing_hold_or_retrying(tmp_path, status):
    calls = []
    class UnreadableBody(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Non-200 response body must not be read, including credential echo")
    def respond(request):
        calls.append(request)
        return httpx.Response(status, stream=UnreadableBody(), headers={"x-request-id": KEY, "location": "https://example.invalid/" + KEY})
    adapter, ledger = setup(tmp_path, respond)
    result = run_agent(adapter, lambda *x: pytest.fail("No tools should run"), lambda: None, lambda: False,
                       initial_context=context())
    logged = result.model_dump_json()
    assert result.status == "failed" and result.final_output is None
    assert f"response.http_{status}" in logged and "response.non_200" in logged
    assert KEY not in logged and "example.invalid" not in logged
    assert result.counters.model_calls == 1 and result.counters.retries == 0
    assert result.counters.reported_input_tokens == 0 and result.counters.reported_output_tokens == 0
    assert len(calls) == 1
    summary = ledger.summary("first-trial")
    assert summary["status"] == "cost_uncertain" and summary["encumbered_microyuan"] == 2232000
    with pytest.raises(ModelError) as error:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "cost_uncertain" and len(calls) == 1


def test_http_diagnostic_cannot_export_arbitrary_status_text():
    error = ModelError("cost_uncertain", diagnostics=("response.http_401", "response.http_401-secret", "response.http_999", "response.http_other"))
    assert error.diagnostics == ("response.http_401", "response.http_other")


def test_read_timeout_uses_remaining_task_time_and_keeps_short_connect_limit(tmp_path, monkeypatch):
    original_client = httpx.Client
    clients = []
    def checked_client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert isinstance(kwargs["transport"], httpx.MockTransport)
        clients.append(kwargs)
        return original_client(**kwargs)
    monkeypatch.setattr(adapter_module.httpx, "Client", checked_client)
    def respond(request):
        assert request.extensions["timeout"] == {"connect": 5, "read": 27, "write": 27, "pool": 27}
        return httpx.Response(200, json=envelope())
    adapter, _ = setup(tmp_path, respond)
    adapter.generate(context(), timeout_seconds=27, max_output_tokens=4000)
    assert len(clients) == 1


@pytest.mark.parametrize("mode,label", [("choices", "response.choices_invalid"),
    ("finish", "response.finish_reason_invalid"), ("reasoning", "response.unexpected_reasoning"),
    ("native_tools", "response.native_tool_calls"), ("empty", "response.empty_content"),
    ("json", "action.json_invalid"), ("array", "action.not_object"), ("usage", "action.model_usage")])
def test_known_usage_rejections_have_fixed_branch_labels(tmp_path, mode, label):
    def respond(request):
        data = envelope(); message = data["choices"][0]["message"]
        if mode == "choices": data["choices"] = []
        if mode == "finish": data["choices"][0]["finish_reason"] = "unexpected"
        if mode == "reasoning": message["reasoning_content"] = KEY
        if mode == "native_tools": message["tool_calls"] = [{"private": KEY}]
        if mode == "empty": message["content"] = ""
        if mode == "json": message["content"] = KEY
        if mode == "array": message["content"] = "[]"
        if mode == "usage": message["content"] = json.dumps({"usage": {"private": KEY}})
        return httpx.Response(200, json=data)
    adapter, ledger = setup(tmp_path, respond)
    with pytest.raises(ModelError) as error:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    expected = (label, "json.value") if mode == "json" else (label,)
    assert error.value.code == "invalid_response" and error.value.diagnostics == expected
    assert error.value.usage.input_tokens == 10000 and KEY not in str(error.value)
    assert ledger.summary("first-trial")["unresolved_requests"] == 0


@pytest.mark.parametrize("content,label", [
    ('\ufeff{"private":"' + KEY + '"}', "json.bom"),
    ('```json\n{"private":"' + KEY + '"}\n```', "json.fence"),
    ('{"private":"' + KEY + '"} {}', "json.extra_data"),
    ('{"private":"' + KEY + r'\q"}', "json.invalid_escape"),
    ('{"private":"' + KEY + r'\uZZZZ"}', "json.invalid_escape"),
    ('{"private":"' + KEY + '\n"}', "json.control_character"),
    ('{"private":"' + KEY, "json.unterminated_string"),
    ('{"private":"' + KEY + '",}', "json.property_name"),
    ('{"private" "' + KEY + '"}', "json.colon"),
    ('{"private":"' + KEY + '" "next":1}', "json.delimiter"),
    ('{"private":"' + KEY + '","next":}', "json.value"),
])
def test_json_syntax_rejections_preserve_receipt_without_exporting_content(tmp_path, content, label):
    adapter, ledger = setup(tmp_path, lambda request: httpx.Response(200, json=envelope(content)))
    result = run_agent(adapter, lambda *args: pytest.fail("Malformed JSON cannot invoke a tool"),
                       lambda: None, lambda: False, initial_context=context())
    logged = result.model_dump_json()
    assert result.status == "failed" and result.final_output is None
    assert "action.json_invalid" in logged and label in logged
    assert KEY not in logged and "private" not in logged and content not in logged
    assert result.counters.reported_input_tokens == 10000
    assert result.counters.reported_output_tokens == 1000
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 28000
    assert ledger.summary("first-trial")["unresolved_requests"] == 0


@pytest.mark.parametrize("mode,label", [("timeout", "transport.timeout"),
    ("network", "transport.http_error"), ("receipt", "receipt.invalid"),
    ("envelope", "envelope.invalid"), ("status", "response.non_200")])
def test_unresolved_failure_has_fixed_diagnostic_without_private_content(tmp_path, mode, label):
    def respond(request):
        if mode == "timeout": raise httpx.ReadTimeout(KEY, request=request)
        if mode == "network": raise httpx.ConnectError(KEY, request=request)
        if mode == "status": return httpx.Response(503, text=KEY)
        if mode == "envelope": return httpx.Response(200, text=KEY)
        data = envelope(); data.pop("usage")
        return httpx.Response(200, json=data)
    adapter, ledger = setup(tmp_path, respond)
    with pytest.raises(ModelError) as error:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    expected = (label, "response.http_503") if mode == "status" else (label,)
    if mode == "timeout":
        expected = (label, "transport.read_timeout", "transport.awaiting_headers")
    assert error.value.code == "cost_uncertain" and error.value.diagnostics == expected
    assert KEY not in str(error.value) and error.value.retryable is False
    assert ledger.summary("first-trial")["unresolved_requests"] == 1


@pytest.mark.parametrize("error_type,label", [
    (httpx.ConnectTimeout, "transport.connect_timeout"),
    (httpx.ReadTimeout, "transport.read_timeout"),
    (httpx.WriteTimeout, "transport.write_timeout"),
    (httpx.PoolTimeout, "transport.pool_timeout"),
    (httpx.TimeoutException, "transport.timeout_other"),
])
def test_timeout_subtype_before_headers_is_safe_unpriced_and_never_retried(tmp_path, error_type, label):
    calls = []
    def respond(request):
        calls.append(request)
        raise error_type(KEY, request=httpx.Request("POST", "https://example.invalid/" + KEY))
    adapter, ledger = setup(tmp_path, respond)
    outcome = run_agent(adapter, lambda *args: pytest.fail("No source tools expected"),
                        lambda: None, lambda: False, initial_context=context())
    assert outcome.status == "failed" and outcome.final_output is None
    errors = [event for event in outcome.events if event["type"] == "model_error"]
    assert len(errors) == 1
    assert errors[0]["summary"].endswith(
        "transport.timeout, " + label + ", transport.awaiting_headers")
    assert "response.http_200" not in outcome.model_dump_json()
    assert KEY not in outcome.model_dump_json() and "example.invalid" not in outcome.model_dump_json()
    assert outcome.counters.model_calls == 1 and outcome.counters.retries == 0
    assert outcome.counters.tool_calls == 0 and outcome.counters.reported_output_tokens == 0
    assert len(calls) == 1
    summary = ledger.summary("first-trial")
    assert summary["status"] == "cost_uncertain" and summary["encumbered_microyuan"] == 2232000
    with pytest.raises(ModelError) as blocked:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert blocked.value.code == "cost_uncertain" and len(calls) == 1


@pytest.mark.parametrize("partial", [False, True])
def test_body_read_timeout_retains_observed_200_but_not_partial_body_or_headers(tmp_path, partial):
    class TimeoutBody(httpx.SyncByteStream):
        def __iter__(self):
            if partial:
                yield KEY.encode()
            raise httpx.ReadTimeout(KEY)
    adapter, ledger = setup(tmp_path, lambda request: httpx.Response(
        200, stream=TimeoutBody(), headers={"x-request-id": KEY}))
    with pytest.raises(ModelError) as error:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert error.value.diagnostics == ("transport.timeout", "transport.read_timeout",
                                       "transport.reading_body", "response.http_200")
    assert error.value.code == "cost_uncertain" and error.value.usage is None
    assert not error.value.retryable and KEY not in str(error.value)
    assert ledger.summary("first-trial")["unresolved_requests"] == 1
    assert ledger.summary("first-trial")["encumbered_microyuan"] == 2232000


def test_timeout_diagnostic_whitelist_rejects_arbitrary_suffixes():
    error = ModelError("cost_uncertain", diagnostics=("transport.read_timeout",
        "transport.read_timeout." + KEY, "transport.awaiting_headers",
        "transport.reading_body." + KEY, "transport.timeout_other", KEY))
    assert error.diagnostics == ("transport.read_timeout", "transport.awaiting_headers",
                                 "transport.timeout_other")


@pytest.mark.parametrize("cleanup,stage", [
    ("response", "transport.reading_body"),
    ("client", "transport.response_complete"),
])
def test_cleanup_timeout_reports_actual_local_progress(tmp_path, cleanup, stage):
    class CloseTimeoutBody(httpx.SyncByteStream):
        def __iter__(self):
            yield json.dumps(envelope()).encode()
        def close(self):
            raise httpx.ReadTimeout(KEY)
    def respond(request):
        return (httpx.Response(200, stream=CloseTimeoutBody()) if cleanup == "response"
                else httpx.Response(200, json=envelope()))
    class CloseTimeoutTransport(httpx.MockTransport):
        def close(self):
            raise httpx.ReadTimeout(KEY)
    adapter, ledger = setup(tmp_path, respond)
    if cleanup == "client":
        adapter._transport = CloseTimeoutTransport(respond)
    with pytest.raises(ModelError) as error:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert error.value.diagnostics == ("transport.timeout", "transport.read_timeout",
                                       stage, "response.http_200")
    assert error.value.usage is None and KEY not in str(error.value)
    assert ledger.summary("first-trial")["unresolved_requests"] == 1


def test_chunk_returned_after_deadline_is_never_parsed_as_a_success(tmp_path, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(adapter_module, "monotonic", lambda: clock["now"])
    class LateBody(httpx.SyncByteStream):
        def __iter__(self):
            clock["now"] = 6.0  # Cooperative check after a blocking read returns.
            yield json.dumps(envelope()).encode()
    adapter, ledger = setup(tmp_path, lambda request: httpx.Response(200, stream=LateBody()))
    with pytest.raises(ModelError) as error:
        adapter.generate(context(), timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "cost_uncertain" and error.value.usage is None
    assert error.value.diagnostics == ("response.size_or_deadline",)
    assert ledger.summary("first-trial")["unresolved_requests"] == 1


def test_guidance_updates_apply_only_to_new_tasks_and_have_auditable_identity(tmp_path, monkeypatch):
    guidance = tmp_path / "directory.txt"
    guidance.write_text("fixture version A", encoding="utf-8")
    monkeypatch.setattr(adapter_module, "GUIDANCE_PATH", guidance)
    instructions = []
    def respond(request):
        instructions.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, json=envelope())
    first, ledger = setup(tmp_path, respond)
    guidance.write_text("fixture version B", encoding="utf-8")
    second = first.for_task("next-task")
    assert first.guidance_sha256 != second.guidance_sha256
    for adapter in [first, second]:
        result = run_agent(adapter, lambda *x: None, lambda: None, lambda: False, initial_context=context())
        assert result.events[0]["details"]["guidance_sha256"] == adapter.guidance_sha256
        assert result.status == "gap"
    assert "fixture version A" in instructions[0] and "fixture version B" not in instructions[0]
    assert "fixture version B" in instructions[1]
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 56_000


@pytest.mark.parametrize("text", ["x" * 12001, "api_key = synthetic-must-never-leak"])
def test_invalid_guidance_blocks_new_task_without_leaking_content(tmp_path, monkeypatch, text):
    guidance = tmp_path / "directory.txt"
    guidance.write_text(text, encoding="utf-8")
    monkeypatch.setattr(adapter_module, "GUIDANCE_PATH", guidance)
    with pytest.raises(ValueError) as error:
        setup(tmp_path, lambda *x: pytest.fail("No network request"))
    assert text not in str(error.value) and "synthetic-must-never-leak" not in str(error.value)
