"""OAuth 2.0 (auth code + PKCE) storage for Estacle MCP connectors."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.crm_service import default_db_path


def _conn():
    path = default_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def ensure_oauth_tables():
    conn = _conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                client_secret TEXT,
                name TEXT,
                redirect_uris TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                redirect_uri TEXT NOT NULL,
                code_challenge TEXT,
                code_challenge_method TEXT,
                scope TEXT,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                access_token TEXT PRIMARY KEY,
                refresh_token TEXT,
                user_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                scope TEXT,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()
        # Public MCP client for Claude custom connectors (no secret; PKCE required)
        row = conn.execute(
            "SELECT client_id FROM oauth_clients WHERE client_id = ?",
            ("estacle-mcp-public",),
        ).fetchone()
        if not row:
            conn.execute(
                """INSERT INTO oauth_clients (client_id, client_secret, name, redirect_uris)
                   VALUES (?, ?, ?, ?)""",
                (
                    "estacle-mcp-public",
                    None,
                    "Estacle MCP (Claude)",
                    # Claude / Anthropic redirect URIs (plus localhost for tests)
                    "https://claude.ai/api/mcp/auth_callback\n"
                    "https://claude.com/api/mcp/auth_callback\n"
                    "http://127.0.0.1/callback\n"
                    "http://localhost/callback",
                ),
            )
            conn.commit()
    finally:
        conn.close()


def get_client(client_id: str) -> Optional[dict]:
    ensure_oauth_tables()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def client_redirects(client: dict) -> list:
    return [u.strip() for u in (client.get("redirect_uris") or "").splitlines() if u.strip()]


def create_auth_code(client_id, user_id, redirect_uri, code_challenge, method, scope) -> str:
    ensure_oauth_tables()
    code = secrets.token_urlsafe(32)
    exp = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO oauth_codes
               (code, client_id, user_id, redirect_uri, code_challenge, code_challenge_method, scope, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, client_id, user_id, redirect_uri, code_challenge, method or "S256", scope or "mcp", exp),
        )
        conn.commit()
    finally:
        conn.close()
    return code


def consume_auth_code(code: str) -> Optional[dict]:
    ensure_oauth_tables()
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM oauth_codes WHERE code = ?", (code,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("used"):
            return None
        if d["expires_at"] < datetime.utcnow().isoformat():
            return None
        conn.execute("UPDATE oauth_codes SET used = 1 WHERE code = ?", (code,))
        conn.commit()
        return d
    finally:
        conn.close()


def verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if not challenge:
        return True  # allow if no challenge recorded (dev only)
    method = (method or "S256").upper()
    if method == "S256":
        import base64
        dig = hashlib.sha256(verifier.encode("ascii")).digest()
        calc = base64.urlsafe_b64encode(dig).decode("ascii").rstrip("=")
        return secrets.compare_digest(calc, challenge)
    if method == "PLAIN":
        return secrets.compare_digest(verifier, challenge)
    return False


def issue_tokens(user_id: int, client_id: str, scope: str = "mcp",
                 access_hours: int = 24, refresh_days: int = 30) -> dict:
    ensure_oauth_tables()
    access = secrets.token_urlsafe(48)
    refresh = secrets.token_urlsafe(48)
    exp = (datetime.utcnow() + timedelta(hours=access_hours)).isoformat()
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO oauth_tokens (access_token, refresh_token, user_id, client_id, scope, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (access, refresh, user_id, client_id, scope or "mcp", exp),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": access_hours * 3600,
        "refresh_token": refresh,
        "scope": scope or "mcp",
    }


def get_user_for_token(access_token: str) -> Optional[dict]:
    if not access_token:
        return None
    ensure_oauth_tables()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM oauth_tokens WHERE access_token = ?", (access_token,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d["expires_at"] < datetime.utcnow().isoformat():
            return None
        user = conn.execute(
            "SELECT id, email, full_name, role, is_active FROM users WHERE id = ?",
            (d["user_id"],),
        ).fetchone()
        if not user or not user["is_active"]:
            return None
        return dict(user)
    finally:
        conn.close()


def authenticate_user(email: str, password: str) -> Optional[dict]:
    from werkzeug.security import check_password_hash
    ensure_oauth_tables()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, email, full_name, role, password, is_active FROM users WHERE lower(email)=lower(?)",
            (email.strip(),),
        ).fetchone()
        if not row or not row["is_active"]:
            return None
        if not check_password_hash(row["password"], password):
            return None
        if row["role"] not in ("admin", "agent"):
            return None
        return {"id": row["id"], "email": row["email"], "full_name": row["full_name"], "role": row["role"]}
    finally:
        conn.close()
