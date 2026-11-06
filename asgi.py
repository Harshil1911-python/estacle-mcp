"""
Combined ASGI: Flask web + MCP at /mcp

MCP Streamable HTTP requires its Starlette lifespan (session manager).
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
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

# Build MCP HTTP app with endpoint at "/" so Mount("/mcp") serves Claude's /mcp URL
mcp_asgi = create_mcp_asgi_app(path="/")


class MCPAuthContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Mcp-Session-Id",
                },
            )

        auth = request.headers.get("authorization") or ""
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

        user = get_user_for_token(token) if token else None
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
            response = await call_next(request)
        finally:
            set_user(None)

        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        response.headers.setdefault(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, Accept, Mcp-Session-Id",
        )
        return response


mcp_app = Starlette(
    routes=[Mount("/", app=mcp_asgi)],
    middleware=[Middleware(MCPAuthContextMiddleware)],
)


@asynccontextmanager
async def lifespan(app: Starlette):
    """Start MCP session manager lifespan if the SDK exposes one."""
    sm = getattr(mcp_server, "session_manager", None)
    if sm is not None and hasattr(sm, "run"):
        async with sm.run():
            yield
        return

    router = getattr(mcp_asgi, "router", None)
    if router is not None and hasattr(router, "lifespan_context"):
        async with router.lifespan_context(mcp_asgi):
            yield
        return

    yield


class SlashNormalize:
    """Send /mcp/ → same handling as /mcp for Claude."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope.get("type") == "http" and (scope.get("path") or "") == "/mcp/":
            scope = dict(scope)
            scope["path"] = "/mcp"
        await self.app(scope, receive, send)


flask_asgi = WSGIMiddleware(flask_app)

app = SlashNormalize(
    Starlette(
        routes=[
            Mount("/mcp", app=mcp_app),
            Mount("/", app=flask_asgi),
        ],
        lifespan=lifespan,
    )
)
