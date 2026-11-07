"""Limited, deterministic structure candidates with actual source evidence."""

import json
import re
from time import monotonic
from typing import Any
import xml.etree.ElementTree as ET

from .tools import DATA_NOTICE, MAX_OUTPUT_CHARS, MAX_SCAN_BYTES, SourceTools


_JAVA_MARKERS = (
    ("boot_entry", re.compile(r"@SpringBootApplication\b")),
    ("boot_entry", re.compile(r"\bSpringApplication\s*\.\s*run\s*\(")),
    ("controller_mapping", re.compile(r"@(?:RestController|Controller|RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b")),
)
_LIMITATIONS = [
    "仅检查最多20个pom文件和80个Java文件的有限片段；截断或遗漏不能证明不存在。",
    "启动和Controller注解仅是源码候选，未证明运行时注册、完整路由或调用图。",
    "不解析完整Java语法、代理、反射、配置和消息跨进程关系。",
    "秘密筛选是保守启发式，可能拒绝无秘密文件，也不宣称穷尽检测。",
    "本单元没有模型调用、智能目录、正文生成或Agent质量验收。",
]


def _evidence(read: dict[str, Any], kind: str, line: int, excerpt: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind, "path": read["path"], "line": line,
        "excerpt": excerpt[:700], "evidence_truncated": len(excerpt) > 700,
        "text": excerpt[:700], "start_line": line, "end_line": line,
        "label": {"pom": "Maven配置候选", "module_name": "Maven模块名称",
                  "module_declaration": "Maven子模块声明",
                  "boot_entry": "Spring Boot启动候选",
                  "controller_mapping": "Controller或映射候选"}[kind],
        "sha256": read["sha256"], "blob_oid": read["blob_oid"],
        "candidate": True, **extra,
    }


def _pom_facts(read: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    text = read["excerpt"]
    lines = text.split("\n")
    facts = [_evidence(read, "pom", read["start_line"], lines[0])]
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        return facts, True
    try:
        # Parse only a complete, bounded document. Never infer a module name
        # from the first artifactId, which could belong to a parent dependency.
        if read["truncated"] or read["end_line"] != read["total_lines"]:
            return facts, True
        document = ET.fromstring(text)
        namespace = document.tag.split("}")[0] + "}" if "}" in document.tag else ""
        if document.tag != namespace + "project":
            return facts, True
        artifact = document.find(namespace + "artifactId")
        if artifact is not None and artifact.text and artifact.text.strip():
            value = artifact.text.strip()
            matches = [
                (index, line) for index, line in enumerate(lines, start=read["start_line"])
                if re.search(r"<artifactId>\s*" + re.escape(value) + r"\s*</artifactId>", line)
            ]
            if len(matches) == 1:
                number, excerpt = matches[0]
                facts.append(_evidence(read, "module_name", number, excerpt, value=value))
        modules = document.find(namespace + "modules")
        if modules is not None:
            for module in modules.findall(namespace + "module"):
                if not module.text or not module.text.strip():
                    continue
                value = module.text.strip()
                for index, line in enumerate(lines, start=read["start_line"]):
                    if re.search(r"<module>\s*" + re.escape(value) + r"\s*</module>", line):
                        facts.append(_evidence(read, "module_declaration", index, line, value=value))
                        break
        return facts, False
    except ET.ParseError:
        return facts, True


def summarize(tools: SourceTools) -> dict[str, Any]:
    deadline = monotonic() + 30.0
    result: dict[str, Any] = {
        "status": "ok", "commit": tools.source.commit, "public_url": tools.source.public_url,
        "facts": [], "truncated": False, "limitations": list(_LIMITATIONS),
        "data_notice": DATA_NOTICE, "examined_files": 0, "skipped_files": 0,
    }
    budget = 0
    for pattern, limit, kind in (("*pom.xml", 20, "pom"), ("*.java", 80, "java")):
        if monotonic() >= deadline:
            result.update(status="timeout", truncated=True)
            return result
        found = tools.find_files(pattern, limit=limit)
        if found["status"] not in {"ok", "no_results"}:
            result.update(status=found["status"], truncated=True)
            return result
        result["source_state"] = found.get("source_state", {})
        result["truncated"] = result["truncated"] or found["truncated"]
        for entry in found["items"]:
            if monotonic() >= deadline:
                result.update(status="timeout", truncated=True)
                return result
            if budget + entry["size"] > MAX_SCAN_BYTES:
                result["truncated"] = True
                return result
            budget += entry["size"]
            read = tools.read_excerpt(entry["path"], start_line=1, end_line=120)
            if monotonic() >= deadline:
                result.update(status="timeout", truncated=True)
                return result
            result["examined_files"] += 1
            if read["status"] != "ok":
                result["skipped_files"] += 1
                result["truncated"] = True
                if read["status"] in {"source_changed", "source_error", "timeout"}:
                    result["status"] = read["status"]
                    return result
                continue
            result["truncated"] = result["truncated"] or read["truncated"] or read["end_line"] < read["total_lines"]
            if kind == "pom":
                candidates, partial = _pom_facts(read)
                result["truncated"] = result["truncated"] or partial
            else:
                candidates = []
                for index, line in enumerate(read["excerpt"].split("\n"), start=read["start_line"]):
                    for candidate_kind, marker in _JAVA_MARKERS:
                        if marker.search(line):
                            candidates.append(_evidence(read, candidate_kind, index, line))
            for fact in candidates:
                result["facts"].append(fact)
                if len(json.dumps(result, ensure_ascii=False)) > MAX_OUTPUT_CHARS:
                    result["facts"].pop()
                    result["truncated"] = True
                    return result
    if not result["facts"]:
        result["status"] = "no_results"
    return result
