"""Learning schema and owned-storage fixtures; no real model or target reads."""
import copy
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from project_evidence.agent_engine import EvidenceReference
from project_evidence.learning_contract import ChapterOutline, SectionExplanation
from project_evidence.learning_store import LearningStore
import project_evidence.learning_store as learning_module
from project_evidence.models import ProjectRegistration
from project_evidence.storage import ProjectStore
from project_evidence.task_storage import TaskStore
from project_evidence.spending import SpendingLedger


def outline(evidence_id):
    return {"kind": "chapter_outline", "title": "查询章", "objective": "逐节查证公开查询代码。",
            "sections": [{"id": "entry", "title": "入口与参数", "objective": "说明参数与边界。",
                          "entry_hints": ["src/QueryController.java"]}],
            "evidence_ids": [evidence_id], "gaps": ["尚未读取SQL。"]}


def explanation(evidence_id):
    return {"kind": "section_explanation", "section_id": "entry", "title": "入口与参数",
            "blocks": [{"category": "inputs", "heading": "输入", "text": "这是样例参数声明。",
                        "support": "fact", "evidence_ids": [evidence_id]}], "gaps": ["运行权限尚未查证。"]}


def setup(tmp_path):
    projects = ProjectStore(tmp_path / "agent")
    record = projects.create(ProjectRegistration(name="fixture", source_path=str(tmp_path / "never-opened"),
                             public_url="https://github.com/example/public-java", commit="a"*40))
    tasks = TaskStore(projects); lessons = LearningStore(projects)
    evidence = projects.save_evidence(record.id, {"commit": record.commit, "public_url": record.public_url,
        "path": "src/QueryController.java", "blob_oid": "b"*40, "sha256": "c"*64,
        "start_line": 1, "end_line": 1, "text": "class QueryController {}", "truncated": False})
    ref = EvidenceReference(evidence_id=evidence["id"], **{k: evidence[k] for k in
        ("path", "public_url", "commit", "blob_oid", "sha256", "start_line", "end_line", "truncated")}).model_dump()
    directory = tasks.create(record, "scripted", {})
    tasks.update(record.id, directory["id"], status="completed", result={"kind": "directory", "overview": "fixture",
        "chapters": [{"title": "查询入口", "summary": "fixture", "kind": "backend", "support": "fact",
                      "evidence_ids": [evidence["id"]]}], "gaps": ["fixture"]}, evidence=[ref])
    return projects, record, tasks, lessons, evidence["id"], ref, directory["id"]


def complete_outline(env, result=None, mode="scripted"):
    projects, record, tasks, lessons, eid, ref, did = env
    task = tasks.create(record, mode, {}, kind="chapter_outline", inputs={"directory_task_id": did, "chapter_index": 0})
    tasks.update(record.id, task["id"], status="completed", result=result or outline(eid), evidence=[ref])
    return task


def test_outline_section_restart_and_repeated_save_preserve_versioned_fixture(tmp_path):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, did = env
    task = complete_outline(env)
    chapter = lessons.save_from_task(record.id, task["id"])
    assert chapter["execution_mode"] == "scripted" and chapter["quality_verified"] is False
    assert chapter == lessons.save_from_task(record.id, task["id"])
    section = tasks.create(record, "scripted", {}, kind="section_explanation", inputs={"outline_id": chapter["id"], "section_id": "entry"})
    tasks.update(record.id, section["id"], status="completed", result=explanation(eid), evidence=[ref])
    body = lessons.save_from_task(record.id, section["id"])
    assert body["parent_outline_id"] == chapter["id"] and body["evidence"] == [ref]
    reopened = LearningStore(ProjectStore(projects.data_dir))
    assert len(reopened.list(record)) == 2
    assert not (tmp_path / "never-opened").exists()


def test_archive_validation_failure_rolls_back_completed_task_and_content(tmp_path):
    projects, record, tasks, lessons, eid, ref, did = setup(tmp_path)
    task = tasks.create(record, "scripted", {}, kind="chapter_outline", inputs={"directory_task_id": did, "chapter_index": 0})
    with pytest.raises(ValueError):
        tasks.update(record.id, task["id"], status="completed", result=outline("unread"), evidence=[ref],
            publish=lambda connection: lessons.save_from_task(record.id, task["id"], transaction=connection))
    assert tasks.get(record.id, task["id"])["status"] == "queued"
    assert tasks.get(record.id, task["id"])["result"] is None and lessons.list(record) == []


@pytest.mark.parametrize("problem", ["duplicate_section", "missing_reference", "unknown_field", "secret", "absolute_path", "duplicate_reference"])
def test_learning_contract_rejects_unsafe_or_unsupported_output(problem):
    data = outline("evidence-1")
    if problem == "duplicate_section": data["sections"].append(copy.deepcopy(data["sections"][0]))
    if problem == "missing_reference": data["evidence_ids"] = []
    if problem == "unknown_field": data["chain_of_thought"] = "not retained"
    if problem == "secret": data["objective"] = "api_key = synthetic-not-a-real-key"
    if problem == "absolute_path": data["objective"] = "D:/private/file"
    if problem == "duplicate_reference": data["evidence_ids"] *= 2
    with pytest.raises(ValidationError): ChapterOutline.model_validate(data)


def test_supported_body_cannot_omit_citations_or_promote_gap():
    data = explanation("evidence-1"); data["blocks"][0]["evidence_ids"] = []
    with pytest.raises(ValidationError): SectionExplanation.model_validate(data)
    data = explanation("evidence-1"); data["blocks"][0]["category"] = "gap"
    with pytest.raises(ValidationError): SectionExplanation.model_validate(data)


@pytest.mark.parametrize("problem", ["foreign_citation", "changed_reference", "mode_mismatch", "not_completed", "old_version"])
def test_learning_store_binds_project_task_mode_version_and_exact_evidence(tmp_path, problem):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, did = env
    data = outline(eid)
    if problem == "foreign_citation": data["evidence_ids"] = ["unread-reference"]
    task = complete_outline(env, data, mode="real" if problem == "mode_mismatch" else "scripted")
    if problem == "changed_reference":
        with projects._connection() as c:
            import json
            row = c.execute("SELECT payload_json FROM agent_tasks WHERE id=?", (task["id"],)).fetchone()
            value = json.loads(row[0]); value["evidence"][0]["end_line"] = 2
            c.execute("UPDATE agent_tasks SET payload_json=? WHERE id=?", (json.dumps(value), task["id"]))
    if problem == "not_completed":
        with projects._connection() as c:
            import json
            row = c.execute("SELECT payload_json FROM agent_tasks WHERE id=?", (task["id"],)).fetchone()
            value = json.loads(row[0]); value["status"] = "cancelled"
            c.execute("UPDATE agent_tasks SET payload_json=? WHERE id=?", (json.dumps(value), task["id"]))
    if problem == "old_version": projects.set_version(record.id, "d"*40)
    with pytest.raises(ValueError): lessons.save_from_task(record.id, task["id"])
    assert lessons.list(projects.get(record.id)) == []


def test_old_content_retained_and_project_delete_keeps_model_spending(tmp_path):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, did = env
    task = complete_outline(env); lessons.save_from_task(record.id, task["id"])
    ledger = SpendingLedger(projects); grant = ledger.authorize(confirmed=True, expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    hold = ledger.reserve(grant, "fixture-task", 1000); ledger.settle(hold, 1, 1)
    projects.set_version(record.id, "d"*40)
    assert lessons.list(projects.get(record.id))[0]["review_status"] == "needs_review"
    assert projects.delete(record.id)
    with projects._connection() as c: assert c.execute("SELECT count(*) FROM learning_content").fetchone()[0] == 0
    assert ledger.summary(grant)["accounted_upper_bound_microyuan"] == 10
    assert not (tmp_path / "never-opened").exists()


def test_section_must_belong_to_selected_outline_and_capacity_preserves_existing(tmp_path, monkeypatch):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, did = env
    task = complete_outline(env); chapter = lessons.save_from_task(record.id, task["id"])
    section = tasks.create(record, "scripted", {}, kind="section_explanation", inputs={"outline_id": chapter["id"], "section_id": "other"})
    data = explanation(eid); data["section_id"] = "other"
    tasks.update(record.id, section["id"], status="completed", result=data, evidence=[ref])
    with pytest.raises(ValueError, match="learning_section_missing"): lessons.save_from_task(record.id, section["id"])
    monkeypatch.setattr(learning_module, "MAX_LEARNING_RECORDS", 1)
    second = complete_outline(env)
    with pytest.raises(ValueError, match="learning_capacity_reached"): lessons.save_from_task(record.id, second["id"])
    assert len(lessons.list(record)) == 1


def test_existing_foreign_directory_cannot_become_a_parent(tmp_path):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, did = env
    other = projects.create(ProjectRegistration(name="other", source_path=str(tmp_path / "other-never-opened"),
                            public_url=record.public_url, commit=record.commit))
    foreign = tasks.create(other, "scripted", {})
    tasks.update(other.id, foreign["id"], status="completed", result=tasks.get(record.id, did)["result"])
    task = tasks.create(record, "scripted", {}, kind="chapter_outline", inputs={"directory_task_id": foreign["id"], "chapter_index": 0})
    tasks.update(record.id, task["id"], status="completed", result=outline(eid), evidence=[ref])
    with pytest.raises(ValueError, match="learning_parent_missing"): lessons.save_from_task(record.id, task["id"])
    assert lessons.list(record) == []


def test_producer_version_change_marks_content_for_review_without_removing_it(tmp_path, monkeypatch):
    env = setup(tmp_path); projects, record, tasks, lessons, eid, ref, did = env
    task = complete_outline(env); saved = lessons.save_from_task(record.id, task["id"])
    monkeypatch.setattr(learning_module, "__version__", "future-fixture-version")
    old = lessons.list(record)[0]
    assert old["review_status"] == "needs_review" and old["content"] == saved["content"]
