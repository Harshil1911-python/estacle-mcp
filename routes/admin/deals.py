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


def _sync_property_status(db, deal_id, stage, property_id):
    if not property_id:
        return
    if stage == 'closed':
        db.execute("UPDATE properties SET status = 'sold' WHERE id = ?", (property_id,))
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
                "UPDATE properties SET status = 'available' WHERE id = ? AND status = 'under_offer'",
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
    properties = db.execute('SELECT id, title FROM properties ORDER BY title').fetchall()
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


@admin.route('/admin/deals/delete/<int:deal_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_deal(deal_id):
    db = get_db()
    deal = db.execute('SELECT id, title FROM deals WHERE id=?', (deal_id,)).fetchone()
    db.execute('DELETE FROM deals WHERE id=?', (deal_id,))
    db.commit()
    if deal:
        log_activity('Deleted deal', 'deal', deal_id, deal['title'])
    flash('Deal deleted.', 'success')
    return redirect(url_for('admin.admin_deals'))




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
    try:
        deal['commission_amount'] = (float(deal.get('value') or 0) * float(deal.get('commission_rate') or 0)) / 100.0
    except Exception:
        deal['commission_amount'] = 0
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_deal_detail.html',
        deal=deal,
        stages=STAGES,
        stage_labels=STAGE_LABELS,
        currency=currency,
        format_money=format_money,
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
    properties = db.execute('SELECT id, title FROM properties ORDER BY title').fetchall()
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
