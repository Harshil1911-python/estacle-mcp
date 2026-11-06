"""Admin inquiries routes."""

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


@admin.route('/admin/inquiries')
@login_required
@role_required('admin')
def admin_inquiries():
    db = get_db()
    migrate_db()
    rows = db.execute('''
        SELECT i.*, p.title as property_title
        FROM inquiries i
        LEFT JOIN properties p ON i.property_id = p.id
        ORDER BY i.created_at DESC
    ''').fetchall()
    return render_template('admin_inquiries.html', inquiries=rows)


@admin.route('/admin/inquiries/status/<int:inq_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_inquiry_status(inq_id):
    status = request.form.get('status', 'new')
    if status not in ('new', 'contacted', 'closed'):
        flash('Invalid status.', 'danger')
        return redirect(url_for('admin.admin_inquiries'))
    db = get_db()
    db.execute('UPDATE inquiries SET status = ? WHERE id = ?', (status, inq_id))
    db.commit()
    flash('Inquiry status updated.', 'success')
    return redirect(url_for('admin.admin_inquiries'))


@admin.route('/admin/inquiries/delete/<int:inq_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_inquiry(inq_id):
    db = get_db()
    db.execute('DELETE FROM inquiries WHERE id = ?', (inq_id,))
    db.commit()
    flash('Inquiry deleted.', 'info')
    return redirect(url_for('admin.admin_inquiries'))


