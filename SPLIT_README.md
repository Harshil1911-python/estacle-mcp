# Estacle – Admin Module Split

## What was done

The original large `routes/admin.py` (~2860 lines) has been split into **feature modules**.

### Backend (Python) – `routes/admin/`

| File              | Responsibility                          |
|-------------------|-----------------------------------------|
| `__init__.py`     | Creates the `admin` Blueprint & imports all modules |
| `common.py`       | Shared constants (import formats)       |
| `dashboard.py`    | Dashboard + presence/offline            |
| `users.py`        | Users CRUD, import, export, bulk, toggle|
| `properties.py`   | Properties CRUD, import, bulk, display, compare, export |
| `clients.py`      | Clients CRUD, bulk, export              |
| `appointments.py` | Appointments                            |
| `analytics.py`    | Analytics dashboard                     |
| `reports.py`      | Reports                                 |
| `settings.py`     | Settings (currency etc.)                |
| `tasks.py`        | Tasks                                   |
| `deals.py`        | Deals pipeline                          |
| `activity.py`     | Activity log                            |
| `documents.py`    | Autuments                               |
| `search.py`       | Global search                           |
| `leads.py`        | Leads                                   |
| `commission.py`   | Commission                              |
| `compare.py`      | Property compare                        |
| `inquiries.py`    | Property inquiries                      |

### Frontend (JS) – `static/js/admin/`

Modular client-side helpers prepared for **future offline support**:

- `index.js`          – entry point, auto-inits modules
- `dashboard.js`
- `users.js`
- `properties.js`
- `clients.js`
- `deals.js`
- `tasks.js`
- `leads.js`
- `inquiries.js`
- `search.js`

These JS modules currently provide AJAX helpers and structure.  
True offline mode would later use IndexedDB + service worker (same pattern as your OrbitBills projects).

## How to run

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:5001

Demo logins remain the same.

## Note about "offline"

Splitting Python backend into `.js` cannot make the server-side Flask app run offline by itself.  
The Python modules keep the app working exactly as before (server required).  
The new JS modules are the foundation if you later want a local-first / PWA version.
