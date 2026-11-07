from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from project_evidence.models import ProjectRegistration
from project_evidence.storage import ProjectStore


def test_evidence_limit_is_atomic_and_history_not_evicted(tmp_path: Path):
    store = ProjectStore(tmp_path / "agent")
    project = store.create(ProjectRegistration(name="Fixture", source_path="D:/fixture",
                          public_url="https://github.com/example/public-java", commit="a" * 40))
    def payload(index):
        return {"commit": "a" * 40, "path": f"src/{index}.java", "start_line": 1,
                "end_line": 1, "sha256": "b" * 64, "blob_oid": "c" * 40, "text": "// fixture"}
    for index in range(999):
        store.save_evidence(project.id, payload(index))
    def add(index):
        try:
            return store.save_evidence(project.id, payload(index))
        except ValueError as error:
            assert str(error) == "evidence_limit"
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(add, (999, 1000)))
    assert sum(item is not None for item in result) == 1
    with store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1000
    first = store.save_evidence(project.id, payload(0))
    assert first["path"] == "src/0.java"  # Existing evidence survives even at the cap.
