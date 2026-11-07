"""Submission topic boundaries with synthetic source/model; no real-model score."""
import pytest
from pydantic import ValidationError
from project_evidence.agent_protocol import ModelContext, ScriptedAdapter, OUTLINE_GOAL, SECTION_GOAL, ModelError
from project_evidence.deepseek_adapter import DeepSeekAdapter
from test_learning_core import setup, outline, explanation
from test_learning_engine import anchor
from test_agent_engine import run, tool_turn, CONTEXT
from test_deepseek_adapter import setup as adapter_setup


@pytest.mark.parametrize('goal',[OUTLINE_GOAL, SECTION_GOAL])
def test_wrong_topic_cannot_publish_even_with_valid_same_task_evidence(goal):
    chosen=anchor(goal==SECTION_GOAL); chosen['topic']='submission'
    output=(outline if goal==OUTLINE_GOAL else explanation)('evidence-1')
    result=run(ScriptedAdapter([tool_turn(),{'kind':'final','final_output':output}]),context={**CONTEXT,'goal':goal,'anchor':chosen})
    assert result.status=='failed' and result.final_output is None


def test_submission_outline_and_section_archive_share_topic_and_preserve_query_history(tmp_path):
    projects, record, tasks, lessons, eid, ref, did=setup(tmp_path)
    task=tasks.create(record,'scripted',{},kind='chapter_outline',inputs={'directory_task_id':did,'chapter_index':0,'topic':'submission'})
    result=outline(eid); result['topic']='submission'
    tasks.update(record.id,task['id'],status='completed',result=result,evidence=[ref],publish=lambda c: lessons.save_from_task(record.id,task['id'],transaction=c))
    saved=lessons.list(record)[0]; assert saved['topic']=='submission'
    body=tasks.create(record,'scripted',{},kind='section_explanation',inputs={'outline_id':saved['id'],'section_id':'entry'})
    with pytest.raises(ValueError):
        tasks.update(record.id,body['id'],status='completed',result=explanation(eid),evidence=[ref],publish=lambda c: lessons.save_from_task(record.id,body['id'],transaction=c))
    assert tasks.get(record.id,body['id'])['status']=='queued'
    result=explanation(eid); result['topic']='submission'
    tasks.update(record.id,body['id'],status='completed',result=result,evidence=[ref],publish=lambda c: lessons.save_from_task(record.id,body['id'],transaction=c))
    assert len(lessons.list(record))==2
    assert tasks.get(record.id,did)['kind']=='directory'


def test_query_permission_cannot_send_submission_or_reserve_new_cost(tmp_path):
    query,ledger=adapter_setup(tmp_path,lambda request: pytest.fail('Submission request under query permission'))
    query=DeepSeekAdapter('synthetic-query-only-key',ledger,'first-trial',transport=query._transport,enable_query=True).for_task('query-task',goal=OUTLINE_GOAL)
    chosen=anchor(); chosen['topic']='submission'
    with pytest.raises(ModelError) as error:
        query.generate(ModelContext(**CONTEXT,goal=OUTLINE_GOAL,anchor=chosen),timeout_seconds=5,max_output_tokens=4000)
    assert error.value.code=='not_authorized' and error.value.request_sent is False
    with pytest.raises(ValueError): query.for_task('submission-task',goal=OUTLINE_GOAL,topic='submission')
    assert ledger.summary('first-trial')['encumbered_microyuan']==0


def test_submission_opt_in_does_not_enable_query_and_schema_is_topic_fixed(tmp_path):
    import httpx
    from test_deepseek_adapter import envelope
    base,ledger=adapter_setup(tmp_path,lambda request:httpx.Response(200,json=envelope()))
    submission=DeepSeekAdapter('synthetic-submission-key',ledger,'first-trial',transport=base._transport,enable_submission=True).for_task('submission-task',goal=OUTLINE_GOAL,topic='submission')
    assert submission.allowed_topics=={'submission'}
    chosen=anchor(); chosen['topic']='submission'
    context=ModelContext(**CONTEXT,goal=OUTLINE_GOAL,anchor=chosen)
    assert '"const": "submission"' in submission._messages(context)[0]['content']
    submission.generate(context,timeout_seconds=5,max_output_tokens=4000)
    assert ledger.summary('first-trial')['trial_limit_microyuan']==5000000
    with ledger.store._connection() as c: assert c.execute('SELECT count(*) FROM model_grants').fetchone()[0]==1


def test_unknown_topic_and_section_http_override_are_rejected():
    from project_evidence.learning_contract import OutlineSelection, SectionSelection
    with pytest.raises(ValidationError): OutlineSelection(directory_task_id='d1',chapter_index=0,topic='shell')
    with pytest.raises(ValidationError): SectionSelection(outline_id='o1',section_id='entry',topic='submission')
