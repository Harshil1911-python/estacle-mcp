"""Estacle AI — Groq-powered CRM assistant (admin only)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from flask import (
    render_template, request, jsonify, session, current_app
)
from werkzeug.utils import secure_filename
import os
import time
from openai import OpenAI

from core.db import get_db, migrate_db
from core.helpers import login_required, role_required
from core.ai_crypto import encrypt_secret, decrypt_secret, mask_key
from core.ai_tools import (
    TOOL_SCHEMAS, CONFIRM_TOOLS, run_tool, SYSTEM_PROMPT,
)
from routes.admin import admin

DEFAULT_MODEL = "openai/gpt-oss-20b"
GROQ_BASE = "https://api.groq.com/openai/v1"


def _ensure_ai_tables(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_settings (
            user_id INTEGER PRIMARY KEY,
            api_key_enc TEXT,
            model TEXT DEFAULT 'openai/gpt-oss-20b',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_name TEXT,
            tool_payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            entity_type TEXT,
            entity_id INTEGER,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_pending (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            arguments TEXT NOT NULL,
            summary TEXT,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.commit()


def _get_settings(user_id: int):
    db = get_db()
    _ensure_ai_tables(db)
    row = db.execute(
        "SELECT api_key_enc, model FROM ai_settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return {"has_key": False, "key_mask": "", "model": DEFAULT_MODEL}
    plain = decrypt_secret(row["api_key_enc"] or "", current_app.secret_key)
    return {
        "has_key": bool(plain),
        "key_mask": mask_key(plain) if plain else "",
        "model": row["model"] or DEFAULT_MODEL,
    }


def _get_api_key(user_id: int) -> str:
    db = get_db()
    _ensure_ai_tables(db)
    row = db.execute(
        "SELECT api_key_enc FROM ai_settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return ""
    return decrypt_secret(row["api_key_enc"] or "", current_app.secret_key)


def _client_for(user_id: int):
    key = _get_api_key(user_id)
    if not key:
        return None, "No API key configured. Add your Groq API key under Settings → Estacle AI."
    return OpenAI(api_key=key, base_url=GROQ_BASE, timeout=60.0), None


@admin.route("/admin/ai")
@login_required
@role_required("admin")
def admin_ai():
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    uid = session["user_id"]
    settings = _get_settings(uid)
    return render_template(
        "admin_ai.html",
        settings=settings,
        default_model=DEFAULT_MODEL,
    )


@admin.route("/admin/ai/settings", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_ai_settings():
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    uid = session["user_id"]
    if request.method == "GET":
        return jsonify(_get_settings(uid))

    data = request.get_json(silent=True) or {}
    model = (data.get("model") or DEFAULT_MODEL).strip()
    api_key = (data.get("api_key") or "").strip()

    row = db.execute(
        "SELECT api_key_enc FROM ai_settings WHERE user_id = ?", (uid,)
    ).fetchone()

    if api_key:
        enc = encrypt_secret(api_key, current_app.secret_key)
        if row:
            db.execute(
                "UPDATE ai_settings SET api_key_enc = ?, model = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (enc, model, uid),
            )
        else:
            db.execute(
                "INSERT INTO ai_settings (user_id, api_key_enc, model) VALUES (?, ?, ?)",
                (uid, enc, model),
            )
    else:
        # update model only
        if row:
            db.execute(
                "UPDATE ai_settings SET model = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (model, uid),
            )
        else:
            db.execute(
                "INSERT INTO ai_settings (user_id, api_key_enc, model) VALUES (?, ?, ?)",
                (uid, "", model),
            )
    db.commit()
    return jsonify({"ok": True, **_get_settings(uid)})


@admin.route("/admin/ai/test", methods=["POST"])
@login_required
@role_required("admin")
def admin_ai_test():
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    # Optional one-shot key for test before save
    temp_key = (data.get("api_key") or "").strip()
    if temp_key:
        client = OpenAI(api_key=temp_key, base_url=GROQ_BASE, timeout=30.0)
        model = (data.get("model") or DEFAULT_MODEL).strip()
    else:
        client, err = _client_for(uid)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        model = _get_settings(uid)["model"]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=16,
        )
        text = (resp.choices[0].message.content or "").strip()
        return jsonify({"ok": True, "message": text or "Connected", "model": model})
    except Exception as e:
        msg = str(e)
        if "401" in msg or "Invalid" in msg or "invalid" in msg:
            msg = "Invalid API key or unauthorized."
        elif "429" in msg:
            msg = "Rate limit exceeded. Try again later."
        return jsonify({"ok": False, "error": msg}), 400




@admin.route("/admin/ai/upload", methods=["POST"])
@login_required
@role_required("admin")
def admin_ai_upload():
    """Upload an image for Estacle AI to attach to property / user / client."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file"}), 400
    fname = secure_filename(f.filename)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".webp"):
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            return jsonify({"ok": False, "error": "Images only (jpg, png, gif, webp)"}), 400
    unique = "{}_{}".format(int(time.time() * 1000), fname)
    # save under properties uploads (shared gallery) and avatars if needed later
    dest_dir = current_app.config.get("UPLOAD_FOLDER") or os.path.join(
        current_app.root_path, "static", "uploads", "properties"
    )
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, unique)
    f.save(path)
    url = "/static/uploads/properties/" + unique
    return jsonify({"ok": True, "filename": unique, "url": url, "original": fname})

@admin.route("/admin/ai/chat", methods=["POST"])
@login_required
@role_required("admin")
def admin_ai_chat():
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    chat_id = data.get("chat_id")
    history = data.get("history") or []  # optional client-side short history

    attachments_early = data.get("attachments") or []
    if not message and not attachments_early:
        return jsonify({"ok": False, "error": "Empty message"}), 400
    if not message and attachments_early:
        message = "Please use the uploaded image(s)."

    client, err = _client_for(uid)
    if err:
        return jsonify({"ok": False, "error": err, "need_key": True}), 400

    settings = _get_settings(uid)
    model = settings["model"] or DEFAULT_MODEL

    if not chat_id:
        cur = db.execute(
            "INSERT INTO ai_chats (user_id, title) VALUES (?, ?)",
            (uid, message[:80]),
        )
        db.commit()
        chat_id = cur.lastrowid
    else:
        chat_id = int(chat_id)

    db.execute(
        "INSERT INTO ai_messages (chat_id, role, content) VALUES (?, 'user', ?)",
        (chat_id, message),
    )
    db.commit()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Keep only the last 3 messages for context (user asked to store last 3)
    short_history = []
    for h in history:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and content:
            short_history.append({"role": role, "content": content})
    # Prior turns only; current user message added below → max 3 total
    for h in short_history[-2:]:
        messages.append(h)
    # Attachments from client (uploaded filenames) — hint for tools
    attachments = data.get("attachments") or []
    user_content = message
    if attachments:
        names = ", ".join(
            str(a.get("filename") or a) for a in attachments if a
        )
        if names:
            user_content = (
                message
                + "\n\n[User uploaded file(s): "
                + names
                + ". Use attach_image to link them to a property, user, or client when asked.]"
            )
    messages.append({"role": "user", "content": user_content})

    pending = None
    final_text = ""
    tool_results_public = []

    try:
        for _round in range(4):
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                max_tokens=2048,
                temperature=0.2,
            )
            choice = resp.choices[0]
            msg = choice.message
            tool_calls = getattr(msg, "tool_calls", None) or []

            if tool_calls:
                # Append assistant tool-call message
                tc_payload = []
                for tc in tool_calls:
                    tc_payload.append(
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": tc_payload,
                    }
                )

                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    if name in CONFIRM_TOOLS:
                        pid = str(uuid.uuid4())
                        # Human-readable summary — never expose SQL or raw dumps to the UI
                        if name == "bulk_delete":
                            summary = "Delete {} {} record(s): {}".format(
                                len(args.get("ids") or []),
                                (args.get("entity") or "item"),
                                ", ".join(str(i) for i in (args.get("ids") or [])[:12]),
                            )
                        elif name.startswith("delete_"):
                            key = next((k for k in args if k.endswith("_id") or k == "id"), None)
                            summary = "Delete {} {}".format(name.replace("delete_", ""), args.get(key, ""))
                        else:
                            summary = "Confirm action"
                        expires = (datetime.utcnow() + timedelta(minutes=15)).isoformat()
                        db.execute(
                            """INSERT INTO ai_pending (id, user_id, tool_name, arguments, summary, expires_at)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (pid, uid, name, json.dumps(args), summary, expires),
                        )
                        db.commit()
                        result = {
                            "ok": False,
                            "needs_confirmation": True,
                            "pending_id": pid,
                            "tool": name,
                            "arguments": args,
                            "message": "Confirmation required before this change is applied.",
                        }
                        pending = {
                            "pending_id": pid,
                            "tool": name,
                            "arguments": args,
                            "summary": summary,
                        }
                    else:
                        result = run_tool(name, args, uid)

                    tool_results_public.append({"tool": name, "result": result})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, default=str)[:8000],
                        }
                    )

                if pending:
                    # Ask model to phrase confirmation, then stop executing
                    confirm_resp = client.chat.completions.create(
                        model=model,
                        messages=messages
                        + [
                            {
                                "role": "user",
                                "content": "A destructive action needs confirmation. Briefly ask the user to confirm or cancel. Do not claim it was done.",
                            }
                        ],
                        max_tokens=400,
                        temperature=0.2,
                    )
                    final_text = confirm_resp.choices[0].message.content or "Please confirm this action."
                    break
                continue

            final_text = msg.content or ""
            break
        else:
            if not final_text:
                final_text = "Done. Check the tool results below."

    except Exception as e:
        err_s = str(e)
        if "429" in err_s:
            err_s = "Rate limit exceeded. Please wait and try again."
        elif "401" in err_s:
            err_s = "API key invalid. Update it in Settings."
        elif "output_parse_failed" in err_s or "Parsing failed" in err_s:
            err_s = (
                "The model could not format that request. "
                "Try a simpler phrasing, e.g. “Add 10 demo deals” or “Create one deal titled …”."
            )
        return jsonify({"ok": False, "error": err_s, "chat_id": chat_id}), 400

    # Strip accidental SQL / tool dumps from model text
    import re
    if final_text:
        final_text = re.sub(
            r"(?is)\b(select|insert|update|delete|drop|alter)\b[\s\S]{0,200}?(;|$)",
            "",
            final_text,
        )
        final_text = re.sub(r"(?im)^\s*(query|sql|tool call|function call)\s*[:=].*$", "", final_text)
        final_text = re.sub(r"\n{3,}", "\n\n", final_text).strip()

    db.execute(
        "INSERT INTO ai_messages (chat_id, role, content, tool_payload) VALUES (?, 'assistant', ?, ?)",
        (chat_id, final_text, json.dumps(tool_results_public, default=str)[:10000]),
    )
    db.commit()

    return jsonify(
        {
            "ok": True,
            "chat_id": chat_id,
            "reply": final_text,
            "tool_results": tool_results_public,
            "pending": pending,
            "model": model,
        }
    )


@admin.route("/admin/ai/confirm", methods=["POST"])
@login_required
@role_required("admin")
def admin_ai_confirm():
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    pending_id = data.get("pending_id")
    approve = bool(data.get("confirm"))

    row = db.execute(
        "SELECT * FROM ai_pending WHERE id = ? AND user_id = ?",
        (pending_id, uid),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "Pending action not found or expired"}), 404

    db.execute("DELETE FROM ai_pending WHERE id = ?", (pending_id,))
    db.commit()

    if not approve:
        return jsonify({"ok": True, "cancelled": True, "message": "Action cancelled."})

    args = json.loads(row["arguments"] or "{}")
    result = run_tool(row["tool_name"], args, uid)
    return jsonify({"ok": True, "executed": True, "tool": row["tool_name"], "result": result})
