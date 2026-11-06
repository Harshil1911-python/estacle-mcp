"""
Combined ASGI: Flask web UI + MCP Streamable HTTP.

Claude must receive real MCP protocol responses at /mcp.
Bearer tokens are attached to context when present; tools enforce login.
"""
from __future__ import annotations

import os
import sys
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

from mcp_estacle.server import create_mcp_asgi_app

mcp_asgi = create_mcp_asgi_app()


class NormalizeMCPPath:
    """Map /mcp → /mcp/ so requests never fall through to Flask."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope.get("type") == "http":
            path = scope.get("path") or ""
            if path == "/mcp":
                scope = dict(scope)
                scope["path"] = "/mcp/"
        await self.app(scope, receive, send)


class MCPAuthContextMiddleware(BaseHTTPMiddleware):
    """
    Attach OAuth user to context when Bearer token is valid.
    Do NOT block the request — Claude needs MCP protocol responses
    even before/during session setup. Tools call get_user() and fail closed.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, Accept, Mcp-Session-Id"
            )
            return response

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
        # Help clients discover OAuth when unauthenticated tool use fails
        if user is None:
            base = (
                os.environ.get("PUBLIC_BASE_URL")
                or os.environ.get("RENDER_EXTERNAL_URL")
                or str(request.base_url).rstrip("/")
            )
            response.headers.setdefault(
                "WWW-Authenticate",
                f'Bearer realm="Estacle MCP", resource="{base}/mcp"',
            )
        return response


mcp_app = Starlette(
    routes=[Mount("/", app=mcp_asgi)],
    middleware=[Middleware(MCPAuthContextMiddleware)],
)

app = NormalizeMCPPath(
    Starlette(
        routes=[
            Mount("/mcp", app=mcp_app),
            Mount("/", app=WSGIMiddleware(flask_app)),
        ],
    )
)
