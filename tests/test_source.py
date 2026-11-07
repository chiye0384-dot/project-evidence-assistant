import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import zlib

import pytest

from project_evidence.source import PublicGitSource, SourceError, MAX_BLOB_BYTES
import project_evidence.source as source_module


PUBLIC_URL = "https://github.com/example/public-java"


@pytest.fixture
def git_executable() -> str:
    executable = os.environ.get("PROJECT_EVIDENCE_TEST_GIT") or shutil.which("git")
    if executable is None:
        pytest.skip("Tests require the task's configured native Git executable")
    return executable


def git(executable: str, root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    result = subprocess.run(
        [executable, "--no-replace-objects", "-C", str(root),
         "-c", "user.name=Evidence test", "-c", "user.email=evidence@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *arguments],
        input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, shell=False, check=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout


@pytest.fixture
def repository(tmp_path: Path, git_executable: str) -> tuple[Path, str]:
    root = tmp_path / "repository-parent" / "public-repository"
    root.mkdir(parents=True)
    git(git_executable, root, "init", "-q")
    git(git_executable, root, "config", "remote.origin.url", PUBLIC_URL + ".git")
    files = {
        "pom.xml": b"<project><artifactId>sample</artifactId></project>\n",
        "src/main/java/Example.java": b"class Example { int answer() { return 42; } }\n",
        "LICENSE": b"Synthetic fixture license, not a third-party work.\n",
        "secrets/credentials.json": b'{"synthetic_private_value": "never_return"}\n',
        "config/application-local.yml": b"synthetic_private_config: never_return\n",
        "target/generated.java": b"// excluded generated fixture\n",
        "src/main/java/Large.java": b"x" * (MAX_BLOB_BYTES + 1),
    }
    for path, content in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    git(git_executable, root, "add", "--", *files)
    # Construct a symlink-mode blob in this fixture without requiring Windows
    # symlink privileges. It must never become a readable source entry.
    link_oid = git(git_executable, root, "hash-object", "-w", "--stdin", input_bytes=b"../private.java").decode().strip()
    git(git_executable, root, "update-index", "--add", "--cacheinfo", f"120000,{link_oid},links/Reference.java")
    git(git_executable, root, "commit", "-qm", "Public fixture version")
    commit = git(git_executable, root, "rev-parse", "HEAD").decode().strip()
    git(git_executable, root, "update-index", "--add", "--cacheinfo", f"160000,{commit},modules/Submodule.java")
    git(git_executable, root, "commit", "-qm", "Fixture gitlink metadata")
    commit = git(git_executable, root, "rev-parse", "HEAD").decode().strip()
    return root, commit


def make_source(repository: tuple[Path, str], executable: str) -> PublicGitSource:
    root, commit = repository
    return PublicGitSource(root, PUBLIC_URL, commit, executable)


def test_commit_metadata_content_is_bound_to_public_oid(repository, git_executable, monkeypatch):
    original = PublicGitSource._run
    def tampered(self, arguments, **kwargs):
        result, status = original(self, arguments, **kwargs)
        if arguments[:2] == ["cat-file", "commit"]:
            result += b"synthetic changed commit message"
        return result, status
    monkeypatch.setattr(PublicGitSource, "_run", tampered)
    with pytest.raises(SourceError) as error:
        make_source(repository, git_executable)
    assert error.value.code == "source_changed"


def assert_source_error(code: str, operation: object) -> None:
    with pytest.raises(SourceError) as captured:
        operation()
    assert captured.value.code == code


def test_manifest_and_reads_use_fixed_objects_not_dirty_worktree(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, commit = repository
    path = "src/main/java/Example.java"
    expected = (root / path).read_bytes()
    source = make_source(repository, git_executable)
    (root / path).write_bytes(b"synthetic secret in the uncommitted workspace")
    (root / "private-untracked.java").write_bytes(b"synthetic untracked secret")
    monkeypatch.setenv("GIT_DIR", str(root / "does-not-exist"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(root / "does-not-exist"))
    assert source.read_bytes(path) == expected
    paths = {item["path"] for item in source.entries()}
    assert "LICENSE" in paths
    assert "private-untracked.java" not in paths
    assert "secrets/credentials.json" not in paths
    assert "config/application-local.yml" not in paths
    assert "target/generated.java" not in paths
    assert "links/Reference.java" not in paths
    assert "modules/Submodule.java" not in paths
    # Status must not hash/open workspace source files or claim they are clean.
    assert source.status() == {
        "commit": commit, "head_commit": commit,
        "working_tree_changed": None, "working_tree_checked": False,
    }
    assert source.tree_oid == git(git_executable, root, "rev-parse", "HEAD^{tree}").decode().strip()


@pytest.mark.parametrize("path", ["", "../Example.java", "/Example.java", "C:/Example.java", "src\\Example.java", "src//Example.java", "src/./Example.java", "src/Example.java\x00"])
def test_invalid_paths_are_rejected_before_blob_access(
    repository: tuple[Path, str], git_executable: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(repository, git_executable)
    monkeypatch.setattr(source, "_run", lambda *args, **kwargs: pytest.fail("Invalid path triggered Git blob access"))
    assert_source_error("invalid_path", lambda: source.read_bytes(path))


@pytest.mark.parametrize("path", ["secrets/credentials.json", "config/application-local.yml", "target/generated.java", "docs/M2-NEXT-CHAT-HANDOFF.md", ".smtp.qq.local", ".env.json"])
def test_denied_paths_never_trigger_blob_access(
    repository: tuple[Path, str], git_executable: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(repository, git_executable)
    monkeypatch.setattr(source, "_run", lambda *args, **kwargs: pytest.fail("Denied path triggered Git blob access"))
    assert_source_error("denied", lambda: source.read_bytes(path))


def test_missing_symlink_and_oversized_files_are_not_read(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(repository, git_executable)
    monkeypatch.setattr(source, "_run", lambda *args, **kwargs: pytest.fail("Rejected file triggered blob access"))
    assert_source_error("not_found", lambda: source.read_bytes("src/Missing.java"))
    assert_source_error("not_found", lambda: source.read_bytes("links/Reference.java"))
    assert_source_error("not_found", lambda: source.read_bytes("modules/Submodule.java"))
    assert_source_error("file_too_large", lambda: source.read_bytes("src/main/java/Large.java"))


def test_staged_or_head_changes_are_reported_without_switching_fixed_version(
    repository: tuple[Path, str], git_executable: str
) -> None:
    root, old_commit = repository
    source = make_source(repository, git_executable)
    original = source.read_bytes("pom.xml")
    (root / "pom.xml").write_bytes(b"<project><artifactId>changed</artifactId></project>\n")
    git(git_executable, root, "add", "--", "pom.xml")
    assert source.status()["working_tree_changed"] is True
    git(git_executable, root, "commit", "-qm", "Changed fixture")
    assert source.status()["head_commit"] != old_commit
    assert source.status()["working_tree_changed"] is True
    assert source.read_bytes("pom.xml") == original


def test_replace_refs_cannot_change_fixed_commit(
    repository: tuple[Path, str], git_executable: str
) -> None:
    root, old_commit = repository
    original = make_source(repository, git_executable).read_bytes("pom.xml")
    (root / "pom.xml").write_bytes(b"<project>replacement</project>\n")
    git(git_executable, root, "add", "--", "pom.xml")
    git(git_executable, root, "commit", "-qm", "Replacement fixture")
    replacement = git(git_executable, root, "rev-parse", "HEAD").decode().strip()
    git(git_executable, root, "replace", old_commit, replacement)
    assert make_source(repository, git_executable).read_bytes("pom.xml") == original


def test_origin_and_commit_are_required(repository: tuple[Path, str], git_executable: str) -> None:
    root, commit = repository
    assert_source_error("invalid_source", lambda: PublicGitSource(root, PUBLIC_URL, "0" * 40, git_executable))
    git(git_executable, root, "config", "remote.origin.url", "git@github.com:other/repository.git")
    assert_source_error("invalid_source", lambda: PublicGitSource(root, PUBLIC_URL, commit, git_executable))
    git(git_executable, root, "config", "remote.origin.url", "git@github.com:example/public-java.git")
    assert make_source(repository, git_executable).read_bytes("pom.xml").startswith(b"<project>")


def test_manifest_limits_and_blob_integrity_fail_closed(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as limited:
        limited.setattr(source_module, "MAX_ENTRIES", 1)
        assert_source_error("limit_exceeded", lambda: make_source(repository, git_executable))
    with monkeypatch.context() as limited:
        limited.setattr(source_module, "MAX_MANIFEST_BYTES", 32)
        assert_source_error("limit_exceeded", lambda: make_source(repository, git_executable))
    source = make_source(repository, git_executable)
    monkeypatch.setattr(source, "_run", lambda *args, **kwargs: (b"tampered content", 0))
    assert_source_error("source_changed", lambda: source.read_bytes("pom.xml"))


def test_unavailable_git_is_a_safe_error(repository: tuple[Path, str], tmp_path: Path) -> None:
    root, commit = repository
    assert_source_error("tool_unavailable", lambda: PublicGitSource(root, PUBLIC_URL, commit, str(tmp_path / "missing-git")))


def directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Directory symlinks are unavailable")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            pytest.skip("Directory symlink/junction creation is unavailable")


def test_root_or_ancestor_link_is_rejected(repository: tuple[Path, str], git_executable: str, tmp_path: Path) -> None:
    root, commit = repository
    linked_parent = tmp_path / "linked-parent"
    directory_link(linked_parent, root.parent)
    assert_source_error("invalid_source", lambda: PublicGitSource(linked_parent / root.name, PUBLIC_URL, commit, git_executable))
    assert_source_error("invalid_source", lambda: PublicGitSource(linked_parent, PUBLIC_URL, commit, git_executable))


def test_git_directory_link_and_worktree_git_file_are_rejected(
    repository: tuple[Path, str], git_executable: str, tmp_path: Path
) -> None:
    root, commit = repository
    actual_git = root / ".fixture-real-git"
    (root / ".git").rename(actual_git)
    directory_link(root / ".git", actual_git)
    assert_source_error("invalid_source", lambda: make_source(repository, git_executable))
    worktree = tmp_path / "linked-worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert_source_error("invalid_source", lambda: PublicGitSource(worktree, PUBLIC_URL, commit, git_executable))


def test_mutated_child_tree_is_not_accepted_as_public_source(
    repository: tuple[Path, str], git_executable: str
) -> None:
    root, commit = repository
    tree_oid = git(git_executable, root, "rev-parse", f"{commit}:src").decode().strip()
    object_path = root / ".git" / "objects" / tree_oid[:2] / tree_oid[2:]
    original = zlib.decompress(object_path.read_bytes())
    mutated = original.replace(b"main\0", b"evil\0")
    assert mutated != original
    object_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    object_path.write_bytes(zlib.compress(mutated))
    with pytest.raises(SourceError) as captured:
        make_source(repository, git_executable)
    assert captured.value.code in {"invalid_source", "source_changed"}


def test_status_has_one_overall_deadline(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(repository, git_executable)
    clock = iter([100.0, 111.0])
    monkeypatch.setattr(source_module.time, "monotonic", lambda: next(clock, 112.0))
    assert_source_error("timeout", source.status)


@pytest.mark.parametrize("header", ["[include]", '[includeIf "gitdir:*"]'])
def test_config_includes_are_rejected_before_git_can_read_outside_files(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch,
    header: str,
) -> None:
    root, _ = repository
    with (root / ".git" / "config").open("ab") as config:
        config.write(f"\n{header}\n    path = ../synthetic-private-config\n".encode())
    monkeypatch.setattr(source_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Git ran before include was rejected"))
    assert_source_error("invalid_source", lambda: make_source(repository, git_executable))


def test_bom_config_include_is_rejected_before_git(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = repository
    config_path = root / ".git" / "config"
    original = config_path.read_bytes()
    config_path.write_bytes(
        b"\xef\xbb\xbf[include]\n    path = ../synthetic-private-config\n" + original
    )
    monkeypatch.setattr(source_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Git ran before BOM include was rejected"))
    assert_source_error("invalid_source", lambda: make_source(repository, git_executable))


@pytest.mark.parametrize("relative", ["commondir", "info/grafts", "objects/info/alternates", "objects/info/http-alternates"])
def test_shared_and_alternate_metadata_is_rejected_before_git(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    root, _ = repository
    path = root / ".git" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"../synthetic-outside-repository\n")
    monkeypatch.setattr(source_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Git ran before alternate metadata was rejected"))
    assert_source_error("invalid_source", lambda: make_source(repository, git_executable))


def test_oversized_git_config_is_rejected_before_read_or_git(
    repository: tuple[Path, str], git_executable: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = repository
    with (root / ".git" / "config").open("ab") as config:
        config.write(b"#" * (source_module.MAX_GIT_CONFIG_BYTES + 1))
    monkeypatch.setattr(source_module.os, "open", lambda *args, **kwargs: pytest.fail("Oversized config was opened"))
    monkeypatch.setattr(source_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Git ran before config was rejected"))
    assert_source_error("limit_exceeded", lambda: make_source(repository, git_executable))


@pytest.mark.parametrize("relative", ["objects", "objects/pack", "objects/info", "loose"])
def test_git_object_directory_junctions_are_rejected_before_git(
    repository: tuple[Path, str], git_executable: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    root, commit = repository
    relative = "objects/" + commit[:2] if relative == "loose" else relative
    original = root / ".git" / relative
    relocated = tmp_path / "metadata-link-target"
    original.rename(relocated)
    directory_link(original, relocated)
    monkeypatch.setattr(source_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Git followed an object directory link"))
    assert_source_error("invalid_source", lambda: make_source(repository, git_executable))


@pytest.mark.parametrize("relative", ["config", "index", "object"])
def test_git_metadata_hardlinks_are_rejected_before_git(
    repository: tuple[Path, str], git_executable: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    root, commit = repository
    relative = f"objects/{commit[:2]}/{commit[2:]}" if relative == "object" else relative
    original = root / ".git" / relative
    relocated = tmp_path / "metadata-hardlink-target"
    original.rename(relocated)
    os.link(relocated, original)
    monkeypatch.setattr(source_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Git followed hard-linked metadata"))
    assert_source_error("invalid_source", lambda: make_source(repository, git_executable))
