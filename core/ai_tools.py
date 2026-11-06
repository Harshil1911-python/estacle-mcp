"""Estacle AI — structured CRM tools (no arbitrary SQL)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.db import get_db, migrate_db
from core.helpers import log_activity, get_setting

# ---- OpenAI-style tool schemas for Groq ----
TOOL_SCHEMAS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_properties",
            "description": "List/search properties. Filter by city, status, bedrooms, listing type, text query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Search title/city/address"},
                    "city": {"type": "string"},
                    "status": {"type": "string", "description": "available, sold, rented, pending"},
                    "bedrooms": {"type": "integer"},
                    "limit": {"type": "integer", "description": "Max rows, default 20"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_property",
            "description": "Create a new property listing. Requires title and price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "price": {"type": "number"},
                    "city": {"type": "string"},
                    "address": {"type": "string"},
                    "property_type": {"type": "string"},
                    "status": {"type": "string"},
                    "bedrooms": {"type": "integer"},
                    "bathrooms": {"type": "number"},
                    "area_sqft": {"type": "number"},
                    "listing_type": {"type": "string", "description": "sale or rent"},
                    "description": {"type": "string"},
                },
                "required": ["title", "price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_property",
            "description": "Update fields on an existing property by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "price": {"type": "number"},
                    "status": {"type": "string"},
                    "city": {"type": "string"},
                    "address": {"type": "string"},
                    "bedrooms": {"type": "integer"},
                },
                "required": ["property_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_property",
            "description": "Permanently delete a property by id. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"property_id": {"type": "integer"}},
                "required": ["property_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_users",
            "description": "List users optionally filtered by role or search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "admin, agent, customer"},
                    "q": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_user",
            "description": "Create a user account. Requires email, full_name, role, password.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "full_name": {"type": "string"},
                    "role": {"type": "string"},
                    "password": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["email", "full_name", "role", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user",
            "description": "Update a user by id (role, name, phone, is_active).",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer"},
                    "full_name": {"type": "string"},
                    "role": {"type": "string"},
                    "phone": {"type": "string"},
                    "is_active": {"type": "integer", "description": "1 or 0"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_user",
            "description": "Delete a user by id. Requires confirmation. Cannot delete self.",
            "parameters": {
                "type": "object",
                "properties": {"user_id": {"type": "integer"}},
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clients",
            "description": "List/search clients/leads by city, status, agent, text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "status": {"type": "string"},
                    "city": {"type": "string"},
                    "unassigned": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_client",
            "description": "Create a client/lead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "status": {"type": "string"},
                    "city": {"type": "string"},
                    "agent_id": {"type": "integer"},
                    "notes": {"type": "string"},
                    "budget": {"type": "number"},
                },
                "required": ["full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_client",
            "description": "Update client fields by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_id": {"type": "integer"},
                    "full_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "status": {"type": "string"},
                    "agent_id": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["client_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_client",
            "description": "Delete a client by id. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"client_id": {"type": "integer"}},
                "required": ["client_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "List tasks, optionally by status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "due_date": {"type": "string"},
                    "priority": {"type": "string"},
                    "assigned_to": {"type": "integer"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Update task status or fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "status": {"type": "string"},
                    "title": {"type": "string"},
                    "priority": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Delete a task. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deals",
            "description": "List deals in the pipeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_appointments",
            "description": "List upcoming or all appointments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inquiries",
            "description": "List property inquiries.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_analytics",
            "description": "Compute real CRM analytics from the database (counts, values, conversion, top agents, cities).",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "string",
                        "description": "overview|properties|leads|agents|sales|tasks",
                    }
                },
            },
        },
    },
]

# Tools that require explicit user confirmation before execute
CONFIRM_TOOLS = {
    "delete_property",
    "delete_user",
    "delete_client",
    "delete_task",
}


def _rows(cur) -> List[dict]:
    return [dict(r) for r in cur.fetchall()]


def _audit(user_id: int, action: str, entity_type: str = None, entity_id: int = None, details: str = None):
    db = get_db()
    try:
        db.execute(
            """INSERT INTO ai_audit_log (user_id, action, entity_type, entity_id, details)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, action, entity_type, entity_id, details),
        )
        db.commit()
    except Exception:
        pass
    try:
        log_activity(action, entity_type or "ai", entity_id, details or "")
    except Exception:
        pass


def exec_get_properties(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    limit = min(int(args.get("limit") or 20), 50)
    sql = "SELECT id, title, price, city, status, bedrooms, property_type, listing_type, address FROM properties WHERE 1=1"
    params: list = []
    if args.get("q"):
        sql += " AND (title LIKE ? OR city LIKE ? OR address LIKE ? OR IFNULL(description,'') LIKE ?)"
        q = "%" + args["q"] + "%"
        params.extend([q, q, q, q])
    if args.get("city"):
        sql += " AND lower(city) LIKE ?"
        params.append("%" + args["city"].lower() + "%")
    if args.get("status"):
        sql += " AND lower(status) = ?"
        params.append(args["status"].lower())
    if args.get("bedrooms") is not None:
        sql += " AND bedrooms = ?"
        params.append(int(args["bedrooms"]))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    items = _rows(db.execute(sql, params))
    return {"ok": True, "count": len(items), "properties": items}


def exec_create_property(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title is required"}
    try:
        price = float(args.get("price") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid price"}
    status = (args.get("status") or "available").strip().lower()
    ptype = (args.get("property_type") or "Apartment").strip()
    listing = (args.get("listing_type") or "sale").strip().lower()
    cur = db.execute(
        """INSERT INTO properties
           (title, description, price, property_type, status, address, city,
            bedrooms, bathrooms, area_sqft, listing_type, agent_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            title,
            args.get("description"),
            price,
            ptype,
            status,
            args.get("address"),
            args.get("city"),
            args.get("bedrooms"),
            args.get("bathrooms"),
            args.get("area_sqft"),
            listing,
            None,
        ),
    )
    db.commit()
    pid = cur.lastrowid
    _audit(user_id, "AI create_property", "property", pid, title)
    return {"ok": True, "property_id": pid, "title": title, "price": price}


def exec_update_property(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    pid = int(args["property_id"])
    row = db.execute("SELECT id, title FROM properties WHERE id = ?", (pid,)).fetchone()
    if not row:
        return {"ok": False, "error": f"property {pid} not found"}
    fields = []
    params: list = []
    for col in ("title", "price", "status", "city", "address", "bedrooms"):
        if col in args and args[col] is not None:
            fields.append(f"{col} = ?")
            params.append(args[col])
    if not fields:
        return {"ok": False, "error": "no fields to update"}
    params.append(pid)
    db.execute(f"UPDATE properties SET {', '.join(fields)} WHERE id = ?", params)
    db.commit()
    _audit(user_id, "AI update_property", "property", pid, json.dumps(args))
    return {"ok": True, "property_id": pid, "updated": fields}


def exec_delete_property(args: dict, user_id: int) -> dict:
    db = get_db()
    pid = int(args["property_id"])
    row = db.execute("SELECT id, title FROM properties WHERE id = ?", (pid,)).fetchone()
    if not row:
        return {"ok": False, "error": f"property {pid} not found"}
    db.execute("DELETE FROM properties WHERE id = ?", (pid,))
    db.commit()
    _audit(user_id, "AI delete_property", "property", pid, row["title"])
    return {"ok": True, "deleted_property_id": pid, "title": row["title"]}


def exec_get_users(args: dict, user_id: int) -> dict:
    db = get_db()
    limit = min(int(args.get("limit") or 30), 50)
    sql = "SELECT id, email, full_name, role, phone, is_active FROM users WHERE 1=1"
    params: list = []
    if args.get("role"):
        sql += " AND role = ?"
        params.append(args["role"])
    if args.get("q"):
        sql += " AND (full_name LIKE ? OR email LIKE ?)"
        q = "%" + args["q"] + "%"
        params.extend([q, q])
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return {"ok": True, "users": _rows(db.execute(sql, params))}


def exec_create_user(args: dict, user_id: int) -> dict:
    from werkzeug.security import generate_password_hash
    db = get_db()
    email = (args.get("email") or "").strip().lower()
    name = (args.get("full_name") or "").strip()
    role = (args.get("role") or "agent").strip().lower()
    password = args.get("password") or ""
    if role not in ("admin", "agent", "customer"):
        return {"ok": False, "error": "role must be admin, agent, or customer"}
    if not email or not name or not password:
        return {"ok": False, "error": "email, full_name, password required"}
    exists = db.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
    if exists:
        return {"ok": False, "error": "email already registered"}
    cur = db.execute(
        "INSERT INTO users (email, password, full_name, role, phone, is_active) VALUES (?,?,?,?,?,1)",
        (email, generate_password_hash(password), name, role, args.get("phone")),
    )
    db.commit()
    _audit(user_id, "AI create_user", "user", cur.lastrowid, email)
    return {"ok": True, "user_id": cur.lastrowid, "email": email, "role": role}


def exec_update_user(args: dict, user_id: int) -> dict:
    db = get_db()
    uid = int(args["user_id"])
    row = db.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        return {"ok": False, "error": "user not found"}
    fields, params = [], []
    if args.get("full_name"):
        fields.append("full_name = ?"); params.append(args["full_name"])
    if args.get("role") in ("admin", "agent", "customer"):
        fields.append("role = ?"); params.append(args["role"])
    if "phone" in args:
        fields.append("phone = ?"); params.append(args.get("phone"))
    if args.get("is_active") is not None:
        fields.append("is_active = ?"); params.append(int(args["is_active"]))
    if not fields:
        return {"ok": False, "error": "no fields"}
    params.append(uid)
    db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)
    db.commit()
    _audit(user_id, "AI update_user", "user", uid, json.dumps(args))
    return {"ok": True, "user_id": uid}


def exec_delete_user(args: dict, user_id: int) -> dict:
    db = get_db()
    uid = int(args["user_id"])
    if uid == user_id:
        return {"ok": False, "error": "cannot delete your own account"}
    row = db.execute("SELECT id, email FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        return {"ok": False, "error": "user not found"}
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    _audit(user_id, "AI delete_user", "user", uid, row["email"])
    return {"ok": True, "deleted_user_id": uid}


def exec_get_clients(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    limit = min(int(args.get("limit") or 30), 50)
    sql = "SELECT id, full_name, email, phone, status, preferred_city, agent_id, budget FROM clients WHERE 1=1"
    params: list = []
    if args.get("q"):
        sql += " AND (full_name LIKE ? OR IFNULL(email,'') LIKE ? OR IFNULL(phone,'') LIKE ?)"
        q = "%" + args["q"] + "%"
        params.extend([q, q, q])
    if args.get("status"):
        sql += " AND lower(status) = ?"
        params.append(args["status"].lower())
    if args.get("city"):
        sql += " AND lower(IFNULL(preferred_city,'')) LIKE ?"
        params.append("%" + args["city"].lower() + "%")
    if args.get("unassigned"):
        sql += " AND (agent_id IS NULL OR agent_id = 0)"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return {"ok": True, "clients": _rows(db.execute(sql, params))}


def exec_create_client(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    name = (args.get("full_name") or "").strip()
    if not name:
        return {"ok": False, "error": "full_name required"}
    status = (args.get("status") or "lead").strip().lower()
    cur = db.execute(
        """INSERT INTO clients (full_name, email, phone, status, notes, agent_id, budget, preferred_city)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            args.get("email"),
            args.get("phone"),
            status,
            args.get("notes"),
            args.get("agent_id"),
            args.get("budget"),
            args.get("city") or args.get("preferred_city"),
        ),
    )
    db.commit()
    _audit(user_id, "AI create_client", "client", cur.lastrowid, name)
    return {"ok": True, "client_id": cur.lastrowid, "full_name": name}


def exec_update_client(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    cid = int(args["client_id"])
    row = db.execute("SELECT id FROM clients WHERE id = ?", (cid,)).fetchone()
    if not row:
        return {"ok": False, "error": "client not found"}
    fields, params = [], []
    mapping = {
        "full_name": "full_name",
        "email": "email",
        "phone": "phone",
        "status": "status",
        "agent_id": "agent_id",
        "notes": "notes",
    }
    for k, col in mapping.items():
        if k in args and args[k] is not None:
            fields.append(f"{col} = ?")
            params.append(args[k])
    if not fields:
        return {"ok": False, "error": "no fields"}
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(cid)
    db.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", params)
    db.commit()
    _audit(user_id, "AI update_client", "client", cid, json.dumps(args))
    return {"ok": True, "client_id": cid}


def exec_delete_client(args: dict, user_id: int) -> dict:
    db = get_db()
    cid = int(args["client_id"])
    row = db.execute("SELECT id, full_name FROM clients WHERE id = ?", (cid,)).fetchone()
    if not row:
        return {"ok": False, "error": "client not found"}
    db.execute("DELETE FROM clients WHERE id = ?", (cid,))
    db.commit()
    _audit(user_id, "AI delete_client", "client", cid, row["full_name"])
    return {"ok": True, "deleted_client_id": cid}


def exec_get_tasks(args: dict, user_id: int) -> dict:
    db = get_db()
    limit = min(int(args.get("limit") or 30), 50)
    sql = "SELECT id, title, status, priority, due_date, assigned_to FROM tasks WHERE 1=1"
    params: list = []
    if args.get("status"):
        sql += " AND lower(status) = ?"
        params.append(args["status"].lower())
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return {"ok": True, "tasks": _rows(db.execute(sql, params))}


def exec_create_task(args: dict, user_id: int) -> dict:
    db = get_db()
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title required"}
    cur = db.execute(
        """INSERT INTO tasks (title, description, due_date, status, priority, assigned_to, created_by)
           VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
        (
            title,
            args.get("description"),
            args.get("due_date"),
            args.get("priority") or "medium",
            args.get("assigned_to"),
            user_id,
        ),
    )
    db.commit()
    _audit(user_id, "AI create_task", "task", cur.lastrowid, title)
    return {"ok": True, "task_id": cur.lastrowid}


def exec_update_task(args: dict, user_id: int) -> dict:
    db = get_db()
    tid = int(args["task_id"])
    if not db.execute("SELECT id FROM tasks WHERE id = ?", (tid,)).fetchone():
        return {"ok": False, "error": "task not found"}
    fields, params = [], []
    for col in ("status", "title", "priority"):
        if col in args and args[col] is not None:
            fields.append(f"{col} = ?")
            params.append(args[col])
    if not fields:
        return {"ok": False, "error": "no fields"}
    params.append(tid)
    db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", params)
    db.commit()
    _audit(user_id, "AI update_task", "task", tid, json.dumps(args))
    return {"ok": True, "task_id": tid}


def exec_delete_task(args: dict, user_id: int) -> dict:
    db = get_db()
    tid = int(args["task_id"])
    if not db.execute("SELECT id FROM tasks WHERE id = ?", (tid,)).fetchone():
        return {"ok": False, "error": "task not found"}
    db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
    db.commit()
    _audit(user_id, "AI delete_task", "task", tid, None)
    return {"ok": True, "deleted_task_id": tid}


def exec_get_deals(args: dict, user_id: int) -> dict:
    db = get_db()
    limit = min(int(args.get("limit") or 30), 50)
    sql = "SELECT id, title, value, status, client_id, property_id FROM deals WHERE 1=1"
    params: list = []
    if args.get("status"):
        sql += " AND lower(status) = ?"
        params.append(args["status"].lower())
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    try:
        return {"ok": True, "deals": _rows(db.execute(sql, params))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def exec_get_appointments(args: dict, user_id: int) -> dict:
    db = get_db()
    limit = min(int(args.get("limit") or 30), 50)
    sql = "SELECT id, client_id, property_id, agent_id, appointment_date, status, notes FROM appointments WHERE 1=1"
    params: list = []
    if args.get("status"):
        sql += " AND lower(status) = ?"
        params.append(args["status"].lower())
    sql += " ORDER BY appointment_date DESC LIMIT ?"
    params.append(limit)
    return {"ok": True, "appointments": _rows(db.execute(sql, params))}


def exec_get_inquiries(args: dict, user_id: int) -> dict:
    db = get_db()
    limit = min(int(args.get("limit") or 30), 50)
    try:
        rows = _rows(db.execute(
            "SELECT id, property_id, user_id, message, status, created_at FROM inquiries ORDER BY id DESC LIMIT ?",
            (limit,),
        ))
        return {"ok": True, "inquiries": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def exec_get_analytics(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    focus = (args.get("focus") or "overview").lower()
    out: Dict[str, Any] = {"ok": True, "focus": focus, "currency": get_setting("currency", "INR")}

    out["properties_total"] = db.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    out["properties_available"] = db.execute(
        "SELECT COUNT(*) FROM properties WHERE lower(status) IN ('available','active')"
    ).fetchone()[0]
    out["properties_sold"] = db.execute(
        "SELECT COUNT(*) FROM properties WHERE lower(status) = 'sold'"
    ).fetchone()[0]
    out["inventory_value"] = db.execute(
        "SELECT COALESCE(SUM(price),0) FROM properties WHERE lower(IFNULL(status,'')) NOT IN ('sold','rented')"
    ).fetchone()[0]
    out["clients_total"] = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    out["leads_unqualified"] = db.execute(
        "SELECT COUNT(*) FROM clients WHERE lower(IFNULL(status,'lead')) IN ('lead','unqualified','new')"
    ).fetchone()[0]
    out["users_total"] = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    out["tasks_pending"] = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE lower(IFNULL(status,'pending')) IN ('pending','open','todo')"
    ).fetchone()[0]
    try:
        out["deals_total"] = db.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        out["deals_value"] = db.execute("SELECT COALESCE(SUM(value),0) FROM deals").fetchone()[0]
    except Exception:
        out["deals_total"] = 0
        out["deals_value"] = 0

    # top cities
    out["top_cities"] = _rows(db.execute(
        """SELECT city AS name, COUNT(*) AS cnt FROM properties
           WHERE city IS NOT NULL AND city != '' GROUP BY city ORDER BY cnt DESC LIMIT 8"""
    ))
    # agents with most assigned clients
    out["top_agents_by_clients"] = _rows(db.execute(
        """SELECT u.full_name AS name, COUNT(c.id) AS cnt
           FROM users u LEFT JOIN clients c ON c.agent_id = u.id
           WHERE u.role = 'agent' GROUP BY u.id ORDER BY cnt DESC LIMIT 8"""
    ))
    # this month properties
    out["properties_this_month"] = db.execute(
        """SELECT COUNT(*) FROM properties
           WHERE created_at >= date('now', 'start of month')"""
    ).fetchone()[0]
    closed = db.execute(
        "SELECT COUNT(*) FROM clients WHERE lower(status) IN ('closed','won')"
    ).fetchone()[0]
    total_c = max(out["clients_total"], 1)
    out["lead_conversion_rate_pct"] = round(100.0 * closed / total_c, 1)
    return out


EXECUTORS: Dict[str, Callable[[dict, int], dict]] = {
    "get_properties": exec_get_properties,
    "create_property": exec_create_property,
    "update_property": exec_update_property,
    "delete_property": exec_delete_property,
    "get_users": exec_get_users,
    "create_user": exec_create_user,
    "update_user": exec_update_user,
    "delete_user": exec_delete_user,
    "get_clients": exec_get_clients,
    "create_client": exec_create_client,
    "update_client": exec_update_client,
    "delete_client": exec_delete_client,
    "get_tasks": exec_get_tasks,
    "create_task": exec_create_task,
    "update_task": exec_update_task,
    "delete_task": exec_delete_task,
    "get_deals": exec_get_deals,
    "get_appointments": exec_get_appointments,
    "get_inquiries": exec_get_inquiries,
    "get_analytics": exec_get_analytics,
}


def run_tool(name: str, arguments: dict, user_id: int) -> dict:
    fn = EXECUTORS.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown tool {name}"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid tool arguments JSON"}
    try:
        return fn(arguments or {}, user_id)
    except Exception as e:
        return {"ok": False, "error": str(e)}


SYSTEM_PROMPT = """You are Estacle AI — the official intelligent CRM operator for Estacle, the real-estate platform by TechSerenia.

OWNER & BRAND
- Estacle and TechSerenia are owned by Harshil Sandip Khandhar.
- Address the product as Estacle; when relevant you may acknowledge the founder (Harshil Sandip Khandhar) and TechSerenia professionally.
- Tone: polished, concise, trustworthy SaaS CRM assistant — never casual slang.

HELPDESK
- You also act as an in-app helpdesk for Estacle.
- If the user asks how to do something manually in the UI (e.g. how to add a property, where clients are, how to change a status), explain the steps clearly using the Estacle admin navigation (Overview, Users, Properties, Clients, Leads, Tasks, etc.).
- You may both guide the user and offer to perform the action via tools when appropriate.

OPERATIONS
- You execute real database operations only through the provided tools.
- Never invent IDs, counts, prices, or claim success unless a tool result confirms it.
- Prefer tools for every CRM read or write.
- For delete_* tools, still emit the tool call; the app will require user confirmation.
- If a tool returns ok:false, explain the error clearly and suggest the next step.

REPLY FORMAT (important)
- Lead with a one-line outcome (e.g. “Created property #42.” / “Here is a snapshot of inventory.”).
- Use clean markdown: short headings (##), bullet lists, and compact tables for multi-row data.
- Table headers must be plain text; keep columns few (id, name/title, status, key metric).
- Format INR amounts with ₹ and Indian-style grouping when possible (e.g. ₹85,00,000 or ₹85 lakh if the user used lakh).
- End with a brief optional “Next step” only when helpful.
- Do not dump raw JSON to the user; translate tool results into business language.
- Never ask for or expose API keys.
- Only use the provided tools — never invent SQL or shell commands.
"""
