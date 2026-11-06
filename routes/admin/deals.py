"""Admin deals routes."""

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


@admin.route('/admin/deals')
@login_required
@role_required('admin')
def admin_deals():
    db = get_db()
    deals = db.execute(
        "SELECT d.*, p.title as property_title, c.full_name as client_name, u.full_name as agent_name FROM deals d LEFT JOIN properties p ON d.property_id = p.id LEFT JOIN clients c ON d.client_id = c.id LEFT JOIN users u ON d.agent_id = u.id ORDER BY d.updated_at DESC"
    ).fetchall()
    return render_template('admin_deals.html', deals=deals)


@admin.route('/admin/deals/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_deal():
    db = get_db()
    properties = db.execute('SELECT id, title FROM properties ORDER BY title').fetchall()
    clients = db.execute('SELECT id, full_name FROM clients ORDER BY full_name').fetchall()
    agents = db.execute("SELECT id, full_name FROM users WHERE role='agent' AND is_active=1 ORDER BY full_name").fetchall()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title required.', 'danger')
            return redirect(url_for('admin.admin_add_deal'))
        try:
            raw_val = (request.form.get('value') or '').strip().replace(',', '')
            try:
                value = float(raw_val) if raw_val else 0.0
            except ValueError:
                value = 0.0
            rate = float(request.form.get('commission_rate') or 2.5)
            prop_id = int(request.form.get('property_id') or 0) or None
            client_id = int(request.form.get('client_id') or 0) or None
            agent_id = int(request.form.get('agent_id') or 0) or None
        except ValueError:
            flash('Invalid numbers.', 'danger')
            return redirect(url_for('admin.admin_add_deal'))
        db.execute(
            "INSERT INTO deals (title, property_id, client_id, agent_id, stage, value, commission_rate, expected_close, notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (title, prop_id, client_id, agent_id, request.form.get('stage','offer'), value, rate,
             request.form.get('expected_close') or None, request.form.get('notes',''))
        )
        db.commit()
        log_activity('Created deal', 'deal', None, title)
        flash('Deal created.', 'success')
        return redirect(url_for('admin.admin_deals'))
    return render_template('admin_add_deal.html', properties=properties, clients=clients, agents=agents)


@admin.route('/admin/deals/stage/<int:deal_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_deal_stage(deal_id):
    stage = request.form.get('stage', 'offer')
    if stage not in ('offer', 'negotiation', 'under_contract', 'closed', 'lost'):
        flash('Invalid stage.', 'danger')
        return redirect(url_for('admin.admin_deals'))
    db = get_db()
    deal = db.execute('SELECT id, title, property_id FROM deals WHERE id=?', (deal_id,)).fetchone()
    db.execute("UPDATE deals SET stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (stage, deal_id))
    if deal and deal['property_id']:
        if stage == 'closed':
            db.execute("UPDATE properties SET status = 'sold' WHERE id = ?", (deal['property_id'],))
        elif stage in ('offer', 'negotiation', 'under_contract'):
            db.execute(
                "UPDATE properties SET status = 'under_offer' WHERE id = ? AND status IN ('available', 'under_offer')",
                (deal['property_id'],)
            )
        elif stage == 'lost':
            other = db.execute(
                "SELECT id FROM deals WHERE property_id = ? AND id != ? AND stage NOT IN ('closed','lost') LIMIT 1",
                (deal['property_id'], deal_id)
            ).fetchone()
            if not other:
                db.execute(
                    "UPDATE properties SET status = 'available' WHERE id = ? AND status = 'under_offer'",
                    (deal['property_id'],)
                )
    db.commit()
    title = deal['title'] if deal else str(deal_id)
    log_activity('Changed deal stage', 'deal', deal_id, '{} → {}'.format(title, stage))
    flash('Deal stage updated.', 'success')
    return redirect(url_for('admin.admin_deals'))


@admin.route('/admin/deals/delete/<int:deal_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_deal(deal_id):
    db = get_db()
    db.execute('DELETE FROM deals WHERE id=?', (deal_id,))
    db.commit()
    flash('Deal deleted.', 'success')
    return redirect(url_for('admin.admin_deals'))

@admin.route('/admin/export/deals')
@login_required
@role_required('admin')
def admin_export_deals():
    import csv
    import io
    from flask import Response
    db = get_db()
    rows = db.execute('''
        SELECT d.id, d.title, d.stage, d.value, d.commission_rate, d.expected_close,
               p.title as property_title, c.full_name as client_name, u.full_name as agent_name, d.created_at
        FROM deals d
        LEFT JOIN properties p ON d.property_id = p.id
        LEFT JOIN clients c ON d.client_id = c.id
        LEFT JOIN users u ON d.agent_id = u.id
        ORDER BY d.id
    ''').fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Title', 'Stage', 'Value', 'Commission %', 'Expected Close',
                     'Property', 'Client', 'Agent', 'Created'])
    for r in rows:
        writer.writerow([r['id'], r['title'], r['stage'], r['value'], r['commission_rate'],
                         r['expected_close'], r['property_title'], r['client_name'],
                         r['agent_name'], r['created_at']])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=estacle_deals.csv'}
    )


