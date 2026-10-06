"""Server-rendered pages. JSON routes stay on the API routers."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.web.routes import LoginRequired, login_required_handler, router

_APP_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = _APP_DIR / "static"

# Mermaid injects a <style> element while drawing. Scripts stay on 'self'.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

_HTML_EXACT = {"/login", "/dashboard", "/discovery", "/admin/view"}


def _sentinel_html_path(path: str) -> bool:
    if path in _HTML_EXACT:
        return True
    if path.startswith("/dashboard/fragments/") or path.startswith("/admin/view/fragments/"):
        return True
    return path.startswith("/companies/") and path.endswith("/view")


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code != 200:
            return response
        if path.startswith("vendor/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


class SentinelPagePolicy:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _sentinel_html_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_with_policy(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if _header(headers, b"content-type").startswith(b"text/html"):
                    headers = _upsert(headers, b"content-security-policy", CONTENT_SECURITY_POLICY.encode())
                    headers = _upsert(headers, b"cache-control", b"no-cache")
                    headers = _upsert(headers, b"x-content-type-options", b"nosniff")
                    headers = _upsert(headers, b"referrer-policy", b"same-origin")
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_policy)


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes:
    for key, value in headers:
        if key.lower() == name:
            return value.split(b";", 1)[0].strip().lower()
    return b""


def _upsert(headers: list[tuple[bytes, bytes]], name: bytes, value: bytes) -> list[tuple[bytes, bytes]]:
    kept = [(key, current) for key, current in headers if key.lower() != name]
    kept.append((name, value))
    return kept


def install_web(app: FastAPI) -> None:
    app.include_router(router)
    app.add_exception_handler(LoginRequired, login_required_handler)
    app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")
    app.add_middleware(SentinelPagePolicy)
