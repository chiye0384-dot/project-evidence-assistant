"""Versioned learning records in assistant-owned SQLite; no target file access."""
from datetime import datetime, timezone
from contextlib import nullcontext
import json
import re
from uuid import uuid4

from .agent_protocol import DirectoryOutput
from .agent_engine import EvidenceReference
from .learning_contract import (ChapterOutline, SectionExplanation, FollowUpExplanation,
                                OutlineSelection, SectionSelection, FollowUpSelection, validate_followup_spans)
from .source_policy import normalize_github_repository, require_allowed_path
from . import __version__

MAX_LEARNING_RECORDS = 100


class LearningStore:
    def __init__(self, projects):
        self.projects = projects
        with projects._connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS learning_content (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL UNIQUE REFERENCES agent_tasks(id) ON DELETE CASCADE,
                directory_task_id TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                parent_outline_id TEXT REFERENCES learning_content(id) ON DELETE CASCADE,
                commit_sha TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )""")

    @staticmethod
    def _task(connection, project_id, task_id):
        row = connection.execute("SELECT payload_json FROM agent_tasks WHERE project_id=? AND id=?",
                                 (str(project_id), str(task_id))).fetchone()
        if row is None:
            raise ValueError("learning_parent_missing")
        return json.loads(row[0])

    @staticmethod
    def _identity(task, record, *, kind=None, mode=None):
        if (task["status"] != "completed" or task["commit"] != record.commit
                or normalize_github_repository(task["public_url"]) != normalize_github_repository(record.public_url)
                or task["execution_mode"] not in {"scripted", "real"}
                or (kind is not None and task["kind"] != kind)
                or (mode is not None and task["execution_mode"] != mode)):
            raise ValueError("learning_identity_mismatch")

    @staticmethod
    def _evidence(connection, record, task, used):
        references = {item["evidence_id"]: item for item in task.get("evidence", [])}
        output = []
        for identifier in sorted(set(used)):
            if identifier not in references:
                raise ValueError("learning_citation_not_from_task")
            reference = EvidenceReference.model_validate(references[identifier]).model_dump()
            require_allowed_path(reference["path"])
            if (reference["commit"] != record.commit
                    or normalize_github_repository(reference["public_url"]) != normalize_github_repository(record.public_url)
                    or not re.fullmatch(r"[0-9a-f]{40}", reference["blob_oid"])
                    or not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])
                    or not 1 <= reference["start_line"] <= reference["end_line"]):
                raise ValueError("learning_reference_invalid")
            row = connection.execute("SELECT commit_sha,payload_json FROM evidence WHERE project_id=? AND id=?",
                                     (str(record.id), identifier)).fetchone()
            if row is None or row["commit_sha"] != record.commit:
                raise ValueError("learning_evidence_missing")
            saved = json.loads(row["payload_json"])
            for key in ("commit", "path", "blob_oid", "sha256", "start_line", "end_line"):
                if reference.get(key) != saved.get(key):
                    raise ValueError("learning_evidence_identity_mismatch")
            if reference["truncated"] != saved.get("truncated", False):
                raise ValueError("learning_evidence_truncation_mismatch")
            if normalize_github_repository(saved["public_url"]) != normalize_github_repository(record.public_url):
                raise ValueError("learning_evidence_repository_mismatch")
            output.append(reference)
        return output

    def save_from_task(self, project_id, task_id, *, transaction=None):
        # The task and all parent/citation identities are read in one transaction.
        # This stores only validated output, never a replacement source snapshot.
        with (self.projects._connection() if transaction is None else nullcontext(transaction)) as connection:
            if transaction is None:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM projects WHERE id=?", (str(project_id),)).fetchone()
            if row is None:
                raise ValueError("learning_project_missing")
            record = self.projects._record(row)
            task = self._task(connection, project_id, task_id)
            self._identity(task, record)
            parent_id = None
            if task["kind"] == "chapter_outline":
                selection = OutlineSelection.model_validate(task["inputs"])
                directory = self._task(connection, project_id, selection.directory_task_id)
                self._identity(directory, record, kind="directory", mode=task["execution_mode"])
                tree = DirectoryOutput.model_validate(directory["result"])
                if selection.chapter_index >= len(tree.chapters) or tree.chapters[selection.chapter_index].kind != "backend":
                    raise ValueError("learning_chapter_invalid")
                chapter = tree.chapters[selection.chapter_index]
                if selection.topic == "query" and "查询" not in chapter.title and "详情" not in chapter.title:
                    raise ValueError("learning_query_chapter_required")
                content = ChapterOutline.model_validate(task["result"])
                if content.topic != selection.topic:
                    raise ValueError("learning_topic_mismatch")
                used = content.evidence_ids
                directory_id, chapter_index = directory["id"], selection.chapter_index
            elif task["kind"] == "section_explanation":
                selection = SectionSelection.model_validate(task["inputs"])
                row = connection.execute("SELECT payload_json FROM learning_content WHERE project_id=? AND id=? AND kind='chapter_outline'",
                                         (str(project_id), selection.outline_id)).fetchone()
                if row is None:
                    raise ValueError("learning_outline_missing")
                parent = json.loads(row[0])
                if (parent["commit"] != record.commit or parent["execution_mode"] != task["execution_mode"]
                        or parent.get("producer_version") != __version__ or parent.get("content_schema_version") != 1):
                    raise ValueError("learning_outline_identity_mismatch")
                outline = ChapterOutline.model_validate(parent["content"])
                if selection.section_id not in {section.id for section in outline.sections}:
                    raise ValueError("learning_section_missing")
                content = SectionExplanation.model_validate(task["result"])
                if content.section_id != selection.section_id or content.topic != outline.topic:
                    raise ValueError("learning_section_identity_mismatch")
                used = [identifier for block in content.blocks for identifier in block.evidence_ids]
                parent_id, directory_id, chapter_index = parent["id"], parent["directory_task_id"], parent["chapter_index"]
            elif task["kind"] == "follow_up":
                selection = FollowUpSelection.model_validate(task["inputs"])
                row = connection.execute("SELECT payload_json FROM learning_content WHERE project_id=? AND id=? AND kind='section_explanation'",
                                         (str(project_id), selection.body_id)).fetchone()
                if row is None:
                    raise ValueError("followup_body_missing")
                body = json.loads(row[0])
                row = connection.execute("SELECT payload_json FROM learning_content WHERE project_id=? AND id=? AND kind='chapter_outline'",
                                         (str(project_id), body["parent_outline_id"])).fetchone()
                if row is None:
                    raise ValueError("followup_outline_missing")
                parent = json.loads(row[0])
                for saved in (body, parent):
                    if (saved["commit"] != record.commit or saved["execution_mode"] != task["execution_mode"]
                            or saved.get("producer_version") != __version__ or saved.get("content_schema_version") != 1
                            or normalize_github_repository(saved["public_url"]) != normalize_github_repository(record.public_url)):
                        raise ValueError("followup_parent_identity_mismatch")
                previous = SectionExplanation.model_validate(body["content"])
                plan = ChapterOutline.model_validate(parent["content"])
                content = FollowUpExplanation.model_validate(task["result"])
                if (content.section_id != previous.section_id or content.topic != previous.topic or content.topic != plan.topic
                        or content.section_id not in {section.id for section in plan.sections}):
                    raise ValueError("followup_identity_mismatch")
                directory = self._task(connection, project_id, parent["directory_task_id"])
                self._identity(directory, record, kind="directory", mode=task["execution_mode"])
                used = [identifier for block in content.blocks for identifier in block.evidence_ids]
                parent_id, directory_id, chapter_index = parent["id"], parent["directory_task_id"], parent["chapter_index"]
            else:
                raise ValueError("learning_task_kind_invalid")
            references = self._evidence(connection, record, task, used)
            if isinstance(content, FollowUpExplanation):
                validate_followup_spans(content, {ref["evidence_id"]: ref for ref in references})
            previous = connection.execute("SELECT payload_json FROM learning_content WHERE project_id=? AND task_id=?",
                                          (str(project_id), str(task_id))).fetchone()
            if previous is not None:
                previous = json.loads(previous[0])
                if previous["content"] != content.model_dump() or previous["evidence"] != references:
                    raise ValueError("learning_saved_content_changed")
                return previous
            count = connection.execute("SELECT count(*) FROM learning_content WHERE project_id=?", (str(project_id),)).fetchone()[0]
            if count >= MAX_LEARNING_RECORDS:
                raise ValueError("learning_capacity_reached")
            stamp = datetime.now(timezone.utc).isoformat()
            payload = {"id": str(uuid4()), "project_id": str(project_id), "task_id": str(task_id),
                       "directory_task_id": directory_id, "chapter_index": chapter_index,
                       "parent_outline_id": parent_id, "section_id": getattr(content, "section_id", None),
                       "kind": content.kind, "commit": record.commit, "public_url": record.public_url,
                       "topic": content.topic,
                       "execution_mode": task["execution_mode"], "created_at": stamp,
                       "content": content.model_dump(), "evidence": references,
                       "content_schema_version": 1, "producer_version": __version__,
                       "quality_verified": False}
            if task["kind"] == "follow_up":
                payload.update(parent_body_id=body["id"], question=selection.question)
            connection.execute("INSERT INTO learning_content VALUES (?,?,?,?,?,?,?,?,?)",
                               (payload["id"], str(project_id), str(task_id), directory_id, parent_id, record.commit,
                                content.kind, stamp, json.dumps(payload, ensure_ascii=False)))
            return payload

    def list(self, record):
        with self.projects._connection() as connection:
            rows = connection.execute("SELECT payload_json FROM learning_content WHERE project_id=? ORDER BY created_at DESC",
                                      (str(record.id),)).fetchall()
        output = []
        for row in rows:
            payload = json.loads(row[0])
            current = payload["commit"] == record.commit and payload.get("producer_version") == __version__ and payload.get("content_schema_version") == 1
            output.append(dict(payload, review_status="current" if current else "needs_review"))
        return output

    def get(self, record, content_id):
        return next((item for item in self.list(record) if item["id"] == str(content_id)), None)
