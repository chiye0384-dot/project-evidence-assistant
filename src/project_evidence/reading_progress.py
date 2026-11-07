"""Local reading position and explicit self-report; never a mastery verdict."""
from datetime import datetime, timezone
import json
from typing import Literal

from pydantic import Field

from . import __version__
from .content_contract import StrictContract, _IDENTIFIER
from .learning_contract import ChapterOutline, SectionExplanation


class ReadingMark(StrictContract):
    content_id: str = Field(pattern=_IDENTIFIER)
    section_id: str | None = Field(default=None, pattern=_IDENTIFIER)
    action: Literal["viewed", "self_understood", "clear_understood"] = "viewed"


class ReadingProgress:
    def __init__(self, projects):
        self.projects = projects
        with projects._connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS reading_progress (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                content_id TEXT NOT NULL REFERENCES learning_content(id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(project_id, content_id))""")

    @staticmethod
    def _current(record, content):
        return (content["commit"] == record.commit and content.get("producer_version") == __version__
                and content.get("content_schema_version") == 1)

    def mark(self, record, selection: ReadingMark):
        with self.projects._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM projects WHERE id=?", (str(record.id),)).fetchone()
            if row is None:
                raise ValueError("reading_project_missing")
            record = self.projects._record(row)
            row = connection.execute("SELECT payload_json FROM learning_content WHERE project_id=? AND id=?",
                                     (str(record.id), selection.content_id)).fetchone()
            if row is None:
                raise ValueError("reading_content_missing")
            content = json.loads(row[0])
            if content.get("execution_mode") != "real":
                raise ValueError("reading_real_content_required")
            if content["kind"] == "chapter_outline":
                outline = ChapterOutline.model_validate(content["content"])
                if selection.section_id is not None and selection.section_id not in {item.id for item in outline.sections}:
                    raise ValueError("reading_section_missing")
                section_id = selection.section_id
                if selection.action != "viewed":
                    raise ValueError("reading_body_required_for_self_report")
            elif content["kind"] == "section_explanation":
                body = SectionExplanation.model_validate(content["content"])
                section_id = body.section_id
                if selection.section_id is not None and selection.section_id != section_id:
                    raise ValueError("reading_section_mismatch")
            else:
                raise ValueError("reading_kind_invalid")
            if selection.action == "self_understood" and not self._current(record, content):
                raise ValueError("reading_content_needs_review")
            previous = connection.execute("SELECT payload_json FROM reading_progress WHERE project_id=? AND content_id=?",
                                          (str(record.id), selection.content_id)).fetchone()
            previous = json.loads(previous[0]) if previous else {}
            stamp = datetime.now(timezone.utc).isoformat()
            payload = {"project_id": str(record.id), "content_id": content["id"], "kind": content["kind"],
                       "parent_outline_id": content.get("parent_outline_id"), "section_id": section_id,
                       "commit": content["commit"], "content_producer_version": content.get("producer_version"),
                       "content_schema_version": content.get("content_schema_version"),
                       "viewed": True, "last_viewed_at": stamp, "updated_at": stamp,
                       "self_reported_understood": previous.get("self_reported_understood", False),
                       "self_reported_at": previous.get("self_reported_at"), "independent_acceptance": False}
            if selection.action != "viewed":
                payload["self_reported_understood"] = selection.action == "self_understood"
                payload["self_reported_at"] = stamp
            if previous == {}:
                count = connection.execute("SELECT COUNT(*) FROM reading_progress WHERE project_id=?", (str(record.id),)).fetchone()[0]
                if count >= 100:
                    raise ValueError("reading_capacity_reached")
            connection.execute("""INSERT INTO reading_progress VALUES (?,?,?,?) ON CONFLICT(project_id,content_id)
                DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                               (str(record.id), content["id"], json.dumps(payload, ensure_ascii=False), stamp))
        return dict(payload, review_status="current" if self._current(record, content) else "needs_review")

    def list(self, record):
        with self.projects._connection() as connection:
            rows = connection.execute("""SELECT rp.payload_json, lc.payload_json FROM reading_progress rp
                JOIN learning_content lc ON rp.content_id=lc.id AND rp.project_id=lc.project_id
                WHERE rp.project_id=? ORDER BY rp.updated_at DESC,rp.content_id""",
                                      (str(record.id),)).fetchall()
        output = []
        for row in rows:
            mark, content = json.loads(row[0]), json.loads(row[1])
            output.append(dict(mark, independent_acceptance=False,
                               review_status="current" if self._current(record, content) else "needs_review"))
        return output
