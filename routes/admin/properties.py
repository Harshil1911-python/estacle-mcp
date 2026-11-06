"""Admin properties routes."""

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
from routes.admin.common import PROPERTY_IMPORT_FORMAT


@admin.route('/admin/properties')
@login_required
@role_required('admin')
def admin_properties():
    db = get_db()
    migrate_db()
    # Reconcile: closed deals → property sold (keeps list in sync with dashboard)
    try:
        db.execute(
            "UPDATE properties SET status = 'sold' WHERE id IN ("
            "SELECT property_id FROM deals WHERE stage = 'closed' AND property_id IS NOT NULL"
            ") AND IFNULL(status, '') != 'sold'"
        )
        db.execute(
            "UPDATE properties SET status = 'under_offer' WHERE id IN ("
            "SELECT property_id FROM deals WHERE stage IN ('offer','negotiation','under_contract') AND property_id IS NOT NULL"
            ") AND IFNULL(status, '') = 'available'"
        )
        db.commit()
    except Exception:
        pass
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '').strip()
    listing_type = request.args.get('listing_type', '').strip()
    prop_type = request.args.get('property_type', '').strip()
    city = request.args.get('city', '').strip()
    agent_id = request.args.get('agent_id', '').strip()
    beds = request.args.get('beds', '').strip()
    pmin = request.args.get('pmin', '').strip()
    pmax = request.args.get('pmax', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    view = request.args.get('view', '').strip()

    sql = (
        'SELECT p.*, u.full_name as agent_name, '
        '(SELECT d.stage FROM deals d WHERE d.property_id = p.id '
        "AND d.stage NOT IN ('closed','lost') ORDER BY d.updated_at DESC LIMIT 1) as deal_stage, "
        '(SELECT d.id FROM deals d WHERE d.property_id = p.id '
        "AND d.stage NOT IN ('closed','lost') ORDER BY d.updated_at DESC LIMIT 1) as active_deal_id "
        'FROM properties p LEFT JOIN users u ON p.agent_id = u.id WHERE 1=1'
    )
    params = []
    if q:
        sql += ' AND (p.title LIKE ? OR p.city LIKE ? OR p.address LIKE ? OR p.description LIKE ?)'
        params.extend(['%' + q + '%'] * 4)
    if status_filter in ('available', 'under_offer', 'sold', 'rented'):
        sql += ' AND p.status = ?'
        params.append(status_filter)
    if listing_type in ('sale', 'rent'):
        sql += ' AND p.listing_type = ?'
        params.append(listing_type)
    if prop_type:
        sql += ' AND p.property_type = ?'
        params.append(prop_type)
    if city:
        sql += ' AND p.city LIKE ?'
        params.append('%' + city + '%')
    if agent_id.isdigit():
        sql += ' AND p.agent_id = ?'
        params.append(int(agent_id))
    if beds.isdigit():
        sql += ' AND p.bedrooms >= ?'
        params.append(int(beds))
    if pmin:
        try:
            sql += ' AND p.price >= ?'
            params.append(float(pmin))
        except ValueError:
            pass
    if pmax:
        try:
            sql += ' AND p.price <= ?'
            params.append(float(pmax))
        except ValueError:
            pass
    def _parse_date(val):
        """Accept YYYY-MM-DD or DD/MM/YYYY."""
        if not val:
            return None
        val = val.strip()
        if len(val) == 10 and val[4] == '-' and val[7] == '-':
            return val
        if len(val) == 10 and val[2] == '/' and val[5] == '/':
            d, m, y = val.split('/')
            return '{}-{}-{}'.format(y, m, d)
        return None

    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df:
        sql += ' AND date(p.created_at) >= date(?)'
        params.append(df)
    if dt:
        sql += ' AND date(p.created_at) <= date(?)'
        params.append(dt)
    sql += ' ORDER BY p.created_at ASC'
    properties = db.execute(sql, params).fetchall()

    # Relative "added" labels for table
    now = datetime.now().date()
    props_out = []
    for row in properties:
        d = dict(row)
        created = d.get('created_at')
        label = '—'
        if created:
            try:
                cdt = datetime.strptime(str(created)[:10], '%Y-%m-%d').date()
                days = (now - cdt).days
                if days <= 0:
                    label = 'Today'
                elif days == 1:
                    label = '1 day ago'
                else:
                    label = '{} days ago'.format(days)
            except Exception:
                label = str(created)[:10]
        d['added_label'] = label
        props_out.append(d)
    properties = props_out

    total = db.execute('SELECT COUNT(*) FROM properties').fetchone()[0]
    stats = {
        'total': total,
        'available': db.execute("SELECT COUNT(*) FROM properties WHERE status = 'available'").fetchone()[0],
        'under_offer': db.execute("SELECT COUNT(*) FROM properties WHERE status = 'under_offer'").fetchone()[0],
        'sold': db.execute("SELECT COUNT(*) FROM properties WHERE status = 'sold'").fetchone()[0],
        'rented': db.execute("SELECT COUNT(*) FROM properties WHERE status = 'rented'").fetchone()[0],
        'sale': db.execute("SELECT COUNT(*) FROM properties WHERE listing_type = 'sale'").fetchone()[0],
        'rent': db.execute("SELECT COUNT(*) FROM properties WHERE listing_type = 'rent'").fetchone()[0],
    }
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role = 'agent' AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    cities = [
        r[0] for r in db.execute(
            "SELECT DISTINCT city FROM properties WHERE city IS NOT NULL AND city != '' ORDER BY city"
        ).fetchall()
    ]

    active_filters = 0
    for v in (q, status_filter, listing_type, prop_type, city, agent_id, beds, pmin, pmax, date_from, date_to):
        if v:
            active_filters += 1

    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_properties.html',
        properties=properties,
        stats=stats,
        agents=agents,
        cities=cities,
        q=q,
        status_filter=status_filter,
        listing_type=listing_type,
        prop_type=prop_type,
        city=city,
        agent_id=agent_id,
        beds=beds,
        pmin=pmin,
        pmax=pmax,
        date_from=date_from,
        date_to=date_to,
        view=view,
        active_filters=active_filters,
        currency=currency,
        format_money=format_money,
    )



# --- Additional property routes ---
@admin.route('/admin/properties/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_property():
    db = get_db()
    migrate_db()
    agents = db.execute("SELECT id, full_name FROM users WHERE role = 'agent' AND is_active = 1 ORDER BY full_name").fetchall()
    currency = get_setting('currency', 'INR')

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'danger')
            return redirect(url_for('admin.admin_add_property'))
        try:
            price = float(request.form.get('price') or 0)
            bedrooms = int(request.form.get('bedrooms') or 0) or None
            bathrooms = float(request.form.get('bathrooms') or 0) or None
            area_sqft = float(request.form.get('area_sqft') or 0) or None
            carpet_area = float(request.form.get('carpet_area') or 0) or None
            monthly_rent = float(request.form.get('monthly_rent') or 0) or None
            security_deposit = float(request.form.get('security_deposit') or 0) or None
            maintenance = float(request.form.get('maintenance') or 0) or None
            per_sqft = float(request.form.get('per_sqft') or 0) or None
            agent_id = int(request.form.get('agent_id') or 0) or None
        except ValueError:
            flash('Invalid numeric values.', 'danger')
            return redirect(url_for('admin.admin_add_property'))

        photo_names = []
        files = request.files.getlist('photos')
        for f in files:
            if f and f.filename:
                fname = secure_filename(f.filename)
                unique = '{}_{}'.format(int(__import__('time').time()), fname)
                f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique))
                photo_names.append(unique)
        photos_str = ','.join(photo_names) if photo_names else None

        video_names = []
        vfiles = request.files.getlist('videos')
        for f in vfiles:
            if f and f.filename:
                fname = secure_filename(f.filename)
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ('.mp4', '.webm', '.mov', '.ogg', '.m4v'):
                    continue
                unique = 'vid_{}_{}'.format(int(__import__('time').time()), fname)
                f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique))
                video_names.append(unique)
        videos_str = ','.join(video_names) if video_names else None

        db.execute(
            """INSERT INTO properties (
                title, description, price, property_type, status, address, city, state, zip_code,
                bedrooms, bathrooms, area_sqft, agent_id, category, listing_type, floor, carpet_area,
                furnishing, parking, amenities, seller_name, seller_phone, monthly_rent, security_deposit,
                maintenance, per_sqft, latitude, longitude, photos, year_built, facing, videos
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                title, request.form.get('description', ''), price,
                request.form.get('property_type', 'house'), request.form.get('status', 'available'),
                request.form.get('address', ''), request.form.get('city', ''), request.form.get('state', ''),
                request.form.get('zip_code', ''), bedrooms, bathrooms, area_sqft, agent_id,
                request.form.get('category', 'residential'), request.form.get('listing_type', 'sale'),
                request.form.get('floor', ''), carpet_area, request.form.get('furnishing', ''),
                request.form.get('parking', ''), request.form.get('amenities', ''),
                request.form.get('seller_name', ''), request.form.get('seller_phone', ''),
                monthly_rent, security_deposit, maintenance, per_sqft,
                request.form.get('latitude', ''), request.form.get('longitude', ''),
                photos_str, request.form.get('year_built', ''), request.form.get('facing', ''), videos_str
            )
        )
        db.commit()
        log_activity('Added property', 'property', None, title)
        flash(f'Property "{title}" published.', 'success')
        return redirect(url_for('admin.admin_properties'))

    print(PROPERTY_IMPORT_FORMAT)
    return render_template('admin_add_property.html', agents=agents, currency=currency)


@admin.route('/admin/properties/import', methods=['POST'])
@login_required
@role_required('admin')
def admin_import_properties():
    print(PROPERTY_IMPORT_FORMAT)
    f = request.files.get('import_file')
    if not f or not f.filename:
        flash('Please choose a CSV or Excel file.', 'danger')
        return redirect(url_for('admin.admin_add_property'))

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
                return redirect(url_for('admin.admin_add_property'))
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            ws = wb.active
            data = list(ws.iter_rows(values_only=True))
            if not data:
                flash('Excel file is empty.', 'danger')
                return redirect(url_for('admin.admin_add_property'))
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
            return redirect(url_for('admin.admin_add_property'))
    except Exception as e:
        flash('Could not read file: {}'.format(str(e)), 'danger')
        return redirect(url_for('admin.admin_add_property'))

    if not rows:
        flash('No data rows found.', 'warning')
        return redirect(url_for('admin.admin_add_property'))

    db = get_db()
    migrate_db()
    created = 0
    skipped = 0
    errors = []

    for idx_row, row in enumerate(rows, start=2):
        row = {(k or '').strip().lower(): ('' if v is None else str(v).strip()) for k, v in row.items()}
        title = row.get('title', '').strip()
        if not title:
            skipped += 1
            continue
        try:
            price = float(row.get('price') or 0)
        except ValueError:
            skipped += 1
            errors.append('Row {}: invalid price'.format(idx_row))
            continue

        def num(key, cast=float):
            raw = row.get(key, '').strip()
            if not raw:
                return None
            try:
                return cast(raw)
            except ValueError:
                return None

        agent_id = None
        agent_email = row.get('agent_email', '').strip().lower()
        if agent_email:
            ag = db.execute(
                "SELECT id FROM users WHERE email = ? AND role = 'agent'", (agent_email,)
            ).fetchone()
            if ag:
                agent_id = ag['id']

        status = row.get('status', 'available').strip().lower() or 'available'
        if status not in ('available', 'under_offer', 'sold', 'rented'):
            status = 'available'
        listing_type = row.get('listing_type', 'sale').strip().lower() or 'sale'
        if listing_type not in ('sale', 'rent'):
            listing_type = 'sale'

        try:
            db.execute(
                "INSERT INTO properties ("
                "title, description, price, property_type, status, address, city, state, zip_code, "
                "bedrooms, bathrooms, area_sqft, agent_id, category, listing_type, floor, carpet_area, "
                "furnishing, parking, amenities, seller_name, seller_phone, monthly_rent, security_deposit, "
                "maintenance, per_sqft, year_built, facing"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    title,
                    row.get('description', ''),
                    price,
                    row.get('property_type', 'house') or 'house',
                    status,
                    row.get('address', ''),
                    row.get('city', ''),
                    row.get('state', ''),
                    row.get('zip_code', ''),
                    num('bedrooms', int),
                    num('bathrooms', float),
                    num('area_sqft', float),
                    agent_id,
                    row.get('category', 'residential') or 'residential',
                    listing_type,
                    row.get('floor') or None,
                    num('carpet_area', float),
                    row.get('furnishing') or None,
                    row.get('parking') or None,
                    row.get('amenities') or None,
                    row.get('seller_name') or None,
                    row.get('seller_phone') or None,
                    num('monthly_rent', float),
                    num('security_deposit', float),
                    num('maintenance', float),
                    num('per_sqft', float),
                    row.get('year_built') or None,
                    row.get('facing') or None,
                )
            )
            created += 1
        except Exception as e:
            skipped += 1
            errors.append('Row {}: {}'.format(idx_row, str(e)))

    db.commit()
    if created:
        log_activity('import_properties', 'property', None, 'Imported {} properties'.format(created))
    msg = 'Import finished: {} created, {} skipped.'.format(created, skipped)
    if errors:
        msg += ' Issues: ' + '; '.join(errors[:4])
    flash(msg, 'success' if created else 'warning')
    print('[PROPERTY IMPORT] ' + msg)
    return redirect(url_for('admin.admin_properties'))


@admin.route('/admin/properties/import/template.csv')
@login_required
@role_required('admin')
def admin_properties_import_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'title', 'description', 'price', 'property_type', 'status', 'listing_type', 'category',
        'address', 'city', 'state', 'zip_code', 'bedrooms', 'bathrooms', 'area_sqft', 'carpet_area',
        'floor', 'furnishing', 'parking', 'amenities', 'seller_name', 'seller_phone',
        'monthly_rent', 'security_deposit', 'maintenance', 'per_sqft', 'year_built', 'facing', 'agent_email'
    ])
    writer.writerow([
        '3BHK Sea View', 'Corner unit with balcony', '12500000', 'apartment', 'available', 'sale',
        'residential', 'Marine Drive', 'Mumbai', 'MH', '400001', '3', '2', '1450', '1200',
        '12', 'furnished', '2 covered', 'Pool; Gym', 'Rajesh', '+919800000000',
        '', '', '5000', '8620', '2019', 'East', ''
    ])
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=estacle_properties_import_template.csv'
    return resp


@admin.route('/admin/properties/delete/<int:prop_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_property(prop_id):
    db = get_db()
    prop = db.execute('SELECT id, title FROM properties WHERE id = ?', (prop_id,)).fetchone()
    if not prop:
        flash('Property not found.', 'danger')
        return redirect(url_for('admin.admin_properties'))
    db.execute('DELETE FROM properties WHERE id = ?', (prop_id,))
    db.commit()
    flash(f'Property "{prop["title"]}" deleted.', 'success')
    return redirect(url_for('admin.admin_properties'))



@admin.route('/admin/properties/status/<int:prop_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_update_property_status(prop_id):
    status = request.form.get('status', '')
    if status not in ('available', 'under_offer', 'sold', 'rented'):
        flash('Invalid status.', 'danger')
        return redirect(url_for('admin.admin_properties'))
    db = get_db()
    prop = db.execute('SELECT id, title FROM properties WHERE id = ?', (prop_id,)).fetchone()
    if not prop:
        flash('Property not found.', 'danger')
        return redirect(url_for('admin.admin_properties'))
    db.execute('UPDATE properties SET status = ? WHERE id = ?', (status, prop_id))
    db.commit()
    flash(f'Status of "{prop["title"]}" updated to {status}.', 'success')
    return redirect(url_for('admin.admin_properties'))


@admin.route('/admin/properties/<int:prop_id>')
@login_required
@role_required('admin')
def admin_property_detail(prop_id):
    db = get_db()
    migrate_db()
    prop = db.execute(
        "SELECT p.*, u.full_name as agent_name FROM properties p LEFT JOIN users u ON p.agent_id = u.id WHERE p.id = ?",
        (prop_id,)
    ).fetchone()
    if not prop:
        flash('Property not found.', 'danger')
        return redirect(url_for('admin.admin_properties'))
    photos = []
    if prop['photos']:
        photos = [p.strip() for p in prop['photos'].split(',') if p.strip()]
    videos = []
    try:
        if prop['videos']:
            videos = [v.strip() for v in prop['videos'].split(',') if v.strip()]
    except (KeyError, IndexError, TypeError):
        videos = []
    currency = get_setting('currency', 'INR')
    return render_template(
        'admin_property_detail.html', prop=prop, photos=photos, videos=videos,
        currency=currency, format_money=format_money
    )


@admin.route('/admin/properties/edit/<int:prop_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_edit_property(prop_id):
    db = get_db()
    migrate_db()
    prop = db.execute('SELECT * FROM properties WHERE id = ?', (prop_id,)).fetchone()
    if not prop:
        flash('Property not found.', 'danger')
        return redirect(url_for('admin.admin_properties'))
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role = 'agent' AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    currency = get_setting('currency', 'INR')

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'danger')
            return redirect(url_for('admin.admin_edit_property', prop_id=prop_id))
        try:
            price = float(request.form.get('price') or 0)
            bedrooms = int(request.form.get('bedrooms') or 0) or None
            bathrooms = float(request.form.get('bathrooms') or 0) or None
            area_sqft = float(request.form.get('area_sqft') or 0) or None
            carpet_area = float(request.form.get('carpet_area') or 0) or None
            monthly_rent = float(request.form.get('monthly_rent') or 0) or None
            security_deposit = float(request.form.get('security_deposit') or 0) or None
            maintenance = float(request.form.get('maintenance') or 0) or None
            per_sqft = float(request.form.get('per_sqft') or 0) or None
            agent_id = int(request.form.get('agent_id') or 0) or None
        except ValueError:
            flash('Invalid numeric values.', 'danger')
            return redirect(url_for('admin.admin_edit_property', prop_id=prop_id))

        photos_str = prop['photos'] if 'photos' in prop.keys() else None
        files = request.files.getlist('photos')
        photo_names = [p.strip() for p in (photos_str or '').split(',') if p.strip()]
        for f in files:
            if f and f.filename:
                fname = secure_filename(f.filename)
                unique = '{}_{}'.format(int(__import__('time').time()), fname)
                f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique))
                photo_names.append(unique)
        if photo_names:
            photos_str = ','.join(photo_names)

        videos_str = prop['videos'] if 'videos' in prop.keys() else None
        vfiles = request.files.getlist('videos')
        video_names = [v.strip() for v in (videos_str or '').split(',') if v.strip()]
        for f in vfiles:
            if f and f.filename:
                fname = secure_filename(f.filename)
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ('.mp4', '.webm', '.mov', '.ogg', '.m4v'):
                    continue
                unique = 'vid_{}_{}'.format(int(__import__('time').time()), fname)
                f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], unique))
                video_names.append(unique)
        if video_names:
            videos_str = ','.join(video_names)

        db.execute(
            "UPDATE properties SET title=?, description=?, price=?, property_type=?, status=?, "
            "address=?, city=?, state=?, zip_code=?, bedrooms=?, bathrooms=?, area_sqft=?, agent_id=?, "
            "category=?, listing_type=?, floor=?, carpet_area=?, furnishing=?, parking=?, amenities=?, "
            "seller_name=?, seller_phone=?, monthly_rent=?, security_deposit=?, maintenance=?, per_sqft=?, "
            "latitude=?, longitude=?, photos=?, year_built=?, facing=?, videos=? WHERE id=?",
            (
                title,
                request.form.get('description', ''),
                price,
                request.form.get('property_type', 'house'),
                request.form.get('status', 'available'),
                request.form.get('address', ''),
                request.form.get('city', ''),
                request.form.get('state', ''),
                request.form.get('zip_code', ''),
                bedrooms, bathrooms, area_sqft, agent_id,
                request.form.get('category', 'sale'),
                request.form.get('listing_type', 'sale'),
                request.form.get('floor', '') or None,
                carpet_area,
                request.form.get('furnishing', '') or None,
                request.form.get('parking', '') or None,
                request.form.get('amenities', '') or None,
                request.form.get('seller_name', '') or None,
                request.form.get('seller_phone', '') or None,
                monthly_rent, security_deposit, maintenance, per_sqft,
                request.form.get('latitude', '') or None,
                request.form.get('longitude', '') or None,
                photos_str,
                request.form.get('year_built', '') or None,
                request.form.get('facing', '') or None,
                videos_str,
                prop_id,
            )
        )
        db.commit()
        log_activity('Updated property', 'property', prop_id, title)
        flash('Property updated.', 'success')
        return redirect(url_for('admin.admin_property_detail', prop_id=prop_id))

    return render_template(
        'admin_edit_property.html', prop=prop, agents=agents, currency=currency, format_money=format_money
    )


@admin.route('/admin/properties/display')
@login_required
@role_required('admin')
def admin_display_properties():
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    listing_type = request.args.get('listing_type', '').strip()
    prop_type = request.args.get('property_type', '').strip()
    city = request.args.get('city', '').strip()
    status = request.args.get('status', '').strip()
    beds = request.args.get('beds', '').strip()
    pmin = request.args.get('pmin', '').strip()
    pmax = request.args.get('pmax', '').strip()
    present = request.args.get('present', '').strip()
    queens = request.args.get('queens', '').strip()
    sort = request.args.get('sort', 'status').strip()
    density = request.args.get('density', 'comfortable').strip()

    sql = (
        "SELECT p.*, u.full_name as agent_name FROM properties p "
        "LEFT JOIN users u ON p.agent_id = u.id WHERE 1=1"
    )
    params = []
    if q:
        sql += " AND (p.title LIKE ? OR p.city LIKE ? OR p.address LIKE ? OR p.description LIKE ?)"
        params.extend(['%' + q + '%'] * 4)
    if listing_type in ('sale', 'rent'):
        sql += " AND p.listing_type = ?"
        params.append(listing_type)
    if prop_type:
        sql += " AND p.property_type = ?"
        params.append(prop_type)
    if city:
        sql += " AND p.city LIKE ?"
        params.append('%' + city + '%')
    if status in ('available', 'under_offer', 'sold', 'rented'):
        sql += " AND p.status = ?"
        params.append(status)
    if beds.isdigit():
        sql += " AND p.bedrooms >= ?"
        params.append(int(beds))
    if pmin:
        try:
            sql += " AND p.price >= ?"
            params.append(float(pmin))
        except ValueError:
            pass
    if pmax:
        try:
            sql += " AND p.price <= ?"
            params.append(float(pmax))
        except ValueError:
            pass

    order_map = {
        'price_asc': 'p.price ASC',
        'price_desc': 'p.price DESC',
        'newest': 'p.created_at DESC',
        'beds': 'p.bedrooms DESC',
        'status': "CASE p.status WHEN 'available' THEN 0 WHEN 'under_offer' THEN 1 ELSE 2 END, p.created_at DESC",
    }
    sql += " ORDER BY " + order_map.get(sort, order_map['status'])
    properties = db.execute(sql, params).fetchall()
    cities = [
        r[0] for r in db.execute(
            "SELECT DISTINCT city FROM properties WHERE city IS NOT NULL AND city != '' ORDER BY city"
        ).fetchall()
    ]
    currency = get_setting('currency', 'INR')
    filters = {
        'q': q, 'listing_type': listing_type, 'property_type': prop_type,
        'city': city, 'status': status, 'beds': beds, 'pmin': pmin, 'pmax': pmax,
        'sort': sort, 'density': density,
    }
    return render_template(
        'admin_display_properties.html',
        properties=properties,
        cities=cities,
        currency=currency,
        format_money=format_money,
        present=present,
        queens=queens,
        sort=sort,
        density=density,
        filters=filters,
    )



@admin.route('/admin/properties/bulk', methods=['POST'])
@login_required
@role_required('admin')
def admin_bulk_properties():
    db = get_db()
    action = request.form.get('action', '')
    ids = request.form.getlist('ids')
    if not ids:
        flash('No properties selected.', 'warning')
        return redirect(url_for('admin.admin_properties'))
    try:
        ids = [int(i) for i in ids]
    except ValueError:
        flash('Invalid selection.', 'danger')
        return redirect(url_for('admin.admin_properties'))
    placeholders = ','.join('?' * len(ids))
    if action == 'delete':
        db.execute(f'DELETE FROM properties WHERE id IN ({placeholders})', ids)
        db.commit()
        log_activity('Bulk deleted properties', 'property', None, f'{len(ids)} items')
        flash(f'Deleted {len(ids)} properties.', 'success')
    elif action in ('available', 'under_offer', 'sold', 'rented'):
        db.execute(f"UPDATE properties SET status = ? WHERE id IN ({placeholders})", [action] + ids)
        db.commit()
        log_activity('Bulk status update', 'property', None, f'{len(ids)} → {action}')
        flash(f'Updated status of {len(ids)} properties to {action}.', 'success')
    else:
        flash('Unknown action.', 'warning')
    return redirect(url_for('admin.admin_properties'))


@admin.route('/admin/export/properties')
@login_required
@role_required('admin')
def admin_export_properties():
    import csv
    import io
    from flask import Response
    db = get_db()
    rows = db.execute('''
        SELECT p.id, p.title, p.property_type, p.listing_type, p.status, p.price, p.city, p.state,
               p.bedrooms, p.bathrooms, p.area_sqft, u.full_name as agent_name, p.created_at
        FROM properties p LEFT JOIN users u ON p.agent_id = u.id
        ORDER BY p.id
    ''').fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Title', 'Type', 'Listing', 'Status', 'Price', 'City', 'State',
                     'Bedrooms', 'Bathrooms', 'Area Sqft', 'Agent', 'Created'])
    for r in rows:
        writer.writerow([r['id'], r['title'], r['property_type'], r['listing_type'], r['status'],
                         r['price'], r['city'], r['state'], r['bedrooms'], r['bathrooms'],
                         r['area_sqft'], r['agent_name'], r['created_at']])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=estacle_properties.csv'}
    )




