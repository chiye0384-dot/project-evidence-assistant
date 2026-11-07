"""Line-provenance guards are not semantic proof. All fixtures are synthetic."""
import copy
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from pydantic import ValidationError

from project_evidence.agent_protocol import FOLLOW_UP_GOAL, ModelContext, ModelError, ScriptedAdapter
from project_evidence.deepseek_adapter import DeepSeekAdapter
from project_evidence.learning_contract import FollowUpExplanation
from project_evidence.spending import SpendingLedger
from project_evidence.storage import ProjectStore
from test_agent_engine import CONTEXT, excerpt, run, tool_turn
from test_followup import answer, followup_anchor, saved_body
from test_learning_core import setup


def observed(eid="piece-a", start=10, end=12, truncated=False):
    result = excerpt(eid, text="int base = 1;\nint next = base;\nuse(next);")
    result.update(start_line=start, end_line=end, truncated=truncated)
    return result


def grounded(eid="piece-a", start=10, end=12):
    result = answer(eid)
    # Saved historical artifacts already contain the default topic. Include it
    # explicitly when checking exact serialization/replay compatibility.
    result["topic"] = "query"
    result["blocks"][0]["source_lines"] = [dict(evidence_id=eid, lines=list(range(start, end + 1)))]
    return result


def followup_context(observations=None):
    return ModelContext(**CONTEXT, goal=FOLLOW_UP_GOAL, anchor=followup_anchor(), observations=observations or [])


@pytest.mark.parametrize("problem", [None, "missing_span", "wrong_range", "unread_id", "truncated"])
def test_new_task_requires_observed_complete_line_ranges_before_publishing(problem):
    result = grounded()
    evidence = observed(truncated=problem == "truncated")
    if problem == "missing_span": result["blocks"][0].pop("source_lines")
    if problem == "wrong_range": result["blocks"][0]["source_lines"][0]["lines"] = [4, 5]
    if problem == "unread_id":
        result["blocks"][0]["evidence_ids"] = ["historical-id"]
        result["blocks"][0]["source_lines"][0]["evidence_id"] = "historical-id"
    outcome = run(ScriptedAdapter([tool_turn(), {"kind": "final", "final_output": result}]),
                  tools=lambda *_: evidence, context=followup_context())
    assert outcome.status == ("completed" if problem is None else "failed")
    assert outcome.semantic_verified is False
    assert outcome.counters.model_calls == 2 and outcome.counters.retries == 0
    if problem is None:
        assert outcome.final_output.blocks[0].source_lines[0].lines == [10, 11, 12]
        assert any(event["type"] == "source_lines_validated" for event in outcome.events)
    else:
        assert outcome.final_output is None and not outcome.citations_validated


def test_cross_fragment_claim_keeps_explicit_separate_spans_without_auto_filling_ids():
    result = grounded()
    result["blocks"][0]["evidence_ids"].append("piece-b")
    result["blocks"][0]["source_lines"].append(dict(evidence_id="piece-b", lines=[30, 31]))
    tools = iter([observed(), observed("piece-b", 30, 32)])
    outcome = run(ScriptedAdapter([tool_turn(identifier="a"), tool_turn(identifier="b"),
                                  {"kind": "final", "final_output": result}]),
                  tools=lambda *_: next(tools), context=followup_context())
    assert outcome.status == "completed" and not outcome.semantic_verified
    assert len(outcome.final_output.blocks[0].source_lines) == 2


@pytest.mark.parametrize("problem", ["reversed", "too_wide", "mismatched_id", "duplicate", "private_field"])
def test_span_contract_rejects_forged_or_unbounded_metadata(problem):
    result = grounded()
    result["blocks"][0].pop("source_lines")
    result["blocks"][0]["source_spans"] = [dict(evidence_id="piece-a", start_line=10, end_line=12)]
    span = result["blocks"][0]["source_spans"][0]
    if problem == "reversed": span.update(start_line=12, end_line=10)
    if problem == "too_wide": span.update(start_line=10, end_line=30)
    if problem == "mismatched_id": span["evidence_id"] = "another-piece"
    if problem == "duplicate": result["blocks"][0]["source_spans"].append(copy.deepcopy(span))
    if problem == "private_field": span["private_path"] = "synthetic-private-marker"
    with pytest.raises(ValidationError): FollowUpExplanation.model_validate(result)


def test_old_followup_roundtrips_unchanged_without_inventing_spans():
    legacy = grounded()
    legacy["blocks"][0].pop("source_lines")
    assert FollowUpExplanation.model_validate(legacy).model_dump() == legacy


@pytest.mark.parametrize("support", ["documentation", "inference", "gap"])
def test_new_supported_blocks_cannot_use_legacy_shape_but_gaps_still_can(support):
    result = grounded(); block = result["blocks"][0]
    block.pop("source_lines"); block["support"] = support
    if support == "gap":
        block["category"] = "gap"; block["evidence_ids"] = []
    outcome = run(ScriptedAdapter([tool_turn(), {"kind": "final", "final_output": result}]),
                  tools=lambda *_: observed(), context=followup_context())
    assert outcome.status == ("completed" if support == "gap" else "failed")
    assert outcome.semantic_verified is False


@pytest.mark.parametrize("legacy", [False, True, "span"])
def test_storage_replay_preserves_old_records_and_checks_new_span_bounds(tmp_path, legacy):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, _ = env
    body = saved_body(env)
    result = grounded(eid, 1, 1)
    if legacy:
        result["blocks"][0].pop("source_lines")
        if legacy == "span":
            result["blocks"][0]["source_spans"] = [dict(evidence_id=eid, start_line=1, end_line=1)]
    task = tasks.create(record, "scripted", {}, kind=FOLLOW_UP_GOAL,
                        inputs={"body_id": body["id"], "question": "说明这条调用。"})
    tasks.update(record.id, task["id"], status="completed", result=result, evidence=[ref])
    first = lessons.save_from_task(record.id, task["id"])
    assert first == lessons.save_from_task(record.id, task["id"])
    assert first["content"] == result and first["quality_verified"] is False
    if not legacy:
        bad_task = tasks.create(record, "scripted", {}, kind=FOLLOW_UP_GOAL,
                               inputs={"body_id": body["id"], "question": "说明这条调用。"})
        bad = grounded(eid, 2, 2)
        with pytest.raises(ValueError):
            tasks.update(record.id, bad_task["id"], status="completed", result=bad, evidence=[ref],
                         publish=lambda c: lessons.save_from_task(record.id, bad_task["id"], transaction=c))
        assert tasks.get(record.id, bad_task["id"])["status"] == "queued"


def adapter(tmp_path):
    ledger = SpendingLedger(ProjectStore(tmp_path / "cost"))
    grant = ledger.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    transport = httpx.MockTransport(lambda _: pytest.fail("Local projection must never dispatch"))
    instance = DeepSeekAdapter("synthetic-unused-key", ledger, grant, enable_query=True, enable_followup=True,
                               transport=transport).for_task("span-fixture", goal=FOLLOW_UP_GOAL)
    return instance, ledger, grant


def observation(result):
    return {"call_id": "read-one", "name": "read_excerpt", "result": result}


def test_model_display_numbers_lines_without_mutating_source_or_metadata(tmp_path):
    instance, _, _ = adapter(tmp_path)
    context = followup_context([observation(observed())]); before = context.model_dump()
    messages = instance._messages(context)
    projected = json.loads(messages[1]["content"])["observations"][0]["result"]
    assert projected["excerpt"] == "10 | int base = 1;\n11 | int next = base;\n12 | use(next);"
    assert projected["evidence_id"] == "piece-a" and projected["start_line"] == 10
    assert context.model_dump() == before
    contract = json.loads(messages[0]["content"][messages[0]["content"].index('{"turn":'):])
    block_schema = contract["turn"]["$defs"]["FollowUpBlock"]
    assert "source_lines" in block_schema["required"]
    assert "source_spans" not in block_schema["properties"]
    assert "SourceSpan" not in contract["turn"]["$defs"]


@pytest.mark.parametrize("problem", ["zero", "wide", "duplicate", "wrong_id", "mixed", "extra", "repeated_id"])
def test_line_contract_rejects_invalid_or_ambiguous_references(problem):
    result = grounded(); block = result["blocks"][0]; reference = block["source_lines"][0]
    if problem == "zero": reference["lines"] = [0]
    if problem == "wide": reference["lines"] = list(range(1, 22))
    if problem == "duplicate": reference["lines"] = [10, 10]
    if problem == "wrong_id": reference["evidence_id"] = "another-piece"
    if problem == "mixed": block["source_spans"] = [dict(evidence_id="piece-a", start_line=10, end_line=12)]
    if problem == "extra": reference["private_path"] = "synthetic-private-marker"
    if problem == "repeated_id": block["source_lines"].append(copy.deepcopy(reference))
    with pytest.raises(ValidationError): FollowUpExplanation.model_validate(result)


def test_new_task_rejects_old_span_format_with_safe_reason_but_retains_history():
    result = grounded(); block = result["blocks"][0]; block.pop("source_lines")
    block["source_spans"] = [dict(evidence_id="piece-a", start_line=10, end_line=12)]
    assert FollowUpExplanation.model_validate(result).model_dump() == result
    outcome = run(ScriptedAdapter([tool_turn(), {"kind": "final", "final_output": result}]),
                  tools=lambda *_: observed(), context=followup_context())
    assert outcome.status == "failed" and outcome.final_output is None
    assert any(event["type"] == "source_lines_rejected" and "legacy_format" in event["summary"]
               for event in outcome.events)


def test_inconsistent_line_metadata_stops_before_transport_and_cost_reservation(tmp_path):
    instance, ledger, grant = adapter(tmp_path)
    evidence = observed(); evidence["end_line"] = 13
    with pytest.raises(ModelError) as error:
        instance.generate(followup_context([observation(evidence)]), timeout_seconds=5, max_output_tokens=4000)
    assert error.value.request_sent is False and error.value.code == "invalid_response"
    assert ledger.summary(grant)["accounted_upper_bound_microyuan"] == 0


def test_numbered_projection_cannot_silently_expand_context_or_create_a_reservation(tmp_path):
    instance, ledger, grant = adapter(tmp_path)
    evidence = observed(start=1, end=120)
    evidence["excerpt"] = "a" + "\nz" * 119
    context = followup_context([observation(evidence)])
    padding = 23_990 - len(json.dumps(context.model_dump(), ensure_ascii=False))
    evidence["excerpt"] = "a" * (1 + padding) + "\nz" * 119
    context = followup_context([observation(evidence)])
    assert len(json.dumps(context.model_dump(), ensure_ascii=False)) <= 24_000
    with pytest.raises(ModelError) as error:
        instance.generate(context, timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "output_limit" and error.value.request_sent is False
    assert ledger.summary(grant)["accounted_upper_bound_microyuan"] == 0
