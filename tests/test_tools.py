import hashlib
import json

import pytest

from project_evidence.source import SourceError
from project_evidence.structure import summarize
from project_evidence.tools import (
    MAX_FILE_BYTES, MAX_OUTPUT_CHARS, MAX_SCAN_BYTES,
    SourceTools, contains_sensitive_content,
)


class FakeSource:
    commit = "a" * 40
    public_url = "https://github.com/example/original-fixture"

    def __init__(self, files, error=None):
        self.files = files
        self.reads = []
        self.error = error

    def entries(self):
        if self.error:
            raise self.error
        return [{"path": path, "blob_oid": hashlib.sha1(raw).hexdigest(), "size": len(raw)}
                for path, raw in self.files.items()]

    def read_bytes(self, path):
        self.reads.append(path)
        return self.files[path]

    def status(self):
        return {"commit": self.commit, "head_commit": self.commit,
                "working_tree_changed": None, "working_tree_checked": False}


@pytest.mark.parametrize("pattern", ["../*.java", "/a", "C:*.java", "a\\b", "a//b", "./*.java"])
def test_invalid_patterns_never_read(pattern):
    source = FakeSource({"A.java": b"class A {}"})
    assert SourceTools(source).find_files(pattern)["status"] == "invalid_argument"
    assert source.reads == []


@pytest.mark.parametrize("query", ["", "x" * 201, "a\nb", None])
def test_invalid_query(query):
    source = FakeSource({"A.java": b"class A {}"})
    assert SourceTools(source).search_text(query)["status"] == "invalid_query"
    assert source.reads == []


@pytest.mark.parametrize("text", [
    'password="real-password"', "api_key: actual-example-credential",
    'token="' + "ghp_" + "a" * 30 + '"',
    "-----BEGIN PRIVATE KEY-----\nprivate material",
    "Authorization: Bearer " + "a" * 32,
    "<password>private-value</password>",
    'token="${TOKEN}" + "actual-value"',
])
def test_secret_filter_and_whole_file_rejection(text):
    source = FakeSource({"A.java": ("safe line\n" + text).encode()})
    result = SourceTools(source).read_excerpt("A.java", 1, 1)
    assert result["status"] == "denied"
    assert "excerpt" not in result
    assert "real-password" not in json.dumps(result)


@pytest.mark.parametrize("text", ['password=""', "password=${DB_PASSWORD}", 'api_key="${API_KEY}"'])
def test_empty_and_placeholder_allowed(text):
    assert not contains_sensitive_content(text)


def test_lines_hash_and_real_range():
    raw = b"first\r\nsecond\r\nthird\r\n"
    result = SourceTools(FakeSource({"A.java": raw})).read_excerpt("A.java", 2, 80)
    assert result["excerpt"] == "second\nthird"
    assert (result["start_line"], result["end_line"], result["total_lines"]) == (2, 3, 3)
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["source_state"]["working_tree_changed"] is None
    assert result["source_state"]["working_tree_checked"] is False


def test_unicode_separator_does_not_invent_line_number():
    result = SourceTools(FakeSource({"A.java": "a\u2028b\nc".encode()})).search_text("c")
    assert result["items"][0]["line"] == 2


def test_excerpt_line_and_character_limits():
    source = FakeSource({"A.java": ("short\n" * 200).encode(), "B.java": b"x" * 15_000})
    result = SourceTools(source).read_excerpt("A.java", 1, 200)
    assert result["end_line"] == 120 and result["truncated"]
    result = SourceTools(source).read_excerpt("B.java")
    assert len(result["excerpt"]) <= 12_000
    assert result["text"] == result["excerpt"]
    assert result["end_line"] == 1 and result["last_line_truncated"]


def test_serialized_output_bound_including_escaped_characters():
    result = SourceTools(FakeSource({"A.java": b'"' * 15_000})).read_excerpt("A.java")
    assert len(json.dumps(result, ensure_ascii=False)) <= MAX_OUTPUT_CHARS
    assert result["truncated"] and result["end_line"] == 1


def test_missing_empty_and_outside_range():
    tools = SourceTools(FakeSource({"A.java": b"one\n", "empty.java": b""}))
    assert tools.read_excerpt("missing.java")["status"] == "not_found"
    result = tools.read_excerpt("A.java", 9, 10)
    assert result["status"] == "no_results" and result["start_line"] is None
    assert tools.read_excerpt("empty.java")["end_line"] is None


@pytest.mark.parametrize("path", [".smtp.qq.local", "docs/M2-NEXT-CHAT-HANDOFF.md", ".env.example", "target/App.java"])
def test_forbidden_path_is_denied_before_manifest_access(path):
    source = FakeSource({})
    def forbidden_entries():
        raise AssertionError("Forbidden request must not enumerate files")
    source.entries = forbidden_entries
    result = SourceTools(source).read_excerpt(path)
    assert result["status"] == "denied" and source.reads == []


@pytest.mark.parametrize("raw,status", [(b"\xff", "invalid_encoding"), (b"a\x00b", "binary"), (b"x" * (MAX_FILE_BYTES + 1), "file_too_large")], ids=["invalid-utf8", "binary", "oversized"])
def test_unsafe_files(raw, status):
    assert SourceTools(FakeSource({"A.java": raw})).read_excerpt("A.java")["status"] == status


def test_literal_case_sensitive_search_and_window():
    raw = ("Needle\n" + "x" * 1000 + "needle.*" + "y" * 1000).encode()
    result = SourceTools(FakeSource({"A.java": raw})).search_text("needle.*")
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["line"] == 2 and "needle.*" in item["excerpt"]
    assert item["line_truncated"] and item["start_column"] > 1


def test_search_file_and_byte_budgets():
    source = FakeSource({f"F{i:03}.java": b"none" for i in range(250)})
    result = SourceTools(source).search_text("missing")
    assert len(source.reads) == 200 and result["truncated"]
    source = FakeSource({f"F{i:03}.java": b"x" * MAX_FILE_BYTES for i in range(17)})
    result = SourceTools(source).search_text("missing")
    assert len(source.reads) == 16
    assert result["scanned_bytes"] == MAX_SCAN_BYTES and result["truncated"]


def test_search_does_not_return_partial_safe_lines_from_secret_file():
    result = SourceTools(FakeSource({"A.java": b"needle\npassword=real-secret"})).search_text("needle")
    assert result["items"] == [] and result["skipped_files"] == 1


def test_source_error_does_not_echo_exception_message():
    result = SourceTools(FakeSource({}, SourceError("unrecognized", "private path and secret"))).find_files()
    assert result["status"] == "source_error"
    assert "private path" not in json.dumps(result)


def test_structure_uses_own_module_not_parent_and_real_evidence():
    pom = b'<project>\n<parent><artifactId>parent</artifactId></parent>\n<artifactId>child</artifactId>\n<modules><module>api</module></modules>\n</project>\n'
    java = b'package fixture;\n@SpringBootApplication\nclass App {}\n@RestController\nclass Web {\n@GetMapping("/hello")\n}\n'
    result = summarize(SourceTools(FakeSource({"pom.xml": pom, "src/App.java": java})))
    names = [fact for fact in result["facts"] if fact["kind"] == "module_name"]
    assert len(names) == 1 and names[0]["value"] == "child" and names[0]["line"] == 3
    mappings = [fact for fact in result["facts"] if fact["kind"] == "controller_mapping"]
    assert [fact["line"] for fact in mappings] == [4, 6]
    assert all(fact["candidate"] and fact["sha256"] for fact in result["facts"])
    assert any("调用图" in text for text in result["limitations"])


def test_structure_limits_and_denied_content():
    files = {f"F{i:03}.java": b"@RestController\n" for i in range(90)}
    files["secret.java"] = b"password=real-secret\n@RestController"
    source = FakeSource(files)
    result = summarize(SourceTools(source))
    assert len(source.reads) <= 80 and result["truncated"]
    assert "real-secret" not in json.dumps(result)


def test_tool_deadline_discards_result_after_slow_read(monkeypatch):
    clock = [0.0]
    source = FakeSource({"A.java": b"needle"})
    original_read = source.read_bytes
    def slow_read(path):
        clock[0] = 21.0
        return original_read(path)
    source.read_bytes = slow_read
    monkeypatch.setattr("project_evidence.tools.monotonic", lambda: clock[0])
    result = SourceTools(source).search_text("needle")
    assert result["status"] == "timeout" and result["truncated"]
    assert "items" not in result


def test_state_cache_is_short_and_preserves_unknown(monkeypatch):
    clock = [0.0]
    source = FakeSource({"A.java": b"one"})
    calls = []
    original_status = source.status
    def counted_status():
        calls.append(True)
        return original_status()
    source.status = counted_status
    monkeypatch.setattr("project_evidence.tools.monotonic", lambda: clock[0])
    tools = SourceTools(source)
    tools.find_files()
    result = tools.read_excerpt("A.java")
    assert len(calls) == 1 and result["source_state"]["working_tree_changed"] is None
    clock[0] = 2.1
    tools.find_files()
    assert len(calls) == 2


def test_structure_deadline(monkeypatch):
    clock = [0.0]
    source = FakeSource({"A.java": b"@RestController"})
    original_read = source.read_bytes
    def slow_read(path):
        clock[0] = 31.0
        return original_read(path)
    source.read_bytes = slow_read
    monkeypatch.setattr("project_evidence.structure.monotonic", lambda: clock[0])
    result = summarize(SourceTools(source))
    assert result["status"] == "timeout" and result["truncated"]
