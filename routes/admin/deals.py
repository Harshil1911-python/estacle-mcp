"""Admin deals — Kanban pipeline (offer → closed / lost)."""

from flask import (
    render_template, request, redirect, url_for, session, flash,
    current_app, make_response, jsonify
)
from datetime import datetime
import csv
import io

from core.db import get_db, migrate_db
from core.helpers import (
    login_required, role_required, get_setting, set_setting,
    format_money, log_activity, get_redirect_for_role
)
from routes.admin import admin

STAGES = ('offer', 'negotiation', 'under_contract', 'closed', 'lost')
STAGE_LABELS = {
    'offer': 'Offer',
    'negotiation': 'Negotiation',
    'under_contract': 'Under Contract',
    'closed': 'Closed',
    'lost': 'Lost',
}


def _normalize_stage(st):
    st = (st or 'offer').strip().lower().replace(' ', '_')
    aliases = {
        'under contract': 'under_contract',
        'under-contract': 'under_contract',
        'won': 'closed',
        'sold': 'closed',
    }
    st = aliases.get(st, st)
    if st not in STAGES:
        st = 'offer'
    return st


def _property_status_for_purpose(purpose):
    """Map deal/listing purpose to property status stamp."""
    p = (purpose or '').strip().lower()
    if p in ('rent', 'rental', 'rented'):
        return 'rented'
    if p in ('lease', 'leased'):
        return 'leased'
    if p in ('sale', 'sell', 'sold', 'resale'):
        return 'sold'
    return 'sold'


def _sync_property_status(db, deal_id, stage, property_id, purpose=None):
    if not property_id:
        return
    if stage == 'closed':
        if not purpose:
            # try deal notes / property listing_type
            deal = db.execute('SELECT notes FROM deals WHERE id = ?', (deal_id,)).fetchone()
            notes = (deal['notes'] if deal else '') or ''
            purpose = None
            for key in ('Type:', 'type:'):
                if key in notes:
                    # "Type: Rent · Priority: ..."
                    part = notes.split(key, 1)[1].split('·')[0].strip()
                    purpose = part
                    break
            if not purpose:
                prop = db.execute('SELECT listing_type FROM properties WHERE id = ?', (property_id,)).fetchone()
                purpose = prop['listing_type'] if prop else 'sale'
        status = _property_status_for_purpose(purpose)
        db.execute("UPDATE properties SET status = ? WHERE id = ?", (status, property_id))
    elif stage in ('offer', 'negotiation', 'under_contract'):
        db.execute(
            "UPDATE properties SET status = 'under_offer' WHERE id = ? AND status IN ('available', 'under_offer')",
            (property_id,),
        )
    elif stage == 'lost':
        other = db.execute(
            "SELECT id FROM deals WHERE property_id = ? AND id != ? AND stage NOT IN ('closed','lost') LIMIT 1",
            (property_id, deal_id),
        ).fetchone()
        if not other:
            db.execute(
                "UPDATE properties SET status = 'available' WHERE id = ? AND status IN ('under_offer','sold','rented','leased')",
                (property_id,),
            )


def _deals_query(db, q='', agent_id='', stage_filter=''):
    sql = (
        "SELECT d.*, p.title as property_title, c.full_name as client_name, "
        "u.full_name as agent_name "
        "FROM deals d "
        "LEFT JOIN properties p ON d.property_id = p.id "
        "LEFT JOIN clients c ON d.client_id = c.id "
        "LEFT JOIN users u ON d.agent_id = u.id "
        "WHERE 1=1"
    )
    params = []
    if q:
        sql += (
            " AND (d.title LIKE ? OR IFNULL(p.title,'') LIKE ? OR IFNULL(c.full_name,'') LIKE ? "
            "OR IFNULL(u.full_name,'') LIKE ? OR IFNULL(d.notes,'') LIKE ?)"
        )
        params.extend(['%' + q + '%'] * 5)
    if agent_id.isdigit():
        sql += " AND d.agent_id = ?"
        params.append(int(agent_id))
    if stage_filter in STAGES:
        sql += " AND d.stage = ?"
        params.append(stage_filter)
    sql += " ORDER BY d.updated_at DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    for r in rows:
        r['stage'] = _normalize_stage(r.get('stage'))
        try:
            r['commission_amount'] = (float(r.get('value') or 0) * float(r.get('commission_rate') or 0)) / 100.0
        except Exception:
            r['commission_amount'] = 0
    return rows


@admin.route('/admin/deals')
@login_required
@role_required('admin')
def admin_deals():
    """All Deals — table list (like All Clients)."""
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    agent_id = request.args.get('agent_id', '').strip()
    stage_filter = _normalize_stage(request.args.get('stage', '').strip()) if request.args.get('stage') else ''
    if request.args.get('stage') and stage_filter not in STAGES:
        stage_filter = ''

    # Stats from all deals (unfiltered by stage for KPI totals)
    all_rows = _deals_query(db, q='', agent_id='')
    stats = {st: sum(1 for r in all_rows if r['stage'] == st) for st in STAGES}
    stats['total'] = len(all_rows)

    rows = _deals_query(db, q=q, agent_id=agent_id, stage_filter=stage_filter)
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name"
    ).fetchall()
    currency = get_setting('currency', 'INR')
    active_filters = sum(1 for v in (q, agent_id, stage_filter) if v)

    return render_template(
        'admin_deals.html',
        deals=rows,
        stats=stats,
        agents=agents,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        q=q,
        agent_id=agent_id,
        stage_filter=stage_filter,
        active_filters=active_filters,
        currency=currency,
        format_money=format_money,
    )


@admin.route('/admin/deals/pipeline')
@login_required
@role_required('admin')
def admin_deals_pipeline():
    """Deals Kanban pipeline."""
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    agent_id = request.args.get('agent_id', '').strip()
    focus = request.args.get('stage', '').strip()

    rows = _deals_query(db, q=q, agent_id=agent_id, stage_filter='')
    columns = []
    for st in STAGES:
        items = [r for r in rows if r['stage'] == st]
        columns.append({
            'key': st,
            'label': STAGE_LABELS[st],
            'deals': items,
            'count': len(items),
        })

    stats = {st: sum(1 for r in rows if r['stage'] == st) for st in STAGES}
    stats['total'] = len(rows)
    try:
        stats['pipeline_value'] = sum(
            float(r.get('value') or 0) for r in rows if r['stage'] not in ('closed', 'lost')
        )
    except Exception:
        stats['pipeline_value'] = 0

    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name"
    ).fetchall()
    currency = get_setting('currency', 'INR')

    return render_template(
        'admin_deals_pipeline.html',
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


@admin.route('/admin/deals/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_deal():
    db = get_db()
    migrate_db()
    properties = db.execute('SELECT id, title, price, listing_type, agent_id FROM properties ORDER BY title').fetchall()
    clients = db.execute('SELECT id, full_name FROM clients ORDER BY full_name').fetchall()
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name"
    ).fetchall()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'danger')
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
        stage = _normalize_stage(request.form.get('stage', 'offer'))
        expected_close = request.form.get('expected_close') or None
        notes = request.form.get('notes', '').strip() or None
        # fold extra detail into notes
        extra = []
        for key, label in (
            ('deal_type', 'Type'),
            ('priority', 'Priority'),
            ('source', 'Source'),
            ('probability', 'Probability'),
        ):
            v = (request.form.get(key) or '').strip()
            if v:
                extra.append('{}: {}'.format(label, v))
        if extra:
            notes = ((notes + '\n') if notes else '') + ' · '.join(extra)

        cur = db.execute(
            """INSERT INTO deals
               (title, property_id, client_id, agent_id, stage, value, commission_rate, expected_close, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (title, prop_id, client_id, agent_id, stage, value, rate, expected_close, notes),
        )
        deal_id = cur.lastrowid
        _sync_property_status(db, deal_id, stage, prop_id)
        db.commit()
        log_activity('Created deal', 'deal', deal_id, title)
        flash('Deal "{}" added to pipeline.'.format(title), 'success')
        return redirect(url_for('admin.admin_deals'))

    return render_template(
        'admin_add_deal.html',
        properties=properties,
        clients=clients,
        agents=agents,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
    )


@admin.route('/admin/deals/stage/<int:deal_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_deal_stage(deal_id):
    db = get_db()
    migrate_db()
    deal = db.execute(
        'SELECT id, title, property_id, stage FROM deals WHERE id=?', (deal_id,)
    ).fetchone()
    if not deal:
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': False, 'error': 'not found'}), 404
        flash('Deal not found.', 'danger')
        return redirect(url_for('admin.admin_deals'))

    if request.is_json:
        data = request.get_json(silent=True) or {}
        stage = _normalize_stage(data.get('stage') or data.get('status'))
    else:
        stage = _normalize_stage(request.form.get('stage'))

    db.execute(
        'UPDATE deals SET stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
        (stage, deal_id),
    )
    _sync_property_status(db, deal_id, stage, deal['property_id'])
    db.commit()
    log_activity('Pipeline → {}'.format(stage), 'deal', deal_id, deal['title'])

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True, 'id': deal_id, 'stage': stage})
    flash('Deal stage updated.', 'success')
    return redirect(url_for('admin.admin_deals'))


def _cascade_delete_deal_and_property(db, deal_id, delete_property=True):
    """
    Remove a deal and (optionally) its linked property from the live CRM.
    Cleans related payment / favorite / inquiry rows so analytics and agent
    views stay consistent and never hit broken foreign references.
    Historical stats simply reflect remaining live rows (no orphaned data).
    """
    deal = db.execute(
        'SELECT id, title, property_id FROM deals WHERE id=?', (deal_id,)
    ).fetchone()
    if not deal:
        return None, None

    prop_id = deal['property_id']
    title = deal['title']

    # Payments / brokerage tied to this deal
    try:
        db.execute('DELETE FROM deal_payments WHERE deal_id=?', (deal_id,))
    except Exception:
        pass
    try:
        db.execute('DELETE FROM deal_brokerage_payments WHERE deal_id=?', (deal_id,))
    except Exception:
        pass

    db.execute('DELETE FROM deals WHERE id=?', (deal_id,))

    prop_title = None
    if delete_property and prop_id:
        prop = db.execute('SELECT id, title FROM properties WHERE id=?', (prop_id,)).fetchone()
        if prop:
            prop_title = prop['title']
            # Other deals still pointing at this property → remove them too so property can go
            other_deals = db.execute(
                'SELECT id FROM deals WHERE property_id=?', (prop_id,)
            ).fetchall()
            for od in other_deals:
                try:
                    db.execute('DELETE FROM deal_payments WHERE deal_id=?', (od['id'],))
                    db.execute('DELETE FROM deal_brokerage_payments WHERE deal_id=?', (od['id'],))
                except Exception:
                    pass
                db.execute('DELETE FROM deals WHERE id=?', (od['id'],))

            # Related tables that reference the property
            for sql in (
                'DELETE FROM favorites WHERE property_id=?',
                'DELETE FROM client_properties WHERE property_id=?',
                'DELETE FROM inquiries WHERE property_id=?',
            ):
                try:
                    db.execute(sql, (prop_id,))
                except Exception:
                    pass
            try:
                db.execute(
                    "UPDATE appointments SET property_id=NULL WHERE property_id=?",
                    (prop_id,),
                )
            except Exception:
                pass
            try:
                db.execute(
                    "UPDATE tasks SET related_property_id=NULL WHERE related_property_id=?",
                    (prop_id,),
                )
            except Exception:
                pass

            db.execute('DELETE FROM properties WHERE id=?', (prop_id,))

    return title, prop_title


@admin.route('/admin/deals/delete/<int:deal_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_deal(deal_id):
    db = get_db()
    migrate_db()
    # From Overall Summary we also remove the linked property
    also_property = request.form.get('also_property', '0') in ('1', 'true', 'yes', 'on')
    title, prop_title = _cascade_delete_deal_and_property(
        db, deal_id, delete_property=also_property
    )
    db.commit()
    if title:
        log_activity(
            'Deleted deal' + (' + property' if prop_title else ''),
            'deal',
            deal_id,
            title + (f' / property: {prop_title}' if prop_title else ''),
        )
        pass
    next_url = request.form.get('next') or request.args.get('next') or ''
    if next_url == 'summary':
        return redirect(url_for('admin.admin_deals_summary', toast='Updated'))
    if title:
        if prop_title:
            flash(f'Deal and property “{prop_title}” removed. Live stats & agent views updated.', 'success')
        else:
            flash('Deal deleted.', 'success')
    else:
        flash('Deal not found.', 'warning')
    return redirect(url_for('admin.admin_deals'))


@admin.route('/admin/deals/summary')
@login_required
@role_required('admin')
def admin_deals_summary():
    """Overall Summary — every deal with status, agent, customer, and cascade delete.

    Status rules for display:
    - closed: customer has paid the full property/deal value
    - completed: customer has paid full property value AND full brokerage
    """
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    stage_filter = request.args.get('stage', '').strip()
    if stage_filter:
        stage_filter = _normalize_stage(stage_filter)
        if stage_filter not in STAGES:
            stage_filter = ''

    rows = _deals_query(db, q=q, agent_id='', stage_filter=stage_filter)

    # Attach payment and brokerage totals for closed / completed classification
    for r in rows:
        deal_id = r['id']
        paid = db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM deal_payments WHERE deal_id = ?",
            (deal_id,),
        ).fetchone()[0]
        brokerage_received = db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM deal_brokerage_payments WHERE deal_id = ?",
            (deal_id,),
        ).fetchone()[0]
        value = float(r.get('value') or 0)
        try:
            brokerage_due = (value * float(r.get('commission_rate') or 0)) / 100.0
        except Exception:
            brokerage_due = 0.0
        paid = float(paid or 0)
        brokerage_received = float(brokerage_received or 0)
        r['customer_paid'] = paid
        r['brokerage_received'] = brokerage_received
        r['brokerage_due'] = brokerage_due
        r['customer_balance'] = max(value - paid, 0)
        r['brokerage_balance'] = max(brokerage_due - brokerage_received, 0)
        fully_paid = value <= 0 or paid >= value - 0.01
        fully_brokerage = brokerage_due <= 0 or brokerage_received >= brokerage_due - 0.01
        # Display status: Closed when property fully paid; Completed when both paid
        if fully_paid and fully_brokerage:
            r['summary_status'] = 'completed'
            r['summary_status_label'] = 'Completed'
        elif fully_paid or r['stage'] == 'closed':
            r['summary_status'] = 'closed'
            r['summary_status_label'] = 'Closed'
        else:
            r['summary_status'] = r['stage']
            r['summary_status_label'] = STAGE_LABELS.get(r['stage'], r['stage'])

    stats = {st: sum(1 for r in rows if r['stage'] == st) for st in STAGES}
    stats['total'] = len(rows)
    stats['closed'] = sum(1 for r in rows if r.get('summary_status') == 'closed')
    stats['completed'] = sum(1 for r in rows if r.get('summary_status') == 'completed')
    currency = get_setting('currency', 'INR')

    toast = request.args.get('toast', '').strip()
    return render_template(
        'admin_deals_summary.html',
        deals=rows,
        stats=stats,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        q=q,
        stage_filter=stage_filter,
        currency=currency,
        format_money=format_money,
        toast=toast,
    )




@admin.route('/admin/deals/<int:deal_id>')
@login_required
@role_required('admin')
def admin_deal_detail(deal_id):
    db = get_db()
    migrate_db()
    row = db.execute(
        """SELECT d.*, p.title as property_title, c.full_name as client_name, u.full_name as agent_name
           FROM deals d
           LEFT JOIN properties p ON d.property_id = p.id
           LEFT JOIN clients c ON d.client_id = c.id
           LEFT JOIN users u ON d.agent_id = u.id
           WHERE d.id = ?""",
        (deal_id,),
    ).fetchone()
    if not row:
        flash('Deal not found.', 'danger')
        return redirect(url_for('admin.admin_deals'))
    deal = dict(row)
    deal['stage'] = _normalize_stage(deal.get('stage'))
    value = float(deal.get('value') or 0)
    try:
        deal['commission_amount'] = (value * float(deal.get('commission_rate') or 0)) / 100.0
    except Exception:
        deal['commission_amount'] = 0
    customer_paid = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM deal_payments WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()[0] or 0)
    brokerage_received = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM deal_brokerage_payments WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()[0] or 0)
    brokerage_due = float(deal['commission_amount'] or 0)
    fully_paid = value <= 0 or customer_paid >= value - 0.01
    fully_brokerage = brokerage_due <= 0 or brokerage_received >= brokerage_due - 0.01
    if fully_paid and fully_brokerage:
        summary_status = 'completed'
        summary_status_label = 'Completed'
    elif fully_paid or deal['stage'] == 'closed':
        summary_status = 'closed'
        summary_status_label = 'Closed'
    else:
        summary_status = deal['stage']
        summary_status_label = STAGE_LABELS.get(deal['stage'], deal['stage'])
    payments = [dict(p) for p in db.execute(
        "SELECT * FROM deal_payments WHERE deal_id = ? ORDER BY COALESCE(payment_date, created_at) DESC, id DESC",
        (deal_id,),
    ).fetchall()]
    brokerage_payments = [dict(p) for p in db.execute(
        "SELECT * FROM deal_brokerage_payments WHERE deal_id = ? ORDER BY COALESCE(payment_date, created_at) DESC, id DESC",
        (deal_id,),
    ).fetchall()]
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_deal_detail.html',
        deal=deal,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        currency=currency,
        format_money=format_money,
        customer_paid=customer_paid,
        customer_balance=max(value - customer_paid, 0),
        customer_pct=round(100.0 * customer_paid / value, 1) if value > 0 else 0,
        brokerage_due=brokerage_due,
        brokerage_received=brokerage_received,
        brokerage_balance=max(brokerage_due - brokerage_received, 0),
        brokerage_pct=round(100.0 * brokerage_received / brokerage_due, 1) if brokerage_due > 0 else 0,
        summary_status=summary_status,
        summary_status_label=summary_status_label,
        payments=payments,
        brokerage_payments=brokerage_payments,
    )


@admin.route('/admin/deals/<int:deal_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_edit_deal(deal_id):
    db = get_db()
    migrate_db()
    deal = db.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not deal:
        flash('Deal not found.', 'danger')
        return redirect(url_for('admin.admin_deals'))
    properties = db.execute('SELECT id, title, price, listing_type, agent_id FROM properties ORDER BY title').fetchall()
    clients = db.execute('SELECT id, full_name FROM clients ORDER BY full_name').fetchall()
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name"
    ).fetchall()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'danger')
            return redirect(url_for('admin.admin_edit_deal', deal_id=deal_id))
        try:
            raw_val = (request.form.get('value') or '').strip().replace(',', '')
            value = float(raw_val) if raw_val else 0.0
            rate = float(request.form.get('commission_rate') or 2.5)
            prop_id = int(request.form.get('property_id') or 0) or None
            client_id = int(request.form.get('client_id') or 0) or None
            agent_id = int(request.form.get('agent_id') or 0) or None
        except ValueError:
            flash('Invalid numbers.', 'danger')
            return redirect(url_for('admin.admin_edit_deal', deal_id=deal_id))
        stage = _normalize_stage(request.form.get('stage', deal['stage']))
        expected_close = request.form.get('expected_close') or None
        notes = request.form.get('notes', '').strip() or None
        db.execute(
            """UPDATE deals SET title=?, property_id=?, client_id=?, agent_id=?, stage=?,
               value=?, commission_rate=?, expected_close=?, notes=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (title, prop_id, client_id, agent_id, stage, value, rate, expected_close, notes, deal_id),
        )
        _sync_property_status(db, deal_id, stage, prop_id)
        db.commit()
        log_activity('Updated deal', 'deal', deal_id, title)
        flash('Deal updated.', 'success')
        return redirect(url_for('admin.admin_deal_detail', deal_id=deal_id))
    return render_template(
        'admin_edit_deal.html',
        deal=dict(deal),
        properties=properties,
        clients=clients,
        agents=agents,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
    )

@admin.route('/admin/export/deals')
@admin.route('/admin/deals/export')
@login_required
@role_required('admin')
def admin_export_deals():
    db = get_db()
    migrate_db()
    stage = request.args.get('stage', '').strip().lower()
    if stage:
        stage = _normalize_stage(stage)
    sql = (
        "SELECT d.id, d.title, d.stage, d.value, d.commission_rate, d.expected_close, d.notes, d.created_at, "
        "p.title as property_title, c.full_name as client_name, u.full_name as agent_name "
        "FROM deals d "
        "LEFT JOIN properties p ON d.property_id = p.id "
        "LEFT JOIN clients c ON d.client_id = c.id "
        "LEFT JOIN users u ON d.agent_id = u.id WHERE 1=1"
    )
    params = []
    if stage in STAGES:
        sql += " AND d.stage = ?"
        params.append(stage)
    sql += " ORDER BY d.updated_at DESC"
    rows = db.execute(sql, params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'id', 'title', 'stage', 'value', 'commission_rate', 'expected_close',
        'property', 'client', 'agent', 'notes', 'created_at',
    ])
    for r in rows:
        writer.writerow([
            r['id'], r['title'], _normalize_stage(r['stage']), r['value'],
            r['commission_rate'], r['expected_close'], r['property_title'],
            r['client_name'], r['agent_name'], r['notes'], r['created_at'],
        ])
    filename = 'deals_{}.csv'.format(stage) if stage in STAGES else 'deals.csv'
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = 'attachment; filename={}'.format(filename)
    return resp


@admin.route('/admin/deals/import', methods=['POST'])
@login_required
@role_required('admin')
def admin_import_deals():
    db = get_db()
    migrate_db()
    f = request.files.get('import_file')
    if not f or not f.filename:
        flash('Choose a CSV or Excel file to import.', 'danger')
        return redirect(url_for('admin.admin_add_deal') + '#import')
    rows = []
    try:
        name = (f.filename or '').lower()
        if name.endswith(('.xlsx', '.xls')):
            import openpyxl
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            ws = wb.active
            data = list(ws.iter_rows(values_only=True))
            if not data:
                flash('Empty spreadsheet.', 'warning')
                return redirect(url_for('admin.admin_add_deal') + '#import')
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
        return redirect(url_for('admin.admin_add_deal') + '#import')

    # Resolve property / client / agent by title or name / email
    props = {
        (r['title'] or '').strip().lower(): r['id']
        for r in db.execute('SELECT id, title FROM properties').fetchall()
    }
    clients = {
        (r['full_name'] or '').strip().lower(): r['id']
        for r in db.execute('SELECT id, full_name FROM clients').fetchall()
    }
    agents_by_name = {
        (r['full_name'] or '').strip().lower(): r['id']
        for r in db.execute(
            "SELECT id, full_name FROM users WHERE role IN ('admin','agent')"
        ).fetchall()
    }
    agents_by_email = {
        (r['email'] or '').strip().lower(): r['id']
        for r in db.execute(
            "SELECT id, email FROM users WHERE role IN ('admin','agent')"
        ).fetchall()
    }

    added = 0
    skipped = 0
    for row in rows:
        title = str(row.get('title') or '').strip()
        if not title:
            skipped += 1
            continue
        stage = _normalize_stage(str(row.get('stage') or 'offer'))
        value = 0.0
        try:
            if row.get('value') not in (None, ''):
                value = float(str(row.get('value')).replace(',', ''))
        except Exception:
            value = 0.0
        rate = 2.5
        try:
            if row.get('commission_rate') not in (None, ''):
                rate = float(row.get('commission_rate'))
        except Exception:
            rate = 2.5
        prop_key = str(row.get('property') or row.get('property_title') or '').strip().lower()
        client_key = str(row.get('client') or row.get('client_name') or '').strip().lower()
        agent_key = str(row.get('agent') or row.get('agent_name') or '').strip().lower()
        agent_email = str(row.get('agent_email') or '').strip().lower()
        prop_id = props.get(prop_key)
        client_id = clients.get(client_key)
        agent_id = agents_by_email.get(agent_email) or agents_by_name.get(agent_key)
        expected_close = str(row.get('expected_close') or '').strip() or None
        notes = str(row.get('notes') or '').strip() or None
        cur = db.execute(
            """INSERT INTO deals
               (title, property_id, client_id, agent_id, stage, value, commission_rate, expected_close, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (title, prop_id, client_id, agent_id, stage, value, rate, expected_close, notes),
        )
        _sync_property_status(db, cur.lastrowid, stage, prop_id)
        added += 1
    db.commit()
    if added:
        log_activity('Imported {} deal(s)'.format(added), 'deal', None, None)
        flash('Imported {} deal(s){}.'.format(
            added, ' · skipped {}'.format(skipped) if skipped else ''
        ), 'success')
    else:
        flash('No deals imported{}.'.format(
            ' · skipped {}'.format(skipped) if skipped else ''
        ), 'warning')
    return redirect(url_for('admin.admin_deals'))


@admin.route('/admin/deals/payments')
@login_required
@role_required('admin')
def admin_track_payments():
    """Track payments against deals (customer payments toward deal value)."""
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    deal_id = request.args.get('deal_id', '').strip()
    sql = (
        "SELECT d.id, d.title, d.value, d.stage, d.client_id, "
        "c.full_name as client_name, p.title as property_title, "
        "COALESCE((SELECT SUM(amount) FROM deal_payments dp WHERE dp.deal_id = d.id), 0) as paid "
        "FROM deals d "
        "LEFT JOIN clients c ON d.client_id = c.id "
        "LEFT JOIN properties p ON d.property_id = p.id WHERE 1=1"
    )
    params = []
    if q:
        sql += " AND (d.title LIKE ? OR IFNULL(c.full_name,'') LIKE ? OR IFNULL(p.title,'') LIKE ?)"
        params.extend(['%' + q + '%'] * 3)
    if deal_id.isdigit():
        sql += " AND d.id = ?"
        params.append(int(deal_id))
    sql += " ORDER BY d.updated_at DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    for r in rows:
        val = float(r.get('value') or 0)
        paid = float(r.get('paid') or 0)
        r['balance'] = max(val - paid, 0)
        r['pct'] = round(100.0 * paid / val, 1) if val > 0 else 0
        r['stage'] = _normalize_stage(r.get('stage'))
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_deal_payments.html',
        deals=rows,
        q=q,
        deal_id=deal_id,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        currency=currency,
        format_money=format_money,
    )


@admin.route('/admin/deals/<int:deal_id>/payments', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_deal_payments(deal_id):
    db = get_db()
    migrate_db()
    deal = db.execute(
        """SELECT d.*, c.full_name as client_name, p.title as property_title
           FROM deals d
           LEFT JOIN clients c ON d.client_id = c.id
           LEFT JOIN properties p ON d.property_id = p.id
           WHERE d.id = ?""",
        (deal_id,),
    ).fetchone()
    if not deal:
        flash('Deal not found.', 'danger')
        return redirect(url_for('admin.admin_track_payments'))
    if request.method == 'POST':
        try:
            amount = float((request.form.get('amount') or '0').replace(',', ''))
        except ValueError:
            amount = 0
        if amount <= 0:
            flash('Enter a valid payment amount.', 'danger')
            return redirect(url_for('admin.admin_deal_payments', deal_id=deal_id))
        db.execute(
            """INSERT INTO deal_payments
               (deal_id, amount, payment_date, method, reference, notes, created_by)
               VALUES (?,?,?,?,?,?,?)""",
            (
                deal_id,
                amount,
                request.form.get('payment_date') or None,
                request.form.get('method') or None,
                request.form.get('reference') or None,
                request.form.get('notes') or None,
                session.get('user_id'),
            ),
        )
        # Auto-close deal when payments reach 100% of deal value
        total_paid = db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM deal_payments WHERE deal_id = ?",
            (deal_id,),
        ).fetchone()[0]
        deal_value = float(deal['value'] or 0)
        closed_now = False
        if deal_value > 0 and float(total_paid) >= deal_value - 0.01:
            purpose = request.form.get('deal_purpose') or None
            if not purpose:
                notes = deal['notes'] or ''
                if 'Type:' in notes:
                    purpose = notes.split('Type:', 1)[1].split('·')[0].strip()
            if not purpose and deal['property_id']:
                prop = db.execute(
                    'SELECT listing_type FROM properties WHERE id = ?', (deal['property_id'],)
                ).fetchone()
                purpose = prop['listing_type'] if prop else 'sale'
            db.execute(
                "UPDATE deals SET stage = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (deal_id,),
            )
            _sync_property_status(db, deal_id, 'closed', deal['property_id'], purpose=purpose)
            closed_now = True
            log_activity('Deal auto-closed (paid in full)', 'deal', deal_id, deal['title'])
        db.commit()
        log_activity('Payment recorded', 'deal', deal_id, '{} on deal {}'.format(amount, deal['title']))
        if closed_now:
            flash('Payment recorded. Deal marked Closed — property stamped complete.', 'success')
        else:
            flash('Payment recorded.', 'success')
        return redirect(url_for('admin.admin_deal_payments', deal_id=deal_id))
    payments = db.execute(
        "SELECT * FROM deal_payments WHERE deal_id = ? ORDER BY COALESCE(payment_date, created_at) DESC, id DESC",
        (deal_id,),
    ).fetchall()
    paid = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM deal_payments WHERE deal_id = ?", (deal_id,)
    ).fetchone()[0]
    value = float(deal['value'] or 0)
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_deal_payment_detail.html',
        deal=dict(deal),
        payments=payments,
        paid=float(paid or 0),
        balance=max(value - float(paid or 0), 0),
        pct=round(100.0 * float(paid or 0) / value, 1) if value > 0 else 0,
        currency=currency,
        format_money=format_money,
        stage_labels=STAGE_LABELS,
    )


@admin.route('/admin/deals/payments/<int:payment_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_payment(payment_id):
    db = get_db()
    row = db.execute('SELECT id, deal_id, amount FROM deal_payments WHERE id = ?', (payment_id,)).fetchone()
    if not row:
        flash('Payment not found.', 'danger')
        return redirect(url_for('admin.admin_track_payments'))
    deal_id = row['deal_id']
    db.execute('DELETE FROM deal_payments WHERE id = ?', (payment_id,))
    db.commit()
    flash('Payment removed.', 'success')
    return redirect(url_for('admin.admin_deal_payments', deal_id=deal_id))


@admin.route('/admin/deals/<int:deal_id>/payments/export')
@login_required
@role_required('admin')
def admin_export_deal_payments(deal_id):
    db = get_db()
    migrate_db()
    deal = db.execute('SELECT id, title, value FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not deal:
        flash('Deal not found.', 'danger')
        return redirect(url_for('admin.admin_track_payments'))
    rows = db.execute(
        """SELECT id, amount, payment_date, method, reference, notes, created_at
           FROM deal_payments WHERE deal_id = ?
           ORDER BY COALESCE(payment_date, created_at) ASC, id ASC""",
        (deal_id,),
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'amount', 'payment_date', 'method', 'reference', 'notes', 'created_at', 'deal_id', 'deal_title', 'deal_value'])
    for r in rows:
        writer.writerow([
            r['id'], r['amount'], r['payment_date'], r['method'], r['reference'],
            r['notes'], r['created_at'], deal_id, deal['title'], deal['value'],
        ])
    filename = 'deal_{}_payments.csv'.format(deal_id)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = 'attachment; filename={}'.format(filename)
    return resp



@admin.route('/admin/deals/brokerage')
@login_required
@role_required('admin')
def admin_brokerage_payments():
    """Track brokerage due and received for open and closed deals."""
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    sql = (
        "SELECT d.id, d.title, d.value, d.commission_rate, d.stage, d.expected_close, "
        "c.full_name as client_name, p.title as property_title, u.full_name as agent_name, "
        "COALESCE((SELECT SUM(amount) FROM deal_payments dp WHERE dp.deal_id = d.id), 0) as customer_paid, "
        "COALESCE((SELECT SUM(amount) FROM deal_brokerage_payments bp WHERE bp.deal_id = d.id), 0) as brokerage_received "
        "FROM deals d "
        "LEFT JOIN clients c ON d.client_id = c.id "
        "LEFT JOIN properties p ON d.property_id = p.id "
        "LEFT JOIN users u ON d.agent_id = u.id "
        "WHERE lower(IFNULL(d.stage,'')) != 'lost'"
    )
    params = []
    if q:
        sql += " AND (d.title LIKE ? OR IFNULL(c.full_name,'') LIKE ? OR IFNULL(u.full_name,'') LIKE ? OR IFNULL(p.title,'') LIKE ?)"
        params.extend(['%' + q + '%'] * 4)
    sql += " ORDER BY CASE WHEN lower(IFNULL(d.stage,'')) = 'closed' THEN 1 ELSE 0 END, d.updated_at DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    total_open_brokerage = 0.0
    received_open_brokerage = 0.0
    total_all_brokerage = 0.0
    received_all = 0.0
    for r in rows:
        r['stage'] = _normalize_stage(r.get('stage'))
        try:
            r['brokerage_due'] = (float(r.get('value') or 0) * float(r.get('commission_rate') or 0)) / 100.0
        except Exception:
            r['brokerage_due'] = 0.0
        r['customer_paid'] = float(r.get('customer_paid') or 0)
        r['brokerage_received'] = float(r.get('brokerage_received') or 0)
        val = float(r.get('value') or 0)
        r['customer_balance'] = max(val - r['customer_paid'], 0)
        r['brokerage_balance'] = max(r['brokerage_due'] - r['brokerage_received'], 0)
        r['customer_pct'] = round(100.0 * r['customer_paid'] / val, 1) if val > 0 else 0
        r['brokerage_pct'] = round(100.0 * r['brokerage_received'] / r['brokerage_due'], 1) if r['brokerage_due'] > 0 else 0
        fully_paid = val <= 0 or r['customer_paid'] >= val - 0.01
        fully_brokerage = r['brokerage_due'] <= 0 or r['brokerage_received'] >= r['brokerage_due'] - 0.01
        r['completed'] = bool(fully_paid and fully_brokerage and r['stage'] == 'closed')
        total_all_brokerage += r['brokerage_due']
        received_all += r['brokerage_received']
        if r['stage'] != 'closed':
            total_open_brokerage += r['brokerage_due']
            received_open_brokerage += r['brokerage_received']
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_brokerage.html',
        deals=rows,
        total_open_brokerage=total_open_brokerage,
        received_open_brokerage=received_open_brokerage,
        total_all_brokerage=total_all_brokerage,
        received_all=received_all,
        q=q,
        stage_labels=STAGE_LABELS,
        currency=currency,
        format_money=format_money,
    )


@admin.route('/admin/deals/<int:deal_id>/brokerage', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_deal_brokerage(deal_id):
    db = get_db()
    migrate_db()
    deal = db.execute(
        """SELECT d.*, c.full_name as client_name, p.title as property_title, u.full_name as agent_name
           FROM deals d
           LEFT JOIN clients c ON d.client_id = c.id
           LEFT JOIN properties p ON d.property_id = p.id
           LEFT JOIN users u ON d.agent_id = u.id
           WHERE d.id = ?""",
        (deal_id,),
    ).fetchone()
    if not deal:
        flash('Deal not found.', 'danger')
        return redirect(url_for('admin.admin_brokerage_payments'))
    if request.method == 'POST':
        try:
            amount = float((request.form.get('amount') or '0').replace(',', ''))
        except ValueError:
            amount = 0
        if amount <= 0:
            flash('Enter a valid brokerage amount.', 'danger')
            return redirect(url_for('admin.admin_deal_brokerage', deal_id=deal_id))
        db.execute(
            """INSERT INTO deal_brokerage_payments
               (deal_id, amount, payment_date, method, reference, notes, created_by)
               VALUES (?,?,?,?,?,?,?)""",
            (
                deal_id,
                amount,
                request.form.get('payment_date') or None,
                request.form.get('method') or None,
                request.form.get('reference') or None,
                request.form.get('notes') or None,
                session.get('user_id'),
            ),
        )
        db.commit()
        log_activity('Brokerage recorded', 'deal', deal_id, str(amount))
        flash('Brokerage payment recorded.', 'success')
        return redirect(url_for('admin.admin_deal_brokerage', deal_id=deal_id))

    payments = db.execute(
        "SELECT * FROM deal_brokerage_payments WHERE deal_id = ? ORDER BY COALESCE(payment_date, created_at) DESC, id DESC",
        (deal_id,),
    ).fetchall()
    received = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM deal_brokerage_payments WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()[0] or 0)
    customer_paid = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM deal_payments WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()[0] or 0)
    value = float(deal['value'] or 0)
    try:
        due = (value * float(deal['commission_rate'] or 0)) / 100.0
    except Exception:
        due = 0.0
    stage = _normalize_stage(deal['stage'])
    completed = (
        stage == 'closed'
        and (value <= 0 or customer_paid >= value - 0.01)
        and (due <= 0 or received >= due - 0.01)
    )
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_deal_brokerage_detail.html',
        deal=dict(deal),
        payments=payments,
        due=due,
        received=received,
        balance=max(due - received, 0),
        pct=round(100.0 * received / due, 1) if due > 0 else 0,
        customer_paid=customer_paid,
        customer_balance=max(value - customer_paid, 0),
        completed=completed,
        stage=stage,
        currency=currency,
        format_money=format_money,
        stage_labels=STAGE_LABELS,
    )


@admin.route('/admin/deals/brokerage/<int:payment_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_brokerage_payment(payment_id):
    db = get_db()
    row = db.execute(
        'SELECT id, deal_id FROM deal_brokerage_payments WHERE id = ?', (payment_id,)
    ).fetchone()
    if not row:
        flash('Record not found.', 'danger')
        return redirect(url_for('admin.admin_brokerage_payments'))
    deal_id = row['deal_id']
    db.execute('DELETE FROM deal_brokerage_payments WHERE id = ?', (payment_id,))
    db.commit()
    flash('Brokerage entry removed.', 'success')
    return redirect(url_for('admin.admin_deal_brokerage', deal_id=deal_id))


@admin.route('/admin/deals/<int:deal_id>/brokerage/export')
@login_required
@role_required('admin')
def admin_export_deal_brokerage(deal_id):
    db = get_db()
    migrate_db()
    deal = db.execute('SELECT id, title, value, commission_rate FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not deal:
        flash('Deal not found.', 'danger')
        return redirect(url_for('admin.admin_brokerage_payments'))
    rows = db.execute(
        """SELECT id, amount, payment_date, method, reference, notes, created_at
           FROM deal_brokerage_payments WHERE deal_id = ?
           ORDER BY COALESCE(payment_date, created_at) ASC, id ASC""",
        (deal_id,),
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'amount', 'payment_date', 'method', 'reference', 'notes', 'created_at', 'deal_id', 'deal_title'])
    for r in rows:
        writer.writerow([r['id'], r['amount'], r['payment_date'], r['method'], r['reference'], r['notes'], r['created_at'], deal_id, deal['title']])
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = 'attachment; filename=deal_{}_brokerage.csv'.format(deal_id)
    return resp
