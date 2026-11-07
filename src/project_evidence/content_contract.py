"""Shared bounded content checks; no model, files, or network access."""
import re
from typing import Any
from pydantic import BaseModel, ConfigDict
from .tools import contains_sensitive_content


_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"

_LOCAL_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])"
    # A real UNC path has a server and share with single separators. In Java
    # source an escaped UNC literal has four leading slashes and two separators.
    # Regex literals such as \\s*package\\s and \\bpublic\\s have two leading
    # slashes and doubled separators; those are not network paths.
    r"|(?<![\\A-Za-z0-9])\\\\[^\\/\s:*?\"<>|]+[\\/](?![\\/])[^\\/\s:*?\"<>|]+"
    r"|(?<![\\A-Za-z0-9])\\\\\\\\[^\\/\s:*?\"<>|]+\\\\(?!\\)[^\\/\s:*?\"<>|]+"
    r"|/(?:home|users|etc|var|tmp|mnt|opt|root|usr|private|workspace|workspaces)(?:/|\b)",
    re.IGNORECASE,
)

_FORBIDDEN_KEYS = frozenset({
    "sourcepath", "localpath", "apikey", "authorization", "privatekey", "password",
    "secret", "accesstoken", "chainofthought", "cot", "reasoning", "reasoningcontent",
    "thoughts", "thoughtprocess",
})

_PROTECTED_NAMES = ("m2-next-chat-handoff.md", ".smtp.qq.local")

def _safe_text(value: str) -> str:
    if _LOCAL_PATH.search(value) or any(name in value.casefold() for name in _PROTECTED_NAMES):
        raise ValueError("Local paths or protected content are not permitted")
    if contains_sensitive_content(value):
        raise ValueError("Content did not pass the sensitive-data boundary")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise ValueError("Control characters are not permitted")
    return value

def _safe_tree(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("Context nesting exceeds the allowed depth")
    if isinstance(value, str):
        _safe_text(value)
    elif isinstance(value, dict):
        if len(value) > 64:
            raise ValueError("Context object exceeds the allowed field count")
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError("Private metadata or internal reasoning is not permitted")
            _safe_text(key)
            _safe_tree(item, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 100:
            raise ValueError("Context list exceeds the allowed item count")
        for item in value:
            _safe_tree(item, depth=depth + 1)

class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")
