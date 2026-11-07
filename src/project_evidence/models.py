"""Metadata contracts; registering a project does not verify its source."""

from datetime import datetime
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class ProjectRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    source_path: str = Field(min_length=1, max_length=2048)
    public_url: HttpUrl
    commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")

    @field_validator("source_path")
    @classmethod
    def reject_null_path(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Source path must not contain a null character")
        return value

    @field_validator("public_url")
    @classmethod
    def reject_url_credentials(cls, value: HttpUrl) -> HttpUrl:
        if len(str(value)) > 500:
            raise ValueError("Public source URL is too long")
        if value.username is not None or value.password is not None:
            raise ValueError("Public source URL must not contain credentials")
        return value

    @field_validator("public_url", mode="before")
    @classmethod
    def require_github_repository(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Public source URL must be a GitHub repository address")
        value = value.strip()
        if re.fullmatch(
            r"https://github\.com/[^\s/?#]+/[^\s/?#]+/?", value, re.IGNORECASE
        ) is None:
            raise ValueError("Use https://github.com/owner/repository without credentials, query or fragment")
        return value

    @field_validator("commit")
    @classmethod
    def normalize_commit(cls, value: str) -> str:
        return value.lower()


class ProjectRecord(BaseModel):
    id: UUID
    name: str
    source_path: str
    public_url: str
    commit: str
    created_at: datetime
    source_status: Literal["unverified", "verified"] = "unverified"
    analysis_status: Literal["not_started"] = "not_started"


class VersionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class FindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(default="*.java", min_length=1, max_length=240)
    limit: int = Field(default=100, ge=1, le=100)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    path_glob: str = Field(default="*.java", min_length=1, max_length=240)
    limit: int = Field(default=30, ge=1, le=100)


class ExcerptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1000)
    start_line: int = Field(default=1, ge=1, le=1000000)
    end_line: int = Field(default=80, ge=1, le=1000000)
