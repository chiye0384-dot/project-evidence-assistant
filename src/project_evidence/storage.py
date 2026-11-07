"""SQLite storage owned by the assistant, separate from registered source paths."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Iterator
from uuid import UUID, uuid4

from .models import ProjectRecord, ProjectRegistration


class ProjectStore:
    def __init__(self, data_dir: Path) -> None:
        # Only this application-owned location is created or written. Registered
        # source_path values are metadata and never passed to filesystem APIs.
        self.data_dir = data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "project_evidence.sqlite3"
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    public_url TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_status TEXT NOT NULL CHECK(source_status = 'unverified'),
                    analysis_status TEXT NOT NULL CHECK(analysis_status = 'not_started')
                )
                """
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS source_receipts (
                project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
                receipt_json TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                commit_sha TEXT NOT NULL,
                evidence_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(project_id, evidence_key)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS structures (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                commit_sha TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(project_id, commit_sha)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS source_licenses (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                commit_sha TEXT NOT NULL,
                license_path TEXT NOT NULL,
                license_sha256 TEXT NOT NULL,
                license_text TEXT NOT NULL,
                PRIMARY KEY(project_id, commit_sha)
            )""")
            # Preserve the already-approved license from pre-history M0 data.
            for row in connection.execute("SELECT project_id, receipt_json FROM source_receipts").fetchall():
                receipt = json.loads(row["receipt_json"])
                if all(field in receipt for field in ("commit", "license_path", "license_sha256", "license_text")):
                    self._save_license(connection, row["project_id"], receipt)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> ProjectRecord:
        values = dict(row)
        values["commit"] = values.pop("commit_sha")
        return ProjectRecord.model_validate(values)

    def create(self, registration: ProjectRegistration) -> ProjectRecord:
        record = ProjectRecord(
            id=uuid4(),
            name=registration.name,
            source_path=registration.source_path,
            public_url=str(registration.public_url),
            commit=registration.commit,
            created_at=datetime.now(timezone.utc),
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO projects
                    (id, name, source_path, public_url, commit_sha, created_at,
                     source_status, analysis_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.id), record.name, record.source_path,
                    record.public_url, record.commit, record.created_at.isoformat(),
                    record.source_status, record.analysis_status,
                ),
            )
        return record

    def list(self) -> list[ProjectRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY created_at, id"
            ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, project_id: UUID) -> ProjectRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (str(project_id),)
            ).fetchone()
        return self._record(row) if row is not None else None

    def delete(self, project_id: UUID) -> bool:
        # Do not resolve or delete source_path, folders, caches or external data.
        with self._connection() as connection:
            result = connection.execute(
                "DELETE FROM projects WHERE id = ?", (str(project_id),)
            )
        return result.rowcount == 1

    def set_version(self, project_id: UUID, commit: str) -> ProjectRecord | None:
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE projects SET commit_sha = ? WHERE id = ?", (commit, str(project_id))
            ).rowcount
            connection.execute("DELETE FROM source_receipts WHERE project_id = ?", (str(project_id),))
        return self.get(project_id) if changed else None

    def save_receipt(self, project_id: UUID, receipt: dict) -> bool:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT commit_sha FROM projects WHERE id = ?", (str(project_id),)).fetchone()
            if row is None or row[0] != receipt["commit"]:
                return False
            connection.execute(
                "INSERT OR REPLACE INTO source_receipts VALUES (?, ?)",
                (str(project_id), json.dumps(receipt, ensure_ascii=False)),
            )
            self._save_license(connection, str(project_id), receipt)
        return True

    @staticmethod
    def _save_license(connection: sqlite3.Connection, project_id: str, receipt: dict) -> None:
        connection.execute("INSERT OR REPLACE INTO source_licenses VALUES (?, ?, ?, ?, ?)",
                           (project_id, receipt["commit"], receipt["license_path"],
                            receipt["license_sha256"], receipt["license_text"]))

    def clear_receipt(self, project_id: UUID) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM source_receipts WHERE project_id = ?", (str(project_id),))

    def receipt(self, project_id: UUID) -> dict | None:
        with self._connection() as connection:
            row = connection.execute("SELECT receipt_json FROM source_receipts WHERE project_id = ?", (str(project_id),)).fetchone()
        return json.loads(row[0]) if row else None

    def save_structure(self, project_id: UUID, commit: str, result: dict) -> None:
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO structures VALUES (?, ?, ?)",
                               (str(project_id), commit, json.dumps(result, ensure_ascii=False)))

    def structure(self, project_id: UUID, commit: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute("SELECT payload_json FROM structures WHERE project_id = ? AND commit_sha = ?",
                                     (str(project_id), commit)).fetchone()
        return json.loads(row[0]) if row else None

    def save_evidence(self, project_id: UUID, payload: dict) -> dict:
        import hashlib

        key = hashlib.sha256(json.dumps({
            field: payload[field] for field in ("commit", "path", "start_line", "end_line", "sha256", "text")
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload_json FROM evidence WHERE project_id = ? AND evidence_key = ?",
                                          (str(project_id), key)).fetchone()
            if existing:
                return json.loads(existing[0])
            if connection.execute("SELECT count(*) FROM evidence WHERE project_id = ?", (str(project_id),)).fetchone()[0] >= 1000:
                raise ValueError("evidence_limit")
            record = dict(payload, id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
            connection.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
                               (record["id"], str(project_id), record["commit"], key,
                                json.dumps(record, ensure_ascii=False), record["created_at"]))
        return record

    def list_evidence(self, project_id: UUID, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute("""SELECT evidence.payload_json, source_licenses.license_path,
                source_licenses.license_sha256 FROM evidence LEFT JOIN source_licenses
                ON evidence.project_id = source_licenses.project_id AND evidence.commit_sha = source_licenses.commit_sha
                WHERE evidence.project_id = ? ORDER BY evidence.created_at DESC, evidence.id LIMIT ? OFFSET ?""",
                                      (str(project_id), limit, offset)).fetchall()
        return [dict(json.loads(row[0]), license_path=row[1], license_sha256=row[2]) for row in rows]

    def get_evidence(self, project_id: UUID, evidence_id: UUID) -> dict | None:
        with self._connection() as connection:
            row = connection.execute("""SELECT evidence.payload_json, source_licenses.license_path,
                source_licenses.license_sha256 FROM evidence LEFT JOIN source_licenses
                ON evidence.project_id = source_licenses.project_id AND evidence.commit_sha = source_licenses.commit_sha
                WHERE evidence.project_id = ? AND evidence.id = ?""",
                                     (str(project_id), str(evidence_id))).fetchone()
        return None if row is None else dict(json.loads(row[0]), license_path=row[1], license_sha256=row[2])
