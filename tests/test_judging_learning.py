"""Judging purpose stays opt-in, owned and versioned; no paid API calls."""
import httpx
import pytest
from fastapi.testclient import TestClient
from project_evidence.agent_protocol import ModelContext, ModelError, ScriptedAdapter, OUTLINE_GOAL, SECTION_GOAL
from project_evidence.deepseek_adapter import DeepSeekAdapter, _load_guidance
from project_evidence.app import create_app
from test_agent_engine import run, tool_turn, CONTEXT
from test_learning_engine import anchor
from test_learning_core import setup, outline, explanation
from test_deepseek_adapter import setup as adapter_setup, envelope
from test_learning_api import steps, parent
from test_m0_api import repository, git_exe, register
from test_agent_api import terminal


@pytest.mark.parametrize("goal", [OUTLINE_GOAL, SECTION_GOAL])
@pytest.mark.parametrize("wrong_topic", ["query", "submission", "consumption"])
def test_judging_cannot_publish_another_topic_even_with_valid_evidence(goal, wrong_topic):
    chosen = anchor(goal == SECTION_GOAL); chosen["topic"] = "judging"
    output = (outline if goal == OUTLINE_GOAL else explanation)("evidence-1"); output["topic"] = wrong_topic
    result = run(ScriptedAdapter([tool_turn(), {"kind": "final", "final_output": output}]),
                 context={**CONTEXT, "goal": goal, "anchor": chosen})
    assert result.status == "failed" and result.final_output is None


@pytest.mark.parametrize("options,old_topic", [
    ({"enable_query": True}, "query"), ({"enable_submission": True}, "submission"),
    ({"enable_consumption": True}, "consumption"),
    ({"enable_query": True, "enable_submission": True, "enable_consumption": True}, "consumption"),
])
@pytest.mark.parametrize("goal", [OUTLINE_GOAL, SECTION_GOAL])
def test_all_previous_purposes_cannot_send_or_reserve_judging(tmp_path, options, old_topic, goal):
    base, ledger = adapter_setup(tmp_path, lambda request: pytest.fail("No judging-purpose authorization"))
    adapter = DeepSeekAdapter("synthetic-old-topic-key", ledger, "first-trial", transport=base._transport,
                              **options).for_task("old-task", goal=goal, topic=old_topic)
    chosen = anchor(goal == SECTION_GOAL); chosen["topic"] = "judging"
    with pytest.raises(ModelError) as error:
        adapter.generate(ModelContext(**CONTEXT, goal=goal, anchor=chosen), timeout_seconds=5, max_output_tokens=4000)
    assert error.value.code == "not_authorized" and error.value.request_sent is False
    with pytest.raises(ValueError): adapter.for_task("new-task", goal=goal, topic="judging")
    assert ledger.summary("first-trial")["encumbered_microyuan"] == 0


@pytest.mark.parametrize("goal", [OUTLINE_GOAL, SECTION_GOAL])
def test_judging_opt_in_reuses_ledger_and_keeps_other_topics_disabled(tmp_path, goal):
    base, ledger = adapter_setup(tmp_path, lambda request: httpx.Response(200, json=envelope()))
    adapter = DeepSeekAdapter("synthetic-judging-key", ledger, "first-trial", transport=base._transport,
                              enable_judging=True).for_task("judge-task", goal=goal, topic="judging")
    chosen = anchor(goal == SECTION_GOAL); chosen["topic"] = "judging"
    context = ModelContext(**CONTEXT, goal=goal, anchor=chosen)
    assert adapter.allowed_topics == {"judging"} and '"const": "judging"' in adapter._messages(context)[0]["content"]
    assert adapter.guidance_sha256 == _load_guidance(goal, "judging")[1]
    adapter.generate(context, timeout_seconds=5, max_output_tokens=4000)
    assert ledger.summary("first-trial")["accounted_upper_bound_microyuan"] == 28000
    assert ledger.summary("first-trial")["trial_limit_microyuan"] == 5000000
    with ledger.store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_grants").fetchone()[0] == 1
    for topic in ["query", "submission", "consumption"]:
        with pytest.raises(ValueError): adapter.for_task("other-task", goal=goal, topic=topic)


def test_judging_archive_rejects_wrong_parent_topic_atomically(tmp_path):
    projects, record, tasks, lessons, eid, ref, did = setup(tmp_path)
    task = tasks.create(record, "scripted", {}, kind=OUTLINE_GOAL,
                        inputs={"directory_task_id": did, "chapter_index": 0, "topic": "judging"})
    output = outline(eid); output["topic"] = "judging"
    tasks.update(record.id, task["id"], status="completed", result=output, evidence=[ref],
                 publish=lambda c: lessons.save_from_task(record.id, task["id"], transaction=c))
    saved = lessons.list(record)[0]
    task = tasks.create(record, "scripted", {}, kind=SECTION_GOAL,
                        inputs={"outline_id": saved["id"], "section_id": "entry"})
    wrong = explanation(eid); wrong["topic"] = "consumption"
    with pytest.raises(ValueError):
        tasks.update(record.id, task["id"], status="completed", result=wrong, evidence=[ref],
                     publish=lambda c: lessons.save_from_task(record.id, task["id"], transaction=c))
    assert tasks.get(record.id, task["id"])["status"] == "queued"
    assert len(lessons.list(record)) == 1 and saved["topic"] == "judging"


def test_disabled_judging_http_stops_before_source_task_or_payment(tmp_path):
    base, ledger = adapter_setup(tmp_path, lambda request: pytest.fail("No purpose opt-in"))
    adapter = DeepSeekAdapter("synthetic-old-topic-key", ledger, "first-trial", transport=base._transport,
                              enable_query=True, enable_submission=True, enable_consumption=True)
    with TestClient(create_app(tmp_path / "http", model_adapter=adapter), base_url="http://127.0.0.1:8123") as client:
        route = register(client, tmp_path / "never-opened", "a" * 40)
        result = client.post(route + "/learning/outline", json={"directory_task_id": "missing", "chapter_index": 0,
                            "topic": "judging"})
        assert result.json()["status"] == "judging_model_not_enabled"
        assert client.get(route + "/tasks").json() == []
        assert client.get(route + "/learning/status").json()["judging_enabled"] is False
        assert client.get("/api/health").json()["capabilities"]["judging_generation"] is False
    assert ledger.summary("first-trial")["encumbered_microyuan"] == 0
    assert not (tmp_path / "never-opened").exists()


def test_judging_scripted_lifecycle_reopen_and_stale_version(tmp_path, repository, git_exe):
    root, sha, controller, _, transport = repository; before = controller.read_bytes(); data = tmp_path / "agent"
    actions = []
    for kind in [OUTLINE_GOAL, SECTION_GOAL]:
        phase = steps(kind); finish = phase[-1]
        def topic_finish(context, finish=finish):
            assert context.anchor.topic == "judging"
            result = finish(context); result["final_output"]["topic"] = "judging"
            return result
        phase[-1] = topic_finish; actions.extend(phase)
    with TestClient(create_app(data, github_transport=transport, git_executable=git_exe,
                              model_adapter=ScriptedAdapter(actions)), base_url="http://127.0.0.1:8123") as client:
        route = register(client, root, sha); did = parent(client, route)
        assert client.post(route + "/source/verify").json()["status"] == "verified"
        started = client.post(route + "/learning/outline", json={"directory_task_id": did, "chapter_index": 0,
                                                                 "topic": "judging"}).json()
        assert terminal(client, route, started["id"])["status"] == "completed"
        saved = client.get(route + "/learning").json()[0]
        started = client.post(route + "/learning/section", json={"outline_id": saved["id"], "section_id": "entry"}).json()
        assert terminal(client, route, started["id"])["status"] == "completed"
        body = next(item for item in client.get(route + "/learning").json() if item["kind"] == SECTION_GOAL)
        assert body["topic"] == "judging" and body["parent_outline_id"] == saved["id"]
        assert body["quality_verified"] is False
        assert client.patch(route + "/version", json={"commit": "b" * 40}).status_code == 200
        assert all(item["review_status"] == "needs_review" for item in client.get(route + "/learning").json())
        assert client.post(route + "/learning/section", json={"outline_id": saved["id"], "section_id": "entry"}).json()["status"] == "invalid_selection"
    with TestClient(create_app(data), base_url="http://127.0.0.1:8123") as client:
        assert len(client.get(route + "/learning").json()) == 2
        assert client.get(route + "/learning/status").json()["judging_enabled"] is False
    assert controller.read_bytes() == before
