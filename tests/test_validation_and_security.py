from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from project_evidence.app import create_app


def payload() -> dict[str, str]:
    return {
        "name": "Sample",
        "source_path": "D:\\public-java",
        "public_url": "https://github.com/example/public-java",
        "commit": "a" * 40,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_url", "not a URL"),
        ("public_url", "file:///D:/public-java"),
        ("public_url", "https://user:password@github.com/example/public-java"),
        ("public_url", "http://github.com/example/public-java"),
        ("public_url", "https://github.com:443/example/public-java"),
        ("public_url", "https://github.com/example/public-java?token=synthetic"),
        ("public_url", "https://github.com/example/public-java#readme"),
        ("public_url", "https://github.com/example/public-java/blob/main/pom.xml"),
        ("public_url", "https://example.com/example/public-java"),
        ("commit", "a" * 39),
        ("commit", "z" * 40),
    ],
)
def test_invalid_source_metadata_is_not_saved(
    tmp_path: Path, field: str, value: str
) -> None:
    data = payload()
    data[field] = value
    with TestClient(create_app(tmp_path), base_url="http://localhost:8123") as client:
        assert client.post("/api/projects", json=data).status_code == 422
        assert client.get("/api/projects").json() == []


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "http://localhost:9999", "null"],
)
def test_cross_origin_modifications_are_rejected(tmp_path: Path, origin: str) -> None:
    with TestClient(create_app(tmp_path), base_url="http://localhost:8123") as client:
        assert client.post("/api/projects", json=payload(), headers={"Origin": origin}).status_code == 403
        assert client.get("/api/projects").json() == []


@pytest.mark.parametrize(
    "host", ["evil.example", "localhost.evil.example", "127.0.0.1:99999", "[invalid"]
)
def test_unexpected_hosts_are_rejected(tmp_path: Path, host: str) -> None:
    with TestClient(create_app(tmp_path), base_url="http://localhost:8123") as client:
        assert client.get("/api/health", headers={"Host": host}).status_code == 400


def test_rejected_input_is_not_echoed_in_validation_error(tmp_path: Path) -> None:
    data = payload()
    data["api_key"] = "synthetic-not-a-real-credential"
    with TestClient(create_app(tmp_path), base_url="http://localhost:8123") as client:
        response = client.post("/api/projects", json=data)
        assert response.status_code == 422
        assert data["api_key"] not in response.text


def test_same_origin_browser_and_no_origin_cli_remain_usable(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path), base_url="http://localhost:8123") as client:
        browser = client.post(
            "/api/projects", json=payload(),
            headers={"Origin": "http://localhost:8123", "Sec-Fetch-Site": "same-origin"},
        )
        assert browser.status_code == 201
        assert client.post("/api/projects", json=payload()).status_code == 201


def test_ipv6_loopback_host_and_origin_are_supported(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path), base_url="http://[::1]:8123") as client:
        response = client.post(
            "/api/projects", json=payload(), headers={"Origin": "http://[::1]:8123"}
        )
        assert response.status_code == 201
