"""Admin tasks routes."""

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


@admin.route('/admin/tasks')
@login_required
@role_required('admin')
def admin_tasks():
    db = get_db()
    tasks = db.execute(
        "SELECT t.*, u.full_name as assignee_name, c.full_name as client_name, p.title as property_title FROM tasks t LEFT JOIN users u ON t.assigned_to = u.id LEFT JOIN clients c ON t.related_client_id = c.id LEFT JOIN properties p ON t.related_property_id = p.id ORDER BY t.created_at DESC"
    ).fetchall()
    return render_template('admin_tasks.html', tasks=tasks)


@admin.route('/admin/tasks/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_task():
    db = get_db()
    agents = db.execute("SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name").fetchall()
    clients = db.execute('SELECT id, full_name FROM clients ORDER BY full_name').fetchall()
    properties = db.execute('SELECT id, title FROM properties ORDER BY title').fetchall()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title required.', 'danger')
            return redirect(url_for('admin.admin_add_task'))
        assigned = request.form.get('assigned_to') or None
        client_id = request.form.get('related_client_id') or None
        prop_id = request.form.get('related_property_id') or None
        db.execute(
            "INSERT INTO tasks (title, description, due_date, status, priority, assigned_to, related_client_id, related_property_id, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (title, request.form.get('description',''), request.form.get('due_date') or None,
             request.form.get('status','pending'), request.form.get('priority','medium'),
             int(assigned) if assigned else None, int(client_id) if client_id else None,
             int(prop_id) if prop_id else None, session['user_id'])
        )
        db.commit()
        log_activity('Created task', 'task', None, title)
        flash('Task created.', 'success')
        return redirect(url_for('admin.admin_tasks'))
    return render_template('admin_add_task.html', agents=agents, clients=clients, properties=properties)


@admin.route('/admin/tasks/status/<int:task_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_task_status(task_id):
    status = request.form.get('status', 'pending')
    if status not in ('pending', 'in_progress', 'done', 'cancelled'):
        flash('Invalid status.', 'danger')
        return redirect(url_for('admin.admin_tasks'))
    db = get_db()
    task = db.execute('SELECT title FROM tasks WHERE id=?', (task_id,)).fetchone()
    db.execute('UPDATE tasks SET status=? WHERE id=?', (status, task_id))
    db.commit()
    if task:
        log_activity('Updated task', 'task', task_id, f"{task['title']} → {status}")
    flash('Task updated.', 'success')
    if request.form.get('next') == 'dashboard':
        return redirect(url_for('admin.admin_dashboard'))
    return redirect(url_for('admin.admin_tasks'))


@admin.route('/admin/tasks/delete/<int:task_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_task(task_id):
    db = get_db()
    db.execute('DELETE FROM tasks WHERE id=?', (task_id,))
    db.commit()
    flash('Task deleted.', 'success')
    return redirect(url_for('admin.admin_tasks'))


