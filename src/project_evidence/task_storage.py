"""Bounded task history in the assistant's SQLite file; never source writes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sqlite3
from uuid import uuid4

from .storage import ProjectStore
from .task_diagnostics import safe_metadata
from .preparation_size_diagnostics import safe_size


ACTIVE = {"queued", "running"}
MAX_TASKS = 100
MAX_TASK_RECORDS = 1000
MAX_TASK_PAYLOAD_BYTES = 128_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, projects: ProjectStore) -> None:
        self.projects = projects
        with projects._connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                commit_sha TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )""")
            # Optional diagnostics never consume the original task JSON budget.
            # One bounded row per existing event, deleted with its owning task.
            connection.execute("""CREATE TABLE IF NOT EXISTS task_preparation_sizes (
                task_id TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                event_seq INTEGER NOT NULL CHECK(event_seq BETWEEN 1 AND 120),
                metadata_json TEXT NOT NULL CHECK(length(CAST(metadata_json AS BLOB)) <= 640),
                PRIMARY KEY(task_id, event_seq)
            )""")
            # Restart never silently resumes a model call or charges again.
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT id, payload_json FROM agent_tasks WHERE status IN ('queued', 'running')").fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload.update(status="interrupted", message="服务重开使任务中断；历史记录已保留，不会自动重新调用模型。", updated_at=now(), result=None)
                self._write(connection, payload)

    @staticmethod
    def _encode(payload: dict) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_TASK_PAYLOAD_BYTES:
            raise ValueError("task_payload_limit")
        return text

    @staticmethod
    def _write(connection, payload: dict) -> None:
        connection.execute("UPDATE agent_tasks SET status=?, payload_json=? WHERE id=? AND status IN ('queued', 'running')",
                           (payload["status"], TaskStore._encode(payload), payload["id"]))

    def create(self, record, mode: str, limits: dict, *, kind="directory", inputs=None) -> dict:
        if kind not in {"directory", "chapter_outline", "section_explanation", "follow_up"}:
            raise ValueError("invalid_task_kind")
        if kind != "directory":
            from .learning_contract import OutlineSelection, SectionSelection, FollowUpSelection
            contract = {"chapter_outline": OutlineSelection, "section_explanation": SectionSelection, "follow_up": FollowUpSelection}[kind]
            inputs = contract.model_validate(inputs).model_dump()
        elif inputs is not None:
            raise ValueError("directory_inputs_not_supported")
        stamp = now()
        task = {"id": str(uuid4()), "project_id": str(record.id), "commit": record.commit,
                "public_url": record.public_url, "kind": kind, "status": "queued",
                "execution_mode": mode, "created_at": stamp, "updated_at": stamp,
                "message": "任务已登记，等待有界调用循环。", "limits": limits,
                "counters": {}, "events": [], "result": None, "evidence": [],
                "quality_verified": False}
        if inputs is not None:
            task["inputs"] = inputs
        with self.projects._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT commit_sha FROM projects WHERE id=?", (str(record.id),)).fetchone()
            if row is None or row[0] != record.commit:
                raise ValueError("source_changed")
            count = connection.execute("SELECT count(*) FROM agent_tasks WHERE project_id=?", (str(record.id),)).fetchone()[0]
            if count >= MAX_TASK_RECORDS:
                raise ValueError("task_limit")
            connection.execute("INSERT INTO agent_tasks VALUES (?, ?, ?, ?, ?, ?)",
                               (task["id"], task["project_id"], task["commit"], task["status"], stamp,
                                self._encode(task)))
        return task

    def get(self, project_id, task_id) -> dict | None:
        with self.projects._connection() as connection:
            row = connection.execute("SELECT payload_json FROM agent_tasks WHERE project_id=? AND id=?",
                                     (str(project_id), str(task_id))).fetchone()
            return self._with_preparation_sizes(connection, json.loads(row[0])) if row else None

    def list(self, project_id, *, limit=MAX_TASKS, offset=0) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= MAX_TASKS or type(offset) is not int or not 0 <= offset < MAX_TASK_RECORDS:
            raise ValueError("task_page_invalid")
        with self.projects._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM agent_tasks WHERE project_id=? ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                (str(project_id), limit, offset)).fetchall()
            return [self._with_preparation_sizes(connection, json.loads(row[0])) for row in rows]

    @staticmethod
    def _remember_preparation_size(connection, task_id, event_seq, size) -> None:
        """A refused optional insert cannot roll back a legal original event."""
        connection.execute('SAVEPOINT preparation_size')
        try:
            connection.execute('INSERT OR IGNORE INTO task_preparation_sizes VALUES (?, ?, ?)',
                               (task_id, event_seq, json.dumps(size, ensure_ascii=False)))
        except sqlite3.Error:
            connection.execute('ROLLBACK TO preparation_size')
        finally:
            connection.execute('RELEASE preparation_size')

    @staticmethod
    def _with_preparation_sizes(connection, payload: dict) -> dict:
        """Bounded read view only; never write the enriched view back to history."""
        try:
            rows = connection.execute('SELECT event_seq, metadata_json FROM task_preparation_sizes '
                                      'WHERE task_id=? ORDER BY event_seq LIMIT 120', (payload['id'],)).fetchall()
        except sqlite3.Error:
            return payload
        events = payload.get('events', [])
        for seq, text in rows:
            if type(seq) is not int or not 1 <= seq <= min(120, len(events)):
                continue
            event = events[seq - 1]
            if type(event.get('seq')) is not int or event['seq'] != seq or 'preparation_size' in event:
                continue
            if type(text) is not str:
                continue
            try:
                size = safe_size(json.loads(text))
            except (TypeError, ValueError):
                continue
            if size is None:
                continue
            event['preparation_size'] = size
            if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > MAX_TASK_PAYLOAD_BYTES:
                event.pop('preparation_size')
        return payload

    def capacity(self, project_id) -> dict:
        with self.projects._connection() as connection:
            count, size = connection.execute(
                "SELECT count(*),coalesce(sum(length(cast(payload_json AS blob))),0) FROM agent_tasks WHERE project_id=?",
                (str(project_id),)).fetchone()
        return {"total_records": count, "max_records": MAX_TASK_RECORDS,
                "remaining_records": max(0, MAX_TASK_RECORDS - count),
                "page_size": MAX_TASKS, "max_record_bytes": MAX_TASK_PAYLOAD_BYTES,
                "payload_bytes": size, "can_create_task": count < MAX_TASK_RECORDS}

    def update(self, project_id, task_id, *, status: str | None = None,
               message: str | None = None, event: dict | None = None,
               counters: dict | None = None, result: dict | None = None,
               evidence: list[dict] | None = None, publish=None) -> bool:
        """CAS on active state and source version; late workers cannot resurrect tasks."""
        with self.projects._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""SELECT agent_tasks.payload_json, projects.commit_sha
                FROM agent_tasks JOIN projects ON projects.id=agent_tasks.project_id
                WHERE agent_tasks.project_id=? AND agent_tasks.id=?""", (str(project_id), str(task_id))).fetchone()
            if row is None:
                return False
            payload = json.loads(row[0])
            if payload["status"] not in ACTIVE:
                return False
            if row[1] != payload["commit"]:
                payload.update(status="source_changed", message="选定版本变化，任务停止；不会发布旧版本目录。", result=None, updated_at=now())
                self._write(connection, payload)
                return False
            preparation_size = None
            if event is not None:
                # Action metadata only. No raw model response or private reasoning.
                safe = {key: event[key] for key in ("kind", "message", "tool", "status", "arguments", "truncated", "items_count", "evidence_id", "guidance_sha256") if key in event}
                safe.update(safe_metadata(event))
                preparation_size = safe.pop('preparation_size', None)
                safe["seq"] = len(payload["events"]) + 1
                if len(payload["events"]) < 120:
                    payload["events"].append(safe)
            if counters is not None:
                payload["counters"] = counters
            if status is not None:
                payload["status"] = status
            if message is not None:
                payload["message"] = message
            if result is not None:
                payload["result"] = result
            if evidence is not None:
                payload["evidence"] = evidence
            payload["updated_at"] = now()
            self._write(connection, payload)
            if preparation_size is not None and payload['events'] and payload['events'][-1] is safe:
                self._remember_preparation_size(connection, payload['id'], safe['seq'], preparation_size)
            if publish is not None:
                if payload["status"] != "completed":
                    raise ValueError("only_completed_task_can_publish")
                publish(connection)
        return True

    def stop_project(self, project_id, status: str, message: str) -> None:
        with self.projects._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT payload_json FROM agent_tasks WHERE project_id=? AND status IN ('queued', 'running')",
                                      (str(project_id),)).fetchall()
            for row in rows:
                payload = json.loads(row[0])
                payload.update(status=status, message=message, result=None, updated_at=now())
                self._write(connection, payload)

    def record_counters(self, project_id, task_id, counters: dict, evidence: list[dict] | None = None) -> None:
        """Late usage and existing evidence can be recorded without reviving a task."""
        with self.projects._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload_json FROM agent_tasks WHERE project_id=? AND id=?",
                                     (str(project_id), str(task_id))).fetchone()
            if row is None:
                return
            payload = json.loads(row[0])
            existing = payload.setdefault("counters", {})
            for key in ("model_calls", "tool_calls", "retries", "context_chars", "output_chars", "input_tokens", "output_tokens",
                        "reported_input_tokens", "reported_output_tokens", "estimated_input_tokens", "estimated_output_tokens", "elapsed_seconds"):
                value = counters.get(key)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    existing[key] = max(value, existing.get(key, 0))
            for key in ("input_tokens_estimated", "output_tokens_estimated"):
                if type(counters.get(key)) is bool:
                    existing[key] = existing.get(key, False) or counters[key]
            if evidence is not None:
                payload["evidence"] = [item for item in evidence if item.get("commit") == payload["commit"]][:24]
            payload["updated_at"] = now()
            connection.execute("UPDATE agent_tasks SET payload_json=? WHERE project_id=? AND id=?",
                               (self._encode(payload), str(project_id), str(task_id)))
