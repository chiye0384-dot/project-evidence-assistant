"""Public-version sessions, independently retained evidence and structure facts."""

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil
from threading import RLock
from uuid import UUID

from .models import ProjectRecord
from .public_verify import PublicGitHubVerifier
from .source import PublicGitSource, SourceError
from .storage import ProjectStore
from .tools import SourceTools, contains_sensitive_content


SESSION_SECONDS = 3600


def default_git() -> str:
    installed = shutil.which("git")
    if installed:
        return str(Path(installed).resolve())
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe"
    if bundled.is_file():
        return str(bundled)
    raise SourceError("tool_unavailable", "未找到Git；只读工具暂不可用。")


class SourceService:
    def __init__(self, store: ProjectStore, verifier: PublicGitHubVerifier, git_executable: str | None = None) -> None:
        self.store, self.verifier = store, verifier
        self.git_executable = git_executable
        self._sessions: dict[str, tuple[PublicGitSource, dict]] = {}
        self._generations: dict[str, int] = {}
        self._lock = RLock()

    def clear(self, project_id) -> None:
        with self._lock:
            self._sessions.pop(str(project_id), None)
            self._generations[str(project_id)] = self._generations.get(str(project_id), 0) + 1

    def _session(self, record: ProjectRecord):
        with self._lock:
            session = self._sessions.get(str(record.id))
            if session is None or session[0].commit != record.commit:
                return None
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(session[1]["verified_at"])).total_seconds()
            return session if 0 <= age < SESSION_SECONDS else None

    def active(self, record: ProjectRecord) -> bool:
        return self._session(record) is not None

    def session_identity(self, record: ProjectRecord) -> tuple[int, str]:
        """Bind a task to this verification, not merely to an old saved receipt."""
        with self._lock:
            session = self._session(record)
            if session is None:
                raise SourceError("unverified", "请先手动核对公开版本；不能凭历史证据启动新任务。")
            return self._generations.get(str(record.id), 0), session[1]["verified_at"]

    def require_session(self, record: ProjectRecord, identity: tuple[int, str]) -> None:
        with self._lock:
            if self.session_identity(record) != identity:
                raise SourceError("unverified", "公开核对会话已更新；本任务停止，请重新开始。")

    @contextmanager
    def checked_session(self, record: ProjectRecord, identity: tuple[int, str]):
        """Keep re-verification from invalidating permission during publication."""
        with self._lock:
            self.require_session(record, identity)
            yield

    def verify(self, record: ProjectRecord) -> dict:
        with self._lock:
            self.clear(record.id)
            generation = self._generations[str(record.id)]
            self.store.clear_receipt(record.id)
        receipt = self.verifier.verify(record.public_url, record.commit)
        source = PublicGitSource(Path(record.source_path), record.public_url, record.commit,
                                 self.git_executable or default_git())
        if receipt.get("verification_method") == "github_anonymous_public_commit_page":
            if receipt.get("public_commit_oid") != source.commit or not source.commit_object_verified:
                raise SourceError("unverified", "公开提交与本地对象身份未核对，源码仍关闭。")
            receipt.update(tree_oid=source.tree_oid, tree_oid_origin="hash_verified_git_commit_header",
                           tree_binding_method="git_commit_sha1_and_tree_header")
        if source.tree_oid != receipt["tree_oid"]:
            raise SourceError("source_changed", "本地提交树与公开核对结果不一致。")
        license_entries = [entry for entry in source.entries() if entry["path"].lower()
                           in {"license", "license.txt", "license.md", "licence", "licence.txt", "licence.md", "copying", "copying.txt"}]
        if license_entries:
            entry = license_entries[0]
            raw_license = source.read_bytes(entry["path"])
            try:
                license_text = raw_license.decode("utf-8")
            except UnicodeError:
                raise SourceError("denied", "固定版本许可文件不是受支持的文本。") from None
            if not license_text.strip():
                raise SourceError("unverified", "固定版本许可文件为空，不能作为明确许可依据。")
            if contains_sensitive_content(license_text):
                raise SourceError("denied", "固定版本许可文件未通过内容边界检查。")
            receipt.update(license_path=entry["path"], license_sha256=hashlib.sha256(raw_license).hexdigest(),
                           license_text=license_text)
        else:
            raise SourceError("unverified", "固定提交中没有受支持的根许可文件，请另选明确许可的公开版本。")
        latest = self.store.get(record.id)
        if latest is None or latest.commit != record.commit:
            raise SourceError("source_changed", "登记版本已变化，请重新核对。")
        receipt["file_count"] = len(source.entries())
        with self._lock:
            if self._generations.get(str(record.id)) != generation or not self.store.save_receipt(record.id, receipt):
                raise SourceError("source_changed", "登记记录、版本或核对请求已变化，请重新核对。")
            self._sessions[str(record.id)] = (source, receipt)
        return self.status(latest)

    def source(self, record: ProjectRecord) -> PublicGitSource:
        session = self._session(record)
        if session is None:
            raise SourceError("unverified", "请先手动核对公开版本；核对过期或服务重开后需要再次核对。")
        return session[0]

    def status(self, record: ProjectRecord) -> dict:
        saved = self.store.receipt(record.id)
        session = self._session(record)
        if session is None:
            return {"status": "unverified", "commit": record.commit, "public_url": record.public_url,
                    "reason": "需要手动核对公开仓库与提交，历史证据仍可查看。",
                    "previously_verified_at": saved.get("verified_at") if saved else None,
                    "limitations": ["不读取未提交或未跟踪文件；未配置模型。"]}
        source, receipt = session
        result = source.status()
        safe_receipt = {key: value for key, value in receipt.items() if key != "license_text"}
        age = int((datetime.now(timezone.utc) - datetime.fromisoformat(receipt["verified_at"])).total_seconds())
        return dict(safe_receipt, **result, status="verified", expires_in_seconds=max(0, SESSION_SECONDS - age),
                    limitations=["只对应固定公开提交；未读取工作区内容，不能证明工作区干净。",
                                 "公开核对会话最多保留1小时；更换版本或重开服务后须重新核对。",
                                 "文件树与候选入口是程序事实，不是智能目录或完整调用图。"])

    def evidence(self, record: ProjectRecord, limit: int, offset: int) -> list[dict]:
        return self._evidence_status(record, self.store.list_evidence(record.id, limit, offset))

    def evidence_by_id(self, record: ProjectRecord, evidence_id: UUID) -> dict | None:
        item = self.store.get_evidence(record.id, evidence_id)
        return self._evidence_status(record, [item])[0] if item is not None else None

    def _evidence_status(self, record: ProjectRecord, items: list[dict]) -> list[dict]:
        changed = False
        session = self._session(record)
        if session is not None:
            try:
                changed = session[0].status().get("working_tree_changed") is True
            except SourceError:
                changed = True
                with self._lock:
                    if self._sessions.get(str(record.id)) is session:
                        self.clear(record.id)
        return [dict(item, source_verified=self.active(record),
                     status="needs_review" if item["commit"] != record.commit or changed else "current")
                for item in items]

    def tools(self, record: ProjectRecord) -> SourceTools:
        return SourceTools(self.source(record))
