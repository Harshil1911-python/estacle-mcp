"""Admin search routes."""

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


@admin.route('/admin/search')
@login_required
@role_required('admin')
def admin_search():
    q = request.args.get('q', '').strip()
    stype = request.args.get('type', '').strip().lower()
    valid_types = {
        'users', 'properties', 'clients', 'deals', 'leads',
        'inquiries', 'appointments', 'tasks', 'documents', 'activity',
    }
    if stype not in valid_types:
        stype = ''

    db = get_db()
    migrate_db()
    results = {
        'users': [], 'properties': [], 'clients': [], 'deals': [],
        'leads': [], 'inquiries': [], 'appointments': [], 'tasks': [],
        'documents': [], 'activity': [],
    }
    counts = {
        'users': db.execute('SELECT COUNT(*) FROM users').fetchone()[0],
        'properties': db.execute('SELECT COUNT(*) FROM properties').fetchone()[0],
        'clients': db.execute('SELECT COUNT(*) FROM clients').fetchone()[0],
        'deals': db.execute('SELECT COUNT(*) FROM deals').fetchone()[0],
        'leads': db.execute(
            "SELECT COUNT(*) FROM clients WHERE lower(IFNULL(status,'')) = 'lead'"
        ).fetchone()[0],
        'inquiries': db.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0],
        'appointments': db.execute('SELECT COUNT(*) FROM appointments').fetchone()[0],
        'tasks': db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],
        'documents': db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
        'activity': db.execute('SELECT COUNT(*) FROM activity_log').fetchone()[0],
    }

    like = f'%{q}%' if q else None
    num = None
    if q:
        q_clean = q.replace(',', '').replace('₹', '').replace('$', '').replace('AED', '').strip()
        try:
            num = float(q_clean)
        except ValueError:
            num = None

    def want(key):
        return (not stype) or stype == key

    # Load data when searching or when a category type is selected
    if q or stype:
        if want('users'):
            if q:
                results['users'] = db.execute(
                    "SELECT id, full_name, email, role, phone FROM users "
                    "WHERE full_name LIKE ? OR email LIKE ? OR phone LIKE ? LIMIT 50",
                    (like, like, like)
                ).fetchall()
            else:
                results['users'] = db.execute(
                    "SELECT id, full_name, email, role, phone FROM users ORDER BY created_at DESC LIMIT 50"
                ).fetchall()

        if want('properties'):
            if q:
                prop_sql = (
                    "SELECT id, title, city, price, status FROM properties "
                    "WHERE title LIKE ? OR city LIKE ? OR address LIKE ? OR seller_name LIKE ?"
                )
                prop_params = [like, like, like, like]
                if num is not None:
                    prop_sql += (
                        " OR price = ? OR CAST(price AS TEXT) LIKE ? OR bedrooms = ? "
                        "OR bathrooms = ? OR area_sqft = ? OR monthly_rent = ?"
                    )
                    prop_params.extend([num, like, int(num) if num == int(num) else -1, num, num, num])
                prop_sql += " LIMIT 50"
                results['properties'] = db.execute(prop_sql, prop_params).fetchall()
            else:
                results['properties'] = db.execute(
                    "SELECT id, title, city, price, status FROM properties ORDER BY created_at DESC LIMIT 50"
                ).fetchall()

        if want('clients'):
            if q:
                results['clients'] = db.execute(
                    "SELECT id, full_name, email, phone, status FROM clients "
                    "WHERE full_name LIKE ? OR email LIKE ? OR phone LIKE ? LIMIT 50",
                    (like, like, like)
                ).fetchall()
            else:
                results['clients'] = db.execute(
                    "SELECT id, full_name, email, phone, status FROM clients ORDER BY created_at DESC LIMIT 50"
                ).fetchall()

        if want('deals'):
            if q:
                deal_sql = "SELECT id, title, stage, value FROM deals WHERE title LIKE ? OR notes LIKE ?"
                deal_params = [like, like]
                if num is not None:
                    deal_sql += " OR value = ? OR CAST(value AS TEXT) LIKE ? OR commission_rate = ?"
                    deal_params.extend([num, like, num])
                deal_sql += " LIMIT 50"
                results['deals'] = db.execute(deal_sql, deal_params).fetchall()
            else:
                results['deals'] = db.execute(
                    "SELECT id, title, stage, value FROM deals ORDER BY created_at DESC LIMIT 50"
                ).fetchall()

        if want('leads'):
            # Global Search "Leads" = clients with status exactly "lead"
            if q:
                client_lead_sql = (
                    "SELECT id, full_name, email, phone, status, budget, "
                    "preferred_city as interest, notes "
                    "FROM clients WHERE lower(IFNULL(status,'')) = 'lead' "
                    "AND (full_name LIKE ? OR email LIKE ? OR phone LIKE ? "
                    "OR IFNULL(notes,'') LIKE ? OR IFNULL(preferred_city,'') LIKE ?)"
                )
                client_params = [like, like, like, like, like]
                if num is not None:
                    client_lead_sql += " AND (budget = ? OR CAST(budget AS TEXT) LIKE ?)"
                    client_params.extend([num, like])
                client_lead_sql += " ORDER BY id DESC LIMIT 50"
                client_leads = [dict(r) for r in db.execute(client_lead_sql, client_params).fetchall()]
                for r in client_leads:
                    r['source'] = r.get('source') or 'lead'
                results['leads'] = client_leads
            else:
                client_leads = [dict(r) for r in db.execute(
                    "SELECT id, full_name, email, phone, status, budget, "
                    "preferred_city as interest, notes "
                    "FROM clients WHERE lower(IFNULL(status,'')) = 'lead' "
                    "ORDER BY id DESC LIMIT 50"
                ).fetchall()]
                for r in client_leads:
                    r['source'] = r.get('source') or 'lead'
                results['leads'] = client_leads

        if want('inquiries'):
            if q:
                results['inquiries'] = db.execute(
                    "SELECT i.id, i.full_name, i.email, i.phone, i.message, i.status, i.property_id, "
                    "p.title AS property_title FROM inquiries i "
                    "LEFT JOIN properties p ON i.property_id = p.id "
                    "WHERE i.full_name LIKE ? OR i.email LIKE ? OR i.phone LIKE ? OR i.message LIKE ? LIMIT 50",
                    (like, like, like, like)
                ).fetchall()
            else:
                results['inquiries'] = db.execute(
                    "SELECT i.id, i.full_name, i.email, i.phone, i.message, i.status, i.property_id, "
                    "p.title AS property_title FROM inquiries i "
                    "LEFT JOIN properties p ON i.property_id = p.id ORDER BY i.created_at DESC LIMIT 50"
                ).fetchall()

        if want('appointments'):
            if q:
                results['appointments'] = db.execute(
                    "SELECT a.id, a.appointment_date, a.status, a.notes, "
                    "c.full_name AS client_name, p.title AS property_title "
                    "FROM appointments a "
                    "LEFT JOIN clients c ON a.client_id = c.id "
                    "LEFT JOIN properties p ON a.property_id = p.id "
                    "WHERE a.notes LIKE ? OR c.full_name LIKE ? OR p.title LIKE ? LIMIT 50",
                    (like, like, like)
                ).fetchall()
            else:
                results['appointments'] = db.execute(
                    "SELECT a.id, a.appointment_date, a.status, a.notes, "
                    "c.full_name AS client_name, p.title AS property_title "
                    "FROM appointments a "
                    "LEFT JOIN clients c ON a.client_id = c.id "
                    "LEFT JOIN properties p ON a.property_id = p.id "
                    "ORDER BY a.appointment_date DESC LIMIT 50"
                ).fetchall()

        if want('tasks'):
            if q:
                results['tasks'] = db.execute(
                    "SELECT id, title, description, due_date, status, priority FROM tasks "
                    "WHERE title LIKE ? OR description LIKE ? LIMIT 50",
                    (like, like)
                ).fetchall()
            else:
                results['tasks'] = db.execute(
                    "SELECT id, title, description, due_date, status, priority FROM tasks "
                    "ORDER BY created_at DESC LIMIT 50"
                ).fetchall()

        if want('documents'):
            if q:
                results['documents'] = db.execute(
                    "SELECT id, title, doc_type, related_type, expiry_date, notes FROM documents "
                    "WHERE title LIKE ? OR doc_type LIKE ? OR notes LIKE ? LIMIT 50",
                    (like, like, like)
                ).fetchall()
            else:
                results['documents'] = db.execute(
                    "SELECT id, title, doc_type, related_type, expiry_date, notes FROM documents "
                    "ORDER BY created_at DESC LIMIT 50"
                ).fetchall()

        if want('activity'):
            if q:
                results['activity'] = db.execute(
                    "SELECT a.id, a.action, a.entity_type, a.entity_id, a.details, a.created_at, "
                    "u.full_name AS user_name FROM activity_log a "
                    "LEFT JOIN users u ON a.user_id = u.id "
                    "WHERE a.action LIKE ? OR a.details LIKE ? OR a.entity_type LIKE ? "
                    "OR u.full_name LIKE ? LIMIT 50",
                    (like, like, like, like)
                ).fetchall()
            else:
                results['activity'] = db.execute(
                    "SELECT a.id, a.action, a.entity_type, a.entity_id, a.details, a.created_at, "
                    "u.full_name AS user_name FROM activity_log a "
                    "LEFT JOIN users u ON a.user_id = u.id ORDER BY a.created_at DESC LIMIT 50"
                ).fetchall()

    currency = get_setting('currency', 'INR')
    total = sum(len(v) for v in results.values())
    return render_template(
        'admin_search.html', q=q, stype=stype, results=results, total=total,
        counts=counts, currency=currency, format_money=format_money
    )


