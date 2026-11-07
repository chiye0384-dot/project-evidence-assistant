"""Read only public GitHub metadata; never send local files or credentials."""

from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
import re
import time

import httpx

from .source import SourceError


class _PublicApiRateLimit(SourceError):
    pass


class _PublicPage(HTMLParser):
    def __init__(self, capture_commit=False):
        super().__init__()
        self.meta, self.scripts, self.parts = {}, [], []
        self.capture_commit, self.active = capture_commit, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = attrs.get("name", "")
        if tag == "meta" and name in {"octolytics-dimension-repository_nwo", "octolytics-dimension-repository_public", "octolytics-dimension-repository_id"}:
            if name in self.meta:
                raise ValueError("duplicate identity")
            self.meta[name] = attrs.get("content")
        if self.capture_commit and tag == "script" and attrs.get("type") == "application/json" and attrs.get("data-target") == "react-app.embeddedData":
            self.active = True

    def handle_data(self, value):
        if self.active:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.parts, self.active = [], False


def repository_name(url: str) -> str:
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?", url,
        re.IGNORECASE,
    )
    if match is None or any(part in {".", ".."} for part in match.groups()):
        raise SourceError("invalid_source", "请提供标准公开GitHub仓库地址。")
    owner, repo = match.groups()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo:
        raise SourceError("invalid_source", "仓库名称不能为空。")
    return f"{owner}/{repo}"


class PublicGitHubVerifier:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    @staticmethod
    def _json(client: httpx.Client, endpoint: str, deadline: float) -> dict:
        if time.monotonic() >= deadline:
            raise SourceError("verification_unavailable", "公开版本核对超过总时限，请稍后重试。")
        try:
            with client.stream("GET", f"https://api.github.com{endpoint}") as response:
                if response.status_code in {403, 429}:
                    if response.headers.get("x-ratelimit-remaining") == "0" and response.headers.get("x-ratelimit-resource") == "core":
                        raise _PublicApiRateLimit("verification_unavailable", "GitHub公开API配额耗尽。")
                    raise SourceError("verification_unavailable", "GitHub核对暂不可用或达到请求上限，请稍后重试。")
                if response.status_code != 200:
                    raise SourceError("unverified", "无法在公开GitHub接口核对该仓库或提交。")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if time.monotonic() >= deadline:
                        raise SourceError("verification_unavailable", "公开版本核对超过总时限，请稍后重试。")
                    body.extend(chunk)
                    if len(body) > 262144:
                        raise SourceError("limit_exceeded", "公开核对响应超过限制。")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("Unexpected metadata type")
                return result
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise SourceError("verification_unavailable", "公开版本核对失败；未读取本地源码。") from None

    def verify(self, public_url: str, commit: str) -> dict:
        repository = repository_name(public_url)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise SourceError("invalid_source", "需要40位完整提交编号。")
        deadline = time.monotonic() + 16.0
        with httpx.Client(
            transport=self.transport, timeout=httpx.Timeout(8.0),
            follow_redirects=False, trust_env=False,
            headers={"Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2026-03-10",
                     "User-Agent": "project-evidence-assistant/0.1"},
        ) as client:
            try:
                repo = self._json(client, f"/repos/{repository}", deadline)
            except _PublicApiRateLimit:
                return self._verify_web(client, repository, commit, deadline)
            if (repo.get("private") is not False or repo.get("visibility") != "public"
                    or str(repo.get("full_name", "")).lower() != repository.lower()):
                raise SourceError("unverified", "该来源未被核对为指定的公开仓库。")
            try:
                version = self._json(client, f"/repos/{repository}/git/commits/{commit}", deadline)
            except _PublicApiRateLimit:
                return self._verify_web(client, repository, commit, deadline)
            tree = version.get("tree", {}).get("sha") if isinstance(version.get("tree"), dict) else None
            if version.get("sha") != commit or not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40}", tree):
                raise SourceError("unverified", "公开提交或源码树编号不一致。")
        license_info = repo.get("license")
        spdx = license_info.get("spdx_id") if isinstance(license_info, dict) else None
        return {
            "public_url": f"https://github.com/{repository}", "commit": commit,
            "tree_oid": tree, "verified_at": datetime.now(timezone.utc).isoformat(),
            "license_spdx": spdx if isinstance(spdx, str) and re.fullmatch(r"[A-Za-z0-9.+-]{1,80}", spdx) else "unreported",
            "license_spdx_origin": "github_current_repository_metadata",
            "verification_method": "github_public_repository_and_git_commit",
        }

    @staticmethod
    def _page(client, url, deadline, capture_commit=False):
        if time.monotonic() >= deadline:
            raise SourceError("verification_unavailable", "公开核对超过总时限。")
        try:
            client.cookies.clear()
            with client.stream("GET", url, headers={"Accept": "text/html"}) as response:
                if response.status_code != 200 or not response.headers.get("content-type", "").lower().startswith("text/html"):
                    raise SourceError("unverified", "官方公开页面无法核对，未读取本地源码。")
                raw = bytearray()
                for chunk in response.iter_bytes(65536):
                    if time.monotonic() >= deadline:
                        raise SourceError("verification_unavailable", "公开核对超过总时限。")
                    raw.extend(chunk)
                    if len(raw) > 786432:
                        raise SourceError("limit_exceeded", "公开核对页面超过限制。")
            parsed = _PublicPage(capture_commit)
            parsed.feed(raw.decode("utf-8"))
            return parsed, hashlib.sha256(raw).hexdigest()
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise SourceError("verification_unavailable", "公开页面格式无法安全核对；未保留网页原文。") from None

    def _verify_web(self, client, repository, commit, deadline):
        # A separate anonymous public surface, never a login/challenge bypass.
        # No cached page, arbitrary URL, credentials, redirect, or page execution.
        base = f"https://github.com/{repository}"
        repo_page, repo_hash = self._page(client, base, deadline)
        commit_page, commit_hash = self._page(client, f"{base}/commit/{commit}", deadline, True)
        try:
            ids = []
            for page in (repo_page, commit_page):
                meta = page.meta
                if meta["octolytics-dimension-repository_nwo"].lower() != repository.lower() or meta["octolytics-dimension-repository_public"] != "true":
                    raise ValueError
                identifier = meta["octolytics-dimension-repository_id"]
                if not re.fullmatch(r"[1-9][0-9]{0,19}", identifier):
                    raise ValueError
                ids.append(identifier)
            if ids[0] != ids[1] or len(commit_page.scripts) != 1:
                raise ValueError
            route = json.loads(commit_page.scripts[0])["payload"]["commitRoute"]
            remote, repo = route["commit"], route["repo"]
            if (remote["oid"] != commit or remote["url"].lower() != f"/{repository}/commit/{commit}".lower()
                    or type(repo["id"]) is not int or str(repo["id"]) != ids[0]
                    or f"{repo['ownerLogin']}/{repo['name']}".lower() != repository.lower()
                    or repo["public"] is not True or repo["private"] is not False):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise SourceError("unverified", "公开仓库身份或完整提交无法一致核对。") from None
        return {"public_url": base, "commit": commit, "public_commit_oid": commit,
                "repository_id": ids[0], "verified_at": datetime.now(timezone.utc).isoformat(),
                "license_spdx": "unreported", "license_spdx_origin": "github_web_not_reported",
                "verification_method": "github_anonymous_public_commit_page",
                "verification_response_sha256": [repo_hash, commit_hash]}
