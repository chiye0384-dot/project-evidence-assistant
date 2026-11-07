"""Bounded, read-only access to one externally verified public Git commit."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from typing import Sequence

from .source_policy import (
    SourceError, is_allowed_path, normalize_github_repository, require_allowed_path,
    validate_relative_path,
)


MAX_ENTRIES = 20_000
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_BLOB_BYTES = 256 * 1024
GIT_TIMEOUT_SECONDS = 10.0
MAX_GIT_CONFIG_BYTES = 64 * 1024
MAX_METADATA_ENTRIES = 100_000
_SHA1 = re.compile(r"^[0-9a-fA-F]{40}$")
_LOOSE_DIRECTORY = re.compile(r"^[0-9a-f]{2}$", re.IGNORECASE)
_LOOSE_FILENAME = re.compile(r"^[0-9a-f]{38}$", re.IGNORECASE)
_CONFIG_INCLUDE = re.compile(rb"(?im)^[ \t]*\[[ \t]*include(?:if)?(?:[ \t.]|\])")
_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class _Entry:
    path: str
    blob_oid: str
    size: int


def _has_link_metadata(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
    )


class PublicGitSource:
    """The caller must verify visibility, public commit and tree identity first.

    No source workspace file is opened. All returned bytes come from fixed Git
    blob objects; the local worktree is not a fallback for missing objects.
    """

    def __init__(
        self, root: Path, public_url: str, commit: str, git_executable: str
    ) -> None:
        self.root = Path(root)
        if not self.root.is_absolute() or ".." in self.root.parts or str(self.root).startswith("\\\\"):
            raise SourceError("invalid_source", "Source root must be an absolute local directory")
        if not isinstance(commit, str) or _SHA1.fullmatch(commit) is None:
            raise SourceError("invalid_source", "Use one full SHA-1 commit identifier")
        self.public_url = normalize_github_repository(public_url)
        self.commit = commit.lower()
        self.git_executable = git_executable
        self._opening_deadline: float | None = time.monotonic() + GIT_TIMEOUT_SECONDS
        self._identity: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._check_layout()

        top = self._text(["rev-parse", "--show-toplevel"])
        if os.path.normcase(os.path.abspath(top)) != os.path.normcase(str(self.root)):
            raise SourceError("invalid_source", "Source root must be the repository top-level directory")
        if self._text(["rev-parse", "--show-object-format"]) != "sha1":
            raise SourceError("invalid_source", "Only SHA-1 Git repositories are supported in this version")
        origin = self._text(["config", "--get-all", "remote.origin.url"])
        if "\n" in origin or normalize_github_repository(origin) != self.public_url:
            raise SourceError("invalid_source", "Local origin does not match the verified public repository")
        verified = self._text(["rev-parse", "--verify", f"{self.commit}^{{commit}}"])
        if verified != self.commit:
            raise SourceError("invalid_source", "The fixed commit is not available as a local commit")
        self.tree_oid = self._text(["rev-parse", "--verify", f"{self.commit}^{{tree}}"])
        if _SHA1.fullmatch(self.tree_oid) is None:
            raise SourceError("invalid_source", "The fixed commit tree is not valid")
        commit_bytes, _ = self._run(["cat-file", "commit", self.commit], maximum=65536)
        object_hash = hashlib.sha1(f"commit {len(commit_bytes)}\0".encode("ascii") + commit_bytes).hexdigest()
        if object_hash != self.commit or commit_bytes.split(b"\n", 1)[0] != b"tree " + self.tree_oid.encode("ascii"):
            raise SourceError("source_changed", "Commit object hash or tree header does not match the verified public identity")
        self.commit_object_verified = True
        self._entries = self._load_manifest()
        if time.monotonic() >= self._opening_deadline:
            raise SourceError("timeout", "Source opening exceeded its overall time limit")
        self._opening_deadline = None

    def _check_layout(self, *, deadline: float | None = None) -> None:
        deadline = deadline if deadline is not None else (self._opening_deadline or time.monotonic() + GIT_TIMEOUT_SECONDS)
        try:
            for ancestor in (self.root, *self.root.parents):
                if _has_link_metadata(ancestor):
                    raise SourceError("invalid_source", "Source roots and ancestors cannot be links or junctions")
            git_dir = self.root / ".git"
            if _has_link_metadata(git_dir) or not git_dir.is_dir() or not self.root.is_dir():
                raise SourceError("invalid_source", "Source requires a normal local Git directory")
            root_stat, git_stat = self.root.stat(), git_dir.stat()
            identity = ((root_stat.st_dev, root_stat.st_ino), (git_stat.st_dev, git_stat.st_ino))
        except (OSError, RuntimeError) as error:
            raise SourceError("invalid_source", "Source directory is not accessible") from error
        if self._identity is not None and identity != self._identity:
            raise SourceError("source_changed", "Source repository location changed; verify it again")
        self._identity = identity
        self._check_git_metadata(git_dir, deadline)

    @staticmethod
    def _normal_metadata(path: Path, *, directory: bool = False) -> os.stat_result:
        metadata = path.lstat()
        if _has_link_metadata(path) or not (
            stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        ):
            raise SourceError("invalid_source", "Git metadata cannot use links, junctions or special files")
        if not directory and metadata.st_nlink > 1:
            raise SourceError("invalid_source", "Hard-linked Git metadata is not supported")
        return metadata

    def _check_git_metadata(self, git_dir: Path, deadline: float) -> None:
        """Inspect Git metadata solely to enforce scope, never as source evidence."""
        try:
            for relative in ("objects", "objects/info", "objects/pack", "info", "refs"):
                path = git_dir / relative
                if os.path.lexists(path):
                    self._normal_metadata(path, directory=True)
            for relative in ("commondir", "info/grafts", "objects/info/alternates", "objects/info/http-alternates"):
                if os.path.lexists(git_dir / relative):
                    raise SourceError("invalid_source", "Shared or alternate Git object stores are not supported")
            for relative in ("config", "HEAD", "index", "packed-refs", "shallow"):
                path = git_dir / relative
                if os.path.lexists(path):
                    self._normal_metadata(path)

            config_path = git_dir / "config"
            expected = self._normal_metadata(config_path)
            if expected.st_size > MAX_GIT_CONFIG_BYTES:
                raise SourceError("limit_exceeded", "Git configuration exceeds the metadata limit")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(config_path, flags)
            with os.fdopen(descriptor, "rb") as configuration:
                actual = os.fstat(configuration.fileno())
                if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                    raise SourceError("source_changed", "Git configuration changed during its scope check")
                content = configuration.read(MAX_GIT_CONFIG_BYTES + 1)
            if len(content) > MAX_GIT_CONFIG_BYTES:
                raise SourceError("limit_exceeded", "Git configuration exceeds the metadata limit")
            if _CONFIG_INCLUDE.search(content.removeprefix(b"\xef\xbb\xbf")):
                raise SourceError("invalid_source", "Git include and includeIf configuration is not supported")

            checked = 0
            nested_metadata: list[Path] = []
            objects = git_dir / "objects"
            for container in objects.iterdir():
                container_name = container.name.lower()
                if container_name in {"pack", "info"} or _LOOSE_DIRECTORY.fullmatch(container.name):
                    self._normal_metadata(container, directory=True)
                    for entry in container.iterdir():
                        checked += 1
                        if checked > MAX_METADATA_ENTRIES:
                            raise SourceError("limit_exceeded", "Git metadata contains too many entries")
                        if container_name == "info":
                            metadata = entry.lstat()
                            self._normal_metadata(entry, directory=stat.S_ISDIR(metadata.st_mode))
                            if stat.S_ISDIR(metadata.st_mode):
                                nested_metadata.append(entry)
                        elif container_name == "pack" or _LOOSE_FILENAME.fullmatch(entry.name):
                            self._normal_metadata(entry)
                        if checked % 64 == 0 and time.monotonic() >= deadline:
                            raise SourceError("timeout", "Git metadata scope checks exceeded their time limit")
            refs = git_dir / "refs"
            if refs.is_dir():
                nested_metadata.append(refs)
            while nested_metadata:
                for entry in nested_metadata.pop().iterdir():
                    checked += 1
                    if checked > MAX_METADATA_ENTRIES:
                        raise SourceError("limit_exceeded", "Git reference metadata exceeded its scope limit")
                    if time.monotonic() >= deadline:
                        raise SourceError("timeout", "Git reference metadata exceeded its time limit")
                    metadata = entry.lstat()
                    if stat.S_ISDIR(metadata.st_mode):
                        self._normal_metadata(entry, directory=True)
                        nested_metadata.append(entry)
                    else:
                        self._normal_metadata(entry)
            if time.monotonic() >= deadline:
                raise SourceError("timeout", "Git metadata scope checks exceeded their time limit")
        except OSError as error:
            raise SourceError("invalid_source", "Git metadata is not accessible under the source policy") from error

    def _environment(self) -> dict[str, str]:
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        environment.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C", "LANG": "C",
        })
        return environment

    def _run(
        self, arguments: Sequence[str], *, maximum: int = 16_384,
        allowed_codes: frozenset[int] = frozenset({0}),
        input_bytes: bytes | None = None,
        deadline: float | None = None,
    ) -> tuple[bytes, int]:
        if self._opening_deadline is not None:
            deadline = min(deadline, self._opening_deadline) if deadline is not None else self._opening_deadline
        if deadline is None:
            deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        if time.monotonic() >= deadline:
            raise SourceError("timeout", "Source access exceeded its overall time limit")
        self._check_layout(deadline=deadline)
        if time.monotonic() >= deadline:
            raise SourceError("timeout", "Source access exceeded its overall time limit")
        if input_bytes is not None and len(input_bytes) > MAX_MANIFEST_BYTES:
            raise SourceError("limit_exceeded", "Source request exceeded its input limit")
        command = [
            self.git_executable, "--no-replace-objects", "--no-pager", "--no-optional-locks",
            "-c", f"safe.directory={self.root}",
            "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
            "-c", "core.preloadIndex=false", "-C", str(self.root), *arguments,
        ]
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=self._environment(), shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, ValueError) as error:
            raise SourceError("tool_unavailable", "The configured Git executable is not available") from error

        output, diagnostic = bytearray(), bytearray()
        exceeded = threading.Event()

        def drain(stream: object, destination: bytearray, bound: int) -> None:
            try:
                while chunk := stream.read(65_536):
                    remaining = bound - len(destination)
                    destination.extend(chunk[:max(0, remaining)])
                    if len(chunk) > remaining:
                        exceeded.set()
                        if process.poll() is None:
                            process.kill()  # Only the exact subprocess created above.
                        break
            except (OSError, ValueError):
                exceeded.set()

        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=drain, args=(process.stdout, output, maximum), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, diagnostic, 8192), daemon=True),
        ]
        for reader in readers:
            reader.start()
        writer: threading.Thread | None = None
        if input_bytes is not None:
            assert process.stdin is not None

            def feed() -> None:
                try:
                    process.stdin.write(input_bytes)
                except (OSError, ValueError):
                    pass
                finally:
                    process.stdin.close()

            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
        timed_out = False
        try:
            code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            code = process.wait(timeout=2)
        finally:
            for reader in readers:
                reader.join(timeout=2)
            if writer is not None:
                writer.join(timeout=2)
            if not any(reader.is_alive() for reader in readers):
                process.stdout.close()
                process.stderr.close()
        self._check_layout(deadline=deadline)
        if timed_out or time.monotonic() >= deadline or any(reader.is_alive() for reader in readers) or (writer is not None and writer.is_alive()):
            raise SourceError("timeout", "Source access exceeded its time limit")
        if exceeded.is_set():
            raise SourceError("limit_exceeded", "Source access exceeded its output limit")
        if code not in allowed_codes:
            raise SourceError("invalid_source", "The fixed Git source could not be read")
        return bytes(output), code

    def _text(self, arguments: Sequence[str], *, deadline: float | None = None) -> str:
        data, _ = self._run(arguments, deadline=deadline)
        try:
            return data.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise SourceError("invalid_source", "Source metadata is not supported UTF-8") from error

    def _load_manifest(self) -> dict[str, _Entry]:
        output, _ = self._run(
            ["ls-tree", "-rltz", "--full-tree", self.commit], maximum=MAX_MANIFEST_BYTES
        )
        records = output.split(b"\0")
        if records[-1] != b"":
            raise SourceError("invalid_source", "Source manifest is incomplete")
        if len(records) - 1 > MAX_ENTRIES:
            raise SourceError("limit_exceeded", "Source contains too many entries")
        accepted: dict[str, _Entry] = {}
        tree_oids = {self.tree_oid}
        for record in records[:-1]:
            try:
                header, encoded_path = record.split(b"\t", 1)
                mode, kind, oid, size = header.split()
                path = encoded_path.decode("utf-8")
                validate_relative_path(path)
                identifier = oid.decode("ascii")
                if _SHA1.fullmatch(identifier) is None:
                    raise ValueError("invalid object identity")
                if mode == b"040000" and kind == b"tree" and size == b"-":
                    tree_oids.add(identifier)
                    continue
                if mode not in {b"100644", b"100755"} or kind != b"blob":
                    continue
                if not is_allowed_path(path):
                    continue
                length = int(size)
                if length < 0 or _SHA1.fullmatch(identifier) is None or path in accepted:
                    raise ValueError("invalid manifest entry")
            except (ValueError, UnicodeDecodeError, SourceError) as error:
                raise SourceError("invalid_source", "Source manifest contains an unsupported entry") from error
            accepted[path] = _Entry(path, identifier, length)
        self._verify_trees(tree_oids)
        return accepted

    def _verify_trees(self, tree_oids: set[str]) -> None:
        """Hash tree metadata so local object corruption cannot change scope."""
        identities = sorted(tree_oids)
        output, _ = self._run(
            ["cat-file", "--batch"], maximum=MAX_MANIFEST_BYTES,
            input_bytes=("\n".join(identities) + "\n").encode("ascii"),
        )
        position = 0
        for expected in identities:
            newline = output.find(b"\n", position)
            if newline < 0:
                raise SourceError("invalid_source", "Source tree metadata is incomplete")
            try:
                oid, kind, encoded_size = output[position:newline].split()
                size = int(encoded_size)
            except ValueError as error:
                raise SourceError("invalid_source", "Source tree metadata is not available") from error
            position = newline + 1
            if size < 0 or size > MAX_MANIFEST_BYTES:
                raise SourceError("limit_exceeded", "Source tree exceeded its size limit")
            data = output[position:position + size]
            position += size
            if kind != b"tree" or oid != expected.encode("ascii") or output[position:position + 1] != b"\n":
                raise SourceError("invalid_source", "Source tree metadata is inconsistent")
            position += 1
            calculated = hashlib.sha1(b"tree " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
            if len(data) != size or calculated != expected:
                raise SourceError("source_changed", "Source tree no longer matches its verified identity")
        if position != len(output):
            raise SourceError("invalid_source", "Source tree metadata has unexpected output")

    def entries(self) -> list[dict[str, object]]:
        self._check_layout()
        return [
            {"path": entry.path, "blob_oid": entry.blob_oid, "size": entry.size}
            for entry in sorted(self._entries.values(), key=lambda item: item.path)
        ]

    def read_bytes(self, path: str) -> bytes:
        require_allowed_path(path)
        self._check_layout()
        entry = self._entries.get(path)
        if entry is None:
            raise SourceError("not_found", "This file is not an allowed blob in the fixed commit")
        if entry.size > MAX_BLOB_BYTES:
            raise SourceError("file_too_large", "This source file exceeds the blob size limit")
        data, _ = self._run(["cat-file", "blob", entry.blob_oid], maximum=MAX_BLOB_BYTES)
        identifier = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if len(data) != entry.size or identifier != entry.blob_oid:
            raise SourceError("source_changed", "The source object no longer matches its verified identity")
        return data

    def status(self) -> dict[str, object]:
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        head = self._text(["rev-parse", "--verify", "HEAD^{commit}"], deadline=deadline)
        changed = head != self.commit
        # Inspect only approved index metadata, never git diff or worktree
        # contents. Null means worktree contents were deliberately not checked.
        if not changed:
            output, _ = self._run(
                ["ls-files", "--stage", "-z"], maximum=MAX_MANIFEST_BYTES, deadline=deadline,
            )
            index: dict[str, str] = {}
            allowed_paths = {path.encode("utf-8"): path for path in self._entries}
            for record in output.split(b"\0"):
                if not record:
                    continue
                try:
                    header, encoded_path = record.split(b"\t", 1)
                    if encoded_path not in allowed_paths:
                        continue  # Excluded index paths never enter returned/cached metadata.
                    mode, oid, stage = header.split()
                    path = allowed_paths[encoded_path]
                    if mode not in {b"100644", b"100755"} or stage != b"0":
                        changed = True
                        break
                    index[path] = oid.decode("ascii")
                except (ValueError, UnicodeDecodeError) as error:
                    raise SourceError("invalid_source", "Source index metadata is not supported") from error
            if not changed:
                changed = any(index.get(path) != self._entries[path].blob_oid for path in self._entries)
        if time.monotonic() >= deadline:
            raise SourceError("timeout", "Source status exceeded its overall time limit")
        return {
            "commit": self.commit, "head_commit": head,
            "working_tree_changed": True if changed else None,
            "working_tree_checked": False,
        }
