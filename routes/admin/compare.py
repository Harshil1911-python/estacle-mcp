"""Admin compare routes."""

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


@admin.route('/admin/compare')
@login_required
@role_required('admin')
def admin_compare():
    db = get_db()
    migrate_db()
    all_props = db.execute(
        "SELECT id, title, city, status, price, property_type FROM properties ORDER BY title"
    ).fetchall()
    raw_ids = request.args.getlist('ids')
    id_list = []
    for i in raw_ids:
        if str(i).isdigit():
            v = int(i)
            if v not in id_list:
                id_list.append(v)
        if len(id_list) >= 4:
            break
    props = []
    if id_list:
        placeholders = ','.join('?' * len(id_list))
        rows = db.execute(
            "SELECT p.*, u.full_name as agent_name FROM properties p "
            "LEFT JOIN users u ON p.agent_id = u.id WHERE p.id IN ({})".format(placeholders),
            id_list,
        ).fetchall()
        # preserve selection order
        by_id = {r['id']: dict(r) for r in rows}
        for i in id_list:
            if i in by_id:
                props.append(by_id[i])
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_compare.html',
        all_props=all_props,
        props=props,
        selected_ids=id_list,
        currency=currency,
        format_money=format_money,
    )



