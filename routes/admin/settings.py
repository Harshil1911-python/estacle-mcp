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
                flash('Estacle AI settings saved.', 'success')
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
                flash('Estacle AI model updated.', 'success')
            db.commit()
            return redirect(url_for('admin.admin_settings') + '#estacle-ai')

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
        flash('Settings saved.', 'success')
        return redirect(url_for('admin.admin_settings'))

    settings = {
        'currency': get_setting('currency', 'INR'),
        'company_name': get_setting('company_name', 'Estacle'),
        'support_email': get_setting('support_email', 'support@estacle.com'),
        'support_phone': get_setting('support_phone', ''),
        'timezone': get_setting('timezone', 'Asia/Kolkata'),
        'address': get_setting('address', ''),
    }

    ai_row = db.execute(
        'SELECT api_key_enc, model FROM ai_settings WHERE user_id = ?', (uid,)
    ).fetchone()
    ai_key_plain = ''
    ai_model = DEFAULT_AI_MODEL
    if ai_row:
        ai_key_plain = decrypt_secret(ai_row['api_key_enc'] or '', current_app.secret_key)
        ai_model = ai_row['model'] or DEFAULT_AI_MODEL

    return render_template(
        'admin_settings.html',
        **settings,
        ai_has_key=bool(ai_key_plain),
        ai_key_mask=mask_key(ai_key_plain) if ai_key_plain else '',
        ai_model=ai_model,
    )
