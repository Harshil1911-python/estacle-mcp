"""Admin clients routes — synced with customer users."""

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


def sync_customers_to_clients(db):
    """
    Ensure every user with role='customer' has a linked clients row.
    Match by user_id first, then by email. Creates missing client rows.
    Does NOT create users for plain clients (no login).
    """
    migrate_db()
    customers = db.execute(
        "SELECT id, email, full_name, phone, address, city, notes, created_at "
        "FROM users WHERE role = 'customer' AND is_active = 1"
    ).fetchall()
    for u in customers:
        # Already linked by user_id?
        existing = db.execute(
            "SELECT id FROM clients WHERE user_id = ?", (u['id'],)
        ).fetchone()
        if existing:
            # Keep basic contact fields in sync from user → client
            db.execute(
                """UPDATE clients SET
                    full_name = ?,
                    email = COALESCE(?, email),
                    phone = COALESCE(?, phone),
                    address = COALESCE(?, address),
                    preferred_city = COALESCE(?, preferred_city),
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?""",
                (
                    u['full_name'],
                    u['email'],
                    u['phone'],
                    u['address'],
                    u['city'],
                    u['id'],
                ),
            )
            continue

        # Try match by email
        linked = None
        if u['email']:
            linked = db.execute(
                "SELECT id, user_id FROM clients WHERE lower(email) = lower(?) LIMIT 1",
                (u['email'],),
            ).fetchone()
        if linked:
            db.execute(
                "UPDATE clients SET user_id = ?, full_name = COALESCE(full_name, ?), "
                "phone = COALESCE(phone, ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (u['id'], u['full_name'], u['phone'], linked['id']),
            )
            continue

        # Create new client row for this customer
        db.execute(
            """INSERT INTO clients
               (full_name, email, phone, status, notes, agent_id, source, budget,
                interest, preferred_city, address, user_id)
               VALUES (?, ?, ?, 'lead', ?, NULL, 'User registration', NULL, NULL, ?, ?, ?)""",
            (
                u['full_name'],
                u['email'],
                u['phone'],
                u['notes'],
                u['city'],
                u['address'],
                u['id'],
            ),
        )
    db.commit()


@admin.route('/admin/clients')
@login_required
@role_required('admin')
def admin_clients():
    db = get_db()
    migrate_db()
    sync_customers_to_clients(db)

    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '').strip()
    agent_id = request.args.get('agent_id', '').strip()
    source = request.args.get('source', '').strip()
    view = request.args.get('view', '').strip()
    has_login = request.args.get('has_login', '').strip()

    sql = (
        "SELECT c.*, u.full_name as agent_name, "
        "cu.id as linked_user_id, cu.email as login_email, cu.is_active as user_active "
        "FROM clients c "
        "LEFT JOIN users u ON c.agent_id = u.id "
        "LEFT JOIN users cu ON c.user_id = cu.id "
        "WHERE 1=1"
    )
    params = []
    if q:
        sql += (
            " AND (c.full_name LIKE ? OR c.email LIKE ? OR c.phone LIKE ? "
            "OR IFNULL(c.notes,'') LIKE ? OR IFNULL(c.preferred_city,'') LIKE ?)"
        )
        params.extend(['%' + q + '%'] * 5)
    if status_filter in ('lead', 'contacted', 'qualified', 'negotiation', 'closed', 'lost'):
        sql += " AND c.status = ?"
        params.append(status_filter)
    if agent_id.isdigit():
        sql += " AND c.agent_id = ?"
        params.append(int(agent_id))
    if source:
        sql += " AND IFNULL(c.source,'') LIKE ?"
        params.append('%' + source + '%')
    if has_login == '1':
        sql += " AND c.user_id IS NOT NULL"
    elif has_login == '0':
        sql += " AND c.user_id IS NULL"
    sql += " ORDER BY c.created_at DESC"
    clients = [dict(r) for r in db.execute(sql, params).fetchall()]

    now = datetime.now().date()
    for c in clients:
        created = c.get('created_at')
        label = '—'
        if created:
            try:
                cdt = datetime.strptime(str(created)[:10], '%Y-%m-%d').date()
                d = (now - cdt).days
                if d <= 0:
                    label = 'Today'
                elif d == 1:
                    label = '1 day ago'
                else:
                    label = '{} days ago'.format(d)
            except Exception:
                label = str(created)[:10]
        c['added_label'] = label
        c['has_login'] = bool(c.get('user_id') or c.get('linked_user_id'))

    stats = {
        'total': db.execute('SELECT COUNT(*) FROM clients').fetchone()[0],
        'lead': db.execute("SELECT COUNT(*) FROM clients WHERE status = 'lead'").fetchone()[0],
        'contacted': db.execute("SELECT COUNT(*) FROM clients WHERE status = 'contacted'").fetchone()[0],
        'qualified': db.execute("SELECT COUNT(*) FROM clients WHERE status = 'qualified'").fetchone()[0],
        'negotiation': db.execute("SELECT COUNT(*) FROM clients WHERE status = 'negotiation'").fetchone()[0],
        'closed': db.execute("SELECT COUNT(*) FROM clients WHERE status = 'closed'").fetchone()[0],
        'lost': db.execute("SELECT COUNT(*) FROM clients WHERE status = 'lost'").fetchone()[0],
        'with_login': db.execute('SELECT COUNT(*) FROM clients WHERE user_id IS NOT NULL').fetchone()[0],
    }
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role = 'agent' AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    active_filters = sum(1 for v in (q, status_filter, agent_id, source, has_login) if v)
    return render_template(
        'admin_clients.html',
        clients=clients,
        stats=stats,
        agents=agents,
        q=q,
        status_filter=status_filter,
        agent_id=agent_id,
        source=source,
        view=view,
        has_login=has_login,
        active_filters=active_filters,
    )


@admin.route('/admin/clients/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_client():
    db = get_db()
    migrate_db()
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role = 'agent' AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    properties = db.execute(
        "SELECT id, title, city, price, status FROM properties ORDER BY title"
    ).fetchall()

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        if not full_name:
            flash('Full name is required.', 'danger')
            return redirect(url_for('admin.admin_add_client'))

        email = request.form.get('email', '').strip().lower() or None
        phone = request.form.get('phone', '').strip() or None
        address = request.form.get('address', '').strip() or None
        preferred_city = request.form.get('preferred_city', '').strip() or None
        notes = request.form.get('notes', '').strip() or None
        source = request.form.get('source', '').strip() or None
        interest = request.form.get('interest', '').strip() or None
        agent_id = request.form.get('agent_id') or None
        agent_id = int(agent_id) if agent_id and str(agent_id).isdigit() else None
        budget = None
        try:
            if request.form.get('budget'):
                budget = float(request.form.get('budget'))
        except ValueError:
            budget = None
        status = request.form.get('status', 'lead')
        if status not in ('lead', 'contacted', 'qualified', 'negotiation', 'closed', 'lost'):
            status = 'lead'

        make_id = request.form.get('make_id') in ('1', 'on', 'true', 'yes')
        login_email = (request.form.get('login_email') or email or '').strip().lower()
        login_password = request.form.get('login_password', '')

        user_id = None
        if make_id:
            if not login_email:
                flash('Login email is required when Make ID is enabled.', 'danger')
                return redirect(url_for('admin.admin_add_client'))
            if not login_password or len(login_password) < 6:
                flash('Password must be at least 6 characters when Make ID is enabled.', 'danger')
                return redirect(url_for('admin.admin_add_client'))
            exists = db.execute(
                'SELECT id FROM users WHERE lower(email) = lower(?)', (login_email,)
            ).fetchone()
            if exists:
                flash('A user with that email already exists.', 'danger')
                return redirect(url_for('admin.admin_add_client'))
            cur = db.execute(
                """INSERT INTO users (email, password, full_name, role, phone, address, city, notes, is_active)
                   VALUES (?, ?, ?, 'customer', ?, ?, ?, ?, 1)""",
                (
                    login_email,
                    generate_password_hash(login_password),
                    full_name,
                    phone,
                    address,
                    preferred_city,
                    notes,
                ),
            )
            user_id = cur.lastrowid
            email = email or login_email

        cur = db.execute(
            """INSERT INTO clients
               (full_name, email, phone, status, notes, agent_id, source, budget,
                interest, preferred_city, address, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                full_name, email, phone, status, notes, agent_id, source, budget,
                interest, preferred_city, address, user_id,
            ),
        )
        client_id = cur.lastrowid

        # Interested properties
        prop_ids = request.form.getlist('property_ids')
        for pid in prop_ids:
            if str(pid).isdigit():
                try:
                    db.execute(
                        'INSERT OR IGNORE INTO client_properties (client_id, property_id) VALUES (?, ?)',
                        (client_id, int(pid)),
                    )
                except Exception:
                    pass

        db.commit()
        log_activity('Added client', 'client', client_id, full_name)
        if make_id:
            flash('Client "{}" added with dashboard login ({}).'.format(full_name, login_email), 'success')
        else:
            flash('Client "{}" added successfully.'.format(full_name), 'success')
        return redirect(url_for('admin.admin_clients'))

    print('''full_name,email,phone,status,source,budget,interest,preferred_city,address,notes,agent_email
John Doe,john@email.com,+919800000000,lead,Website,5000000,3BHK Buy,Mumbai,Andheri,,agent@estacle.com
''')
    return render_template('admin_add_client.html', agents=agents, properties=properties)



@admin.route('/admin/clients/delete/<int:client_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_client(client_id):
    db = get_db()
    client = db.execute('SELECT id, full_name, user_id FROM clients WHERE id = ?', (client_id,)).fetchone()
    if not client:
        flash('Client not found.', 'danger')
        return redirect(url_for('admin.admin_clients'))
    # Unlink only — do not auto-delete the user account
    db.execute('DELETE FROM clients WHERE id = ?', (client_id,))
    db.commit()
    flash('Client "{}" deleted.'.format(client['full_name']), 'success')
    return redirect(url_for('admin.admin_clients'))


@admin.route('/admin/clients/status/<int:client_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_update_client_status(client_id):
    status = request.form.get('status', '')
    if status not in ('lead', 'contacted', 'qualified', 'negotiation', 'closed', 'lost'):
        flash('Invalid status.', 'danger')
        return redirect(url_for('admin.admin_clients'))
    db = get_db()
    client = db.execute('SELECT id, full_name FROM clients WHERE id = ?', (client_id,)).fetchone()
    if not client:
        flash('Client not found.', 'danger')
        return redirect(url_for('admin.admin_clients'))
    db.execute('UPDATE clients SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (status, client_id))
    db.commit()
    flash('Status of "{}" updated to {}.'.format(client['full_name'], status), 'success')
    return redirect(url_for('admin.admin_clients'))


@admin.route('/admin/clients/<int:client_id>')
@login_required
@role_required('admin')
def admin_client_detail(client_id):
    db = get_db()
    migrate_db()
    client = db.execute(
        "SELECT c.*, u.full_name as agent_name, "
        "cu.email as login_email, cu.is_active as user_active, cu.id as linked_user_id "
        "FROM clients c "
        "LEFT JOIN users u ON c.agent_id = u.id "
        "LEFT JOIN users cu ON c.user_id = cu.id "
        "WHERE c.id = ?",
        (client_id,),
    ).fetchone()
    if not client:
        flash('Client not found.', 'danger')
        return redirect(url_for('admin.admin_clients'))
    appointments = db.execute(
        """SELECT a.*, p.title as property_title
           FROM appointments a
           LEFT JOIN properties p ON a.property_id = p.id
           WHERE a.client_id = ?
           ORDER BY a.appointment_date DESC LIMIT 20""",
        (client_id,),
    ).fetchall()
    deals = db.execute(
        "SELECT * FROM deals WHERE client_id = ? ORDER BY created_at DESC LIMIT 20",
        (client_id,),
    ).fetchall()
    interested = db.execute(
        """SELECT p.id, p.title, p.city, p.price, p.status, p.listing_type
           FROM client_properties cp
           JOIN properties p ON p.id = cp.property_id
           WHERE cp.client_id = ?
           ORDER BY p.title""",
        (client_id,),
    ).fetchall()
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_client_detail.html',
        client=client,
        appointments=appointments,
        deals=deals,
        interested=interested,
        currency=currency,
        format_money=format_money,
    )


@admin.route('/admin/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_edit_client(client_id):
    db = get_db()
    migrate_db()
    client = db.execute('SELECT * FROM clients WHERE id = ?', (client_id,)).fetchone()
    if not client:
        flash('Client not found.', 'danger')
        return redirect(url_for('admin.admin_clients'))
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role = 'agent' AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    properties = db.execute(
        "SELECT id, title, city, price, status FROM properties ORDER BY title"
    ).fetchall()
    selected_props = {
        r['property_id']
        for r in db.execute(
            'SELECT property_id FROM client_properties WHERE client_id = ?', (client_id,)
        ).fetchall()
    }

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        if not full_name:
            flash('Full name is required.', 'danger')
            return redirect(url_for('admin.admin_edit_client', client_id=client_id))
        agent_id = request.form.get('agent_id') or None
        agent_id = int(agent_id) if agent_id and str(agent_id).isdigit() else None
        budget = None
        try:
            if request.form.get('budget'):
                budget = float(request.form.get('budget'))
        except ValueError:
            budget = None
        status = request.form.get('status', client['status'] or 'lead')
        if status not in ('lead', 'contacted', 'qualified', 'negotiation', 'closed', 'lost'):
            status = 'lead'
        db.execute(
            """UPDATE clients SET
                full_name=?, email=?, phone=?, status=?, notes=?, agent_id=?,
                source=?, budget=?, interest=?, preferred_city=?, address=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (
                full_name,
                request.form.get('email', '').strip() or None,
                request.form.get('phone', '').strip() or None,
                status,
                request.form.get('notes', '').strip() or None,
                agent_id,
                request.form.get('source', '').strip() or None,
                budget,
                request.form.get('interest', '').strip() or None,
                request.form.get('preferred_city', '').strip() or None,
                request.form.get('address', '').strip() or None,
                client_id,
            ),
        )
        if client['user_id']:
            db.execute(
                """UPDATE users SET full_name=?, phone=?, address=?, city=?
                   WHERE id=? AND role='customer'""",
                (
                    full_name,
                    request.form.get('phone', '').strip() or None,
                    request.form.get('address', '').strip() or None,
                    request.form.get('preferred_city', '').strip() or None,
                    client['user_id'],
                ),
            )
        # Refresh interested properties
        db.execute('DELETE FROM client_properties WHERE client_id = ?', (client_id,))
        for pid in request.form.getlist('property_ids'):
            if str(pid).isdigit():
                try:
                    db.execute(
                        'INSERT OR IGNORE INTO client_properties (client_id, property_id) VALUES (?, ?)',
                        (client_id, int(pid)),
                    )
                except Exception:
                    pass
        db.commit()
        flash('Client updated.', 'success')
        return redirect(url_for('admin.admin_client_detail', client_id=client_id))

    return render_template(
        'admin_edit_client.html',
        client=client,
        agents=agents,
        properties=properties,
        selected_props=selected_props,
    )



@admin.route('/admin/clients/bulk', methods=['POST'])
@login_required
@role_required('admin')
def admin_bulk_clients():
    db = get_db()
    action = request.form.get('action', '').strip()
    ids = request.form.getlist('client_ids')
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        flash('No clients selected.', 'warning')
        return redirect(url_for('admin.admin_clients'))
    placeholders = ','.join('?' * len(ids))
    if action == 'delete':
        db.execute('DELETE FROM clients WHERE id IN ({})'.format(placeholders), ids)
        db.commit()
        log_activity('bulk_delete_clients', 'client', None, '{} clients deleted'.format(len(ids)))
        flash('{} client(s) deleted.'.format(len(ids)), 'success')
    else:
        flash('Unknown bulk action.', 'danger')
    return redirect(url_for('admin.admin_clients'))


@admin.route('/admin/export/clients')
@login_required
@role_required('admin')
def admin_export_clients():
    db = get_db()
    migrate_db()
    sync_customers_to_clients(db)
    rows = db.execute(
        """SELECT c.id, c.full_name, c.email, c.phone, c.status, c.source, c.budget,
                  c.interest, c.preferred_city, c.address, c.notes, c.created_at,
                  u.full_name as agent_name,
                  CASE WHEN c.user_id IS NOT NULL THEN 'yes' ELSE 'no' END as has_login
           FROM clients c
           LEFT JOIN users u ON c.agent_id = u.id
           ORDER BY c.created_at DESC"""
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'id', 'full_name', 'email', 'phone', 'status', 'source', 'budget',
        'interest', 'preferred_city', 'address', 'notes', 'created_at',
        'agent_name', 'has_login',
    ])
    for r in rows:
        writer.writerow([r[k] for k in r.keys()])
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = 'attachment; filename=clients.csv'
    return resp


CLIENT_IMPORT_FORMAT = """full_name,email,phone,status,source,budget,interest,preferred_city,address,notes,agent_email
John Doe,john@email.com,+919800000000,lead,Website,5000000,3BHK Buy,Mumbai,Andheri,,agent@estacle.com
"""


@admin.route('/admin/clients/import', methods=['POST'])
@login_required
@role_required('admin')
def admin_import_clients():
    db = get_db()
    migrate_db()
    f = request.files.get('import_file')
    if not f or not f.filename:
        flash('Choose a CSV or Excel file.', 'warning')
        return redirect(url_for('admin.admin_clients'))

    filename = f.filename.lower()
    rows = []
    try:
        if filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            ws = wb.active
            data = list(ws.iter_rows(values_only=True))
            if not data:
                flash('Empty spreadsheet.', 'warning')
                return redirect(url_for('admin.admin_clients'))
            headers = [str(h or '').strip().lower() for h in data[0]]
            for row in data[1:]:
                if not any(row):
                    continue
                rows.append({headers[i]: (row[i] if i < len(row) else None) for i in range(len(headers))})
        else:
            raw = f.read()
            try:
                text = raw.decode('utf-8-sig')
            except Exception:
                text = raw.decode('latin-1')
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                rows.append({(k or '').strip().lower(): v for k, v in row.items()})
    except Exception as e:
        flash('Could not read file: {}'.format(e), 'danger')
        return redirect(url_for('admin.admin_clients'))

    agents_by_email = {
        (r['email'] or '').lower(): r['id']
        for r in db.execute("SELECT id, email FROM users WHERE role='agent'").fetchall()
    }
    added = 0
    skipped = 0
    for row in rows:
        full_name = str(row.get('full_name') or '').strip()
        if not full_name:
            skipped += 1
            continue
        status = str(row.get('status') or 'lead').strip().lower()
        if status not in ('lead', 'contacted', 'qualified', 'negotiation', 'closed', 'lost'):
            status = 'lead'
        budget = None
        try:
            if row.get('budget') not in (None, ''):
                budget = float(row.get('budget'))
        except Exception:
            budget = None
        agent_email = str(row.get('agent_email') or '').strip().lower()
        agent_id = agents_by_email.get(agent_email)
        db.execute(
            """INSERT INTO clients
               (full_name, email, phone, status, notes, agent_id, source, budget,
                interest, preferred_city, address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                full_name,
                str(row.get('email') or '').strip() or None,
                str(row.get('phone') or '').strip() or None,
                status,
                str(row.get('notes') or '').strip() or None,
                agent_id,
                str(row.get('source') or '').strip() or None,
                budget,
                str(row.get('interest') or '').strip() or None,
                str(row.get('preferred_city') or '').strip() or None,
                str(row.get('address') or '').strip() or None,
            ),
        )
        added += 1
    db.commit()
    flash('Imported {} client(s){}.' .format(
        added,
        ', skipped {}'.format(skipped) if skipped else '',
    ), 'success')
    return redirect(url_for('admin.admin_clients'))


@admin.route('/admin/clients/import/template.csv')
@login_required
@role_required('admin')
def admin_clients_import_template():
    output = io.StringIO()
    output.write(CLIENT_IMPORT_FORMAT.strip() + '\n')
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = 'attachment; filename=clients_import_template.csv'
    return resp
