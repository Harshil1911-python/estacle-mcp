"""Admin appointments: list, schedule, edit, status, delete."""

from flask import (
    render_template, request, redirect, url_for, session, flash, jsonify,
)
from datetime import datetime

from core.db import get_db, migrate_db
from core.helpers import (
    login_required, role_required, log_activity,
)
from routes.admin import admin

STATUSES = ('scheduled', 'completed', 'cancelled', 'no_show')
STATUS_LABELS = {
    'scheduled': 'Scheduled',
    'completed': 'Completed',
    'cancelled': 'Cancelled',
    'no_show': 'No show',
}


def _normalize_status(st):
    st = (st or 'scheduled').strip().lower().replace(' ', '_')
    aliases = {
        'done': 'completed',
        'complete': 'completed',
        'cancel': 'cancelled',
        'canceled': 'cancelled',
        'noshow': 'no_show',
        'no-show': 'no_show',
    }
    st = aliases.get(st, st)
    if st not in STATUSES:
        st = 'scheduled'
    return st


def _appointments_query(db, q='', status='', agent_id='', date_from='', date_to=''):
    sql = (
        "SELECT a.*, "
        "c.full_name as client_name, "
        "p.title as property_title, "
        "u.full_name as agent_name "
        "FROM appointments a "
        "LEFT JOIN clients c ON a.client_id = c.id "
        "LEFT JOIN properties p ON a.property_id = p.id "
        "LEFT JOIN users u ON a.agent_id = u.id "
        "WHERE 1=1"
    )
    params = []
    if q:
        sql += (
            " AND (IFNULL(c.full_name,'') LIKE ? OR IFNULL(p.title,'') LIKE ? "
            "OR IFNULL(u.full_name,'') LIKE ? OR IFNULL(a.notes,'') LIKE ?)"
        )
        qq = '%' + q + '%'
        params.extend([qq, qq, qq, qq])
    if status in STATUSES:
        sql += " AND lower(IFNULL(a.status,'')) = ?"
        params.append(status)
    if agent_id and str(agent_id).isdigit():
        sql += " AND a.agent_id = ?"
        params.append(int(agent_id))
    if date_from:
        sql += " AND date(a.appointment_date) >= date(?)"
        params.append(date_from)
    if date_to:
        sql += " AND date(a.appointment_date) <= date(?)"
        params.append(date_to)
    sql += " ORDER BY a.appointment_date DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    for r in rows:
        r['status'] = _normalize_status(r.get('status'))
    return rows


def _form_lists(db):
    clients = db.execute(
        'SELECT id, full_name FROM clients ORDER BY full_name'
    ).fetchall()
    properties = db.execute(
        'SELECT id, title FROM properties ORDER BY title'
    ).fetchall()
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('agent','admin') AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    return clients, properties, agents


@admin.route('/admin/appointments')
@login_required
@role_required('admin')
def admin_appointments():
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    if status:
        status = _normalize_status(status)
        if status not in STATUSES:
            status = ''
    agent_id = request.args.get('agent_id', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    rows = _appointments_query(
        db, q=q, status=status, agent_id=agent_id,
        date_from=date_from, date_to=date_to,
    )
    all_rows = _appointments_query(db)
    stats = {st: sum(1 for r in all_rows if r['status'] == st) for st in STATUSES}
    stats['total'] = len(all_rows)

    today = datetime.now().strftime('%Y-%m-%d')
    stats['upcoming'] = sum(
        1 for r in all_rows
        if r['status'] == 'scheduled' and (r.get('appointment_date') or '')[:10] >= today
    )
    completed_pct = 0
    if stats['total']:
        completed_pct = round(100.0 * stats.get('completed', 0) / stats['total'])
    no_show_pct = 0
    if stats['total']:
        no_show_pct = round(100.0 * stats.get('no_show', 0) / stats['total'])
    stats['completed_pct'] = completed_pct
    stats['no_show_pct'] = no_show_pct

    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('agent','admin') AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    clients, properties, agents_list = _form_lists(db)

    # Serialize for calendar JS (dd/mm/yy display handled client-side)
    import json
    appt_json = json.dumps(all_rows, default=str)

    toast = request.args.get('toast', '').strip()

    return render_template(
        'admin_appointments.html',
        appointments=rows,
        all_appointments_json=appt_json,
        stats=stats,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        agents=agents,
        clients=clients,
        properties=properties,
        q=q,
        status_filter=status,
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        toast=toast,
    )


@admin.route('/admin/appointments/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_appointment():
    db = get_db()
    migrate_db()
    clients, properties, agents = _form_lists(db)

    if request.method == 'POST':
        client_id = request.form.get('client_id') or None
        property_id = request.form.get('property_id') or None
        agent_id = request.form.get('agent_id') or None
        appointment_date = request.form.get('appointment_date', '').strip()
        if not appointment_date:
            appt_date = (request.form.get('appt_date') or '').strip()
            appt_time = (request.form.get('appt_time') or '10:00').strip()
            if appt_date:
                appointment_date = appt_date + 'T' + appt_time
        notes = request.form.get('notes', '').strip()
        status = _normalize_status(request.form.get('status', 'scheduled'))

        if not appointment_date:
            flash('Date and time are required.', 'danger')
            return redirect(url_for('admin.admin_add_appointment'))

        client_id = int(client_id) if client_id else None
        property_id = int(property_id) if property_id else None
        agent_id = int(agent_id) if agent_id else None

        cur = db.execute(
            'INSERT INTO appointments (client_id, property_id, agent_id, appointment_date, status, notes) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (client_id, property_id, agent_id, appointment_date, status, notes),
        )
        db.commit()
        log_activity('Scheduled appointment', 'appointment', cur.lastrowid, appointment_date)
        return redirect(url_for('admin.admin_appointments', toast='Updated'))

    return render_template(
        'admin_add_appointment.html',
        clients=clients,
        properties=properties,
        agents=agents,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
    )


@admin.route('/admin/appointments/<int:appointment_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_edit_appointment(appointment_id):
    db = get_db()
    migrate_db()
    row = db.execute(
        'SELECT * FROM appointments WHERE id = ?', (appointment_id,)
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))

    clients, properties, agents = _form_lists(db)

    if request.method == 'POST':
        client_id = request.form.get('client_id') or None
        property_id = request.form.get('property_id') or None
        agent_id = request.form.get('agent_id') or None
        appointment_date = request.form.get('appointment_date', '').strip()
        notes = request.form.get('notes', '').strip()
        status = _normalize_status(request.form.get('status', 'scheduled'))

        if not appointment_date:
            flash('Date and time are required.', 'danger')
            return redirect(url_for('admin.admin_edit_appointment', appointment_id=appointment_id))

        client_id = int(client_id) if client_id else None
        property_id = int(property_id) if property_id else None
        agent_id = int(agent_id) if agent_id else None

        db.execute(
            'UPDATE appointments SET client_id=?, property_id=?, agent_id=?, '
            'appointment_date=?, status=?, notes=? WHERE id=?',
            (client_id, property_id, agent_id, appointment_date, status, notes, appointment_id),
        )
        db.commit()
        log_activity('Updated appointment', 'appointment', appointment_id, appointment_date)
        return redirect(url_for('admin.admin_appointments', toast='Updated'))

    appt = dict(row)
    # datetime-local needs YYYY-MM-DDTHH:MM
    dt = (appt.get('appointment_date') or '').replace(' ', 'T')
    if len(dt) >= 16:
        dt = dt[:16]
    appt['appointment_date_input'] = dt

    return render_template(
        'admin_edit_appointment.html',
        appointment=appt,
        clients=clients,
        properties=properties,
        agents=agents,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
    )


@admin.route('/admin/appointments/<int:appointment_id>/status', methods=['POST'])
@login_required
@role_required('admin')
def admin_appointment_status(appointment_id):
    db = get_db()
    migrate_db()
    status = _normalize_status(request.form.get('status'))
    minutes = (request.form.get('minutes_of_meeting') or '').strip()
    row = db.execute(
        'SELECT id FROM appointments WHERE id = ?', (appointment_id,)
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))
    if status == 'completed' and minutes:
        db.execute(
            'UPDATE appointments SET status = ?, minutes_of_meeting = ? WHERE id = ?',
            (status, minutes, appointment_id),
        )
    else:
        db.execute(
            'UPDATE appointments SET status = ? WHERE id = ?',
            (status, appointment_id),
        )
    db.commit()
    log_activity('Appointment status ' + status, 'appointment', appointment_id, status)
    return redirect(url_for('admin.admin_appointments', toast='Updated'))


@admin.route('/admin/appointments/<int:appointment_id>/minutes', methods=['POST'])
@login_required
@role_required('admin')
def admin_appointment_minutes(appointment_id):
    """Save Minutes of Meeting and mark appointment completed."""
    db = get_db()
    migrate_db()
    minutes = (request.form.get('minutes_of_meeting') or '').strip()
    row = db.execute(
        'SELECT id FROM appointments WHERE id = ?', (appointment_id,)
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))
    db.execute(
        'UPDATE appointments SET status = ?, minutes_of_meeting = ? WHERE id = ?',
        ('completed', minutes, appointment_id),
    )
    db.commit()
    log_activity('Minutes of meeting saved', 'appointment', appointment_id, 'completed')
    return redirect(url_for('admin.admin_appointments', toast='Updated'))


@admin.route('/admin/appointments/<int:appointment_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_appointment(appointment_id):
    db = get_db()
    migrate_db()
    row = db.execute(
        'SELECT id, appointment_date FROM appointments WHERE id = ?',
        (appointment_id,),
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))
    db.execute('DELETE FROM appointments WHERE id = ?', (appointment_id,))
    db.commit()
    log_activity('Deleted appointment', 'appointment', appointment_id, row['appointment_date'])
    return redirect(url_for('admin.admin_appointments', toast='Updated'))


@admin.route('/admin/appointments/<int:appointment_id>/reschedule', methods=['POST'])
@login_required
@role_required('admin')
def admin_appointment_reschedule(appointment_id):
    """Move appointment to a new date/time (drag and drop)."""
    db = get_db()
    migrate_db()
    data = request.get_json(silent=True) or {}
    new_dt = (data.get('appointment_date') or request.form.get('appointment_date') or '').strip()
    if not new_dt:
        return jsonify({'ok': False, 'error': 'appointment_date required'}), 400
    row = db.execute('SELECT id FROM appointments WHERE id = ?', (appointment_id,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    # Normalize T separator
    new_dt = new_dt.replace(' ', 'T')
    if len(new_dt) == 16:
        new_dt = new_dt + ':00'
    db.execute(
        'UPDATE appointments SET appointment_date = ? WHERE id = ?',
        (new_dt, appointment_id),
    )
    db.commit()
    log_activity('Rescheduled appointment', 'appointment', appointment_id, new_dt)
    return jsonify({'ok': True, 'appointment_date': new_dt})
