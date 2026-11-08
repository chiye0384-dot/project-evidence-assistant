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


def _native_table_plan(active, *, unsupported=False):
    return dict(kind='citation_plan', topic=active['topic'], section_id=active['section_id'],
        title='合成追问', gaps=[], blocks=[dict(category='gap' if unsupported else 'processing',
            support='gap' if unsupported else 'fact',
            source_lines=[] if unsupported else [dict(evidence_id='piece-a', line_numbers='10,11,12')],
            evidence_order=[] if unsupported else [1],
            evidence_plan=dict(claim_kind='gap' if unsupported else 'source',
                anchors=[] if unsupported else [dict(role='source', reference_index=1, line_index=1)]),
            claim_parts=[dict(kind='text', text='暂缺调用方证据。' if unsupported else '第10行定义base。')])])


@pytest.mark.parametrize('prose_format,problem', [
    ('kind_prose_v1', None), ('flat_prose_v1', None),
    ('flat_prose_v1', 'wrong_index'), ('flat_prose_v1', 'bool_index'),
    ('flat_prose_v1', 'extra'), ('flat_prose_v1', 'nested_legacy'),
    ('flat_prose_v1', 'missing'), ('flat_prose_v1', 'long_text'),
    ('flat_prose_v1', 'unselected_line'), ('flat_prose_v1', 'gap_line'),
])
def test_native_two_phase_indexed_transport_keeps_billing_and_rejections(tmp_path, prose_format, problem):
    from project_evidence.followup_prose_kind import FLAT_NOTICE, NOTICE
    requests = []
    def respond(request):
        assert request.method == 'POST' and str(request.url) == 'https://api.deepseek.com/beta/chat/completions'
        body = json.loads(request.content)
        assert len(request.content) <= 128_000 and 'synthetic-flat-key' not in request.content.decode()
        requests.append(body)
        wrapper = json.loads(body['messages'][1]['content'])
        if len(requests) == 1:
            action = tool_turn(arguments=dict(path='src/Example.java', start_line=10, end_line=12))
        elif wrapper['phase'] == 'plan_or_tools':
            action = _native_table_plan(wrapper['context']['active_request'], unsupported=problem == 'gap_line')
            if problem == 'gap_line':
                action['blocks'][0]['claim_parts'][0]['text'] = '第13行的调用方尚未知。'
            # Check the actual inlined native schema is tied to this read.
            def refs(node):
                if type(node) is dict:
                    props = node.get('properties', {})
                    if set(props) == {'evidence_id', 'line_numbers'}:
                        assert props['evidence_id']['enum'] == ['piece-a']
                    for child in node.values():refs(child)
                elif type(node) is list:
                    for child in node:refs(child)
            refs(body['tools'][0]['function']['parameters'])
        else:
            flat = prose_format == 'flat_prose_v1'
            assert (FLAT_NOTICE in body['messages'][0]['content']) == flat
            assert (NOTICE in body['messages'][0]['content']) != flat
            block = dict(block_index=1, heading='合成声明', text='第10行定义base。')
            action = dict(kind='prose', blocks=dict(i1=1, h1=block['heading'], t1=block['text']) if flat else dict(block_1=block))
            if problem == 'wrong_index':action['blocks']['i1'] = 2
            if problem == 'bool_index':action['blocks']['i1'] = True
            if problem == 'extra':action['blocks']['t2'] = '多余块'
            if problem == 'nested_legacy':action['blocks'] = dict(block_1=block)
            if problem == 'missing':action['blocks'].pop('h1')
            if problem == 'long_text':action['blocks']['t1'] = '中' * 1201
            if problem == 'unselected_line':action['blocks']['t1'] = '第13行定义base。'
        return httpx.Response(200, json=dict(model='deepseek-flash', object='chat.completion',
            usage=dict(prompt_tokens=10000, completion_tokens=1000, total_tokens=11000),
            choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
                tool_calls=[dict(id='fixture-call', type='function', function=dict(name='submit_action',
                    arguments=json.dumps(dict(action=action), ensure_ascii=False)))]))]))
    ledger = SpendingLedger(ProjectStore(tmp_path / 'native-cost'))
    grant = ledger.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    instance = DeepSeekAdapter('synthetic-flat-key', ledger, grant, enable_query=True, enable_followup=True,
        transport=httpx.MockTransport(respond), followup_protocol='two_phase',
        followup_claim_format='selected_table_v1', followup_prose_format=prose_format,
        followup_scope_view='java_scope_edge_unit_index_columns_v1').for_task('native-flat', goal=FOLLOW_UP_GOAL)
    result = run(instance, tools=lambda *_: observed(), context=followup_context())
    assert result.status == ('completed' if problem is None else 'failed')
    assert not result.semantic_verified and result.counters.retries == 0
    assert len(requests) == result.counters.model_calls == (2 if problem == 'gap_line' else 3)
    with ledger.store._connection() as db:
        rows = list(db.execute('SELECT state,charged FROM model_spend'))
    assert len(rows) == len(requests) and all(row['state'] == 'settled' for row in rows)
    assert sum(row['charged'] for row in rows) == 28_000 * len(requests)
    if problem is None:
        assert result.citations_validated and result.final_output.blocks[0].source_lines[0].lines == [10, 11, 12]
    else:
        assert result.final_output is None


@pytest.mark.parametrize('mode', ['empty', 'complete', 'partial', 'duplicate'])
def test_table_schema_uses_current_complete_reads_and_keeps_gap_text_only(mode):
    from project_evidence.followup_table_contract import schema
    result = observed(truncated=mode == 'partial')
    observations = [] if mode == 'empty' else [observation(result)]
    if mode == 'duplicate': observations.append(observation(result))
    value = schema(followup_context(observations))
    reference = value['$defs']['SourceLineReference']['properties']['evidence_id']
    assert reference.get('enum', []) == (['piece-a'] if mode in ('complete', 'duplicate') else [])
    branches = value['$defs']['FollowUpBlock']['oneOf']
    for branch in branches[1:]:
        item = branch['properties']['claim_parts']['items']
        assert set(item['properties']) == {'kind', 'text'}
        assert item['properties']['kind']['const'] == 'text'
    assert len(branches[0]['properties']['claim_parts']['items']['oneOf']) == 2


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
