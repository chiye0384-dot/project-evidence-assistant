"""Persistent state races; no model and no target-source reads."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from project_evidence.models import ProjectRegistration
from project_evidence.storage import ProjectStore
from project_evidence.task_storage import TaskStore
import project_evidence.task_storage as task_storage


def fixture_store(tmp_path):
    projects = ProjectStore(tmp_path / "agent")
    record = projects.create(ProjectRegistration(name="Fixture", source_path=str(tmp_path / "never-opened"),
                                               public_url="https://github.com/example/public-java", commit="a" * 40))
    return projects, record, TaskStore(projects)


def test_old_directory_stays_reachable_after_twenty_new_tasks(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    first = tasks.create(record, "scripted", {})
    for _ in range(21):
        task = tasks.create(record, "scripted", {})
        tasks.update(record.id, task["id"], status="failed", message="fixture failure preserved")
    listed = tasks.list(record.id)
    assert len(listed) == 22 and first["id"] in {item["id"] for item in listed}
    assert len(TaskStore(ProjectStore(tmp_path / "agent")).list(record.id)) == 22
    other = projects.create(ProjectRegistration(name="Other", source_path=str(tmp_path / "other-unopened"),
                       public_url=record.public_url, commit=record.commit))
    assert tasks.list(other.id) == []


def test_cancel_is_terminal_and_keeps_action_metadata(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    task = tasks.create(record, "scripted", {"model_calls": 3})
    tasks.update(record.id, task["id"], status="running", event={"kind": "tool", "tool": "read_excerpt", "status": "ok", "message": "read", "raw_response": "never persisted"})
    assert tasks.update(record.id, task["id"], status="cancelled", message="cancelled")
    assert not tasks.update(record.id, task["id"], status="completed", result={"overview": "late result"})
    saved = tasks.get(record.id, task["id"])
    assert saved["status"] == "cancelled" and saved["result"] is None
    assert saved["events"] == [{"seq": 1, "kind": "tool", "tool": "read_excerpt", "status": "ok", "message": "read"}]
    assert not (tmp_path / "never-opened").exists()


def test_restart_preserves_completed_and_interrupts_active_without_resume(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    done = tasks.create(record, "scripted", {})
    tasks.update(record.id, done["id"], status="completed", result={"overview": "fixture only"})
    active = tasks.create(record, "scripted", {})
    tasks.update(record.id, active["id"], status="running", counters={"model_calls": 1})
    reopened = TaskStore(ProjectStore(projects.data_dir))
    assert reopened.get(record.id, done["id"])["result"] == {"overview": "fixture only"}
    interrupted = reopened.get(record.id, active["id"])
    assert interrupted["status"] == "interrupted" and interrupted["counters"]["model_calls"] == 1
    assert not tasks.update(record.id, active["id"], status="completed", result={"overview": "stale"})


def test_version_compare_and_swap_prevents_old_result_publication(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    task = tasks.create(record, "scripted", {})
    projects.set_version(record.id, "b" * 40)
    assert not tasks.update(record.id, task["id"], status="completed", result={"overview": "stale"})
    saved = tasks.get(record.id, task["id"])
    assert saved["status"] == "source_changed" and saved["result"] is None
    with pytest.raises(ValueError, match="source_changed"):
        tasks.create(record, "scripted", {})


def test_delete_cascades_tasks_and_late_worker_cannot_recreate(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    task = tasks.create(record, "scripted", {})
    assert projects.delete(record.id)
    assert tasks.get(record.id, task["id"]) is None
    assert not tasks.update(record.id, task["id"], status="running")
    with projects._connection() as connection:
        assert connection.execute("SELECT count(*) FROM agent_tasks").fetchone()[0] == 0


def test_concurrent_task_capacity_is_transactional_and_no_history_eviction(tmp_path, monkeypatch):
    _, record, tasks = fixture_store(tmp_path)
    monkeypatch.setattr(task_storage, "MAX_TASK_RECORDS", 3)
    def create(_):
        try:
            return tasks.create(record, "scripted", {})["id"]
        except ValueError as error:
            assert str(error) == "task_limit"
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        ids = list(pool.map(create, range(10)))
    assert len([value for value in ids if value]) == 3
    assert len(tasks.list(record.id)) == 3


def test_stop_project_does_not_touch_other_projects_or_completed_history(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    other = projects.create(ProjectRegistration(name="Other", source_path="D:/not-opened", public_url="https://github.com/example/other", commit="b" * 40))
    done = tasks.create(record, "scripted", {})
    tasks.update(record.id, done["id"], status="gap", message="missing proof")
    active = tasks.create(record, "scripted", {})
    separate = tasks.create(other, "scripted", {})
    tasks.stop_project(record.id, "source_changed", "version changed")
    assert tasks.get(record.id, done["id"])["status"] == "gap"
    assert tasks.get(record.id, active["id"])["status"] == "source_changed"
    assert tasks.get(other.id, separate["id"])["status"] == "queued"


def test_late_usage_preserved_without_reviving_cancel_or_deleted_record(tmp_path):
    projects, record, tasks = fixture_store(tmp_path)
    task = tasks.create(record, "scripted", {})
    tasks.update(record.id, task["id"], status="cancelled", counters={"model_calls": 1})
    tasks.record_counters(record.id, task["id"], {"model_calls": 2, "output_tokens": 50, "output_tokens_estimated": False,
                                                "elapsed_seconds": 1.5, "raw_response": "not retained"})
    tasks.record_counters(record.id, task["id"], {"model_calls": 1, "output_tokens": 5})
    saved = tasks.get(record.id, task["id"])
    assert saved["status"] == "cancelled" and saved["result"] is None
    assert saved["counters"]["model_calls"] == 2 and saved["counters"]["output_tokens"] == 50
    assert "raw_response" not in saved["counters"]
    projects.delete(record.id)
    tasks.record_counters(record.id, task["id"], {"model_calls": 3})
    assert tasks.get(record.id, task["id"]) is None
