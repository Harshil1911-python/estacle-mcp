"""Admin reports routes."""

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


@admin.route('/admin/reports')
@login_required
@role_required('admin')
def admin_reports():
    db = get_db()
    recent_props = db.execute('SELECT id, title, price, status, city, created_at FROM properties ORDER BY created_at DESC LIMIT 10').fetchall()
    recent_clients = db.execute('SELECT id, full_name, status, email, created_at FROM clients ORDER BY created_at DESC LIMIT 10').fetchall()
    agents = db.execute("""
        SELECT u.id, u.full_name,
               (SELECT COUNT(*) FROM properties WHERE agent_id = u.id) as prop_count,
               (SELECT COUNT(*) FROM clients WHERE agent_id = u.id) as client_count
        FROM users u WHERE u.role = 'agent' ORDER BY u.full_name
    """).fetchall()
    return render_template('admin_reports.html', recent_props=recent_props, recent_clients=recent_clients, agents=agents)


