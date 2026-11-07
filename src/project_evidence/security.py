"""Loopback Host checks and same-origin protection for changing requests."""

import re
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


_LOCAL_AUTHORITY = re.compile(
    r"^(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$", re.IGNORECASE
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _local_origin(authority: str, scheme: str) -> tuple[str, str, int] | None:
    match = _LOCAL_AUTHORITY.fullmatch(authority)
    if match is None or scheme not in {"http", "https"}:
        return None
    hostname = match.group(1).lower().strip("[]")
    port = int(match.group(2)) if match.group(2) else (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535:
        return None
    return scheme, hostname, port


def _header_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.path or parts.query or parts.fragment:
        return None
    return _local_origin(parts.netloc, parts.scheme)


class LocalRequestMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        expected_origin = _local_origin(
            request.headers.get("host", ""), request.scope.get("scheme", "http")
        )
        if expected_origin is None:
            return JSONResponse({"detail": "Unexpected Host"}, status_code=400)

        if request.method not in _SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and _header_origin(origin) != expected_origin:
                return JSONResponse(
                    {"detail": "Cross-origin modification is not allowed"},
                    status_code=403,
                )
            # Browser writes are same-origin. No-Origin CLI clients remain
            # usable, while an explicitly cross-site browser request is denied.
            fetch_site = request.headers.get("sec-fetch-site")
            if fetch_site is not None and fetch_site not in {"same-origin", "none"}:
                return JSONResponse(
                    {"detail": "Cross-origin modification is not allowed"},
                    status_code=403,
                )

        response = await call_next(request)
        if request.scope["path"].startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
