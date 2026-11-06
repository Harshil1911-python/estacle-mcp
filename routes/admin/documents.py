"""Admin documents routes."""

from flask import (
    render_template, request, redirect, url_for, session, flash,
    current_app, make_response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import json
import csv
import io

from core.db import get_db, migrate_db
from core.helpers import (
    login_required, role_required, get_setting, set_setting,
    format_money, log_activity, get_redirect_for_role
)
from routes.admin import admin


@admin.route('/admin/documents')
@login_required
@role_required('admin')
def admin_documents():
    db = get_db()
    docs = db.execute(
        "SELECT d.*, u.full_name as creator_name FROM documents d LEFT JOIN users u ON d.created_by = u.id ORDER BY d.created_at DESC"
    ).fetchall()
    return render_template('admin_documents.html', docs=docs)


@admin.route('/admin/documents/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_document():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title required.', 'danger')
            return redirect(url_for('admin.admin_add_document'))
        db = get_db()
        rid = request.form.get('related_id') or None
        db.execute(
            "INSERT INTO documents (title, doc_type, related_type, related_id, expiry_date, notes, created_by) VALUES (?,?,?,?,?,?,?)",
            (title, request.form.get('doc_type',''), request.form.get('related_type',''),
             int(rid) if rid else None, request.form.get('expiry_date') or None,
             request.form.get('notes',''), session['user_id'])
        )
        db.commit()
        log_activity('Added document', 'document', None, title)
        flash('Document recorded.', 'success')
        return redirect(url_for('admin.admin_documents'))
    return render_template('admin_add_document.html')


@admin.route('/admin/documents/delete/<int:doc_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_document(doc_id):
    db = get_db()
    db.execute('DELETE FROM documents WHERE id=?', (doc_id,))
    db.commit()
    flash('Document removed.', 'success')
    return redirect(url_for('admin.admin_documents'))


