"""Real local Git fixtures plus mocked metadata; no claim of live GitHub/model quality."""

import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from fastapi.testclient import TestClient
import httpx
import pytest
from test_public_web_verify import transport as public_page_transport

from project_evidence.app import create_app
from project_evidence.public_verify import PublicGitHubVerifier
from project_evidence.source import SourceError


@pytest.fixture
def git_exe():
    value = os.environ.get("PROJECT_EVIDENCE_TEST_GIT") or shutil.which("git")
    assert value, "A real Git executable is required for the M0 integration gate"
    return value


def git(exe, root, *arguments):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run([exe, "-C", str(root), *arguments], env=env,
                            capture_output=True, check=True, timeout=15, text=True, encoding="utf-8")
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path, git_exe):
    root = tmp_path / "source"
    root.mkdir()
    git(git_exe, root, "init", "-q")
    git(git_exe, root, "config", "user.name", "Fixture")
    git(git_exe, root, "config", "user.email", "fixture@example.invalid")
    git(git_exe, root, "remote", "add", "origin", "https://github.com/example/public-java.git")
    (root / "LICENSE").write_text("MIT License\nCopyright synthetic fixture\n", encoding="utf-8")
    (root / "pom.xml").write_text('<project><modelVersion>4.0.0</modelVersion><groupId>sample</groupId><artifactId>demo</artifactId></project>', encoding="utf-8")
    directory = root / "src/main/java/example"
    directory.mkdir(parents=True)
    controller = directory / "DemoController.java"
    controller.write_text('@RestController\nclass DemoController {\n @GetMapping("/hello")\n String hello() { return "hello"; }\n}\n', encoding="utf-8")
    (directory / "Config.java").write_text('class Config { String apiKey = "synthetic-fixture-value-never-a-real-secret"; }', encoding="utf-8")
    git(git_exe, root, "add", ".")
    git(git_exe, root, "commit", "-qm", "Fixture version one")
    sha = git(git_exe, root, "rev-parse", "HEAD")
    commits = {sha: git(git_exe, root, "rev-parse", "HEAD^{tree}")}

    def metadata(request):
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert "authorization" not in request.headers
        if request.url.path == "/repos/example/public-java":
            return httpx.Response(200, json={"full_name": "example/public-java", "private": False,
                                             "visibility": "public", "license": {"spdx_id": "MIT"}})
        commit = request.url.path.rsplit("/", 1)[-1]
        assert commit in commits
        return httpx.Response(200, json={"sha": commit, "tree": {"sha": commits[commit]}})

    return root, sha, controller, commits, httpx.MockTransport(metadata)


def register(client, root, sha):
    result = client.post("/api/projects", json={"name": "M0 fixture", "source_path": str(root),
                                                "public_url": "https://github.com/example/public-java", "commit": sha})
    assert result.status_code == 201
    return f"/api/projects/{result.json()['id']}"


def test_fresh_public_page_proof_still_requires_local_commit_tree_and_license(tmp_path, repository, git_exe):
    root, sha, controller, commits, _ = repository
    mock, requests = public_page_transport(commit=sha)
    with TestClient(create_app(tmp_path / "agent", github_transport=mock, git_executable=git_exe), base_url="http://127.0.0.1") as client:
        base = register(client, root, sha)
        proof = client.post(base + "/source/verify").json()
        assert proof["status"] == "verified"
        assert proof["verification_method"] == "github_anonymous_public_commit_page"
        assert proof["tree_oid"] == commits[sha] and proof["tree_oid_origin"] == "hash_verified_git_commit_header"
        assert proof["license_sha256"] and len(requests) == 3
        result = client.post(base + "/tools/read", json={"path": controller.relative_to(root).as_posix(), "start_line": 1, "end_line": 8}).json()
        assert result["status"] == "ok" and result["evidence_id"]


def test_public_tools_history_version_and_delete(tmp_path, repository, git_exe):
    root, sha, controller, commits, transport = repository
    data = tmp_path / "agent"
    original = controller.read_bytes()
    app = create_app(data, github_transport=transport, git_executable=git_exe)
    with TestClient(app, base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        assert client.post(route + "/tools/read", json={"path": "src/main/java/example/DemoController.java"}).json()["status"] == "unverified"
        verified = client.post(route + "/source/verify").json()
        assert verified["status"] == "verified"
        assert verified["license_path"] == "LICENSE"
        assert "license_text" not in verified
        assert verified["working_tree_checked"] is False
        found = client.post(route + "/tools/find", json={"pattern": "*.java"}).json()
        assert found["status"] == "ok" and any(item["path"].endswith("DemoController.java") for item in found["items"])
        searched = client.post(route + "/tools/search", json={"query": "GetMapping"}).json()
        assert searched["status"] == "ok" and searched["items"][0]["line"] == 3
        result = client.post(route + "/tools/read", json={"path": "src/main/java/example/DemoController.java", "start_line": 2, "end_line": 4}).json()
        assert result["status"] == "ok" and result["start_line"] == 2 and result["end_line"] == 4
        assert result["commit"] == sha and result["sha256"] and result["evidence_id"]
        assert client.get(route + "/structure").json()["status"] == "ok"
        assert len(client.get(route + "/evidence").json()) == 1
        rejected = client.post(route + "/tools/read", json={"path": "src/main/java/example/Config.java"})
        assert rejected.json()["status"] == "denied"
        assert "synthetic-fixture-value" not in rejected.text
        assert len(client.get(route + "/evidence").json()) == 1

        # A new selected public version invalidates permission but retains old evidence.
        controller.write_text(controller.read_text(encoding="utf-8").replace('"hello"', '"updated"'), encoding="utf-8")
        git(git_exe, root, "add", ".")
        git(git_exe, root, "commit", "-qm", "Fixture version two")
        second = git(git_exe, root, "rev-parse", "HEAD")
        commits[second] = git(git_exe, root, "rev-parse", "HEAD^{tree}")
        assert client.get(route + "/source/status").json()["working_tree_changed"] is True
        assert client.patch(route + "/version", json={"commit": second}).status_code == 200
        old = client.get(route + "/evidence").json()[0]
        assert old["status"] == "needs_review" and old["text"] == result["text"]
        assert old["license_path"] == "LICENSE" and old["license_sha256"] == verified["license_sha256"]
        assert client.post(route + "/tools/find", json={}).json()["status"] == "unverified"
        assert client.post(route + "/source/verify").json()["status"] == "verified"

    # Restart: evidence survives; reading new source requires another manual verification.
    with TestClient(create_app(data, github_transport=transport, git_executable=git_exe), base_url="http://localhost:8123") as reopened:
        assert reopened.get(route + "/evidence").json()[0]["text"] == result["text"]
        assert reopened.get(route + "/source/status").json()["status"] == "unverified"
        assert reopened.delete(route).status_code == 204
        assert reopened.get(route + "/evidence").status_code == 404
        with reopened.app.state.project_store._connection() as connection:
            assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM source_receipts").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM structures").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM source_licenses").fetchone()[0] == 0
    assert controller.read_bytes() != original  # Only the fixture itself made this source change.
    assert (root / ".git").is_dir() and (root / "LICENSE").exists()


@pytest.mark.parametrize("mode", ["private", "redirect", "wrong_commit", "large", "malformed"])
def test_public_verification_fails_closed(mode):
    requests = []
    def response(request):
        requests.append(str(request.url))
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "http://localhost/private"})
        if mode == "large":
            return httpx.Response(200, content=b"x" * 262145)
        if mode == "malformed":
            return httpx.Response(200, content=b"not-json")
        if request.url.path.endswith("public-java"):
            return httpx.Response(200, json={"full_name": "example/public-java", "private": mode == "private", "visibility": "public"})
        return httpx.Response(200, json={"sha": "b" * 40, "tree": {"sha": "c" * 40}})
    with pytest.raises(SourceError):
        PublicGitHubVerifier(httpx.MockTransport(response)).verify("https://github.com/example/public-java", "a" * 40)
    assert len(requests) == (2 if mode == "wrong_commit" else 1)


def test_network_failure_does_not_open_local_source(tmp_path, git_exe):
    def failed(request):
        raise httpx.ConnectError("synthetic failure", request=request)
    missing = tmp_path / "never-created"
    with TestClient(create_app(tmp_path / "agent", github_transport=httpx.MockTransport(failed), git_executable=git_exe), base_url="http://localhost:8123") as client:
        route = register(client, missing, "a" * 40)
        assert client.post(route + "/source/verify").json()["status"] == "verification_unavailable"
        assert not missing.exists()


def test_concurrent_excerpt_reads_are_idempotent(tmp_path, repository, git_exe):
    root, sha, _, _, transport = repository
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe), base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        assert client.post(route + "/source/verify").json()["status"] == "verified"
        def read(_):
            return client.post(route + "/tools/read", json={"path": "src/main/java/example/DemoController.java", "start_line": 1, "end_line": 5})
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(read, range(8)))
        assert all(response.status_code == 200 and response.json()["status"] == "ok" for response in responses)
        assert len({response.json()["evidence_id"] for response in responses}) == 1
        assert len(client.get(route + "/evidence").json()) == 1


def test_old_verification_cannot_replace_newer_session(tmp_path, repository, git_exe):
    root, sha, _, _, original_transport = repository
    started, release = Event(), Event()
    counter_lock = Lock()
    calls = 0
    def blocked(request):
        nonlocal calls
        if request.url.path.endswith("public-java"):
            with counter_lock:
                calls += 1
                number = calls
            if number == 1:
                started.set()
                assert release.wait(10), "Fixture synchronization timed out"
        return original_transport.handle_request(request)
    with TestClient(create_app(tmp_path / "agent", github_transport=httpx.MockTransport(blocked), git_executable=git_exe), base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(client.post, route + "/source/verify")
            try:
                assert started.wait(5)
                latest = client.post(route + "/source/verify").json()
                assert latest["status"] == "verified"
            finally:
                release.set()
            assert old.result(timeout=15).json()["status"] == "source_changed"
        assert client.get(route + "/source/status").json()["verified_at"] == latest["verified_at"]


def test_notice_alone_does_not_grant_source_access(tmp_path, repository, git_exe):
    root, _, _, commits, transport = repository
    (root / "LICENSE").rename(root / "NOTICE")
    git(git_exe, root, "add", ".")
    git(git_exe, root, "commit", "-qm", "No license fixture")
    sha = git(git_exe, root, "rev-parse", "HEAD")
    commits[sha] = git(git_exe, root, "rev-parse", "HEAD^{tree}")
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe), base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        assert client.post(route + "/source/verify").json()["status"] == "unverified"
        assert client.post(route + "/tools/find", json={}).json()["status"] == "unverified"


def test_empty_license_cannot_grant_source_access(tmp_path, repository, git_exe):
    root, _, _, commits, transport = repository
    (root / "LICENSE").write_text("", encoding="utf-8")
    git(git_exe, root, "add", ".")
    git(git_exe, root, "commit", "-qm", "Empty license fixture")
    sha = git(git_exe, root, "rev-parse", "HEAD")
    commits[sha] = git(git_exe, root, "rev-parse", "HEAD^{tree}")
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe), base_url="http://localhost:8123") as client:
        route = register(client, root, sha)
        assert client.post(route + "/source/verify").json()["status"] == "unverified"
