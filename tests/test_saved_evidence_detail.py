"""Owned saved citations bypass paging, never source/fee permission gates."""
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx

from project_evidence.app import create_app
from test_m0_api import register, repository, git_exe


def saved(store, project_id, index=0):
    return store.save_evidence(project_id, {
        "commit": "a" * 40, "public_url": "https://github.com/example/public-java",
        "path": "src/Example.java", "blob_oid": "b" * 40, "sha256": "c" * 64,
        "start_line": index + 1, "end_line": index + 1,
        "text": "class Example {}", "truncated": False,
    })


def test_old_citation_detail_is_free_when_not_on_first_fifty(tmp_path):
    def no_network(request):
        raise AssertionError("Saved evidence must not access public verification or a model")
    data = tmp_path / "agent"; missing = tmp_path / "never-opened-source"
    with TestClient(create_app(data, github_transport=httpx.MockTransport(no_network)), base_url="http://localhost:8123") as client:
        route = register(client, missing, "a" * 40); project_id = UUID(route.rsplit("/", 1)[-1])
        store = client.app.state.project_store
        first = saved(store, project_id)
        for index in range(1, 52): saved(store, project_id, index)
        page = client.get(route + "/evidence").json()
        assert len(page) == 50 and first["id"] not in {item["id"] for item in page}
        result = client.get(route + "/evidence/" + first["id"])
        assert result.status_code == 200 and result.json()["text"] == first["text"]
        assert result.json()["source_verified"] is False and result.json()["status"] == "current"
        assert client.get(route + "/tasks").json() == []
        assert client.get("/api/model/status").json()["spending"]["status"] == "not_enabled"
    with TestClient(create_app(data), base_url="http://localhost:8123") as reopened:
        assert reopened.get(route + "/evidence/" + first["id"]).json()["text"] == first["text"]
    assert not missing.exists()


def test_detail_does_not_cross_projects_or_reveal_missing_identity(tmp_path):
    with TestClient(create_app(tmp_path / "agent"), base_url="http://localhost:8123") as client:
        route = register(client, tmp_path / "source-a", "a" * 40)
        other = register(client, tmp_path / "source-b", "a" * 40)
        item = saved(client.app.state.project_store, UUID(route.rsplit("/", 1)[-1]))
        assert client.get(other + "/evidence/" + item["id"]).status_code == 404
        assert client.get(route + "/evidence/" + str(uuid4())).status_code == 404
        assert client.get(route + "/evidence/not-a-uuid").status_code == 422
        assert client.get('/api/projects/' + str(uuid4()) + '/evidence/' + item['id']).status_code == 404
        assert client.delete(route).status_code == 204  # Synthetic Agent records only.
        assert client.get(route + "/evidence/" + item["id"]).status_code == 404
    assert not (tmp_path / "source-a").exists() and not (tmp_path / "source-b").exists()


def test_detail_preserves_license_and_old_version_review_without_new_source(tmp_path, repository, git_exe):
    root, commit, controller, _, transport = repository; before = controller.read_bytes()
    with TestClient(create_app(tmp_path / "agent", github_transport=transport, git_executable=git_exe), base_url="http://localhost:8123") as client:
        route = register(client, root, commit)
        proof = client.post(route + "/source/verify").json()
        source = client.post(route + "/tools/read", json={"path": controller.relative_to(root).as_posix(),
                                                         "start_line": 1, "end_line": 8}).json()
        result = client.get(route + "/evidence/" + source["evidence_id"]).json()
        assert result == client.get(route + "/evidence").json()[0]
        assert result["license_sha256"] == proof["license_sha256"] and result["license_path"] == "LICENSE"
        assert client.patch(route + "/version", json={"commit": "d" * 40}).status_code == 200
        old = client.get(route + "/evidence/" + source["evidence_id"]).json()
        assert old["status"] == "needs_review" and old["source_verified"] is False
        assert old["text"] == source["text"] and old["commit"] == commit
        assert old["license_sha256"] == proof["license_sha256"]
    assert controller.read_bytes() == before
