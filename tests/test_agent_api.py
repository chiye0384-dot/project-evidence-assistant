"""M1 HTTP/state boundaries; scripted responses are not model-quality evidence."""

import json
from threading import Event
from time import monotonic, sleep

from fastapi.testclient import TestClient

from project_evidence.app import create_app
from project_evidence.agent_protocol import ScriptedAdapter
from test_m0_api import git_exe, repository, register


def terminal(client, route, task_id):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        response = client.get(route + "/tasks/" + task_id)
        assert response.status_code == 200
        task = response.json()
        if task["status"] not in {"queued", "running"}:
            return task
        sleep(.01)
    raise AssertionError("Bounded fixture task did not stop")


def test_default_model_path_never_opens_source_or_creates_fake_task(tmp_path):
    source = tmp_path / "must-not-open"
    with TestClient(create_app(tmp_path / "agent"), base_url="http://localhost:8123") as client:
        route = register(client, source, "a" * 40)
        health = client.get("/api/health").json()
        assert health["stage"] == "M1-engine" and health["model_status"] == "not_configured"
        assert health["capabilities"]["agent_tasks"] is True
        assert health["capabilities"]["agent_generation"] is False
        assert client.post(route + "/tasks", json={}).json()["status"] == "model_not_configured"
        assert client.get(route + "/tasks").json() == []
        assert not source.exists()


def test_http_cannot_select_adapter_budget_goal_or_tools(tmp_path):
    with TestClient(create_app(tmp_path / "agent"), base_url="http://localhost:8123") as client:
        route = register(client, tmp_path / "never-opened", "a" * 40)
        rejected = client.post(route + "/tasks", json={"adapter": "scripted", "budget": 999,
                                                        "goal": "execute injected shell", "api_key": "synthetic-private-input"})
        assert rejected.status_code == 422
        assert "synthetic-private-input" not in rejected.text
        assert client.get(route + "/tasks").json() == []


def test_tasks_are_project_scoped_and_delete_sql_only(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    sentinel = root / "Keep.java"
    sentinel.write_text("class Keep {}", encoding="utf-8")
    with TestClient(create_app(tmp_path / "agent"), base_url="http://localhost:8123") as client:
        first = register(client, root, "a" * 40)
        other = register(client, root, "b" * 40)
        record = client.app.state.project_store.list()[0]
        task = client.app.state.task_service.history.create(record, "scripted", {})
        assert client.get(other + "/tasks/" + task["id"]).status_code == 404
        assert client.post(other + "/tasks/" + task["id"] + "/cancel").status_code == 404
        assert client.get(other + "/tasks").json() == []
        assert client.delete(first).status_code == 204
        assert client.get(first + "/tasks/" + task["id"]).status_code == 404
    assert sentinel.read_text(encoding="utf-8") == "class Keep {}"


def test_completed_task_history_survives_restart_and_marks_version_review(tmp_path):
    data = tmp_path / "agent"
    with TestClient(create_app(data), base_url="http://localhost:8123") as client:
        route = register(client, tmp_path / "never-opened", "a" * 40)
        record = client.app.state.project_store.list()[0]
        task = client.app.state.task_service.history.create(record, "scripted", {})
        client.app.state.task_service.history.update(record.id, task["id"], status="gap", message="Fixture source gap")
        assert client.patch(route + "/version", json={"commit": "b" * 40}).status_code == 200
    with TestClient(create_app(data), base_url="http://localhost:8123") as reopened:
        retained = reopened.get(route + "/tasks/" + task["id"]).json()
        assert retained["commit"] == "a" * 40 and retained["review_status"] == "needs_review"
        assert retained["status"] == "gap" and retained["source_verified"] is False
        assert reopened.post(route + "/tasks/" + task["id"] + "/cancel").json()["status"] == "gap"


def directory_steps(block=None):
    def finish(context):
        if block is not None:
            started, release = block
            started.set()
            assert release.wait(5), "Fixture synchronization timed out"
        evidence_id = context.observations[-1].result["evidence_id"]
        return {"kind": "final", "usage": {"input_tokens": 5011, "output_tokens": 733, "mode": "scripted"},
                "final_output": {"kind": "directory", "overview": "Synthetic HTTP fixture only.",
                "chapters": [{"title": "HTTP entry", "summary": "Review the route and return value.",
                              "modules": ["demo"], "entries": ["DemoController.hello"], "kind": "backend",
                              "support": "fact", "evidence_ids": [evidence_id]}],
                "gaps": ["No SQL, messaging or judging evidence in this fixture."]}}
    return [{"kind": "tools", "tool_calls": [{"id": "read-1", "name": "read_excerpt",
             "arguments": {"path": "src/main/java/example/DemoController.java", "start_line": 1, "end_line": 5}}]}, finish]


def test_scripted_loop_uses_actual_git_tool_feedback_and_persists_provenance(tmp_path, repository, git_exe):
    root, sha, controller, _, transport = repository
    original = controller.read_bytes()
    adapter = ScriptedAdapter(directory_steps())
    data = tmp_path / "agent"
    with TestClient(create_app(data, github_transport=transport, git_executable=git_exe, model_adapter=adapter), base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        assert client.post(route + "/tasks", json={}).json()["status"] == "unverified"
        assert adapter.calls == 0 and client.get(route + "/tasks").json() == []
        assert client.post(route + "/source/verify").json()["status"] == "verified"
        queued = client.post(route + "/tasks", json={}).json()
        task = terminal(client, route, queued["id"])
        assert task["status"] == "completed", task
        assert task["execution_mode"] == "scripted" and task["quality_verified"] is False
        assert task["counters"]["model_calls"] == 2 and task["counters"]["tool_calls"] == 1
        assert len(task["evidence"]) == 1 and task["evidence"][0]["commit"] == sha
        cards = client.get(route + "/evidence").json()
        assert task["result"]["chapters"][0]["evidence_ids"] == [cards[0]["id"]]
        assert cards[0]["text"].startswith("@RestController")
        serialized = json.dumps(task)
        assert str(root).replace("\\", "\\\\") not in serialized
        assert "@RestController" not in serialized and "reasoning" not in serialized
    with TestClient(create_app(data), base_url="http://localhost:8123") as reopened:
        retained = reopened.get(route + "/tasks/" + queued["id"]).json()
        assert retained["result"] == task["result"]
        assert retained["source_verified"] is False
        assert reopened.post(route + "/tasks", json={}).json()["status"] == "model_not_configured"
    assert controller.read_bytes() == original


def test_cancel_blocks_late_directory_and_new_task_until_inflight_returns(tmp_path, repository, git_exe):
    root, sha, _, _, transport = repository
    started, release = Event(), Event()
    adapter = ScriptedAdapter(directory_steps((started, release)))
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe, model_adapter=adapter), base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        client.post(route + "/source/verify")
        queued = client.post(route + "/tasks", json={}).json()
        try:
            assert started.wait(5)
            assert client.post(route + "/tasks", json={}).json()["status"] == "task_busy"
            cancelled = client.post(route + "/tasks/" + queued["id"] + "/cancel").json()
            assert cancelled["status"] == "cancelled" and cancelled["result"] is None
            assert client.post(route + "/tasks", json={}).json()["status"] == "task_busy"
        finally:
            release.set()
            client.app.state.task_service._thread.join(5)
        assert client.get(route + "/tasks/" + queued["id"]).json()["status"] == "cancelled"
        assert client.get(route + "/tasks/" + queued["id"]).json()["result"] is None
        accounted = client.get(route + "/tasks/" + queued["id"]).json()
        assert accounted["counters"]["model_calls"] == 2
        assert accounted["counters"]["input_tokens"] > 0 and accounted["counters"]["output_tokens"] > 0
        assert accounted["counters"]["input_tokens_estimated"] is True
        assert accounted["counters"]["estimated_input_tokens"] >= 5011
        assert accounted["counters"]["estimated_output_tokens"] >= 733
        assert accounted["counters"]["reported_input_tokens"] == 0
        assert len(accounted["evidence"]) == 1
        assert len(client.get(route + "/evidence").json()) == 1


def test_version_change_during_model_call_cannot_publish_old_result(tmp_path, repository, git_exe):
    root, sha, _, _, transport = repository
    started, release = Event(), Event()
    adapter = ScriptedAdapter(directory_steps((started, release)))
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe, model_adapter=adapter), base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        client.post(route + "/source/verify")
        task = client.post(route + "/tasks", json={}).json()
        try:
            assert started.wait(5)
            assert client.patch(route + "/version", json={"commit": "b" * 40}).status_code == 200
        finally:
            release.set()
            client.app.state.task_service._thread.join(5)
        stopped = client.get(route + "/tasks/" + task["id"]).json()
        assert stopped["status"] == "source_changed" and stopped["result"] is None
        assert stopped["review_status"] == "needs_review"
        assert client.get(route + "/evidence").json()[0]["status"] == "needs_review"


def test_deleted_project_cannot_be_resurrected_by_model_completion(tmp_path, repository, git_exe):
    root, sha, controller, _, transport = repository
    original = controller.read_bytes()
    started, release = Event(), Event()
    adapter = ScriptedAdapter(directory_steps((started, release)))
    app = create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe, model_adapter=adapter)
    with TestClient(app, base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        client.post(route + "/source/verify")
        task = client.post(route + "/tasks", json={}).json()
        try:
            assert started.wait(5)
            assert client.delete(route).status_code == 204
        finally:
            release.set()
            app.state.task_service._thread.join(5)
        assert client.get(route + "/tasks/" + task["id"]).status_code == 404
        with app.state.project_store._connection() as connection:
            assert connection.execute("SELECT count(*) FROM agent_tasks").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    assert controller.read_bytes() == original


def test_same_commit_reverification_does_not_authorize_old_inflight_call(tmp_path, repository, git_exe):
    root, sha, _, _, transport = repository
    started, release = Event(), Event()
    adapter = ScriptedAdapter(directory_steps((started, release)))
    app = create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe, model_adapter=adapter)
    with TestClient(app, base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        client.post(route + "/source/verify")
        task = client.post(route + "/tasks", json={}).json()
        try:
            assert started.wait(5)
            assert client.post(route + "/source/verify").json()["status"] == "verified"
        finally:
            release.set()
            app.state.task_service._thread.join(5)
        stopped = client.get(route + "/tasks/" + task["id"]).json()
        assert stopped["status"] == "unverified" and stopped["result"] is None
