"""Admin leads — Kanban powered by clients (synced)."""

from flask import (
    render_template, request, redirect, url_for, session, flash,
    current_app, make_response, jsonify
)
from datetime import datetime

from core.db import get_db, migrate_db
from core.helpers import (
    login_required, role_required, get_setting, set_setting,
    format_money, log_activity, get_redirect_for_role
)
from routes.admin import admin

# Client pipeline stages (same as Clients module)
STAGES = ('lead', 'contacted', 'qualified', 'negotiation', 'closed', 'lost')
STAGE_LABELS = {
    'lead': 'Lead',
    'contacted': 'Contacted',
    'qualified': 'Qualified',
    'negotiation': 'Negotiation',
    'closed': 'Closed',
    'lost': 'Lost',
}


def _normalize_status(st):
    st = (st or 'lead').strip().lower()
    # map legacy lead statuses
    aliases = {
        'new': 'lead',
        'won': 'closed',
    }
    st = aliases.get(st, st)
    if st not in STAGES:
        st = 'lead'
    return st


def migrate_orphan_leads_into_clients(db):
    """One-way: copy leads rows into clients if no matching email/name client exists."""
    migrate_db()
    leads = db.execute('SELECT * FROM leads').fetchall()
    for L in leads:
        email = (L['email'] or '').strip().lower()
        name = (L['full_name'] or '').strip()
        if not name:
            continue
        existing = None
        if email:
            existing = db.execute(
                'SELECT id FROM clients WHERE lower(email) = lower(?) LIMIT 1', (email,)
            ).fetchone()
        if not existing:
            existing = db.execute(
                'SELECT id FROM clients WHERE full_name = ? LIMIT 1', (name,)
            ).fetchone()
        if existing:
            continue
        status = _normalize_status(L['status'])
        db.execute(
            """INSERT INTO clients
               (full_name, email, phone, status, notes, agent_id, source, budget, interest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                L['email'],
                L['phone'],
                status,
                L['notes'],
                L['assigned_to'],
                L['source'],
                L['budget'],
                L['interest'],
            ),
        )
    db.commit()


@admin.route('/admin/leads')
@login_required
@role_required('admin')
def admin_leads():
    db = get_db()
    migrate_db()
    migrate_orphan_leads_into_clients(db)

    # Keep customer users mirrored into clients
    try:
        from routes.admin.clients import sync_customers_to_clients
        sync_customers_to_clients(db)
    except Exception:
        pass

    q = request.args.get('q', '').strip()
    agent_id = request.args.get('agent_id', '').strip()
    focus = request.args.get('status', '').strip()

    sql = (
        "SELECT c.*, u.full_name as agent_name "
        "FROM clients c "
        "LEFT JOIN users u ON c.agent_id = u.id "
        "WHERE 1=1"
    )
    params = []
    if q:
        sql += (
            " AND (c.full_name LIKE ? OR IFNULL(c.email,'') LIKE ? OR IFNULL(c.phone,'') LIKE ? "
            "OR IFNULL(c.interest,'') LIKE ? OR IFNULL(c.source,'') LIKE ?)"
        )
        params.extend(['%' + q + '%'] * 5)
    if agent_id.isdigit():
        sql += " AND c.agent_id = ?"
        params.append(int(agent_id))
    sql += " ORDER BY c.created_at DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    for r in rows:
        r['status'] = _normalize_status(r.get('status'))

    columns = []
    for st in STAGES:
        items = [r for r in rows if r['status'] == st]
        columns.append({
            'key': st,
            'label': STAGE_LABELS[st],
            'leads': items,
            'count': len(items),
        })

    stats = {st: sum(1 for r in rows if r['status'] == st) for st in STAGES}
    stats['total'] = len(rows)
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name"
    ).fetchall()
    currency = get_setting('currency', 'INR')

    return render_template(
        'admin_leads.html',
        columns=columns,
        stats=stats,
        agents=agents,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        q=q,
        agent_id=agent_id,
        focus=focus,
        currency=currency,
        format_money=format_money,
    )


@admin.route('/admin/leads/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_lead():
    db = get_db()
    migrate_db()
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name"
    ).fetchall()
    if request.method == 'POST':
        name = request.form.get('full_name', '').strip()
        if not name:
            flash('Full name is required.', 'danger')
            return redirect(url_for('admin.admin_add_lead'))
        assigned = request.form.get('assigned_to') or None
        try:
            budget = float(request.form.get('budget') or 0) or None
        except ValueError:
            budget = None
        status = _normalize_status(request.form.get('status', 'lead'))
        email = request.form.get('email', '').strip() or None
        phone = request.form.get('phone', '').strip() or None
        source = request.form.get('source', '').strip() or None
        interest = request.form.get('interest', '').strip() or None
        notes = request.form.get('notes', '').strip() or None
        agent_id = int(assigned) if assigned and str(assigned).isdigit() else None

        # Primary: clients table (Kanban source of truth)
        cur = db.execute(
            """INSERT INTO clients
               (full_name, email, phone, status, notes, agent_id, source, budget, interest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, email, phone, status, notes, agent_id, source, budget, interest),
        )
        client_id = cur.lastrowid

        # Mirror into leads table for legacy analytics
        try:
            db.execute(
                """INSERT INTO leads
                   (full_name, email, phone, source, interest, budget, status, notes, assigned_to)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (name, email, phone, source, interest, budget, status, notes, agent_id),
            )
        except Exception:
            pass

        db.commit()
        log_activity('Created lead/client', 'client', client_id, name)
        flash('Lead "{}" added to pipeline.'.format(name), 'success')
        return redirect(url_for('admin.admin_leads'))

    return render_template(
        'admin_add_lead.html',
        agents=agents,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
    )


@admin.route('/admin/leads/status/<int:lead_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_lead_status(lead_id):
    """Update client status from Kanban (lead_id is client id)."""
    db = get_db()
    migrate_db()
    client = db.execute(
        'SELECT id, full_name, status, email FROM clients WHERE id = ?', (lead_id,)
    ).fetchone()
    if not client:
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': False, 'error': 'not found'}), 404
        flash('Lead not found.', 'danger')
        return redirect(url_for('admin.admin_leads'))

    if request.is_json:
        data = request.get_json(silent=True) or {}
        status = _normalize_status(data.get('status'))
    else:
        status = _normalize_status(request.form.get('status'))

    db.execute(
        'UPDATE clients SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (status, lead_id),
    )
    # Mirror status onto leads row matched by email or name
    try:
        if client['email']:
            db.execute(
                'UPDATE leads SET status = ? WHERE lower(email) = lower(?)',
                (status, client['email']),
            )
        db.execute(
            'UPDATE leads SET status = ? WHERE full_name = ?',
            (status, client['full_name']),
        )
    except Exception:
        pass
    db.commit()
    log_activity('Pipeline → {}'.format(status), 'client', lead_id, client['full_name'])

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True, 'id': lead_id, 'status': status})
    flash('Status updated.', 'success')
    return redirect(url_for('admin.admin_leads'))


@admin.route('/admin/leads/delete/<int:lead_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_lead(lead_id):
    """Delete from clients (and matching leads)."""
    db = get_db()
    client = db.execute(
        'SELECT id, full_name, email FROM clients WHERE id = ?', (lead_id,)
    ).fetchone()
    if not client:
        flash('Lead not found.', 'danger')
        return redirect(url_for('admin.admin_leads'))
    db.execute('DELETE FROM clients WHERE id = ?', (lead_id,))
    try:
        if client['email']:
            db.execute('DELETE FROM leads WHERE lower(email) = lower(?)', (client['email'],))
        db.execute('DELETE FROM leads WHERE full_name = ?', (client['full_name'],))
    except Exception:
        pass
    db.commit()
    flash('Removed from pipeline.', 'success')
    return redirect(url_for('admin.admin_leads'))
