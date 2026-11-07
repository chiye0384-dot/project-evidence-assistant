"""Bounded single-question follow-up fixtures; no real supplier calls or quality claims."""
import copy
from datetime import datetime, timedelta, timezone
import json
from threading import Event

from fastapi.testclient import TestClient
import httpx
import pytest
from pydantic import ValidationError

from project_evidence.agent_protocol import FOLLOW_UP_GOAL, ModelContext, ModelError, ScriptedAdapter
from project_evidence.app import create_app
from project_evidence.deepseek_adapter import DeepSeekAdapter, _load_guidance
from project_evidence.learning_contract import FollowUpSelection
from project_evidence.spending import SpendingLedger
from project_evidence.storage import ProjectStore
from test_agent_api import terminal
from test_agent_engine import run, tool_turn, CONTEXT
from responses_fixture import response_envelope
from test_learning_core import setup, complete_outline, explanation
from test_learning_engine import anchor
from test_learning_api import parent, steps, OUTLINE_GOAL, SECTION_GOAL
from test_m0_api import repository, git_exe, register


def followup_anchor():
    return dict(anchor(True), body_id="saved-body", question="这个参数如何检查？", previous_excerpt="这是旧讲解，尚待重新核对。")


def answer(eid="evidence-1"):
    result = dict(explanation(eid), kind=FOLLOW_UP_GOAL)
    result["blocks"][0]["source_lines"] = [{"evidence_id": eid, "lines": [1]}]
    return result


@pytest.mark.parametrize("question", [" ", "x" * 501, "D:/private/file", "api_key = synthetic-private-marker", ".smtp.qq.local"])
def test_unsafe_or_unbounded_question_rejected_before_storage(question):
    with pytest.raises(ValidationError):
        FollowUpSelection(body_id="saved-body", question=question)


@pytest.mark.parametrize("problem", [None, "old_citation", "wrong_goal", "wrong_section", "wrong_topic"])
def test_followup_rechecks_this_task_citations_and_never_changes_parent_goal(problem):
    result = answer("old-body-evidence" if problem == "old_citation" else "evidence-1")
    if problem == "wrong_goal": result["kind"] = SECTION_GOAL
    if problem == "wrong_section": result["section_id"] = "other"
    if problem == "wrong_topic": result["topic"] = "judging"
    context = dict(CONTEXT, goal=FOLLOW_UP_GOAL, anchor=followup_anchor())
    response = run(ScriptedAdapter([tool_turn(), {"kind": "final", "final_output": result}]), context=context)
    assert response.status == ("completed" if problem is None else "failed")
    assert response.semantic_verified is False
    if problem is None:
        assert response.final_output.kind == FOLLOW_UP_GOAL
        assert response.citations_validated
    else:
        assert response.final_output is None


def test_followup_requires_body_context_and_cannot_override_fixed_instruction():
    with pytest.raises(ValidationError): ModelContext(**CONTEXT, goal=FOLLOW_UP_GOAL, anchor=anchor(True))
    context = ModelContext(**CONTEXT, goal=FOLLOW_UP_GOAL, anchor=followup_anchor())
    assert "不可信数据" in context.system_instruction
    with pytest.raises(ValidationError):
        ModelContext(**CONTEXT, goal=SECTION_GOAL, anchor=followup_anchor())


def saved_body(env):
    projects, record, tasks, lessons, eid, ref, did = env
    chapter = lessons.save_from_task(record.id, complete_outline(env)["id"])
    task = tasks.create(record, "scripted", {}, kind=SECTION_GOAL, inputs={"outline_id": chapter["id"], "section_id": "entry"})
    tasks.update(record.id, task["id"], status="completed", result=explanation(eid), evidence=[ref])
    return lessons.save_from_task(record.id, task["id"])


@pytest.mark.parametrize("problem", [None, "wrong_topic", "unread", "mode", "old_body", "foreign_body"])
def test_followup_atomic_storage_binds_parent_mode_version_topic_and_new_evidence(tmp_path, problem):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, did = env
    body = saved_body(env); original = copy.deepcopy(body)
    selection = {"body_id": body["id"], "question": "这个参数如何检查？"}
    if problem == "foreign_body": selection["body_id"] = "not-owned"
    if problem == "old_body":
        with projects._connection() as c:
            stale = dict(body, producer_version="old")
            c.execute("UPDATE learning_content SET payload_json=? WHERE id=?", (json.dumps(stale), body["id"]))
    result = answer("unread" if problem == "unread" else eid)
    if problem == "wrong_topic": result["topic"] = "submission"
    task = tasks.create(record, "real" if problem == "mode" else "scripted", {}, kind=FOLLOW_UP_GOAL, inputs=selection)
    publish = lambda c: lessons.save_from_task(record.id, task["id"], transaction=c)
    if problem is None:
        assert tasks.update(record.id, task["id"], status="completed", result=result, evidence=[ref], publish=publish)
        saved = lessons.get(record, lessons.list(record)[0]["id"])
        assert saved["kind"] == FOLLOW_UP_GOAL and saved["parent_body_id"] == body["id"]
        assert saved["question"] == selection["question"] and not saved["quality_verified"]
        assert lessons.get(record, body["id"])["content"] == original["content"]
        assert len(lessons.list(record)) == 3
    else:
        with pytest.raises(ValueError):
            tasks.update(record.id, task["id"], status="completed", result=result, evidence=[ref], publish=publish)
        assert tasks.get(record.id, task["id"])["status"] == "queued"
        assert len(lessons.list(record)) == 2


def test_old_all_topics_cannot_start_followup_or_reserve_cost(tmp_path):
    ledger = SpendingLedger(ProjectStore(tmp_path / "cost"))
    grant = ledger.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    adapter = DeepSeekAdapter("synthetic-unused-key", ledger, grant, enable_query=True, enable_submission=True,
                              enable_consumption=True, enable_judging=True,
                              transport=httpx.MockTransport(lambda request: pytest.fail("Unauthorized follow-up transport")))
    context = ModelContext(**CONTEXT, goal=FOLLOW_UP_GOAL, anchor=followup_anchor())
    with pytest.raises(ModelError) as error:
        adapter.generate(context, timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "not_authorized"
    with TestClient(create_app(tmp_path / "agent", model_adapter=adapter), base_url="http://127.0.0.1:8123") as client:
        route = register(client, tmp_path / "never-opened", "a"*40)
        response = client.post(route + "/learning/followup", json={"body_id": "missing", "question": "问题"})
        assert response.json()["status"] == "followup_model_not_enabled"
        assert client.get(route + "/tasks").json() == []
        assert client.get(route + "/learning/status").json()["followup_enabled"] is False
    with ledger.store._connection() as c: assert c.execute("SELECT count(*) FROM model_spend").fetchone()[0] == 0


@pytest.mark.parametrize("topic", ["query", "submission", "consumption", "judging"])
def test_enabled_adapter_freezes_followup_prompt_schema_topic_and_receipt(tmp_path, topic):
    ledger = SpendingLedger(ProjectStore(tmp_path / "cost"))
    grant = ledger.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    def respond(request):
        payload = json.loads(request.content)
        system = payload["input"][0]["content"]
        assert '"FollowUpExplanation"' in system and '"DirectoryOutput"' not in system
        assert ('"const": "' + topic + '"') in system
        return httpx.Response(200, json=response_envelope())
    adapter = DeepSeekAdapter("synthetic-local-key", ledger, grant, **{"enable_" + topic: True}, enable_followup=True,
                              transport=httpx.MockTransport(respond)).for_task("followup-fixture", goal=FOLLOW_UP_GOAL, topic=topic)
    context = ModelContext(**CONTEXT, goal=FOLLOW_UP_GOAL, anchor=dict(followup_anchor(), topic=topic))
    text, sha = _load_guidance(FOLLOW_UP_GOAL, topic)
    assert sha == adapter.guidance_sha256 and "本次" in text
    assert adapter.generate(context, timeout_seconds=5, max_output_tokens=4000).final_output.kind == "gap"
    assert ledger.summary(grant)["accounted_upper_bound_microyuan"] == 28000


def test_followup_switch_cannot_authorize_a_disabled_topic(tmp_path):
    ledger = SpendingLedger(ProjectStore(tmp_path / "cost"))
    grant = ledger.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    adapter = DeepSeekAdapter("synthetic-unused-key", ledger, grant, enable_query=True, enable_followup=True,
                              transport=httpx.MockTransport(lambda request: pytest.fail("Disabled topic was sent")))
    adapter = adapter.for_task("cross-topic", goal=FOLLOW_UP_GOAL)
    context = ModelContext(**CONTEXT, goal=FOLLOW_UP_GOAL, anchor=dict(followup_anchor(), topic="judging"))
    with pytest.raises(ModelError) as error: adapter.generate(context, timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "not_authorized"
    with ledger.store._connection() as c: assert c.execute("SELECT count(*) FROM model_spend").fetchone()[0] == 0


def followup_steps(block=None):
    actions = steps(SECTION_GOAL)
    def finish(context):
        assert context.goal == FOLLOW_UP_GOAL and context.anchor.body_id and context.anchor.question
        assert context.anchor.previous_excerpt and len(context.anchor.previous_excerpt) <= 3000
        if block:
            block[0].set(); assert block[1].wait(5)
        return {"kind": "final", "final_output": answer(context.observations[-1].result["evidence_id"])}
    actions[-1] = finish
    return actions


def prepare(client, root, sha):
    route = register(client, root, sha); directory_id = parent(client, route)
    assert client.post(route + "/source/verify").json()["status"] == "verified"
    task = client.post(route + "/learning/outline", json={"directory_task_id": directory_id, "chapter_index": 0}).json()
    assert terminal(client, route, task["id"])["status"] == "completed"
    chapter = client.get(route + "/learning").json()[0]
    task = client.post(route + "/learning/section", json={"outline_id": chapter["id"], "section_id": "entry"}).json()
    assert terminal(client, route, task["id"])["status"] == "completed"
    return route, client.get(route + "/learning").json()[0]


def test_scripted_followup_lifecycle_restart_and_http_privacy(tmp_path, repository, git_exe):
    root, sha, controller, _, transport = repository; original = controller.read_bytes()
    data = tmp_path / "agent"; adapter = ScriptedAdapter(steps(OUTLINE_GOAL) + steps(SECTION_GOAL) + followup_steps())
    with TestClient(create_app(data, github_transport=transport, git_executable=git_exe, model_adapter=adapter), base_url="http://127.0.0.1:8123") as client:
        route, body = prepare(client, root, sha)
        other = register(client, root, sha)
        assert client.post(other + "/learning/followup", json={"body_id": body["id"], "question": "问题"}).json()["status"] == "invalid_selection"
        marker = "synthetic-sensitive-field"
        rejected = client.post(route + "/learning/followup", json={"body_id": body["id"], "question": "问题", marker: marker})
        assert rejected.status_code == 422 and marker not in rejected.text
        task = client.post(route + "/learning/followup", json={"body_id": body["id"], "question": "入口参数如何检查？"}).json()
        assert terminal(client, route, task["id"])["status"] == "completed"
        saved = client.get(route + "/learning").json()[0]
        assert saved["kind"] == FOLLOW_UP_GOAL and saved["parent_body_id"] == body["id"]
        assert saved["evidence"] and saved["execution_mode"] == "scripted" and not saved["quality_verified"]
        assert client.get(route + "/learning/" + body["id"]).json() == body
    with TestClient(create_app(data), base_url="http://127.0.0.1:8123") as reopened:
        assert len(reopened.get(route + "/learning").json()) == 3
        assert reopened.get(route + "/learning/status").json()["followup_enabled"] is False
    assert controller.read_bytes() == original


@pytest.mark.parametrize("action", ["cancel", "version", "delete"])
def test_late_followup_never_publishes_after_cancel_version_or_delete(tmp_path, repository, git_exe, action):
    root, sha, _, _, transport = repository; started, release = Event(), Event()
    adapter = ScriptedAdapter(steps(OUTLINE_GOAL) + steps(SECTION_GOAL) + followup_steps((started, release)))
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe, model_adapter=adapter), base_url="http://127.0.0.1:8123") as client:
        route, body = prepare(client, root, sha)
        task = client.post(route + "/learning/followup", json={"body_id": body["id"], "question": "入口参数如何检查？"}).json()
        try:
            assert started.wait(5)
            if action == "cancel": client.post(route + "/tasks/" + task["id"] + "/cancel")
            elif action == "version": client.patch(route + "/version", json={"commit": "a"*40})
            else: client.delete(route)
        finally:
            release.set(); client.app.state.task_service._thread.join(5)
        if action != "delete": assert all(item["kind"] != FOLLOW_UP_GOAL for item in client.get(route + "/learning").json())
        else:
            with client.app.state.project_store._connection() as c:
                assert c.execute("SELECT count(*) FROM learning_content").fetchone()[0] == 0
