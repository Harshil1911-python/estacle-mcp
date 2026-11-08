"""Admin settings routes."""

from flask import (
    render_template, request, redirect, url_for, session, flash,
    current_app
)

from core.db import get_db, migrate_db
from core.helpers import (
    login_required, role_required, get_setting, set_setting,
)
from core.ai_crypto import encrypt_secret, decrypt_secret, mask_key
from routes.admin import admin

DEFAULT_AI_MODEL = "openai/gpt-oss-20b"


def _ensure_ai_settings_table(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_settings (
            user_id INTEGER PRIMARY KEY,
            api_key_enc TEXT,
            model TEXT DEFAULT 'openai/gpt-oss-20b',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.commit()


@admin.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_settings():
    migrate_db()
    db = get_db()
    _ensure_ai_settings_table(db)
    uid = session['user_id']

    if request.method == 'POST':
        section = request.form.get('section', 'general')

        if section == 'ai':
            model = (request.form.get('ai_model') or DEFAULT_AI_MODEL).strip()
            api_key = (request.form.get('groq_api_key') or '').strip()
            clear_key = request.form.get('clear_api_key') == '1'
            row = db.execute(
                'SELECT api_key_enc FROM ai_settings WHERE user_id = ?', (uid,)
            ).fetchone()
            if clear_key:
                if row:
                    db.execute(
                        'UPDATE ai_settings SET api_key_enc = ?, model = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?',
                        ('', model, uid),
                    )
                flash('Groq API key removed.', 'success')
            elif api_key:
                enc = encrypt_secret(api_key, current_app.secret_key)
                if row:
                    db.execute(
                        'UPDATE ai_settings SET api_key_enc = ?, model = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?',
                        (enc, model, uid),
                    )
                else:
                    db.execute(
                        'INSERT INTO ai_settings (user_id, api_key_enc, model) VALUES (?, ?, ?)',
                        (uid, enc, model),
                    )
                flash('Serenia AI settings saved.', 'success')
            else:
                # model only
                if row:
                    db.execute(
                        'UPDATE ai_settings SET model = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?',
                        (model, uid),
                    )
                else:
                    db.execute(
                        'INSERT INTO ai_settings (user_id, api_key_enc, model) VALUES (?, ?, ?)',
                        (uid, '', model),
                    )
                flash('Serenia AI model updated.', 'success')
            db.commit()
            return redirect(url_for('admin.admin_settings') + '#serenia-ai')

        currency = request.form.get('currency', 'INR')
        if currency not in ('USD', 'INR', 'AED'):
            flash('Invalid currency.', 'danger')
            return redirect(url_for('admin.admin_settings'))
        set_setting('currency', currency)
        set_setting('company_name', request.form.get('company_name', 'Estacle').strip() or 'Estacle')
        set_setting('support_email', request.form.get('support_email', '').strip())
        set_setting('support_phone', request.form.get('support_phone', '').strip())
        set_setting('timezone', request.form.get('timezone', 'Asia/Kolkata').strip() or 'Asia/Kolkata')
        set_setting('address', request.form.get('address', '').strip())
        set_setting('theme_mode', request.form.get('theme_mode', 'default').strip() or 'default')
        flash('Settings saved.', 'success')
        return redirect(url_for('admin.admin_settings'))

    settings = {
        'currency': get_setting('currency', 'INR'),
        'company_name': get_setting('company_name', 'Estacle'),
        'support_email': get_setting('support_email', 'support@estacle.com'),
        'support_phone': get_setting('support_phone', ''),
        'timezone': get_setting('timezone', 'Asia/Kolkata'),
        'address': get_setting('address', ''),
        'theme_mode': get_setting('theme_mode', 'default'),
    }

    ai_row = db.execute(
        'SELECT api_key_enc, model FROM ai_settings WHERE user_id = ?', (uid,)
    ).fetchone()
    ai_key_plain = ''
    ai_model = DEFAULT_AI_MODEL
    if ai_row:
        ai_key_plain = decrypt_secret(ai_row['api_key_enc'] or '', current_app.secret_key)
        ai_model = ai_row['model'] or DEFAULT_AI_MODEL

    from routes.admin.ai import _ensure_knowledge_tables
    _ensure_knowledge_tables(db)
    _ensure_prompts_table(db)
    ai_prompts = [dict(r) for r in db.execute(
        'SELECT id, title, text FROM ai_prompts WHERE user_id = ? ORDER BY id DESC',
        (uid,),
    ).fetchall()]
    if not ai_prompts:
        for title, text in DEFAULT_PROMPTS:
            db.execute(
                'INSERT INTO ai_prompts (user_id, title, text) VALUES (?, ?, ?)',
                (uid, title, text),
            )
        db.commit()
        ai_prompts = [dict(r) for r in db.execute(
            'SELECT id, title, text FROM ai_prompts WHERE user_id = ? ORDER BY id DESC',
            (uid,),
        ).fetchall()]
    knowledge_docs = [dict(r) for r in db.execute(
        'SELECT id, title, source_name, length(content) as chars, created_at '
        'FROM ai_knowledge WHERE user_id = ? ORDER BY id DESC',
        (uid,),
    ).fetchall()]

    return render_template(
        'admin_settings.html',
        **settings,
        ai_has_key=bool(ai_key_plain),
        ai_key_mask=mask_key(ai_key_plain) if ai_key_plain else '',
        ai_model=ai_model,
        knowledge_docs=knowledge_docs,
        ai_prompts=ai_prompts,
    )


@admin.route('/admin/settings/knowledge', methods=['POST'])
@login_required
@role_required('admin')
def admin_settings_knowledge():
    from routes.admin.ai import _ensure_knowledge_tables, _extract_pdf_text
    from werkzeug.utils import secure_filename
    import os
    migrate_db()
    db = get_db()
    _ensure_knowledge_tables(db)
    uid = session['user_id']
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    f = request.files.get('file')
    source_name = None
    if f and f.filename:
        fname = secure_filename(f.filename)
        raw = f.read()
        ext = os.path.splitext(fname)[1].lower()
        text = ''
        if ext == '.pdf':
            text = _extract_pdf_text(raw)
        else:
            try:
                text = raw.decode('utf-8', errors='ignore')
            except Exception:
                text = ''
        if not text.strip():
            flash('Could not read that file. Use PDF with text, or .txt / .md / .csv.', 'danger')
            return redirect(url_for('admin.admin_settings') + '#serenia-ai')
        content = text
        if not title:
            title = fname
        source_name = fname
    if not content.strip():
        flash('Document content is required.', 'danger')
        return redirect(url_for('admin.admin_settings') + '#serenia-ai')
    if not title:
        title = 'Company document'
    db.execute(
        'INSERT INTO ai_knowledge (user_id, title, content, source_name) VALUES (?, ?, ?, ?)',
        (uid, title[:200], content[:200000], source_name),
    )
    db.commit()
    flash('Knowledge document added.', 'success')
    return redirect(url_for('admin.admin_settings') + '#serenia-rag')


@admin.route('/admin/settings/knowledge/<int:doc_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_settings_knowledge_delete(doc_id):
    from routes.admin.ai import _ensure_knowledge_tables
    migrate_db()
    db = get_db()
    _ensure_knowledge_tables(db)
    uid = session['user_id']
    db.execute('DELETE FROM ai_knowledge WHERE id = ? AND user_id = ?', (doc_id, uid))
    db.commit()
    flash('Document removed.', 'success')
    return redirect(url_for('admin.admin_settings') + '#serenia-rag')



DEFAULT_PROMPTS = [
    ("CRM overview", "Analyse my CRM and give a clear summary of properties, clients, deals, and tasks."),
    ("Available properties", "List available properties in a compact table with price, city, and status."),
    ("Open deals", "Show all open deals with stage, value, client, and agent."),
    ("Add demo property", "Create a sample property with realistic demo data in Demo City."),
    ("Leads to contact", "List clients with status lead and suggest a next follow-up step."),
    ("How to add a deal", "Explain step by step how to add a deal in Estacle and offer to create one."),
    ("Upcoming appointments", "Show upcoming appointments with client, property, agent, and time."),
    ("Pipeline health", "Summarize the deals pipeline by stage and highlight deals that need attention."),
]

def _ensure_prompts_table(db):
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


@admin.route('/admin/settings/prompts', methods=['POST'])
@login_required
@role_required('admin')
def admin_settings_prompt():
    migrate_db()
    db = get_db()
    _ensure_prompts_table(db)
    uid = session['user_id']
    title = (request.form.get('title') or '').strip()
    text = (request.form.get('text') or '').strip()
    if not title or not text:
        flash('Title and prompt text are required.', 'danger')
        return redirect(url_for('admin.admin_settings') + '#serenia-prompts')
    db.execute(
        'INSERT INTO ai_prompts (user_id, title, text) VALUES (?, ?, ?)',
        (uid, title[:120], text[:2000]),
    )
    db.commit()
    flash('Prompt added to library.', 'success')
    return redirect(url_for('admin.admin_settings') + '#serenia-prompts')


@admin.route('/admin/settings/prompts/<int:prompt_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_settings_prompt_delete(prompt_id):
    migrate_db()
    db = get_db()
    _ensure_prompts_table(db)
    uid = session['user_id']
    db.execute('DELETE FROM ai_prompts WHERE id = ? AND user_id = ?', (prompt_id, uid))
    db.commit()
    flash('Prompt removed.', 'success')
    return redirect(url_for('admin.admin_settings') + '#serenia-prompts')
