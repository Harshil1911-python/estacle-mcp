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
    _ensure_knowledge_tables(db)
    uid = session["user_id"]
    settings = _get_settings(uid)
    chats = db.execute(
        """SELECT c.id, c.title, c.created_at,
                  (SELECT content FROM ai_messages m WHERE m.chat_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_message
           FROM ai_chats c
           WHERE c.user_id = ?
           ORDER BY c.id DESC LIMIT 30""",
        (uid,),
    ).fetchall()
    return render_template(
        "admin_ai.html",
        settings=settings,
        default_model=DEFAULT_MODEL,
        chats=[dict(r) for r in chats],
    )


@admin.route("/admin/ai/chats", methods=["GET"])
@login_required
@role_required("admin")
def admin_ai_chats():
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    uid = session["user_id"]
    rows = db.execute(
        """SELECT c.id, c.title, c.created_at,
                  (SELECT COUNT(*) FROM ai_messages m WHERE m.chat_id = c.id) AS message_count
           FROM ai_chats c WHERE c.user_id = ? ORDER BY c.id DESC LIMIT 40""",
        (uid,),
    ).fetchall()
    return jsonify({"ok": True, "chats": [dict(r) for r in rows]})


@admin.route("/admin/ai/chats/<int:chat_id>", methods=["GET"])
@login_required
@role_required("admin")
def admin_ai_chat_detail(chat_id):
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    uid = session["user_id"]
    chat = db.execute(
        "SELECT id, title, created_at FROM ai_chats WHERE id = ? AND user_id = ?",
        (chat_id, uid),
    ).fetchone()
    if not chat:
        return jsonify({"ok": False, "error": "Chat not found"}), 404
    messages = db.execute(
        """SELECT role, content, created_at FROM ai_messages
           WHERE chat_id = ? AND role IN ('user', 'assistant')
           ORDER BY id ASC""",
        (chat_id,),
    ).fetchall()
    return jsonify({
        "ok": True,
        "chat": dict(chat),
        "messages": [dict(m) for m in messages],
    })


@admin.route("/admin/ai/chats/<int:chat_id>", methods=["DELETE"])
@login_required
@role_required("admin")
def admin_ai_chat_delete(chat_id):
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    uid = session["user_id"]
    chat = db.execute(
        "SELECT id FROM ai_chats WHERE id = ? AND user_id = ?",
        (chat_id, uid),
    ).fetchone()
    if not chat:
        return jsonify({"ok": False, "error": "Chat not found"}), 404
    db.execute("DELETE FROM ai_messages WHERE chat_id = ?", (chat_id,))
    db.execute("DELETE FROM ai_chats WHERE id = ?", (chat_id,))
    db.commit()
    return jsonify({"ok": True})


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




def _ensure_knowledge_tables(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.commit()


def _rag_context(db, user_id: int, query: str, limit: int = 4) -> str:
    """Simple keyword RAG over company knowledge docs."""
    _ensure_knowledge_tables(db)
    rows = db.execute(
        "SELECT id, title, content, source_name FROM ai_knowledge WHERE user_id = ? ORDER BY id DESC LIMIT 80",
        (user_id,),
    ).fetchall()
    if not rows:
        return ""
    q = (query or "").lower()
    tokens = [t for t in q.replace(",", " ").split() if len(t) > 2]
    scored = []
    for r in rows:
        text = ((r["title"] or "") + "\n" + (r["content"] or "")).lower()
        score = sum(1 for t in tokens if t in text) if tokens else 0
        # Always keep recent docs with a low base score so empty queries still get context
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], -x[1]["id"]))
    picked = [r for s, r in scored if s > 0][:limit]
    if not picked:
        picked = [r for _, r in scored[:limit]]
    chunks = []
    for r in picked:
        body = (r["content"] or "")[:1800]
        chunks.append("### {}\n{}".format(r["title"] or "Document", body))
    return "\n\n".join(chunks)


def _extract_pdf_text(raw: bytes) -> str:
    """Best-effort PDF text extraction without heavy deps."""
    try:
        from pypdf import PdfReader  # type: ignore
        import io
        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages[:30]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                pass
        text = "\n".join(parts).strip()
        if text:
            return text
    except Exception:
        pass
    # Fallback: pull readable strings from PDF streams
    try:
        data = raw.decode("latin-1", errors="ignore")
        import re
        chunks = re.findall(r"\((?:\\.|[^\\)]){3,}\)", data)
        cleaned = []
        for ch in chunks:
            s = ch[1:-1]
            s = s.replace("\\n", "\n").replace("\\r", "").replace("\\t", " ")
            s = re.sub(r"\\[0-9]{3}", " ", s)
            s = re.sub(r"[^\x20-\x7E\n]", " ", s)
            if len(s.strip()) > 4:
                cleaned.append(s.strip())
        return "\n".join(cleaned[:400])
    except Exception:
        return ""


@admin.route("/admin/ai/upload", methods=["POST"])
@login_required
@role_required("admin")
def admin_ai_upload():
    """Upload image, video, PDF brochure, or screenshot for Serenia AI."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file"}), 400
    fname = secure_filename(f.filename)
    ext = os.path.splitext(fname)[1].lower()
    image_ext = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    video_ext = {".mp4", ".webm", ".mov", ".m4v"}
    doc_ext = {".pdf", ".txt", ".md", ".csv"}
    if ext not in image_ext and ext not in video_ext and ext not in doc_ext:
        return jsonify({
            "ok": False,
            "error": "Supported: images, video (mp4/webm/mov), PDF, txt, md, csv",
        }), 400

    if ext in video_ext:
        kind = "video"
    elif ext in doc_ext:
        kind = "document"
    else:
        kind = "image"

    unique = "{}_{}".format(int(time.time() * 1000), fname)
    dest_dir = current_app.config.get("UPLOAD_FOLDER") or os.path.join(
        current_app.root_path, "static", "uploads", "properties"
    )
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, unique)
    raw = f.read()
    with open(path, "wb") as out:
        out.write(raw)
    url = "/static/uploads/properties/" + unique

    extracted_text = ""
    if ext == ".pdf":
        extracted_text = _extract_pdf_text(raw)
    elif ext in {".txt", ".md", ".csv"}:
        try:
            extracted_text = raw.decode("utf-8", errors="ignore")
        except Exception:
            extracted_text = ""

    return jsonify({
        "ok": True,
        "filename": unique,
        "url": url,
        "original": fname,
        "kind": kind,
        "extracted_text": (extracted_text or "")[:12000],
    })


@admin.route("/admin/ai/knowledge", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_ai_knowledge():
    """List or add company knowledge docs used for RAG answers."""
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    _ensure_knowledge_tables(db)
    uid = session["user_id"]

    if request.method == "GET":
        rows = db.execute(
            "SELECT id, title, source_name, length(content) as chars, created_at "
            "FROM ai_knowledge WHERE user_id = ? ORDER BY id DESC",
            (uid,),
        ).fetchall()
        return jsonify({
            "ok": True,
            "docs": [dict(r) for r in rows],
        })

    # multipart: file and/or title+content
    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    source_name = (request.form.get("source_name") or "").strip()
    f = request.files.get("file")
    if f and f.filename:
        fname = secure_filename(f.filename)
        raw = f.read()
        ext = os.path.splitext(fname)[1].lower()
        text = ""
        if ext == ".pdf":
            text = _extract_pdf_text(raw)
        else:
            try:
                text = raw.decode("utf-8", errors="ignore")
            except Exception:
                text = ""
        if not text.strip():
            return jsonify({
                "ok": False,
                "error": "Could not read text from file. Use PDF with text, or .txt / .md / .csv.",
            }), 400
        content = text
        if not title:
            title = fname
        if not source_name:
            source_name = fname
    if not content.strip():
        return jsonify({"ok": False, "error": "Document content is required"}), 400
    if not title:
        title = "Company document"
    cur = db.execute(
        "INSERT INTO ai_knowledge (user_id, title, content, source_name) VALUES (?, ?, ?, ?)",
        (uid, title[:200], content[:200000], source_name[:200] or None),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "title": title})


@admin.route("/admin/ai/knowledge/<int:doc_id>", methods=["DELETE"])
@login_required
@role_required("admin")
def admin_ai_knowledge_delete(doc_id):
    migrate_db()
    db = get_db()
    _ensure_knowledge_tables(db)
    uid = session["user_id"]
    db.execute(
        "DELETE FROM ai_knowledge WHERE id = ? AND user_id = ?",
        (doc_id, uid),
    )
    db.commit()
    return jsonify({"ok": True})

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
        kinds = [str(a.get("kind") or "") for a in attachments_early if a]
        if any(k == "document" for k in kinds):
            message = "Read the uploaded brochure or document and create or update CRM records from it."
        elif any(k == "video" for k in kinds):
            message = "Use the uploaded video(s) and locate or update the related property."
        else:
            message = "Use the uploaded image or screenshot and create or update matching CRM records."

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

    # RAG: company knowledge grounded answers
    rag = _rag_context(db, uid, message)
    system_content = SYSTEM_PROMPT
    if rag:
        system_content = (
            SYSTEM_PROMPT
            + "\n\nCOMPANY KNOWLEDGE (answer from this when relevant; do not invent policy):\n"
            + rag
        )

    messages = [{"role": "system", "content": system_content}]

    # Last 3 messages: load from DB for this chat (excluding the user msg just inserted)
    db_hist = db.execute(
        """SELECT role, content FROM ai_messages
           WHERE chat_id = ? AND role IN ('user','assistant')
           ORDER BY id DESC LIMIT 4""",
        (chat_id,),
    ).fetchall()
    # db_hist[0] is the message we just inserted; prior up to 2 → total 3 with current
    prior = list(reversed([dict(r) for r in db_hist[1:3]]))
    for h in prior:
        if h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})

    # Optional client history as fallback if DB empty
    if len(prior) == 0 and history:
        short_history = []
        for h in history:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                short_history.append({"role": role, "content": content})
        for h in short_history[-2:]:
            messages.append(h)

    attachments = data.get("attachments") or []
    user_content = message
    if attachments:
        parts = []
        extracted_blocks = []
        for a in attachments:
            if not a:
                continue
            fn = str(a.get("filename") or a)
            kind = str(a.get("kind") or "image")
            parts.append("{} ({})".format(fn, kind))
            et = (a.get("extracted_text") or "").strip()
            if et:
                extracted_blocks.append("---- Extracted from {} ----\n{}".format(fn, et[:6000]))
        if parts:
            user_content = (
                message
                + "\n\n[User uploaded media: "
                + ", ".join(parts)
                + ". Media is already uploaded — do NOT ask the user to re-upload. "
                + "For photos use attach_image; for videos use attach_video. "
                + "If the user asked to add a client, property, or user, create it now with best-effort data "
                + "(optional fields may be empty) and attach the file. "
                + "For brochure/PDF text below, extract fields and call create tools.]"
            )
        if extracted_blocks:
            user_content += "\n\n" + "\n\n".join(extracted_blocks)
    # Auto-fetch public URLs so the model always has page text (no "I can't scrape" refusals)
    import re as _re
    from core.ai_tools import exec_fetch_url
    urls = _re.findall(r'https?://[^\s<>"\']+', message or '')
    scraped_blocks = []
    for u in urls[:2]:
        result = exec_fetch_url({"url": u, "max_chars": 10000}, uid)
        if result.get("ok") and result.get("text"):
            scraped_blocks.append(
                "---- Content fetched from {} ----\n{}".format(u, result["text"][:10000])
            )
        else:
            scraped_blocks.append(
                "---- Could not fetch {} ----\n{}".format(u, result.get("error") or "unavailable")
            )
    if scraped_blocks:
        user_content += (
            "\n\n[Page content was retrieved server-side. Use it below. "
            "Do NOT say you cannot scrape or access websites. "
            "Summarize or create CRM records from this content.]\n\n"
            + "\n\n".join(scraped_blocks)
        )
    elif urls:
        user_content += (
            "\n\n[Detected URL(s): "
            + ", ".join(urls[:3])
            + ". Call fetch_url now. Never claim you cannot access the web.]"
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



@admin.route("/admin/ai/prompts", methods=["GET"])
@login_required
@role_required("admin")
def admin_ai_prompts():
    """Prompt library entries managed under Settings → Serenia AI."""
    migrate_db()
    db = get_db()
    _ensure_ai_tables(db)
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.commit()
    uid = session["user_id"]
    rows = db.execute(
        "SELECT id, title, text FROM ai_prompts WHERE user_id = ? ORDER BY id DESC",
        (uid,),
    ).fetchall()
    prompts = [dict(r) for r in rows]
    if not prompts:
        prompts = [
            {"title": "CRM overview", "text": "Analyse my CRM and give a clear summary of properties, clients, deals, and tasks."},
            {"title": "Available properties", "text": "List available properties in a compact table with price, city, and status."},
            {"title": "Open deals", "text": "Show all open deals with stage, value, client, and agent."},
            {"title": "Add demo property", "text": "Create a sample property with realistic demo data in Demo City."},
            {"title": "Leads to contact", "text": "List clients with status lead and suggest a next follow-up step."},
            {"title": "How to add a deal", "text": "Explain step by step how to add a deal in Estacle and offer to create one."},
            {"title": "Upcoming appointments", "text": "Show upcoming appointments with client, property, agent, and time."},
            {"title": "Pipeline health", "text": "Summarize the deals pipeline by stage and highlight deals that need attention."},
        ]
    return jsonify({"ok": True, "prompts": prompts})
