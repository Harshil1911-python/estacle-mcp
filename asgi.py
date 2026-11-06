"""
Estacle ASGI entrypoint for Render / uvicorn.

Routes:
  /mcp  and  /mcp/*  -> authenticated MCP Streamable HTTP
  everything else   -> Flask (web UI + OAuth)

Unauthenticated MCP requests receive an OAuth 401 challenge. This is the
signal Claude needs to detect the connector as requiring sign-in.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app as create_flask_app
from core.oauth_store import ensure_oauth_tables, get_user_for_token
from mcp_estacle.context import set_user

try:
    from a2wsgi import WSGIMiddleware
except ImportError:
    from starlette.middleware.wsgi import WSGIMiddleware

flask_app = create_flask_app()

from routes.oauth import oauth_bp
if "oauth" not in flask_app.blueprints:
    flask_app.register_blueprint(oauth_bp)

ensure_oauth_tables()

from mcp_estacle.server import create_mcp_asgi_app, mcp as mcp_server

mcp_raw = create_mcp_asgi_app(path="/mcp")
flask_asgi = WSGIMiddleware(flask_app)


def _is_mcp(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _public_base(scope: Scope) -> str:
    """Return the canonical public origin used by OAuth resource metadata."""
    configured = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if configured:
        return configured.rstrip("/")

    # Local development fallback. Production deployments should set
    # PUBLIC_BASE_URL so the challenge always points at the canonical host.
    headers = {
        k.decode("latin1").lower(): v.decode("latin1")
        for k, v in (scope.get("headers") or [])
    }
    host = headers.get("host", "localhost:5000")
    scheme = scope.get("scheme", "https")
    forwarded_proto = headers.get("x-forwarded-proto")
    if forwarded_proto in ("http", "https"):
        scheme = forwarded_proto
    return f"{scheme}://{host}".rstrip("/")


class MCPAuthASGI:
    """Require an Estacle OAuth bearer token before dispatching MCP requests."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def _unauthorized(self, scope: Scope, send: Send):
        base = _public_base(scope)
        metadata_url = f"{base}/.well-known/oauth-protected-resource"
        body = (
            '{"error":"unauthorized",'
            '"error_description":"Sign in with your Estacle admin or agent account."}'
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (
                    b"www-authenticate",
                    f'Bearer resource_metadata="{metadata_url}", scope="mcp"'.encode("latin1"),
                ),
                (b"access-control-allow-origin", b"*"),
                (b"access-control-expose-headers", b"WWW-Authenticate"),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await send({
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
                    (b"access-control-allow-headers",
                     b"Authorization, Content-Type, Accept, Mcp-Session-Id"),
                    (b"access-control-expose-headers", b"WWW-Authenticate, Mcp-Session-Id"),
                    (b"access-control-max-age", b"86400"),
                ],
            })
            await send({"type": "http.response.body", "body": b""})
            return

        user = None
        if scope["type"] in ("http", "websocket"):
            headers = {
                k.decode("latin1").lower(): v.decode("latin1")
                for k, v in (scope.get("headers") or [])
            }
            auth = headers.get("authorization") or ""
            token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
            if token:
                user = get_user_for_token(token)

            # MCP must never fall through as an anonymous session. Claude detects
            # OAuth from this 401 + WWW-Authenticate resource_metadata challenge.
            if scope["type"] == "http" and user is None:
                await self._unauthorized(scope, send)
                return

        set_user(user)
        try:
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers") or [])
                    headers.append((b"access-control-allow-origin", b"*"))
                    headers.append((
                        b"access-control-allow-headers",
                        b"Authorization, Content-Type, Accept, Mcp-Session-Id",
                    ))
                    headers.append((
                        b"access-control-expose-headers",
                        b"WWW-Authenticate, Mcp-Session-Id",
                    ))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_wrapper)
        finally:
            set_user(None)


mcp_asgi = MCPAuthASGI(mcp_raw)


@asynccontextmanager
async def lifespan(app: Starlette):
    sm = getattr(mcp_server, "session_manager", None)
    if sm is not None and hasattr(sm, "run"):
        async with sm.run():
            yield
        return
    router = getattr(mcp_raw, "router", None)
    if router is not None and hasattr(router, "lifespan_context"):
        async with router.lifespan_context(mcp_raw):
            yield
        return
    yield


class PathRouter:
    """Explicit path router — /mcp never falls through to Flask."""

    def __init__(self, mcp: ASGIApp, flask: ASGIApp, inner: ASGIApp):
        self.mcp = mcp
        self.flask = flask
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "lifespan":
            await self.inner(scope, receive, send)
            return

        if scope["type"] in ("http", "websocket"):
            path = scope.get("path") or ""
            if _is_mcp(path):
                await self.mcp(scope, receive, send)
                return

        await self.flask(scope, receive, send)


_starlette = Starlette(routes=[], lifespan=lifespan)
app = PathRouter(mcp_asgi, flask_asgi, _starlette)
