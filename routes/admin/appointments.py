"""Admin appointments routes."""

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


@admin.route('/admin/appointments')
@login_required
@role_required('admin')
def admin_appointments():
    db = get_db()
    appointments = db.execute("""
        SELECT a.*,
               c.full_name as client_name,
               p.title as property_title,
               u.full_name as agent_name
        FROM appointments a
        LEFT JOIN clients c ON a.client_id = c.id
        LEFT JOIN properties p ON a.property_id = p.id
        LEFT JOIN users u ON a.agent_id = u.id
        ORDER BY a.appointment_date DESC
    """).fetchall()
    return render_template('admin_appointments.html', appointments=appointments)


@admin.route('/admin/appointments/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_appointment():
    db = get_db()
    clients = db.execute('SELECT id, full_name FROM clients ORDER BY full_name').fetchall()
    properties = db.execute('SELECT id, title FROM properties ORDER BY title').fetchall()
    agents = db.execute("SELECT id, full_name FROM users WHERE role = 'agent' AND is_active = 1 ORDER BY full_name").fetchall()

    if request.method == 'POST':
        client_id = request.form.get('client_id') or None
        property_id = request.form.get('property_id') or None
        agent_id = request.form.get('agent_id') or None
        appointment_date = request.form.get('appointment_date', '').strip()
        notes = request.form.get('notes', '').strip()
        status = request.form.get('status', 'scheduled')

        if not appointment_date:
            flash('Appointment date is required.', 'danger')
            return redirect(url_for('admin.admin_add_appointment'))

        client_id = int(client_id) if client_id else None
        property_id = int(property_id) if property_id else None
        agent_id = int(agent_id) if agent_id else None

        db.execute(
            'INSERT INTO appointments (client_id, property_id, agent_id, appointment_date, status, notes) VALUES (?, ?, ?, ?, ?, ?)',
            (client_id, property_id, agent_id, appointment_date, status, notes)
        )
        db.commit()
        flash('Appointment scheduled successfully.', 'success')
        return redirect(url_for('admin.admin_appointments'))

    return render_template('admin_add_appointment.html', clients=clients, properties=properties, agents=agents)


