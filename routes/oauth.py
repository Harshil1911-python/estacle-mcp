"""OAuth 2.0 Authorization Server endpoints for Estacle remote MCP."""
from __future__ import annotations

import os
from urllib.parse import urlencode

from flask import Blueprint, request, redirect, jsonify, render_template

from core.oauth_store import (
    ensure_oauth_tables, get_client, client_redirects, create_auth_code,
    consume_auth_code, verify_pkce, issue_tokens, authenticate_user,
)

oauth_bp = Blueprint("oauth", __name__)


def _public_base() -> str:
    base = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if base:
        return base.rstrip("/")
    # fallback from request
    return request.url_root.rstrip("/")


@oauth_bp.route("/.well-known/oauth-authorization-server")
def oauth_as_metadata():
    ensure_oauth_tables()
    base = _public_base()
    return jsonify({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["mcp"],
    })


@oauth_bp.route("/.well-known/oauth-protected-resource")
def oauth_protected_resource():
    base = _public_base()
    return jsonify({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"],
    })


@oauth_bp.route("/.well-known/oauth-protected-resource/mcp")
def oauth_protected_resource_mcp():
    return oauth_protected_resource()


@oauth_bp.route("/oauth/register", methods=["POST"])
def oauth_register():
    """Dynamic client registration (RFC 7591 subset) for Claude connectors."""
    ensure_oauth_tables()
    data = request.get_json(silent=True) or {}
    import secrets
    from core.oauth_store import _conn
    client_id = "dyn-" + secrets.token_urlsafe(16)
    redirects = data.get("redirect_uris") or []
    if isinstance(redirects, str):
        redirects = [redirects]
    if not redirects:
        return jsonify({"error": "invalid_client_metadata", "error_description": "redirect_uris required"}), 400
    name = data.get("client_name") or "MCP Client"
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO oauth_clients (client_id, client_secret, name, redirect_uris)
               VALUES (?, NULL, ?, ?)""",
            (client_id, name, "\n".join(redirects)),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({
        "client_id": client_id,
        "client_name": name,
        "redirect_uris": redirects,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }), 201


@oauth_bp.route("/oauth/authorize", methods=["GET", "POST"])
def oauth_authorize():
    ensure_oauth_tables()
    if request.method == "GET":
        client_id = request.args.get("client_id", "")
        redirect_uri = request.args.get("redirect_uri", "")
        state = request.args.get("state", "")
        challenge = request.args.get("code_challenge", "")
        method = request.args.get("code_challenge_method", "S256")
        scope = request.args.get("scope", "mcp")
        client = get_client(client_id)
        error = None
        if not client:
            error = "Unknown client_id. Use dynamic registration or estacle-mcp-public."
        elif redirect_uri not in client_redirects(client) and not redirect_uri.startswith("https://claude."):
            # allow claude.* callbacks even if not pre-listed
            if "claude." not in redirect_uri and "localhost" not in redirect_uri and "127.0.0.1" not in redirect_uri:
                error = "redirect_uri is not registered for this client."
        return render_template(
            "oauth/authorize.html",
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
            code_challenge_method=method,
            scope=scope,
            error=error,
        )

    client_id = request.form.get("client_id", "")
    redirect_uri = request.form.get("redirect_uri", "")
    state = request.form.get("state", "")
    challenge = request.form.get("code_challenge", "")
    method = request.form.get("code_challenge_method", "S256")
    scope = request.form.get("scope", "mcp")
    email = request.form.get("email", "")
    password = request.form.get("password", "")

    client = get_client(client_id)
    if not client:
        return render_template("oauth/authorize.html", error="Unknown client.", client_id=client_id,
                               redirect_uri=redirect_uri, state=state, code_challenge=challenge,
                               code_challenge_method=method, scope=scope)

    user = authenticate_user(email, password)
    if not user:
        return render_template(
            "oauth/authorize.html",
            error="Invalid credentials or account is not admin/agent.",
            client_id=client_id, redirect_uri=redirect_uri, state=state,
            code_challenge=challenge, code_challenge_method=method, scope=scope,
        )

    code = create_auth_code(client_id, user["id"], redirect_uri, challenge, method, scope)
    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if ("?" in redirect_uri) else "?"
    return redirect(redirect_uri + sep + urlencode(params))


@oauth_bp.route("/oauth/token", methods=["POST"])
def oauth_token():
    ensure_oauth_tables()
    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    grant = data.get("grant_type")
    if grant != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    code = data.get("code")
    redirect_uri = data.get("redirect_uri")
    client_id = data.get("client_id")
    verifier = data.get("code_verifier")

    row = consume_auth_code(code or "")
    if not row:
        return jsonify({"error": "invalid_grant", "error_description": "Invalid or expired code"}), 400
    if client_id and row["client_id"] != client_id:
        return jsonify({"error": "invalid_grant"}), 400
    if redirect_uri and row["redirect_uri"] != redirect_uri:
        return jsonify({"error": "invalid_grant"}), 400
    if row.get("code_challenge"):
        if not verifier or not verify_pkce(verifier, row["code_challenge"], row.get("code_challenge_method") or "S256"):
            return jsonify({"error": "invalid_grant", "error_description": "PKCE verification failed"}), 400

    tokens = issue_tokens(row["user_id"], row["client_id"], row.get("scope") or "mcp")
    return jsonify(tokens)
