"""Scripted orchestration tests; these are not real model quality results."""

import json

import pytest

import project_evidence.agent_engine as engine_module
from project_evidence.agent_engine import run_agent
from project_evidence.agent_protocol import (
    AgentBudget, ModelContext, ModelError, ModelTurn, ScriptedAdapter,
    UnconfiguredAdapter,
)
from project_evidence.source_policy import SourceError


CONTEXT = {"commit": "a" * 40, "public_url": "https://github.com/example/public-java"}


def tool_turn(name="read_excerpt", arguments=None, identifier="call-1"):
    return {
        "kind": "tools",
        "tool_calls": [{"id": identifier, "name": name, "arguments": arguments or {"path": "src/Example.java"}}],
    }


def gap_turn():
    return {"kind": "final", "final_output": {
        "kind": "gap", "message": "有限范围内证据不足，不能判断。", "gaps": ["缺少实现文件。"],
    }}


def directory_turn(evidence_id="evidence-1", support="fact"):
    return {"kind": "final", "final_output": {
        "kind": "directory", "overview": "这是有证据入口的候选学习目录。",
        "chapters": [{
            "title": "查询入口", "summary": "查看公开入口代码及返回类型。",
            "modules": ["api"], "entries": ["src/Example.java"], "kind": "backend",
            "support": support, "evidence_ids": [evidence_id] if evidence_id else [],
        }], "gaps": ["其他章节还没有足够证据。"],
    }}


def excerpt(evidence_id="evidence-1", text="class Example {}", path="src/Example.java"):
    return {
        "status": "ok", "commit": CONTEXT["commit"], "public_url": CONTEXT["public_url"],
        "path": path, "blob_oid": "b" * 40, "sha256": "c" * 64,
        "start_line": 1, "end_line": 1, "excerpt": text, "text": text,
        "truncated": False, "evidence_id": evidence_id,
    }


def run(adapter, tools=None, guard=lambda: None, cancelled=lambda: False, limits=None,
        record_event=None, context=CONTEXT):
    return run_agent(adapter, tools or (lambda name, args: excerpt()), guard, cancelled,
                     limits, record_event, context)


def test_unconfigured_model_does_not_touch_source_or_tools():
    outcome = run(UnconfiguredAdapter(),
                  tools=lambda *args: pytest.fail("Unconfigured model invoked a tool"),
                  guard=lambda: pytest.fail("Unconfigured model read a source session"))
    assert outcome.status == "not_configured"
    assert outcome.counters.model_calls == outcome.counters.tool_calls == 0
    assert outcome.final_output is None


def test_large_navigation_is_projected_without_becoming_citable_or_losing_limits():
    def inspect(context):
        result = context.observations[0].result
        assert len(result["items"]) == 20 and result["truncated"] is True
        assert all(set(item) == {"path"} for item in result["items"])
        assert result["commit"] == CONTEXT["commit"] and result["projection_notice"]
        return gap_turn()
    source = {"status": "ok", **CONTEXT, "items": [
        {"path": f"src/Class{i}.java", "blob_oid": "b" * 40, "size": 1000} for i in range(100)],
        "truncated": False}
    outcome = run(ScriptedAdapter([tool_turn("find_files", {"pattern": "*.java", "limit": 100}), inspect]),
                  tools=lambda *args: source)
    assert outcome.status == "gap" and outcome.evidence == []
    assert len(source["items"]) == 100  # Direct UI/tool output remains intact.
    assert outcome.counters.context_chars < 6000


def test_projection_checks_even_paths_outside_displayed_prefix():
    source = {"status": "ok", "items": [{"path": f"src/Class{i}.java"} for i in range(30)]}
    source["items"][-1]["path"] = "docs/M2-NEXT-CHAT-HANDOFF.md"
    outcome = run(ScriptedAdapter([tool_turn("find_files", {"pattern": "*.java", "limit": 100})]),
                  tools=lambda *args: source)
    assert outcome.status == "denied" and outcome.final_output is None


def test_valid_directory_uses_only_this_tasks_read_evidence_and_safe_events():
    events = []
    adapter = ScriptedAdapter([tool_turn(), directory_turn()])
    outcome = run(adapter, record_event=events.append)
    assert outcome.status == "completed"
    assert outcome.adapter_kind == outcome.mode == "scripted"
    assert outcome.semantic_verified is False
    assert outcome.citations_validated is True
    assert outcome.evidence[0].path == "src/Example.java"
    assert outcome.evidence[0].commit == CONTEXT["commit"]
    assert outcome.evidence[0].sha256 == "c" * 64
    serialized_events = json.dumps(events, ensure_ascii=False)
    assert "class Example {}" not in serialized_events
    assert "overview" not in serialized_events
    requested = next(event for event in events if event["type"] == "tool_requested")
    assert requested["details"]["arguments"]["path"] == "src/Example.java"
    assert requested["counters"]["tool_calls"] == 1
    accepted = next(event for event in events if event["type"] == "tool_feedback_accepted")
    assert accepted["details"]["evidence_id"] == "evidence-1"
    assert outcome.counters.reported_input_tokens == outcome.counters.reported_output_tokens == 0
    assert outcome.counters.estimated_input_tokens > 0
    assert outcome.counters.estimated_output_tokens > 0


def test_missing_file_can_prompt_one_bounded_supplement_then_honest_gap():
    adapter = ScriptedAdapter([
        tool_turn(), tool_turn("search_text", {"query": "Example"}, "call-2"), gap_turn(),
    ])
    outcome = run(adapter, tools=lambda name, args: {
        "status": "not_found" if name == "read_excerpt" else "no_results", "items": [],
    })
    assert outcome.status == "gap"
    assert outcome.counters.tool_calls == 2
    assert outcome.evidence == []


def test_empty_results_do_not_allow_unbounded_different_searches():
    steps = [tool_turn("search_text", {"query": name}, f"call-{index}") for index, name in enumerate(["First", "Second", "Third"])]
    outcome = run(ScriptedAdapter(steps), tools=lambda *args: {"status": "no_results", "items": []})
    assert outcome.status == "no_progress"
    assert outcome.counters.tool_calls == 2


def test_duplicate_request_with_new_evidence_id_is_still_no_progress():
    ids = iter(["evidence-1", "evidence-2"])
    adapter = ScriptedAdapter([tool_turn(), tool_turn(identifier="call-2"), directory_turn()])
    outcome = run(adapter, tools=lambda *args: excerpt(evidence_id=next(ids)))
    assert outcome.status == "no_progress"
    assert adapter.calls == 2
    assert len(outcome.evidence) == 2  # Both saved historical receipts are retained.


def test_temporary_model_errors_retry_at_most_two_without_resetting_calls():
    adapter = ScriptedAdapter([ModelError("temporary_error", True), ModelError("temporary_error", True), gap_turn()])
    outcome = run(adapter, limits=AgentBudget(max_retries=2))
    assert outcome.status == "gap"
    assert adapter.calls == outcome.counters.model_calls == 3
    assert outcome.counters.retries == 2
    failing = run(ScriptedAdapter([ModelError("temporary_error", True)] * 4), limits=AgentBudget(max_retries=2))
    assert failing.status == "failed"
    assert failing.counters.model_calls == 3
    assert failing.counters.retries == 2


def test_temporary_tool_retry_keeps_the_same_validated_arguments():
    attempts = []

    def tools(name, arguments):
        attempts.append((name, arguments))
        return {"status": "temporary_error"} if len(attempts) == 1 else excerpt()

    outcome = run(ScriptedAdapter([tool_turn(), directory_turn()]), tools=tools)
    assert outcome.status == "completed"
    assert attempts[0] == attempts[1]
    assert outcome.counters.tool_calls == 2
    assert outcome.counters.retries == 1


@pytest.mark.parametrize("status", ["denied", "invalid_argument", "invalid_query"])
def test_refusals_and_invalid_arguments_do_not_get_parameter_bypass_retry(status):
    adapter = ScriptedAdapter([tool_turn(), tool_turn(arguments={"path": "src/Other.java"}, identifier="other")])
    outcome = run(adapter, tools=lambda *args: {"status": status, "message": "synthetic-private-error"})
    assert outcome.status == ("denied" if status == "denied" else "failed")
    assert adapter.calls == outcome.counters.tool_calls == 1
    assert outcome.counters.retries == 0
    assert "synthetic-private-error" not in json.dumps(outcome.model_dump())


@pytest.mark.parametrize("identifier,support", [("historical-foreign-id", "fact"), (None, "candidate")])
def test_directory_cannot_use_foreign_or_missing_evidence(identifier, support):
    outcome = run(ScriptedAdapter([directory_turn(identifier, support)]))
    assert outcome.status == "failed"
    assert outcome.final_output is None


def test_search_hits_cannot_mint_a_read_evidence_reference():
    hit = {"path": "src/Example.java", "line": 1, "excerpt": "class Example {}", "sha256": "c" * 64}
    outcome = run(ScriptedAdapter([
        tool_turn("search_text", {"query": "Example"}), directory_turn(),
    ]), tools=lambda *args: {"status": "ok", "items": [hit], "evidence_id": "evidence-1"})
    assert outcome.status == "failed"
    assert outcome.evidence == []


@pytest.mark.parametrize("call", [
    {"id": "unsafe", "name": "shell", "arguments": {"command": "synthetic-command"}},
    {"id": "unsafe", "name": "read_excerpt", "arguments": {"path": "../Example.java"}},
    {"id": "unsafe", "name": "read_excerpt", "arguments": {"path": "src/Example.java", "write": True}},
])
def test_injected_or_out_of_contract_tools_are_never_executed(call):
    outcome = run(ScriptedAdapter([{"kind": "tools", "tool_calls": [call]}]),
                  tools=lambda *args: pytest.fail("Injected tool executed"))
    assert outcome.status == "failed"
    assert outcome.counters.tool_calls == 0


def test_cancellation_before_start_wins_over_source_failure():
    outcome = run(ScriptedAdapter([gap_turn()]), cancelled=lambda: True,
                  guard=lambda: pytest.fail("Cancelled task inspected source"))
    assert outcome.status == "cancelled"
    assert outcome.counters.model_calls == 0


def test_pending_attempt_is_persisted_before_dispatch_and_can_be_cancelled():
    flag = {"cancelled": False}
    events = []
    adapter = ScriptedAdapter([gap_turn()])

    def record(event):
        events.append(event)
        if event["type"] == "model_requested":
            flag["cancelled"] = True

    outcome = run(adapter, cancelled=lambda: flag["cancelled"], record_event=record)
    assert outcome.status == "cancelled"
    assert outcome.counters.model_calls == 1  # Conservative pending attempt.
    assert adapter.calls == 0  # No adapter invocation or billed receipt.
    assert next(event for event in events if event["type"] == "model_requested")["counters"]["model_calls"] == 1


class TestReceiptAdapter:
    """Only simulates the receipt branch; it has no provider or network code."""
    __test__ = False
    configured = True
    adapter_kind = "real"
    mode = "real"

    def __init__(self, on_return=lambda: None, malformed=False):
        self.on_return, self.malformed = on_return, malformed
        self.calls = 0

    def generate(self, context, *, timeout_seconds, max_output_tokens):
        self.calls += 1
        data = gap_turn()
        data["usage"] = {"mode": "real", "input_tokens": 30, "output_tokens": 7}
        if self.malformed:
            data["thoughts"] = "synthetic-private-internal-text"
        self.on_return()
        return data if self.malformed else ModelTurn.model_validate(data)


@pytest.mark.parametrize("malformed", [False, True])
def test_cancel_after_inflight_return_keeps_receipt_without_releasing_output(malformed):
    flag = {"cancelled": False}
    events = []
    adapter = TestReceiptAdapter(on_return=lambda: flag.update(cancelled=True), malformed=malformed)
    outcome = run(adapter, cancelled=lambda: flag["cancelled"], record_event=events.append)
    assert outcome.status == "cancelled"
    assert outcome.final_output is None
    assert outcome.counters.reported_input_tokens == outcome.counters.input_tokens == 30
    assert outcome.counters.reported_output_tokens == outcome.counters.output_tokens == 7
    assert outcome.counters.estimated_input_tokens == outcome.counters.estimated_output_tokens == 0
    assert events[-1]["counters"]["reported_output_tokens"] == 7
    assert "synthetic-private-internal-text" not in json.dumps(outcome.model_dump())


def test_missing_receipt_in_a_real_branch_is_explicitly_estimated():
    adapter = TestReceiptAdapter()
    adapter.generate = lambda *args, **kwargs: ModelTurn.model_validate(gap_turn())
    outcome = run(adapter)
    assert outcome.status == "gap"
    assert outcome.counters.reported_input_tokens == outcome.counters.reported_output_tokens == 0
    assert outcome.counters.estimated_input_tokens > 0
    assert outcome.counters.estimated_output_tokens > 0
    assert outcome.counters.input_tokens_estimated and outcome.counters.output_tokens_estimated


@pytest.mark.parametrize("code", ["unverified", "source_changed"])
def test_guard_failure_prevents_sending_context(code):
    adapter = ScriptedAdapter([gap_turn()])

    def guard():
        raise SourceError(code, "synthetic-private-exception")

    outcome = run(adapter, guard=guard)
    assert outcome.status == code
    assert adapter.calls == 0
    assert "synthetic-private-exception" not in json.dumps(outcome.model_dump())


def test_tool_return_race_keeps_old_evidence_but_prevents_more_model_sends():
    state = {"changed": False}
    adapter = ScriptedAdapter([tool_turn(), directory_turn()])

    def guard():
        if state["changed"]:
            raise SourceError("source_changed", "version race")

    def tools(*args):
        state["changed"] = True
        return excerpt()

    outcome = run(adapter, tools=tools, guard=guard)
    assert outcome.status == "source_changed"
    assert adapter.calls == 1
    assert outcome.evidence[0].evidence_id == "evidence-1"
    assert outcome.final_output is None


def test_final_publication_callback_race_is_guarded():
    state = {"changed": False}

    def record(event):
        if event["type"] == "final_validated":
            state["changed"] = True

    def guard():
        if state["changed"]:
            raise SourceError("source_changed", "version race")

    outcome = run(ScriptedAdapter([tool_turn(), directory_turn()]), guard=guard, record_event=record)
    assert outcome.status == "source_changed"
    assert outcome.final_output is None
    assert outcome.citations_validated is False


def test_model_and_tool_call_budgets_stop_before_the_next_invocation():
    model_limited = run(ScriptedAdapter([tool_turn(), directory_turn()]), limits=AgentBudget(max_turns=1))
    assert model_limited.status == "budget_exhausted"
    assert model_limited.counters.model_calls == model_limited.counters.tool_calls == 1
    turn = tool_turn()
    turn["tool_calls"].append({"id": "call-2", "name": "read_excerpt", "arguments": {"path": "src/Other.java"}})
    calls = []

    def tools(name, arguments):
        calls.append(arguments)
        return excerpt()

    tool_limited = run(ScriptedAdapter([turn]), tools=tools, limits=AgentBudget(max_tool_calls=1))
    assert tool_limited.status == "budget_exhausted"
    assert len(calls) == tool_limited.counters.tool_calls == 1


@pytest.mark.parametrize("limits", [AgentBudget(max_output_tokens=1), AgentBudget(max_output_chars=200)])
def test_output_budget_rejects_response_instead_of_accepting_truncated_final(limits):
    data = gap_turn()
    data["final_output"]["message"] = "证据不足。" * 50
    outcome = run(ScriptedAdapter([data]), limits=limits)
    assert outcome.status == "budget_exhausted"
    assert outcome.final_output is None


def test_context_budget_stops_before_resending_large_feedback():
    adapter = ScriptedAdapter([tool_turn(), directory_turn()])
    outcome = run(adapter, tools=lambda *args: excerpt(text="// " + "a" * 5000),
                  limits=AgentBudget(max_context_chars=2000))
    assert outcome.status == "budget_exhausted"
    assert adapter.calls == 1


def test_elapsed_timeout_after_cooperative_return_keeps_usage(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["now"])
    adapter = TestReceiptAdapter(on_return=lambda: clock.update(now=2.0))
    outcome = run(adapter, limits=AgentBudget(max_seconds=1.0))
    assert outcome.status == "timeout"
    assert outcome.counters.reported_output_tokens == 7
    assert outcome.counters.elapsed_seconds == 2.0
    assert outcome.final_output is None


@pytest.mark.parametrize("terminal", ["timeout", "cancelled", "source_changed"])
@pytest.mark.parametrize("with_receipt", [False, True])
def test_late_model_error_keeps_safe_diagnostic_without_overriding_terminal_or_retrying(monkeypatch, terminal, with_receipt):
    from project_evidence.agent_protocol import Usage
    clock = {"now": 0.0}; state = {"returned": False}; events = []
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["now"])
    adapter = TestReceiptAdapter()
    calls = []
    def fail(context, **kwargs):
        calls.append(context); state["returned"] = True
        if terminal == "timeout": clock["now"] = 2.0
        raise ModelError("cost_uncertain", retryable=True,
                         usage=Usage(mode="real", input_tokens=11, output_tokens=7) if with_receipt else None,
                         diagnostics=("transport.timeout", "transport.read_timeout",
                                      "transport.awaiting_headers", "synthetic-private-error-text"))
    adapter.generate = fail
    def guard():
        if terminal == "source_changed" and state["returned"]:
            raise SourceError("source_changed", "fixture source changed")
    outcome = run(adapter, cancelled=lambda: terminal == "cancelled" and state["returned"],
                  guard=guard, limits=AgentBudget(max_seconds=1.0), record_event=events.append)
    assert outcome.status == terminal and outcome.final_output is None
    assert len(calls) == 1 and outcome.counters.retries == 0 and outcome.counters.tool_calls == 0
    error_events = [event for event in events if event["type"] == "model_error"]
    assert len(error_events) == 1 and "transport.timeout" in error_events[0]["summary"]
    assert "transport.read_timeout, transport.awaiting_headers" in error_events[0]["summary"]
    assert "synthetic-private-error-text" not in outcome.model_dump_json()
    assert outcome.counters.reported_input_tokens == (11 if with_receipt else 0)
    assert outcome.counters.reported_output_tokens == (7 if with_receipt else 0)
    assert outcome.counters.elapsed_seconds == (2.0 if terminal == "timeout" else 0.0)


def test_initial_context_never_sends_local_paths_or_project_names():
    captured = []

    def step(context):
        captured.append(context)
        return gap_turn()

    initial = ModelContext(**CONTEXT, metadata={"project_name": "internal-business-name"})
    assert run(ScriptedAdapter([step]), context=initial).status == "gap"
    assert captured[0].metadata.project_name is None
    invalid = dict(CONTEXT, source_path="D:\\synthetic-private-project")
    adapter = ScriptedAdapter([gap_turn()])
    assert run(adapter, context=invalid).status == "failed"
    assert adapter.calls == 0


def test_raw_adapter_errors_and_record_failures_are_not_echoed():
    def crash(context):
        raise RuntimeError("synthetic-private-exception")

    outcome = run(ScriptedAdapter([crash]))
    assert outcome.status == "failed"
    assert "synthetic-private-exception" not in json.dumps(outcome.model_dump())

    def recorder(event):
        raise RuntimeError("synthetic-private-record-error")

    outcome = run(ScriptedAdapter([gap_turn()]), record_event=recorder)
    assert outcome.status == "failed"
    assert outcome.counters.model_calls == 0
    assert "synthetic-private-record-error" not in json.dumps(outcome.model_dump())
