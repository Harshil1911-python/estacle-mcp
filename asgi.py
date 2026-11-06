"""
Combined ASGI application: Flask (Estacle web) + MCP Streamable HTTP + OAuth.

Run (Render / production):
  uvicorn asgi:app --host 0.0.0.0 --port $PORT

Local web-only (no MCP):
  python app.py
"""
from __future__ import annotations

import os
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

# Ensure project root on path
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app as create_flask_app
from core.oauth_store import ensure_oauth_tables, get_user_for_token
from mcp_estacle.context import set_user

# Flask WSGI → ASGI (a2wsgi preferred; Starlette WSGIMiddleware is deprecated)
try:
    from a2wsgi import WSGIMiddleware
except ImportError:
    from starlette.middleware.wsgi import WSGIMiddleware

flask_app = create_flask_app()

# Register OAuth blueprint on Flask (metadata + authorize + token)
from routes.oauth import oauth_bp
if "oauth" not in flask_app.blueprints:
    flask_app.register_blueprint(oauth_bp)

ensure_oauth_tables()

# MCP ASGI
from mcp_estacle.server import create_mcp_asgi_app, mcp as mcp_server

mcp_asgi = create_mcp_asgi_app()


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Require Bearer token for /mcp (except CORS preflight)."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow unauthenticated GETs for discovery if any
        path = request.url.path
        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

        user = get_user_for_token(token) if token else None
        if user is None and os.environ.get("MCP_INSECURE_DEV") == "1":
            # Local-only bypass: act as first admin (never enable in production)
            from core.crm_service import db_conn
            with db_conn() as conn:
                row = conn.execute(
                    "SELECT id, email, full_name, role, is_active FROM users WHERE role='admin' AND is_active=1 LIMIT 1"
                ).fetchone()
            user = dict(row) if row else None
        if user is None:
            base = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or str(request.base_url).rstrip("/")
            return JSONResponse(
                {"error": "unauthorized", "error_description": "Bearer token required. Authorize via OAuth."},
                status_code=401,
                headers={
                    "WWW-Authenticate": f'Bearer realm="Estacle MCP", resource="{base}/mcp"',
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                },
            )
        set_user(user)
        try:
            response = await call_next(request)
        finally:
            set_user(None)
        return response


class CORSMiddlewareSimple(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
                    "Access-Control-Max-Age": "86400",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
        return response


# Mount MCP under /mcp with auth middleware
mcp_app = Starlette(
    routes=[Mount("/", app=mcp_asgi)],
    middleware=[
        Middleware(CORSMiddlewareSimple),
        Middleware(MCPAuthMiddleware),
    ],
)

app = Starlette(
    routes=[
        Mount("/mcp", app=mcp_app),
        Mount("/", app=WSGIMiddleware(flask_app)),
    ],
)
