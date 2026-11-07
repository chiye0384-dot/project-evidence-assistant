"""Synthetic saved artifacts test user marks; never real mastery/model scores."""
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from project_evidence.app import create_app
from project_evidence.learning_store import LearningStore
from project_evidence.task_storage import TaskStore
from project_evidence.reading_progress import ReadingProgress, ReadingMark
from test_learning_core import setup, outline, explanation


def artifacts(projects, record, mode="real"):
    tasks = TaskStore(projects); lessons = LearningStore(projects)
    eid = projects.save_evidence(record.id, {"commit": record.commit, "public_url": record.public_url,
        "path": "src/QueryController.java", "blob_oid": "b" * 40, "sha256": "c" * 64,
        "start_line": 1, "end_line": 1, "text": "class QueryController {}", "truncated": False})
    ref = {"evidence_id": eid["id"], **{key: eid[key] for key in
        ["path", "public_url", "commit", "blob_oid", "sha256", "start_line", "end_line", "truncated"]}}
    directory = tasks.create(record, mode, {})
    tasks.update(record.id, directory["id"], status="completed", result={"kind": "directory", "overview": "synthetic",
        "chapters": [{"title": "查询入口", "summary": "fixture", "kind": "backend", "support": "fact",
                      "evidence_ids": [eid["id"]]}], "gaps": ["fixture"]}, evidence=[ref])
    chapter = tasks.create(record, mode, {}, kind="chapter_outline", inputs={"directory_task_id": directory["id"], "chapter_index": 0})
    tasks.update(record.id, chapter["id"], status="completed", result=outline(eid["id"]), evidence=[ref],
                 publish=lambda c: lessons.save_from_task(record.id, chapter["id"], transaction=c))
    chapter = lessons.list(record)[0]
    body = tasks.create(record, mode, {}, kind="section_explanation", inputs={"outline_id": chapter["id"], "section_id": "entry"})
    tasks.update(record.id, body["id"], status="completed", result=explanation(eid["id"]), evidence=[ref],
                 publish=lambda c: lessons.save_from_task(record.id, body["id"], transaction=c))
    return chapter, lessons.list(record)[0]


def test_view_self_report_revoke_restart_and_version_are_separate(tmp_path):
    projects, record, _, _, _, _, _ = setup(tmp_path)
    chapter, body = artifacts(projects, record)
    marks = ReadingProgress(projects)
    viewed = marks.mark(record, ReadingMark(content_id=body["id"]))
    assert viewed["viewed"] is True and viewed["self_reported_understood"] is False
    assert viewed["independent_acceptance"] is False
    self_report = marks.mark(record, ReadingMark(content_id=body["id"], action="self_understood"))
    assert self_report["self_reported_understood"] is True
    assert marks.mark(record, ReadingMark(content_id=body["id"]))["self_reported_understood"] is True
    assert ReadingProgress(projects).list(record)[0]["self_reported_understood"] is True
    assert LearningStore(projects).get(record, body["id"])["quality_verified"] is False
    old_record = projects.set_version(record.id, "d" * 40)
    assert marks.list(old_record)[0]["review_status"] == "needs_review"
    with pytest.raises(ValueError): marks.mark(old_record, ReadingMark(content_id=body["id"], action="self_understood"))
    assert marks.mark(old_record, ReadingMark(content_id=body["id"]))["review_status"] == "needs_review"
    assert marks.mark(old_record, ReadingMark(content_id=body["id"], action="clear_understood"))["self_reported_understood"] is False
    projects.delete(record.id)
    with projects._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM reading_progress").fetchone()[0] == 0


@pytest.mark.parametrize("problem", ["foreign", "wrong_section", "outline_self_report", "scripted", "missing"])
def test_invalid_marks_do_not_create_or_mutate_progress(tmp_path, problem):
    projects, record, _, _, _, _, _ = setup(tmp_path)
    chapter, body = artifacts(projects, record, "scripted" if problem == "scripted" else "real")
    marks = ReadingProgress(projects); target = record; selected = {"content_id": body["id"]}
    if problem == "foreign":
        from project_evidence.models import ProjectRegistration
        target = projects.create(ProjectRegistration(name="other", source_path=str(tmp_path / "other-never-opened"),
            public_url=record.public_url, commit=record.commit))
    if problem == "wrong_section": selected["section_id"] = "other"
    if problem == "outline_self_report": selected = {"content_id": chapter["id"], "action": "self_understood"}
    if problem == "missing": selected["content_id"] = "missing"
    with pytest.raises(ValueError): marks.mark(target, ReadingMark(**selected))
    assert marks.list(record) == [] and marks.list(target) == []


def test_outline_position_requires_real_selected_section(tmp_path):
    projects, record, _, _, _, _, _ = setup(tmp_path); chapter, body = artifacts(projects, record)
    marks = ReadingProgress(projects)
    mark = marks.mark(record, ReadingMark(content_id=chapter["id"], section_id="entry"))
    assert mark["section_id"] == "entry" and mark["self_reported_understood"] is False
    with pytest.raises(ValueError): marks.mark(record, ReadingMark(content_id=chapter["id"], section_id="not-in-outline"))
    assert marks.list(record)[0]["section_id"] == "entry"


def test_progress_api_never_opens_source_charges_or_echoes_rejected_secrets(tmp_path):
    data = tmp_path / "agent"; missing = tmp_path / "source-never-opened"
    with TestClient(create_app(data), base_url="http://localhost:8123") as client:
        response = client.post('/api/projects', json={"name":"fixture","source_path":str(missing),
            "public_url":"https://github.com/example/public-java","commit":"a"*40})
        record = client.app.state.project_store.get(UUID(response.json()["id"]))
        chapter, body = artifacts(client.app.state.project_store, record)
        route = '/api/projects/' + str(record.id) + '/learning/progress'
        assert client.get(route).json() == []
        result = client.put(route, json={"content_id":body["id"],"action":"viewed"})
        assert result.status_code == 200 and result.json()["independent_acceptance"] is False
        secret = "synthetic-sensitive-never-a-real-key"
        rejected = client.put(route, json={"content_id":body["id"],"independent_acceptance":True,secret:secret})
        assert rejected.status_code == 422 and secret not in rejected.text
        assert client.get('/api/model/status').json()["spending"]["status"] == "not_enabled"
        assert len(client.get(route).json()) == 1
    with TestClient(create_app(data), base_url="http://localhost:8123") as reopened:
        assert reopened.get(route).json()[0]["content_id"] == body["id"]
    assert not missing.exists()
