"""
Estacle ASGI entrypoint for Render / uvicorn.

Routes:
  /mcp  and  /mcp/*  → MCP Streamable HTTP (Claude connector)
  everything else    → Flask (web UI + OAuth)

Critical: do NOT let Flask handle /mcp (that caused Claude's 404).
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

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

# MCP app registers its endpoint at /mcp (matches Claude URL exactly)
mcp_raw = create_mcp_asgi_app(path="/mcp")
flask_asgi = WSGIMiddleware(flask_app)


def _is_mcp(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


class MCPAuthASGI:
    """Wrap MCP ASGI: attach Bearer user to context; add CORS."""

    def __init__(self, app: ASGIApp):
        self.app = app

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
            if user is None and os.environ.get("MCP_INSECURE_DEV") == "1":
                from core.crm_service import db_conn
                with db_conn() as conn:
                    row = conn.execute(
                        "SELECT id, email, full_name, role, is_active FROM users "
                        "WHERE role='admin' AND is_active=1 LIMIT 1"
                    ).fetchone()
                user = dict(row) if row else None

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
    """
    Explicit path router — /mcp never falls through to Flask.
    """

    def __init__(self, mcp: ASGIApp, flask: ASGIApp, inner: ASGIApp):
        self.mcp = mcp
        self.flask = flask
        self.inner = inner  # Starlette app that owns lifespan

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Lifespan must go to the Starlette app that defines it
        if scope["type"] == "lifespan":
            await self.inner(scope, receive, send)
            return

        if scope["type"] in ("http", "websocket"):
            path = scope.get("path") or ""
            if _is_mcp(path):
                await self.mcp(scope, receive, send)
                return

        await self.flask(scope, receive, send)


# Starlette shell only for lifespan hooks
_starlette = Starlette(routes=[], lifespan=lifespan)

app = PathRouter(mcp_asgi, flask_asgi, _starlette)
