"""Admin users routes."""

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
from routes.admin.common import USER_IMPORT_FORMAT




@admin.route('/admin/users/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_user():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        role = request.form.get('role', 'agent')
        phone = request.form.get('phone', '').strip()
        alt_phone = request.form.get('alt_phone', '').strip()
        job_title = request.form.get('job_title', '').strip()
        department = request.form.get('department', '').strip()
        employee_id = request.form.get('employee_id', '').strip()
        license_no = request.form.get('license_no', '').strip()
        commission_raw = request.form.get('commission_rate', '').strip()
        address = request.form.get('address', '').strip()
        city = request.form.get('city', '').strip()
        state = request.form.get('state', '').strip()
        country = request.form.get('country', '').strip()
        postal_code = request.form.get('postal_code', '').strip()
        notes = request.form.get('notes', '').strip()
        hire_date = request.form.get('hire_date', '').strip()
        timezone = request.form.get('timezone', '').strip()
        is_active = 0 if request.form.get('is_active') == '0' else 1

        commission_rate = None
        if commission_raw:
            try:
                commission_rate = float(commission_raw)
            except ValueError:
                flash('Commission rate must be a number.', 'danger')
                return redirect(url_for('admin.admin_add_user'))

        if not full_name or not email or not password or role not in ('admin', 'agent', 'customer'):
            flash('Please fill all required fields correctly.', 'danger')
            return redirect(url_for('admin.admin_add_user'))
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return redirect(url_for('admin.admin_add_user'))

        db = get_db()
        migrate_db()
        existing = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if existing:
            flash('Email already exists.', 'danger')
            return redirect(url_for('admin.admin_add_user'))

        cur = db.execute(
            "INSERT INTO users ("
            "email, password, full_name, role, phone, is_active, "
            "alt_phone, job_title, department, employee_id, license_no, "
            "commission_rate, address, city, state, country, postal_code, "
            "notes, hire_date, timezone"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                email, generate_password_hash(password), full_name, role, phone, is_active,
                alt_phone or None, job_title or None, department or None, employee_id or None,
                license_no or None, commission_rate, address or None, city or None,
                state or None, country or None, postal_code or None, notes or None,
                hire_date or None, timezone or None
            )
        )
        db.commit()
        new_id = cur.lastrowid

        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            av_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'avatars')
            os.makedirs(av_dir, exist_ok=True)
            ext = os.path.splitext(secure_filename(avatar_file.filename))[1].lower()
            if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                avatar_name = 'user_{}_{}{}'.format(new_id, int(datetime.now().timestamp()), ext)
                avatar_file.save(os.path.join(av_dir, avatar_name))
                db.execute('UPDATE users SET avatar = ? WHERE id = ?', (avatar_name, new_id))
                db.commit()

        log_activity('create_user', 'user', new_id, 'Created user {} ({})'.format(full_name, role))
        # Keep Clients list in sync for customer accounts
        if role == 'customer':
            try:
                from routes.admin.clients import sync_customers_to_clients
                sync_customers_to_clients(db)
            except Exception:
                pass
        flash('User {} created successfully.'.format(full_name), 'success')
        return redirect(url_for('admin.admin_users'))

    print(USER_IMPORT_FORMAT)
    return render_template('admin_add_user.html', import_format=USER_IMPORT_FORMAT)


@admin.route('/admin/users/import', methods=['POST'])
@login_required
@role_required('admin')
def admin_import_users():
    print(USER_IMPORT_FORMAT)
    f = request.files.get('import_file')
    if not f or not f.filename:
        flash('Please choose a CSV or Excel file.', 'danger')
        return redirect(url_for('admin.admin_add_user'))

    filename = secure_filename(f.filename).lower()
    rows = []
    try:
        if filename.endswith('.csv'):
            raw = f.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(raw))
            rows = list(reader)
        elif filename.endswith('.xlsx') or filename.endswith('.xls'):
            try:
                import openpyxl
            except ImportError:
                flash('Excel import requires openpyxl. Run: pip install openpyxl', 'danger')
                return redirect(url_for('admin.admin_add_user'))
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            ws = wb.active
            data = list(ws.iter_rows(values_only=True))
            if not data:
                flash('Excel file is empty.', 'danger')
                return redirect(url_for('admin.admin_add_user'))
            headers = [str(h).strip().lower() if h is not None else '' for h in data[0]]
            for row in data[1:]:
                if row is None or all(c is None or str(c).strip() == '' for c in row):
                    continue
                item = {}
                for i, h in enumerate(headers):
                    if i < len(row):
                        item[h] = '' if row[i] is None else str(row[i]).strip()
                rows.append(item)
        else:
            flash('Unsupported file type. Use .csv or .xlsx', 'danger')
            return redirect(url_for('admin.admin_add_user'))
    except Exception as e:
        flash('Could not read file: {}'.format(str(e)), 'danger')
        return redirect(url_for('admin.admin_add_user'))

    if not rows:
        flash('No data rows found in file.', 'warning')
        return redirect(url_for('admin.admin_add_user'))

    db = get_db()
    created = 0
    skipped = 0
    errors = []
    required_keys = ('full_name', 'email', 'role', 'password')

    for idx, row in enumerate(rows, start=2):
        # normalize keys
        row = { (k or '').strip().lower(): (v or '').strip() if isinstance(v, str) else ('' if v is None else str(v).strip()) for k, v in row.items() }
        full_name = row.get('full_name', '').strip()
        email = row.get('email', '').strip().lower()
        role = row.get('role', '').strip().lower()
        phone = row.get('phone', '').strip()
        password = row.get('password', '').strip()

        if not full_name and not email:
            continue
        if not full_name or not email or not password or role not in ('admin', 'agent', 'customer'):
            skipped += 1
            errors.append('Row {}: missing/invalid fields'.format(idx))
            continue
        if len(password) < 6:
            skipped += 1
            errors.append('Row {}: password too short'.format(idx))
            continue
        existing = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if existing:
            skipped += 1
            errors.append('Row {}: email already exists ({})'.format(idx, email))
            continue
        try:
            db.execute(
                'INSERT INTO users (email, password, full_name, role, phone) VALUES (?, ?, ?, ?, ?)',
                (email, generate_password_hash(password), full_name, role, phone)
            )
            created += 1
        except Exception as e:
            skipped += 1
            errors.append('Row {}: {}'.format(idx, str(e)))

    db.commit()
    if created:
        log_activity('import_users', 'user', None, 'Imported {} users'.format(created))
    msg = 'Import finished: {} created, {} skipped.'.format(created, skipped)
    if errors:
        msg += ' Issues: ' + '; '.join(errors[:5])
        if len(errors) > 5:
            msg += ' (+{} more)'.format(len(errors) - 5)
    flash(msg, 'success' if created else 'warning')
    print('[IMPORT] ' + msg)
    return redirect(url_for('admin.admin_users'))


@admin.route('/admin/users/import/template.csv')
@login_required
@role_required('admin')
def admin_users_import_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['full_name', 'email', 'role', 'phone', 'password'])
    writer.writerow(['John Agent', 'john.agent@estacle.com', 'agent', '+1-555-0101', 'agent123'])
    writer.writerow(['Sarah Customer', 'sarah@estacle.com', 'customer', '+1-555-0102', 'customer123'])
    writer.writerow(['Admin Two', 'admin2@estacle.com', 'admin', '', 'admin12345'])
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=estacle_users_import_template.csv'
    return resp


@admin.route('/admin/users/toggle/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_toggle_user(user_id):
    db = get_db()
    user = db.execute('SELECT id, is_active, role FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.admin_users'))
    if user['id'] == session['user_id']:
        flash('You cannot deactivate yourself.', 'warning')
        return redirect(url_for('admin.admin_users'))

    new_status = 0 if user['is_active'] else 1
    db.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_status, user_id))
    db.commit()
    flash('User status updated.', 'success')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin.route('/admin/users/<int:user_id>')
@login_required
@role_required('admin')
def admin_user_detail(user_id):
    db = get_db()
    migrate_db()
    online_sql = (
        "last_seen IS NOT NULL AND "
        "datetime(last_seen) >= datetime('now', '-10 minutes')"
    )
    user = db.execute(
        'SELECT *, CASE WHEN ' + online_sql + ' THEN 1 ELSE 0 END AS is_online '
        'FROM users WHERE id = ?',
        (user_id,)
    ).fetchone()
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.admin_users'))

    activity = db.execute(
        'SELECT action, entity_type, entity_id, details, created_at '
        'FROM activity_log WHERE user_id = ? ORDER BY created_at DESC LIMIT 20',
        (user_id,)
    ).fetchall()

    return render_template(
        'admin_user_detail.html',
        user=user,
        activity=activity,
    )


@admin.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_user(user_id):
    db = get_db()
    user = db.execute('SELECT id, full_name FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.admin_users'))
    if user['id'] == session.get('user_id'):
        flash('You cannot delete your own account.', 'warning')
        return redirect(url_for('admin.admin_user_detail', user_id=user_id))
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    log_activity('delete_user', 'user', user_id, 'Deleted user ' + user['full_name'])
    flash('User ' + user['full_name'] + ' deleted.', 'success')
    return redirect(url_for('admin.admin_users'))


@admin.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_edit_user(user_id):
    db = get_db()
    migrate_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.admin_users'))

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        role = request.form.get('role', user['role'])
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '').strip()
        alt_phone = request.form.get('alt_phone', '').strip()
        job_title = request.form.get('job_title', '').strip()
        department = request.form.get('department', '').strip()
        employee_id = request.form.get('employee_id', '').strip()
        license_no = request.form.get('license_no', '').strip()
        commission_raw = request.form.get('commission_rate', '').strip()
        address = request.form.get('address', '').strip()
        city = request.form.get('city', '').strip()
        state = request.form.get('state', '').strip()
        country = request.form.get('country', '').strip()
        postal_code = request.form.get('postal_code', '').strip()
        notes = request.form.get('notes', '').strip()
        hire_date = request.form.get('hire_date', '').strip()
        timezone = request.form.get('timezone', '').strip()

        commission_rate = None
        if commission_raw:
            try:
                commission_rate = float(commission_raw)
            except ValueError:
                flash('Commission rate must be a number.', 'danger')
                return redirect(url_for('admin.admin_edit_user', user_id=user_id))

        if not full_name or not email or role not in ('admin', 'agent', 'customer'):
            flash('Please fill all required fields correctly.', 'danger')
            return redirect(url_for('admin.admin_edit_user', user_id=user_id))

        existing = db.execute('SELECT id FROM users WHERE email = ? AND id != ?', (email, user_id)).fetchone()
        if existing:
            flash('Email already in use by another user.', 'danger')
            return redirect(url_for('admin.admin_edit_user', user_id=user_id))

        avatar_name = user['avatar'] if 'avatar' in user.keys() else None
        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            av_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'avatars')
            os.makedirs(av_dir, exist_ok=True)
            ext = os.path.splitext(secure_filename(avatar_file.filename))[1].lower()
            if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                avatar_name = 'user_{}_{}{}'.format(user_id, int(datetime.now().timestamp()), ext)
                avatar_file.save(os.path.join(av_dir, avatar_name))

        fields = (
            full_name, email, role, phone, avatar_name,
            alt_phone or None, job_title or None, department or None, employee_id or None,
            license_no or None, commission_rate, address or None, city or None,
            state or None, country or None, postal_code or None, notes or None,
            hire_date or None, timezone or None, user_id
        )
        if password:
            if len(password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
                return redirect(url_for('admin.admin_edit_user', user_id=user_id))
            db.execute(
                "UPDATE users SET full_name=?, email=?, role=?, phone=?, avatar=?, "
                "alt_phone=?, job_title=?, department=?, employee_id=?, license_no=?, "
                "commission_rate=?, address=?, city=?, state=?, country=?, postal_code=?, "
                "notes=?, hire_date=?, timezone=?, password=? WHERE id=?",
                (
                    full_name, email, role, phone, avatar_name,
                    alt_phone or None, job_title or None, department or None, employee_id or None,
                    license_no or None, commission_rate, address or None, city or None,
                    state or None, country or None, postal_code or None, notes or None,
                    hire_date or None, timezone or None, generate_password_hash(password), user_id
                )
            )
        else:
            db.execute(
                "UPDATE users SET full_name=?, email=?, role=?, phone=?, avatar=?, "
                "alt_phone=?, job_title=?, department=?, employee_id=?, license_no=?, "
                "commission_rate=?, address=?, city=?, state=?, country=?, postal_code=?, "
                "notes=?, hire_date=?, timezone=? WHERE id=?",
                fields
            )
        db.commit()
        if user_id == session.get('user_id'):
            session['full_name'] = full_name
            session['email'] = email
            session['role'] = role
        log_activity('update_user', 'user', user_id, 'Updated user {}'.format(full_name))
        flash('User updated successfully.', 'success')
        if request.form.get('next') == 'dashboard':
            return redirect(url_for('admin.admin_dashboard'))
        return redirect(url_for('admin.admin_user_detail', user_id=user_id))

    return render_template('admin_edit_user.html', user=user)


@admin.route('/admin/export/users')
@login_required
@role_required('admin')
def admin_export_users():
    import csv
    import io
    from flask import Response
    db = get_db()
    rows = db.execute(
        'SELECT id, full_name, email, role, phone, is_active, created_at FROM users ORDER BY id'
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Full Name', 'Email', 'Role', 'Phone', 'Active', 'Created'])
    for r in rows:
        writer.writerow([r['id'], r['full_name'], r['email'], r['role'], r['phone'], r['is_active'], r['created_at']])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=estacle_users.csv'}
    )



