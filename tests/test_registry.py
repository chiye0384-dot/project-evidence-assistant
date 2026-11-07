from pathlib import Path

from fastapi.testclient import TestClient

from project_evidence.app import create_app


def registration(source_path: Path) -> dict[str, str]:
    return {
        "name": "Public Java sample",
        "source_path": str(source_path),
        "public_url": "https://github.com/example/public-java",
        "commit": "a" * 40,
    }


def test_registration_survives_restart_without_reading_source(tmp_path: Path) -> None:
    source_path = tmp_path / "source-does-not-exist"
    data_dir = tmp_path / "agent-data"
    with TestClient(create_app(data_dir), base_url="http://localhost:8123") as client:
        created = client.post("/api/projects", json=registration(source_path))
        assert created.status_code == 201
        project = created.json()
        assert project["source_status"] == "unverified"
        assert project["analysis_status"] == "not_started"
        assert not source_path.exists()

    with TestClient(create_app(data_dir), base_url="http://localhost:8123") as reopened:
        assert reopened.get("/api/projects").json() == [project]
        assert reopened.get(f"/api/projects/{project['id']}").json() == project
        health = reopened.get("/api/health").json()
        assert health["model_status"] == "not_configured"
        assert health["stage"] == "M1-engine"
        assert health["capabilities"] == {
            "project_registry": True,
            "source_analysis": False,
            "agent_generation": False,
            "agent_tasks": True,
            "source_tools": True,
            "structure_facts": True,
            "learning_records": True,
            "query_generation": False,
            "submission_generation": False,
            "consumption_generation": False,
            "judging_generation": False,
        }


def test_delete_removes_only_agent_record(tmp_path: Path) -> None:
    source_path = tmp_path / "original-source"
    source_path.mkdir()
    sentinel = source_path / "keep.java"
    sentinel.write_text("// original source must survive\n", encoding="utf-8")
    original = sentinel.read_bytes()

    with TestClient(create_app(tmp_path / "agent-data"), base_url="http://127.0.0.1") as client:
        project_id = client.post("/api/projects", json=registration(source_path)).json()["id"]
        assert client.delete(f"/api/projects/{project_id}").status_code == 204
        assert client.get("/api/projects").json() == []
        assert client.get(f"/api/projects/{project_id}").status_code == 404

    assert sentinel.read_bytes() == original
    assert list(source_path.iterdir()) == [sentinel]
