"""Admin analytics routes."""

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


@admin.route('/admin/analytics')
@login_required
@role_required('admin')
def admin_analytics():
    from datetime import timedelta
    import csv
    import io
    from flask import make_response

    db = get_db()
    migrate_db()

    from_date = (request.args.get('from') or '').strip()
    to_date = (request.args.get('to') or '').strip()
    export = (request.args.get('export') or '').strip().lower()

    today = datetime.now().date()
    if not to_date:
        to_date = today.isoformat()
    if not from_date:
        from_date = (today - timedelta(days=90)).isoformat()

    params = (from_date, to_date)

    total_properties = db.execute(
        "SELECT COUNT(*) FROM properties WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    available = db.execute(
        "SELECT COUNT(*) FROM properties WHERE status = 'available' AND date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    closed_deals = db.execute(
        "SELECT COUNT(*) FROM deals WHERE stage = 'closed' AND date(updated_at) >= date(?) AND date(updated_at) <= date(?)",
        params
    ).fetchone()[0]
    total_leads = db.execute(
        "SELECT COUNT(*) FROM clients WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    try:
        total_leads += db.execute(
            "SELECT COUNT(*) FROM leads WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)",
            params
        ).fetchone()[0]
    except Exception:
        pass

    sold = db.execute(
        "SELECT COUNT(*) FROM properties WHERE status = 'sold' AND date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    rented = db.execute(
        "SELECT COUNT(*) FROM properties WHERE status = 'rented' AND date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    under_offer = db.execute(
        "SELECT COUNT(*) FROM properties WHERE status = 'under_offer' AND date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    total_appointments = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    total_agents = db.execute("SELECT COUNT(*) FROM users WHERE role = 'agent'").fetchone()[0]
    open_deals = db.execute(
        "SELECT COUNT(*) FROM deals WHERE stage NOT IN ('closed', 'lost') AND date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0]
    pipeline_value = db.execute(
        "SELECT COALESCE(SUM(value), 0) FROM deals WHERE stage NOT IN ('closed', 'lost') AND date(created_at) >= date(?) AND date(created_at) <= date(?)",
        params
    ).fetchone()[0] or 0

    stats = {
        'total_properties': total_properties,
        'available': available,
        'closed_deals': closed_deals,
        'total_leads': total_leads,
        'sold': sold,
        'rented': rented,
        'under_offer': under_offer,
        'total_appointments': total_appointments,
        'total_agents': total_agents,
        'open_deals': open_deals,
        'pipeline_value': pipeline_value,
    }

    by_type = db.execute(
        "SELECT property_type, COUNT(*) as cnt FROM properties WHERE date(created_at) >= date(?) AND date(created_at) <= date(?) GROUP BY property_type ORDER BY cnt DESC",
        params
    ).fetchall()
    by_city = db.execute(
        "SELECT city, COUNT(*) as cnt FROM properties WHERE city IS NOT NULL AND city != '' AND date(created_at) >= date(?) AND date(created_at) <= date(?) GROUP BY city ORDER BY cnt DESC LIMIT 8",
        params
    ).fetchall()

    # Deal pipeline by stage
    stage_order = ['offer', 'negotiation', 'under_contract', 'closed', 'lost']
    by_stage_raw = db.execute(
        "SELECT stage, COUNT(*) as cnt, COALESCE(SUM(value), 0) as total_value FROM deals WHERE date(created_at) >= date(?) AND date(created_at) <= date(?) GROUP BY stage",
        params
    ).fetchall()
    stage_map = {}
    for r in by_stage_raw:
        stage_map[r['stage']] = {
            'stage': r['stage'] or 'unknown',
            'cnt': int(r['cnt'] or 0),
            'total_value': float(r['total_value'] or 0),
        }
    by_stage = []
    for st in stage_order:
        if st in stage_map:
            by_stage.append(stage_map[st])
        else:
            by_stage.append({'stage': st, 'cnt': 0, 'total_value': 0.0})
    for st, row in stage_map.items():
        if st not in stage_order:
            by_stage.append(row)

    monthly = db.execute(
        "SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as cnt FROM properties WHERE date(created_at) >= date(?) AND date(created_at) <= date(?) GROUP BY month ORDER BY month",
        params
    ).fetchall()
    monthly_deals = db.execute(
        "SELECT strftime('%Y-%m', updated_at) as month, COUNT(*) as cnt FROM deals WHERE stage = 'closed' AND date(updated_at) >= date(?) AND date(updated_at) <= date(?) GROUP BY month ORDER BY month",
        params
    ).fetchall()

    month_set = sorted({
        r['month'] for r in list(monthly) + list(monthly_deals)
        if r['month']
    })
    props_map = {r['month']: int(r['cnt'] or 0) for r in monthly if r['month']}
    deals_map = {r['month']: int(r['cnt'] or 0) for r in monthly_deals if r['month']}
    month_labels = list(month_set)
    month_props = [props_map.get(m, 0) for m in month_labels]
    month_deals = [deals_map.get(m, 0) for m in month_labels]

    type_labels = [(r['property_type'] or 'Other') for r in by_type]
    type_counts = [int(r['cnt'] or 0) for r in by_type]
    city_labels = [(r['city'] or '—') for r in by_city]
    city_counts = [int(r['cnt'] or 0) for r in by_city]
    stage_labels = [(r['stage'] or 'unknown').replace('_', ' ').title() for r in by_stage]
    stage_counts = [int(r['cnt'] or 0) for r in by_stage]
    stage_values = [float(r['total_value'] or 0) for r in by_stage]

    if export == 'csv':
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator='\r\n')

        # Title block
        w.writerow(['Estacle Real Estate CRM'])
        w.writerow(['Analytics Report'])
        w.writerow(['Report period', str(from_date), 'to', str(to_date)])
        w.writerow([])

        # Section A
        w.writerow(['SECTION A  KEY METRICS'])
        w.writerow(['Metric', 'Value', 'Description'])
        metrics = [
            ('Total Properties', total_properties, 'All properties in period'),
            ('Available Listings', available, 'Status = available'),
            ('Closed Deals', closed_deals, 'Deals closed in period'),
            ('Total Leads', total_leads, 'Clients and leads created'),
            ('Open Deals', open_deals, 'Active pipeline deals'),
            ('Pipeline Value (INR)', round(float(pipeline_value or 0), 2), 'Sum of open deal values'),
            ('Sold Properties', sold, 'Status = sold'),
            ('Rented Properties', rented, 'Status = rented'),
            ('Under Offer', under_offer, 'Status = under offer'),
            ('Appointments', total_appointments, 'Appointments created'),
            ('Agents', total_agents, 'Agent user accounts'),
        ]
        for m in metrics:
            w.writerow(list(m))
        w.writerow([])

        # Section B
        w.writerow(['SECTION B  DEAL PIPELINE'])
        w.writerow(['Stage', 'Deals', 'Value (INR)'])
        for r in by_stage:
            w.writerow([
                str(r.get('stage') or '').replace('_', ' ').title(),
                int(r.get('cnt') or 0),
                round(float(r.get('total_value') or 0), 2),
            ])
        w.writerow([])

        # Section C
        w.writerow(['SECTION C  PROPERTIES BY TYPE'])
        w.writerow(['Property Type', 'Count'])
        if type_labels:
            for lab, cnt in zip(type_labels, type_counts):
                w.writerow([lab, cnt])
        else:
            w.writerow(['No data', 0])
        w.writerow([])

        # Section D
        w.writerow(['SECTION D  TOP CITIES'])
        w.writerow(['City', 'Properties'])
        if city_labels:
            for lab, cnt in zip(city_labels, city_counts):
                w.writerow([lab, cnt])
        else:
            w.writerow(['No data', 0])
        w.writerow([])

        # Section E
        w.writerow(['SECTION E  MONTHLY PERFORMANCE'])
        w.writerow(['Month', 'Properties Added', 'Deals Closed'])
        if month_labels:
            for m, p, d in zip(month_labels, month_props, month_deals):
                w.writerow([m, p, d])
        else:
            w.writerow(['No data', 0, 0])
        w.writerow([])
        w.writerow(['End of report'])
        w.writerow(['Estacle CRM', 'Confidential  Internal use only'])

        content = '\ufeff' + buf.getvalue()
        resp = make_response(content.encode('utf-8'))
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = 'attachment; filename="estacle_reports.csv"'
        return resp


    max_type_count = max(type_counts) if type_counts else 1
    max_month_val = max(list(month_props) + list(month_deals)) if (month_props or month_deals) else 1
    if max_month_val < 1:
        max_month_val = 1

    if export == 'pdf':
        return render_template(
            'admin_analytics_report.html',
            stats=stats,
            by_type=by_type,
            by_city=by_city,
            by_stage=by_stage,
            from_date=from_date,
            to_date=to_date,
            type_labels=type_labels,
            type_counts=type_counts,
            stage_labels=stage_labels,
            stage_counts=stage_counts,
            stage_values=stage_values,
            month_labels=month_labels,
            month_props=month_props,
            month_deals=month_deals,
            max_type_count=max_type_count,
            max_month_val=max_month_val,
            format_money=format_money,
        )


    return render_template(
        'admin_analytics.html',
        stats=stats,
        by_type=by_type,
        by_city=by_city,
        by_stage=by_stage,
        from_date=from_date,
        to_date=to_date,
        type_labels=type_labels,
        type_counts=type_counts,
        city_labels=city_labels,
        city_counts=city_counts,
        stage_labels=stage_labels,
        stage_counts=stage_counts,
        stage_values=stage_values,
        month_labels=month_labels,
        month_props=month_props,
        month_deals=month_deals,
        format_money=format_money,
    )


