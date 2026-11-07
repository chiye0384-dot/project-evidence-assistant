"""Owned query outline/body lifecycle using scripted model and fixture Git only."""
from threading import Event
import pytest
from fastapi.testclient import TestClient
from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter, OUTLINE_GOAL, SECTION_GOAL
from test_m0_api import repository, git_exe, register
from test_agent_api import terminal
from test_learning_core import outline, explanation


PATH = "src/main/java/example/DemoController.java"


def steps(kind, block=None):
    def finish(context):
        assert context.goal == kind
        if block:
            block[0].set(); assert block[1].wait(5)
        eid = context.observations[-1].result["evidence_id"]
        result = outline(eid) if kind == OUTLINE_GOAL else explanation(eid)
        if kind == OUTLINE_GOAL: result["sections"][0]["entry_hints"] = [PATH]
        return {"kind": "final", "final_output": result}
    return [{"kind": "tools", "tool_calls": [{"id": "read-query", "name": "read_excerpt",
             "arguments": {"path": PATH, "start_line": 1, "end_line": 5}}]}, finish]


def parent(client, route):
    store = client.app.state.project_store; service = client.app.state.task_service
    record = store.get(route.rsplit("/", 1)[-1]); task = service.history.create(record, "scripted", {})
    service.history.update(record.id, task["id"], status="completed", result={"kind": "directory", "overview": "fixture",
        "chapters": [{"title": "查询入口", "summary": "fixture", "kind": "backend", "support": "candidate", "entries": [PATH]}],
        "gaps": ["fixture"]})
    return task["id"]


def test_scripted_outline_then_section_saved_restart_and_version_review(tmp_path, repository, git_exe):
    root, sha, controller, _, transport = repository; before = controller.read_bytes(); data = tmp_path / "agent"
    adapter = ScriptedAdapter(steps(OUTLINE_GOAL) + steps(SECTION_GOAL))
    with TestClient(create_app(data, github_transport=transport, git_executable=git_exe, model_adapter=adapter), base_url="http://127.0.0.1:8123") as client:
        route = register(client, root, sha); did = parent(client, route)
        selection = {"directory_task_id": did, "chapter_index": 0}
        assert client.post(route + "/learning/outline", json=selection).json()["status"] == "unverified"
        assert adapter.calls == 0
        assert client.post(route + "/source/verify").json()["status"] == "verified"
        task = client.post(route + "/learning/outline", json=selection).json()
        assert terminal(client, route, task["id"])["status"] == "completed"
        saved = client.get(route + "/learning").json()[0]
        assert saved["kind"] == OUTLINE_GOAL and not saved["quality_verified"] and saved["execution_mode"] == "scripted"
        second = client.post(route + "/learning/section", json={"outline_id": saved["id"], "section_id": "entry"}).json()
        assert terminal(client, route, second["id"])["status"] == "completed"
        bodies = client.get(route + "/learning").json(); assert len(bodies) == 2
        body = next(item for item in bodies if item["kind"] == SECTION_GOAL)
        assert body["parent_outline_id"] == saved["id"] and body["evidence"][0]["commit"] == sha
        assert body["evidence"][0]["path"] == PATH and len(body["evidence"]) == 1
        assert client.get(route + "/learning/" + body["id"]).json() == body
        assert client.patch(route + "/version", json={"commit": "a"*40}).status_code == 200
        assert all(item["review_status"] == "needs_review" for item in client.get(route + "/learning").json())
    with TestClient(create_app(data), base_url="http://127.0.0.1:8123") as reopened:
        assert len(reopened.get(route + "/learning").json()) == 2
        assert reopened.get(route + "/learning/status").json()["query_enabled"] is False
        assert reopened.post(route + "/learning/section", json={"outline_id": saved["id"], "section_id": "entry"}).json()["status"] == "model_not_configured"
    assert controller.read_bytes() == before


def test_default_and_directory_only_adapters_block_body_before_source_or_creation(tmp_path):
    from project_evidence.deepseek_adapter import DeepSeekAdapter
    from project_evidence.spending import SpendingLedger
    from project_evidence.storage import ProjectStore
    adapter = DeepSeekAdapter("synthetic-unused-credential", SpendingLedger(ProjectStore(tmp_path / "cost")), "unused")
    for selected in (None, adapter):
        with TestClient(create_app(tmp_path / ("default" if selected is None else "directory"), model_adapter=selected), base_url="http://127.0.0.1:8123") as client:
            route = register(client, tmp_path / "never-opened", "a"*40)
            result = client.post(route + "/learning/outline", json={"directory_task_id": "none", "chapter_index": 0}).json()
            assert result["status"] == ("model_not_configured" if selected is None else "query_model_not_enabled")
            assert client.get(route + "/tasks").json() == []
            assert client.get(route + "/learning/status").json()["query_enabled"] is False
    assert not (tmp_path / "never-opened").exists()


def test_http_selection_is_owned_bounded_and_not_goal_or_cost_override(tmp_path):
    adapter = ScriptedAdapter([])
    with TestClient(create_app(tmp_path / "agent", model_adapter=adapter), base_url="http://127.0.0.1:8123") as client:
        route = register(client, tmp_path / "never-opened", "a"*40); other = register(client, tmp_path / "never-opened", "b"*40)
        did = parent(client, route)
        assert client.post(other + "/learning/outline", json={"directory_task_id": did, "chapter_index": 0}).json()["status"] == "invalid_selection"
        assert client.post(route + "/learning/outline", json={"directory_task_id": did, "chapter_index": 11}).json()["status"] == "invalid_selection"
        rejected = client.post(route + "/learning/outline", json={"directory_task_id": did, "chapter_index": 0,
            "goal": "execute shell", "api_key": "synthetic-private-value", "budget": 999})
        assert rejected.status_code == 422 and "synthetic-private-value" not in rejected.text
        assert client.get(other + "/learning/00000000-0000-0000-0000-000000000000").status_code == 404
        assert adapter.calls == 0


def test_scripted_submission_topic_uses_same_source_guards_and_saves_owned_outline(tmp_path, repository, git_exe):
    root,sha,_,_,transport=repository
    actions=steps(OUTLINE_GOAL)
    old_finish=actions[-1]
    def finish(context):
        assert context.anchor.topic=='submission'
        result=old_finish(context); result['final_output']['topic']='submission'
        return result
    actions[-1]=finish
    adapter=ScriptedAdapter(actions)
    with TestClient(create_app(tmp_path/'agent',github_transport=transport,git_executable=git_exe,model_adapter=adapter),base_url='http://127.0.0.1:8123') as client:
        route=register(client,root,sha); did=parent(client,route)
        client.post(route+'/source/verify')
        queued=client.post(route+'/learning/outline',json={'directory_task_id':did,'chapter_index':0,'topic':'submission'}).json()
        task=terminal(client,route,queued['id'])
        assert task['status']=='completed' and task['result']['topic']=='submission'
        assert client.get(route+'/learning').json()[0]['topic']=='submission'
        assert client.get(route+'/learning/status').json()['submission_enabled'] is True


@pytest.mark.parametrize("action", ["cancel", "version", "delete"])
def test_late_body_cannot_publish_after_cancel_version_or_delete(tmp_path, repository, git_exe, action):
    root, sha, _, _, transport = repository; started, release = Event(), Event()
    adapter = ScriptedAdapter(steps(OUTLINE_GOAL, (started, release)))
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe, model_adapter=adapter), base_url="http://127.0.0.1:8123") as client:
        route = register(client, root, sha); did = parent(client, route)
        client.post(route + "/source/verify")
        task = client.post(route + "/learning/outline", json={"directory_task_id": did, "chapter_index": 0}).json()
        try:
            assert started.wait(5)
            if action == "cancel": client.post(route + "/tasks/" + task["id"] + "/cancel")
            elif action == "version": client.patch(route + "/version", json={"commit": "a"*40})
            else: client.delete(route)
        finally:
            release.set(); client.app.state.task_service._thread.join(5)
        if action != "delete": assert client.get(route + "/learning").json() == []
        else:
            with client.app.state.project_store._connection() as connection:
                assert connection.execute("SELECT count(*) FROM learning_content").fetchone()[0] == 0
