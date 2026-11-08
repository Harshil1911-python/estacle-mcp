from flask import Blueprint
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


agent = Blueprint('agent', __name__)

@agent.route('/agent')
@login_required
@role_required('agent')
def agent_dashboard():
    db = get_db()
    migrate_db()
    agent_id = session['user_id']
    currency = get_setting('currency', 'INR')
    stats = {
        'my_properties': db.execute('SELECT COUNT(*) FROM properties WHERE agent_id = ?', (agent_id,)).fetchone()[0],
        'my_clients': db.execute('SELECT COUNT(*) FROM clients WHERE agent_id = ?', (agent_id,)).fetchone()[0],
        'appointments': db.execute('SELECT COUNT(*) FROM appointments WHERE agent_id = ?', (agent_id,)).fetchone()[0],
        'available': db.execute("SELECT COUNT(*) FROM properties WHERE agent_id = ? AND status = 'available'", (agent_id,)).fetchone()[0],
        'pending_tasks': db.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = ? AND status = 'pending'", (agent_id,)).fetchone()[0],
        'open_deals': db.execute("SELECT COUNT(*) FROM deals WHERE agent_id = ? AND stage NOT IN ('closed', 'lost')", (agent_id,)).fetchone()[0],
        'new_leads': db.execute("SELECT COUNT(*) FROM leads WHERE assigned_to = ? AND status = 'new'", (agent_id,)).fetchone()[0],
    }
    my_properties = db.execute('''
        SELECT id, title, price, status, city, property_type, listing_type, created_at
        FROM properties WHERE agent_id = ? ORDER BY created_at DESC LIMIT 8
    ''', (agent_id,)).fetchall()
    upcoming = db.execute('''
        SELECT a.*, c.full_name as client_name, p.title as property_title
        FROM appointments a
        LEFT JOIN clients c ON a.client_id = c.id
        LEFT JOIN properties p ON a.property_id = p.id
        WHERE a.agent_id = ? AND a.status = 'scheduled'
        ORDER BY a.appointment_date ASC LIMIT 6
    ''', (agent_id,)).fetchall()
    my_tasks = db.execute('''
        SELECT * FROM tasks WHERE assigned_to = ? AND status != 'completed'
        ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_date ASC LIMIT 6
    ''', (agent_id,)).fetchall()
    recent_leads = db.execute('''
        SELECT * FROM leads WHERE assigned_to = ? ORDER BY created_at DESC LIMIT 5
    ''', (agent_id,)).fetchall()
    return render_template('agent.html', stats=stats, my_properties=my_properties,
                           upcoming=upcoming, my_tasks=my_tasks, recent_leads=recent_leads,
                           currency=currency, format_money=format_money)



@agent.route('/agent/properties')
@login_required
@role_required('agent')
def agent_properties():
    db = get_db()
    migrate_db()
    agent_id = session['user_id']
    currency = get_setting('currency', 'INR')
    properties = db.execute('''
        SELECT * FROM properties WHERE agent_id = ? ORDER BY created_at DESC
    ''', (agent_id,)).fetchall()
    return render_template('agent_properties.html', properties=properties, currency=currency, format_money=format_money)


AGENT_STAGES = ('offer', 'negotiation', 'under_contract', 'closed', 'lost')
AGENT_STAGE_LABELS = {
    'offer': 'Offer',
    'negotiation': 'Negotiation',
    'under_contract': 'Under Contract',
    'closed': 'Closed',
    'lost': 'Lost',
}


def _agent_normalize_stage(st):
    st = (st or 'offer').strip().lower().replace(' ', '_')
    aliases = {
        'under contract': 'under_contract',
        'under-contract': 'under_contract',
        'won': 'closed',
        'sold': 'closed',
    }
    st = aliases.get(st, st)
    if st not in AGENT_STAGES:
        st = 'offer'
    return st


def _agent_deals_query(db, agent_id, q='', stage_filter=''):
    sql = (
        "SELECT d.*, p.title as property_title, c.full_name as client_name, "
        "u.full_name as agent_name "
        "FROM deals d "
        "LEFT JOIN properties p ON d.property_id = p.id "
        "LEFT JOIN clients c ON d.client_id = c.id "
        "LEFT JOIN users u ON d.agent_id = u.id "
        "WHERE d.agent_id = ?"
    )
    params = [agent_id]
    if q:
        sql += (
            " AND (d.title LIKE ? OR IFNULL(p.title,'') LIKE ? OR IFNULL(c.full_name,'') LIKE ? "
            "OR IFNULL(d.notes,'') LIKE ?)"
        )
        params.extend(['%' + q + '%'] * 4)
    if stage_filter in AGENT_STAGES:
        sql += " AND d.stage = ?"
        params.append(stage_filter)
    sql += " ORDER BY d.updated_at DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    for r in rows:
        r['stage'] = _agent_normalize_stage(r.get('stage'))
        try:
            r['commission_amount'] = (
                float(r.get('value') or 0) * float(r.get('commission_rate') or 0)
            ) / 100.0
        except Exception:
            r['commission_amount'] = 0
    return rows


@agent.route('/agent/deals')
@login_required
@role_required('agent')
def agent_deals():
    """Agent's deals list — scoped to this agent only."""
    db = get_db()
    migrate_db()
    agent_id = session['user_id']
    q = request.args.get('q', '').strip()
    stage_filter = request.args.get('stage', '').strip()
    if stage_filter:
        stage_filter = _agent_normalize_stage(stage_filter)
        if stage_filter not in AGENT_STAGES:
            stage_filter = ''
    rows = _agent_deals_query(db, agent_id, q=q, stage_filter=stage_filter)
    stats = {st: sum(1 for r in rows if r['stage'] == st) for st in AGENT_STAGES}
    stats['total'] = len(rows)
    currency = get_setting('currency', 'INR')
    return render_template(
        'agent_deals.html',
        deals=rows,
        stats=stats,
        stages=AGENT_STAGES,
        stage_labels=AGENT_STAGE_LABELS,
        q=q,
        stage_filter=stage_filter,
        currency=currency,
        format_money=format_money,
        active='deals',
    )


@agent.route('/agent/deals/summary')
@login_required
@role_required('agent')
def agent_deals_summary():
    """
    Overall Summary for agent — same columns as admin (status, customer, property)
    but scoped to this agent's deals. Read-only cascade delete is admin-only;
    agent can request removal of their own deal (not the property).
    """
    db = get_db()
    migrate_db()
    agent_id = session['user_id']
    q = request.args.get('q', '').strip()
    stage_filter = request.args.get('stage', '').strip()
    if stage_filter:
        stage_filter = _agent_normalize_stage(stage_filter)
        if stage_filter not in AGENT_STAGES:
            stage_filter = ''
    rows = _agent_deals_query(db, agent_id, q=q, stage_filter=stage_filter)
    stats = {st: sum(1 for r in rows if r['stage'] == st) for st in AGENT_STAGES}
    stats['total'] = len(rows)
    stats['closed_or_completed'] = stats.get('closed', 0)
    currency = get_setting('currency', 'INR')
    return render_template(
        'agent_deals_summary.html',
        deals=rows,
        stats=stats,
        stages=AGENT_STAGES,
        stage_labels=AGENT_STAGE_LABELS,
        q=q,
        stage_filter=stage_filter,
        currency=currency,
        format_money=format_money,
        active='deals_summary',
    )


@agent.route('/agent/deals/delete/<int:deal_id>', methods=['POST'])
@login_required
@role_required('agent')
def agent_delete_deal(deal_id):
    """Agent may remove their own deal only — never cascades the property."""
    db = get_db()
    migrate_db()
    agent_id = session['user_id']
    deal = db.execute(
        'SELECT id, title, agent_id FROM deals WHERE id = ?', (deal_id,)
    ).fetchone()
    if not deal:
        flash('Deal not found.', 'warning')
        return redirect(url_for('agent.agent_deals_summary'))
    if deal['agent_id'] != agent_id:
        flash('You can only delete your own deals.', 'danger')
        return redirect(url_for('agent.agent_deals_summary'))

    try:
        db.execute('DELETE FROM deal_payments WHERE deal_id = ?', (deal_id,))
    except Exception:
        pass
    try:
        db.execute('DELETE FROM deal_brokerage_payments WHERE deal_id = ?', (deal_id,))
    except Exception:
        pass
    db.execute('DELETE FROM deals WHERE id = ?', (deal_id,))
    db.commit()
    log_activity('Agent deleted deal', 'deal', deal_id, deal['title'])
    flash('Deal removed. Your dashboard and pipeline counts are updated.', 'success')

    next_url = request.form.get('next') or ''
    if next_url == 'list':
        return redirect(url_for('agent.agent_deals'))
    return redirect(url_for('agent.agent_deals_summary'))



