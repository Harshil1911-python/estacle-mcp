from flask import Flask, request
import os

from werkzeug.middleware.proxy_fix import ProxyFix

from core.db import get_db, close_db, init_db, migrate_db
from routes.auth import auth as auth_bp
from routes.admin import admin as admin_bp
from routes.agent import agent as agent_bp
from routes.client import client as client_bp
from routes.oauth import oauth_bp


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get('SECRET_KEY', 'estacle_secret_key_change_in_production_2026')
    app.config['DATABASE'] = os.path.join(app.instance_path, 'estacle.db')
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads', 'properties')
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
    app.config['PREFERRED_URL_SCHEME'] = os.environ.get('PREFERRED_URL_SCHEME', 'https')
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 60 * 60 * 24 * 30

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'static', 'uploads', 'avatars'), exist_ok=True)

    hops = int(os.environ.get('TRUSTED_PROXY_HOPS', '1'))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops)

    app.teardown_appcontext(close_db)

    from core.helpers import highlight_match
    app.jinja_env.filters['highlight'] = highlight_match

    @app.context_processor
    def inject_theme():
        try:
            from core.helpers import get_setting
            mode = get_setting('theme_mode', 'default') or 'default'
        except Exception:
            mode = 'default'
        return {'theme_mode': mode}

    @app.before_request
    def touch_last_seen():
        from flask import session
        uid = session.get('user_id')
        if not uid:
            return
        ep = request.endpoint or ''
        if ep.startswith('static') or ep in ('service_worker', 'web_manifest', 'health'):
            return
        from time import time
        now = int(time())
        last = session.get('_last_seen_ts', 0)
        if now - last < 300:
            return
        try:
            db = get_db()
            db.execute(
                "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?",
                (uid,)
            )
            db.commit()
            session['_last_seen_ts'] = now
        except Exception:
            pass

    @app.after_request
    def add_cache_and_security_headers(resp):
        path = request.path or ''
        if path.startswith('/static/'):
            if '/uploads/' in path:
                resp.headers['Cache-Control'] = 'public, max-age=86400'
            else:
                resp.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
            resp.headers['X-Content-Type-Options'] = 'nosniff'
            return resp
        if path in ('/sw.js', '/manifest.json'):
            resp.headers['Cache-Control'] = 'no-cache'
            return resp
        if resp.mimetype and 'html' in resp.mimetype:
            resp.headers['Cache-Control'] = 'private, no-cache'
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        return resp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(client_bp)
    app.register_blueprint(oauth_bp)

    with app.app_context():
        init_db()
        migrate_db()
        try:
            from core.oauth_store import ensure_oauth_tables
            ensure_oauth_tables()
        except Exception:
            pass

    @app.route('/sw.js')
    def service_worker():
        from flask import send_from_directory, make_response
        resp = make_response(send_from_directory(os.path.join(app.root_path, 'static'), 'sw.js'))
        resp.headers['Content-Type'] = 'application/javascript; charset=utf-8'
        resp.headers['Service-Worker-Allowed'] = '/'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    @app.route('/manifest.json')
    def web_manifest():
        from flask import send_from_directory
        return send_from_directory(os.path.join(app.root_path, 'static'), 'manifest.json')

    @app.route('/health')
    def health():
        return {'ok': True}, 200

    return app


app = create_app()

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    port = int(os.environ.get('PORT', os.environ.get('FLASK_RUN_PORT', '5001')))
    app.run(debug=debug, host='0.0.0.0', port=port)
