"""
Combined ASGI: Flask (web) + MCP at https://host/mcp

MCP SDK owns the /mcp route. Flask handles everything else (UI + OAuth).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
flask_asgi = WSGIMiddleware(flask_app)


def _is_mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


class EstacleASGI:
    """Route /mcp* → MCP server; everything else → Flask."""

    def __init__(self, mcp: ASGIApp, flask: ASGIApp):
        self.mcp = mcp
        self.flask = flask

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "lifespan":
            # Run both lifespan if needed — minimal no-op passthrough to flask
            await self.flask(scope, receive, send)
            return

        if scope["type"] in ("http", "websocket"):
            path = scope.get("path") or ""
            if _is_mcp_path(path):
                # Attach auth user from Bearer token (tools enforce login)
                headers = {
                    k.decode().lower(): v.decode()
                    for k, v in scope.get("headers") or []
                }
                auth = headers.get("authorization") or ""
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
                    await self._mcp_with_cors(scope, receive, send)
                finally:
                    set_user(None)
                return

        await self.flask(scope, receive, send)

    async def _mcp_with_cors(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            async def send_options(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers") or [])
                    headers.extend([
                        (b"access-control-allow-origin", b"*"),
                        (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
                        (b"access-control-allow-headers", b"Authorization, Content-Type, Accept, Mcp-Session-Id"),
                        (b"access-control-max-age", b"86400"),
                    ])
                    message = {**message, "status": 204, "headers": headers}
                await send(message)
            # Minimal empty body
            await send({
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
                    (b"access-control-allow-headers", b"Authorization, Content-Type, Accept, Mcp-Session-Id"),
                ],
            })
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"access-control-allow-origin", b"*"))
                headers.append((b"access-control-allow-headers", b"Authorization, Content-Type, Accept, Mcp-Session-Id"))
                message = {**message, "headers": headers}
            await send(message)

        await self.mcp(scope, receive, send_wrapper)


app = EstacleASGI(mcp_asgi, flask_asgi)
