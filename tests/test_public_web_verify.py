"""Official page contracts against synthetic transport; not live source proof."""
import json
import httpx
import pytest
from project_evidence.public_verify import PublicGitHubVerifier
from project_evidence.source import SourceError

REPO = "example/public-java"
COMMIT = "a" * 40


def page(commit=None, *, public="true", name=REPO, identifier="123", updates=None):
    meta = "".join(f'<meta name="octolytics-dimension-repository_{k}" content="{v}">' for k, v in
                   {"nwo": name, "public": public, "id": identifier}.items())
    if commit is None:
        return meta
    route = {"commit": {"oid": commit, "url": f"/{REPO}/commit/{commit}"},
             "repo": {"id": 123, "ownerLogin": "example", "name": "public-java", "public": True, "private": False}}
    if updates:
        for key, value in updates.items():
            route[key].update(value)
    return meta + '<script type="application/json" data-target="react-app.embeddedData">' + json.dumps({"payload": {"commitRoute": route}}) + '</script>'


def transport(mode="ok", commit=COMMIT):
    requests = []
    def respond(request):
        requests.append(str(request.url))
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if request.url.host == "api.github.com":
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-resource": "core"})
        repo_page = request.url.path == f"/{REPO}"
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid/login"})
        if mode == "not_found":
            return httpx.Response(404)
        body = page() if repo_page else page(commit)
        if mode == "private": body = page(commit if not repo_page else None, public="false")
        if not repo_page:
            if mode == "wrong_commit": body = page("b" * 40)
            if mode == "wrong_repo": body = page(commit, updates={"repo": {"name": "other"}})
            if mode == "wrong_id": body = page(commit, identifier="124")
            if mode == "wrong_url": body = page(commit, updates={"commit": {"url": "/other/repo/commit/"+commit}})
            if mode == "private_payload": body = page(commit, updates={"repo": {"private": True}})
            if mode == "malformed": body = body.replace('"payload"', '"unrecognized"')
            if mode == "duplicate": body += body
            if mode == "oversize": body += "x" * 786433
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8", "set-cookie": "session=synthetic-not-forwarded"}, text=body)
    return httpx.MockTransport(respond), requests


def test_rate_limit_can_use_fresh_anonymous_identity_bound_commit_page():
    mock, requests = transport()
    proof = PublicGitHubVerifier(mock).verify("https://github.com/"+REPO, COMMIT)
    assert proof["verification_method"] == "github_anonymous_public_commit_page"
    assert proof["public_commit_oid"] == COMMIT and proof["repository_id"] == "123"
    assert "tree_oid" not in proof  # Tree must still be bound by verified Git object bytes.
    assert len(proof["verification_response_sha256"]) == 2
    assert len(requests) == 3 and all(url.startswith("https://") for url in requests)
    assert "session" not in json.dumps(proof)


@pytest.mark.parametrize("mode", ["redirect", "not_found", "private", "wrong_commit", "wrong_repo", "wrong_id", "wrong_url", "private_payload", "malformed", "duplicate", "oversize"])
def test_public_page_identity_failures_do_not_create_source_permission(mode):
    mock, requests = transport(mode)
    with pytest.raises(SourceError) as error:
        PublicGitHubVerifier(mock).verify("https://github.com/"+REPO, COMMIT)
    assert error.value.code in {"unverified", "verification_unavailable", "limit_exceeded"}
    assert not any("example.invalid" in url for url in requests)
    assert "synthetic" not in str(error.value)


def test_generic_api_refusal_is_not_treated_as_quota_failure():
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(403)
    with pytest.raises(SourceError):
        PublicGitHubVerifier(httpx.MockTransport(respond)).verify("https://github.com/"+REPO, COMMIT)
    assert len(calls) == 1 and calls[0].startswith("https://api.github.com/")
