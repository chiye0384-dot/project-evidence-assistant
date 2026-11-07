"""Conservative path rules applied before any source blob is read."""

from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import urlsplit


class SourceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


ALLOWED_SUFFIXES = frozenset({
    ".java", ".xml", ".md", ".txt", ".properties", ".yaml", ".yml",
    ".json", ".js", ".ts", ".tsx", ".vue", ".css", ".html", ".sql",
})
DENIED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".agents", ".codex", ".aws", ".ssh",
    ".idea", ".vscode", ".venv", "venv", "__pycache__", "target", "build",
    "dist", "out", "node_modules", "logs", "log", "data", "runtime",
    "secret", "secrets", "credential", "credentials", "privatekey", "privatekeys",
})
_SECRET_NAME = re.compile(
    r"(^|[._-])(secrets?|credentials?|private[-_]?keys?|passwords?|api[-_]?keys?|tokens?)([._-]|$)",
    re.IGNORECASE,
)
_LOCAL_CONFIGURATION = re.compile(
    r"^application(?:[._-].*)?[-_.](?:local|private|secret)(?:\.[^.]+)$", re.IGNORECASE
)
_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ROOT_LICENSE = re.compile(r"^(?:license|licence|copying|notice)(?:\.(?:txt|md))?$", re.IGNORECASE)


def validate_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path or len(path) > 2048:
        raise SourceError("invalid_path", "Use a non-empty relative source path")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in path):
        raise SourceError("invalid_path", "Control characters are not allowed in source paths")
    if "\\" in path or ":" in path or path.startswith("/"):
        raise SourceError("invalid_path", "Source paths must be relative POSIX paths")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise SourceError("invalid_path", "Empty and traversal path components are not allowed")
    return path


def is_allowed_path(path: str) -> bool:
    validate_relative_path(path)
    parts = path.split("/")
    lower = [part.lower() for part in parts]
    if any(part in DENIED_DIRECTORIES for part in lower[:-1]):
        return False
    if any(part.startswith((".env", ".smtp")) for part in lower):
        return False
    if any(_SECRET_NAME.search(part) for part in lower):
        return False
    if lower[-1] == "m2-next-chat-handoff.md":
        return False
    if _LOCAL_CONFIGURATION.fullmatch(lower[-1]):
        return False
    if len(parts) == 1 and _ROOT_LICENSE.fullmatch(parts[0]):
        return True
    return PurePosixPath(path).suffix.lower() in ALLOWED_SUFFIXES


def require_allowed_path(path: str) -> str:
    validate_relative_path(path)
    if not is_allowed_path(path):
        raise SourceError("denied", "This file is excluded from source access")
    return path


def normalize_github_repository(value: str) -> str:
    """Normalize standard HTTPS/SSH origins without contacting any remote."""
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise SourceError("invalid_source", "Source repository address is not supported")
    if value.startswith("git@github.com:"):
        repository = value[len("git@github.com:"):]
    else:
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError as error:
            raise SourceError("invalid_source", "Source repository address is not supported") from error
        if parts.hostname != "github.com" or parts.query or parts.fragment:
            raise SourceError("invalid_source", "Source must identify a GitHub repository")
        if parts.scheme == "https":
            if parts.username is not None or parts.password is not None or port not in {None, 443}:
                raise SourceError("invalid_source", "Repository credentials and custom ports are not supported")
        elif parts.scheme == "ssh":
            if parts.username != "git" or parts.password is not None or port not in {None, 22}:
                raise SourceError("invalid_source", "SSH repository address is not supported")
        else:
            raise SourceError("invalid_source", "Only standard GitHub HTTPS or SSH origins are supported")
        repository = parts.path.lstrip("/")
    repository = repository.removesuffix("/").removesuffix(".git")
    if not _GITHUB_REPOSITORY.fullmatch(repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise SourceError("invalid_source", "Source must identify one GitHub repository")
    return "https://github.com/" + repository.lower()
