"""Admin dashboard routes."""

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


@admin.route('/admin')
@login_required
@role_required('admin')
def admin_dashboard():
    db = get_db()
    migrate_db()

    # Core counts
    total_users = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    total_agents = db.execute("SELECT COUNT(*) FROM users WHERE role = 'agent'").fetchone()[0]
    total_customers = db.execute("SELECT COUNT(*) FROM users WHERE role = 'customer'").fetchone()[0]
    total_properties = db.execute('SELECT COUNT(*) FROM properties').fetchone()[0]
    available_properties = db.execute("SELECT COUNT(*) FROM properties WHERE status = 'available'").fetchone()[0]
    total_clients = db.execute('SELECT COUNT(*) FROM clients').fetchone()[0]
    total_appointments = db.execute('SELECT COUNT(*) FROM appointments').fetchone()[0]

    # Total property value
    total_property_value = db.execute('SELECT COALESCE(SUM(price), 0) FROM properties').fetchone()[0] or 0

    # Total deal value & closed deals
    total_deal_value = db.execute('SELECT COALESCE(SUM(value), 0) FROM deals').fetchone()[0] or 0
    closed_deals = db.execute("SELECT COUNT(*) FROM deals WHERE stage = 'closed'").fetchone()[0]
    closed_deal_value = db.execute("SELECT COALESCE(SUM(value), 0) FROM deals WHERE stage = 'closed'").fetchone()[0] or 0

    # Deals closed this month
    deals_closed_this_month = db.execute(
        """SELECT COUNT(*) FROM deals
           WHERE stage = 'closed'
           AND strftime('%Y-%m', updated_at) = strftime('%Y-%m', 'now')"""
    ).fetchone()[0]

    # Revenue / commission earned (closed deals)
    commission_earned = db.execute(
        """SELECT COALESCE(SUM(value * commission_rate / 100.0), 0)
           FROM deals WHERE stage = 'closed'"""
    ).fetchone()[0] or 0

    # Lead conversion rate
    total_leads_or_clients = db.execute('SELECT COUNT(*) FROM clients').fetchone()[0]
    closed_clients = db.execute("SELECT COUNT(*) FROM clients WHERE status = 'closed'").fetchone()[0]
    lead_conversion_rate = round((closed_clients / total_leads_or_clients * 100), 1) if total_leads_or_clients else 0.0

    # Month-over-month growth (properties added)
    props_this_month = db.execute(
        "SELECT COUNT(*) FROM properties WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
    ).fetchone()[0]
    props_last_month = db.execute(
        """SELECT COUNT(*) FROM properties
           WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', date('now', '-1 month'))"""
    ).fetchone()[0]
    if props_last_month > 0:
        mom_growth = round(((props_this_month - props_last_month) / props_last_month) * 100, 1)
    else:
        mom_growth = 100.0 if props_this_month > 0 else 0.0

    deals_this_month = db.execute(
        "SELECT COUNT(*) FROM deals WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
    ).fetchone()[0]
    deals_last_month = db.execute(
        """SELECT COUNT(*) FROM deals
           WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', date('now', '-1 month'))"""
    ).fetchone()[0]

    stats = {
        'total_users': total_users,
        'total_agents': total_agents,
        'total_customers': total_customers,
        'total_properties': total_properties,
        'available_properties': available_properties,
        'total_clients': total_clients,
        'total_appointments': total_appointments,
        'total_property_value': total_property_value,
        'total_deal_value': total_deal_value,
        'closed_deals': closed_deals,
        'closed_deal_value': closed_deal_value,
        'deals_closed_this_month': deals_closed_this_month,
        'commission_earned': commission_earned,
        'lead_conversion_rate': lead_conversion_rate,
        'mom_growth': mom_growth,
        'props_this_month': props_this_month,
        'props_last_month': props_last_month,
        'deals_this_month': deals_this_month,
        'deals_last_month': deals_last_month,
    }

    # Pending work
    pending_raw = db.execute(
        """SELECT t.id, t.title, t.due_date, t.priority, t.status,
                  u.full_name as assigned_name
           FROM tasks t
           LEFT JOIN users u ON t.assigned_to = u.id
           WHERE t.status IN ('pending', 'in_progress', 'open')
           ORDER BY
             CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
             t.due_date ASC
           LIMIT 8"""
    ).fetchall()

    pending_tasks = []
    today = datetime.now().date()
    for i, t in enumerate(pending_raw, 1):
        days_left = None
        days_label = '—'
        if t['due_date']:
            try:
                due = datetime.strptime(str(t['due_date'])[:10], '%Y-%m-%d').date()
                days_left = (due - today).days
                if days_left < 0:
                    days_label = f'{abs(days_left)}d overdue'
                elif days_left == 0:
                    days_label = 'Due today'
                elif days_left == 1:
                    days_label = '1 day left'
                else:
                    days_label = f'{days_left} days left'
            except Exception:
                days_label = str(t['due_date'])
        pending_tasks.append({
            'id': t['id'],
            'sl': i,
            'title': t['title'],
            'due_date': t['due_date'],
            'priority': t['priority'] or 'medium',
            'status': t['status'],
            'assigned_name': t['assigned_name'],
            'days_label': days_label,
            'days_left': days_left,
        })

    # Recent activity feed
    activity_raw = db.execute(
        """SELECT a.id, a.action, a.entity_type, a.entity_id, a.details, a.created_at,
                  u.full_name as user_name
           FROM activity_log a
           LEFT JOIN users u ON a.user_id = u.id
           ORDER BY a.created_at DESC
           LIMIT 12"""
    ).fetchall()

    def time_ago(ts):
        if not ts:
            return ''
        try:
            if isinstance(ts, str):
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
                    try:
                        dt = datetime.strptime(ts[:26].replace('T', ' '), fmt if '%f' not in fmt else '%Y-%m-%d %H:%M:%S.%f')
                        break
                    except ValueError:
                        dt = None
                if dt is None:
                    return str(ts)
            else:
                dt = ts
            diff = datetime.now() - dt
            secs = int(diff.total_seconds())
            if secs < 60:
                return 'just now'
            if secs < 3600:
                m = secs // 60
                return f'{m} min ago' if m == 1 else f'{m} mins ago'
            if secs < 86400:
                h = secs // 3600
                return f'{h} hour ago' if h == 1 else f'{h} hours ago'
            d = secs // 86400
            return f'{d} day ago' if d == 1 else f'{d} days ago'
        except Exception:
            return str(ts)

    activity_feed = []
    for a in activity_raw:
        action = a['action'] or ''
        details = a['details'] or ''
        if details and details not in action:
            change = f'{action} {details}'.strip()
        else:
            change = action
        activity_feed.append({
            'user_name': a['user_name'] or 'System',
            'change': change,
            'time_ago': time_ago(a['created_at']),
            'entity_type': a['entity_type'],
        })

    currency = 'INR'

    current_user = db.execute(
        'SELECT id, full_name, email, role, phone, avatar FROM users WHERE id = ?',
        (session.get('user_id'),)
    ).fetchone()

    return render_template(
        'admin.html',
        stats=stats,
        pending_tasks=pending_tasks,
        activity_feed=activity_feed,
        currency=currency,
        format_money=format_money,
        current_user=current_user,
    )


@admin.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    role_filter = request.args.get('role', '').strip()
    status_filter = request.args.get('status', '').strip()
    # Online = last_seen within 10 minutes
    online_sql = (
        "last_seen IS NOT NULL AND "
        "datetime(last_seen) >= datetime('now', '-10 minutes')"
    )
    sql = (
        'SELECT id, full_name, email, role, phone, created_at, is_active, last_seen, avatar, '
        f'CASE WHEN {online_sql} THEN 1 ELSE 0 END AS is_online '
        'FROM users WHERE 1=1'
    )
    params = []
    if q:
        sql += ' AND (full_name LIKE ? OR email LIKE ? OR phone LIKE ?)'
        params.extend([f'%{q}%', f'%{q}%', f'%{q}%'])
    if role_filter in ('admin', 'agent', 'customer'):
        sql += ' AND role = ?'
        params.append(role_filter)
    if status_filter == 'active':
        sql += ' AND is_active = 1'
    elif status_filter == 'inactive':
        sql += ' AND is_active = 0'
    elif status_filter == 'online':
        sql += f' AND ({online_sql})'
    elif status_filter == 'offline':
        sql += f' AND NOT ({online_sql})'
    sql += ' ORDER BY created_at ASC'
    users = db.execute(sql, params).fetchall()
    total = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    active_count = db.execute('SELECT COUNT(*) FROM users WHERE is_active = 1').fetchone()[0]
    online_count = db.execute(
        f'SELECT COUNT(*) FROM users WHERE {online_sql}'
    ).fetchone()[0]
    pct = round((active_count / total) * 100) if total else 0
    stats = {
        'total': total,
        'admins': db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0],
        'agents': db.execute("SELECT COUNT(*) FROM users WHERE role = 'agent'").fetchone()[0],
        'customers': db.execute("SELECT COUNT(*) FROM users WHERE role = 'customer'").fetchone()[0],
        'active': active_count,
        'online': online_count,
        'active_pct': pct,
    }
    view = request.args.get('view', '').strip()
    return render_template(
        'admin_users.html', users=users, q=q, role_filter=role_filter,
        status_filter=status_filter, stats=stats, view=view
    )


@admin.route('/admin/users/bulk', methods=['POST'])
@login_required
@role_required('admin')
def admin_bulk_users():
    db = get_db()
    action = request.form.get('action', '').strip()
    ids = request.form.getlist('user_ids')
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        flash('No users selected.', 'warning')
        return redirect(url_for('admin.admin_users'))
    self_id = session.get('user_id')
    ids = [i for i in ids if i != self_id]
    if not ids:
        flash('You cannot apply bulk actions to your own account.', 'warning')
        return redirect(url_for('admin.admin_users'))
    placeholders = ','.join('?' * len(ids))
    if action == 'delete':
        db.execute(f'DELETE FROM users WHERE id IN ({placeholders})', ids)
        db.commit()
        log_activity('bulk_delete_users', 'user', None, '{} users deleted'.format(len(ids)))
        flash('{} user(s) deleted.'.format(len(ids)), 'success')
    else:
        flash('Unknown bulk action.', 'danger')
    return redirect(url_for('admin.admin_users'))


@admin.route('/presence/offline', methods=['POST'])
def presence_offline():
    """Mark current user offline (called on tab/app close via sendBeacon)."""
    uid = session.get('user_id')
    if uid:
        try:
            db = get_db()
            db.execute("UPDATE users SET last_seen = NULL WHERE id = ?", (uid,))
            db.commit()
        except Exception:
            pass
    return ('', 204)


