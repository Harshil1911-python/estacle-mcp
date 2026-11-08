"""Estacle AI — structured CRM tools (no arbitrary SQL)."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.db import get_db, migrate_db
from core.helpers import log_activity, get_setting

# ---- OpenAI-style tool schemas for Groq ----
TOOL_SCHEMAS: List[
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and extract readable text from a public http/https webpage URL the user provided. Use when the user asks to scrape, read, or import data from a link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full http or https URL"},
                    "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)"}
                },
                "required": ["url"],
            },
        },
    },
dict] = [
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
            "description": "List deals in the pipeline. Stages: offer, negotiation, under_contract, closed, lost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "description": "offer|negotiation|under_contract|closed|lost"},
                    "q": {"type": "string", "description": "Search title, property, client"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_deal",
            "description": "Create a deal in the pipeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "value": {"type": "number"},
                    "commission_rate": {"type": "number"},
                    "stage": {"type": "string"},
                    "property_id": {"type": "integer"},
                    "client_id": {"type": "integer"},
                    "agent_id": {"type": "integer"},
                    "expected_close": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_deal",
            "description": "Update a deal by id (title, value, stage, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "deal_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "value": {"type": "number"},
                    "commission_rate": {"type": "number"},
                    "stage": {"type": "string"},
                    "property_id": {"type": "integer"},
                    "client_id": {"type": "integer"},
                    "agent_id": {"type": "integer"},
                    "expected_close": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["deal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_deal",
            "description": "Delete one deal by id. Requires confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"deal_id": {"type": "integer"}},
                "required": ["deal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach_image",
            "description": "Attach an uploaded image filename to a property (photos), user (avatar), or client. Use the filename from the user upload.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "property | user | client"},
                    "entity_id": {"type": "integer"},
                    "filename": {"type": "string"},
                },
                "required": ["entity", "entity_id", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach_video",
            "description": "Attach an uploaded video filename to a property videos field. Use the filename from the user upload. Prefer this for mp4/webm/mov.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "property (default)"},
                    "entity_id": {"type": "integer"},
                    "filename": {"type": "string"},
                },
                "required": ["entity_id", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "seed_demo_deals",
            "description": "Create multiple realistic demo deals in one step. Use this when the user asks for N demo/sample/test deals instead of calling create_deal repeatedly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "How many demo deals to create (1-25). Default 10.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bulk_delete",
            "description": "Delete multiple records of one type in a single confirmed action. entity: property|client|deal|task|user. Pass ids as a list of integers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "property|client|deal|task|user"},
                    "ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of IDs to delete",
                    },
                },
                "required": ["entity", "ids"],
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
    "delete_deal",
    "bulk_delete",
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


def _normalize_deal_stage(st: str) -> str:
    st = (st or "offer").strip().lower().replace(" ", "_")
    aliases = {
        "under contract": "under_contract",
        "under-contract": "under_contract",
        "won": "closed",
        "sold": "closed",
        "status": "offer",
    }
    st = aliases.get(st, st)
    if st not in ("offer", "negotiation", "under_contract", "closed", "lost"):
        st = "offer"
    return st


def exec_get_deals(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    limit = min(int(args.get("limit") or 30), 50)
    sql = (
        "SELECT d.id, d.title, d.value, d.stage, d.commission_rate, d.expected_close, "
        "d.client_id, d.property_id, d.agent_id, "
        "p.title as property_title, c.full_name as client_name, u.full_name as agent_name "
        "FROM deals d "
        "LEFT JOIN properties p ON d.property_id = p.id "
        "LEFT JOIN clients c ON d.client_id = c.id "
        "LEFT JOIN users u ON d.agent_id = u.id WHERE 1=1"
    )
    params: list = []
    stage = args.get("stage") or args.get("status")
    if stage:
        sql += " AND lower(IFNULL(d.stage,'')) = ?"
        params.append(_normalize_deal_stage(stage))
    q = (args.get("q") or "").strip()
    if q:
        sql += (
            " AND (d.title LIKE ? OR IFNULL(p.title,'') LIKE ? OR IFNULL(c.full_name,'') LIKE ?)"
        )
        params.extend(["%" + q + "%"] * 3)
    sql += " ORDER BY d.updated_at DESC LIMIT ?"
    params.append(limit)
    try:
        return {"ok": True, "deals": _rows(db.execute(sql, params))}
    except Exception as e:
        return {"ok": False, "error": "Could not load deals"}



def exec_fetch_url(args: dict, user_id: int) -> dict:
    """Scrape public webpage text for the assistant (user-provided URL only)."""
    import re
    import urllib.request
    import urllib.error
    from html.parser import HTMLParser
    from urllib.parse import urlparse

    url = (args.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url required"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "error": "Only public http/https URLs are allowed"}
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.startswith("192.168.") or host.startswith("10.") or host.endswith(".local"):
        return {"ok": False, "error": "Local or private network URLs are not allowed"}
    try:
        max_chars = int(args.get("max_chars") or 8000)
    except Exception:
        max_chars = 8000
    max_chars = max(500, min(max_chars, 20000))

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self._skip = 0
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript", "svg", "head"):
                self._skip += 1
        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript", "svg", "head") and self._skip:
                self._skip -= 1
        def handle_data(self, data):
            if self._skip:
                return
            text = data.strip()
            if text:
                self.parts.append(text)

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SereniaAI-Estacle/1.0 (+CRM assistant; user-requested fetch)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(2_000_000)
        if "application/json" in ctype:
            text = raw.decode("utf-8", errors="ignore")
        else:
            html = raw.decode("utf-8", errors="ignore")
            parser = _TextExtractor()
            try:
                parser.feed(html)
            except Exception:
                pass
            text = "\n".join(parser.parts)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if not text:
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
        text = text[:max_chars]
        _audit(user_id, "AI fetch_url", "url", None, url[:200])
        return {"ok": True, "url": url, "chars": len(text), "text": text}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP {}".format(e.code)}
    except Exception as e:
        return {"ok": False, "error": "Could not fetch URL"}


def exec_attach_image(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    entity = (args.get("entity") or "").strip().lower()
    try:
        eid = int(args["entity_id"])
    except Exception:
        return {"ok": False, "error": "entity_id required"}
    filename = os.path.basename((args.get("filename") or "").strip())
    if not filename or ".." in filename:
        return {"ok": False, "error": "invalid filename"}
    # Route videos through attach_video automatically
    lower = filename.lower()
    if lower.endswith((".mp4", ".webm", ".mov", ".m4v")):
        return exec_attach_video({"entity": "property", "entity_id": eid, "filename": filename}, user_id)
    if entity in ("property", "properties"):
        row = db.execute("SELECT id, photos FROM properties WHERE id = ?", (eid,)).fetchone()
        if not row:
            return {"ok": False, "error": "property not found"}
        photos = [p.strip() for p in (row["photos"] or "").split(",") if p.strip()]
        if filename not in photos:
            photos.append(filename)
        db.execute(
            "UPDATE properties SET photos = ? WHERE id = ?",
            (",".join(photos), eid),
        )
        db.commit()
        _audit(user_id, "AI attach_image", "property", eid, filename)
        return {"ok": True, "entity": "property", "entity_id": eid, "filename": filename, "photos": photos}
    if entity in ("user", "users"):
        row = db.execute("SELECT id FROM users WHERE id = ?", (eid,)).fetchone()
        if not row:
            return {"ok": False, "error": "user not found"}
        try:
            db.execute("UPDATE users SET avatar = ? WHERE id = ?", (filename, eid))
        except Exception:
            return {"ok": False, "error": "could not set user avatar"}
        db.commit()
        _audit(user_id, "AI attach_image", "user", eid, filename)
        return {"ok": True, "entity": "user", "entity_id": eid, "filename": filename}
    if entity in ("client", "clients"):
        row = db.execute("SELECT id, notes FROM clients WHERE id = ?", (eid,)).fetchone()
        if not row:
            return {"ok": False, "error": "client not found"}
        note = (row["notes"] or "")
        line = "[Image: {}]".format(filename)
        if line not in note:
            note = (note + "\n" if note else "") + line
        db.execute("UPDATE clients SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (note, eid))
        db.commit()
        _audit(user_id, "AI attach_image", "client", eid, filename)
        return {"ok": True, "entity": "client", "entity_id": eid, "filename": filename}
    return {"ok": False, "error": "entity must be property, user, or client"}


def exec_attach_video(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    try:
        eid = int(args["entity_id"])
    except Exception:
        return {"ok": False, "error": "entity_id required"}
    filename = os.path.basename((args.get("filename") or "").strip())
    if not filename or ".." in filename:
        return {"ok": False, "error": "invalid filename"}
    row = db.execute("SELECT id, videos FROM properties WHERE id = ?", (eid,)).fetchone()
    if not row:
        return {"ok": False, "error": "property not found"}
    videos = [v.strip() for v in (row["videos"] or "").split(",") if v.strip()]
    if filename not in videos:
        videos.append(filename)
    try:
        db.execute(
            "UPDATE properties SET videos = ? WHERE id = ?",
            (",".join(videos), eid),
        )
    except Exception:
        return {"ok": False, "error": "could not update property videos"}
    db.commit()
    _audit(user_id, "AI attach_video", "property", eid, filename)
    return {
        "ok": True,
        "entity": "property",
        "entity_id": eid,
        "filename": filename,
        "videos": videos,
    }


def exec_seed_demo_deals(args: dict, user_id: int) -> dict:
    """Create N sample deals across pipeline stages for demos."""
    db = get_db()
    migrate_db()
    try:
        count = int(args.get("count") or 10)
    except Exception:
        count = 10
    count = max(1, min(count, 25))

    props = _rows(db.execute("SELECT id, title FROM properties ORDER BY id DESC LIMIT 20"))
    clients = _rows(db.execute("SELECT id, full_name FROM clients ORDER BY id DESC LIMIT 20"))
    agents = _rows(db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY id LIMIT 10"
    ))

    samples = [
        ("Skyline Residences – Patel offer", "offer", 8500000, 2.5),
        ("Lakeview Villa – negotiation", "negotiation", 12500000, 2.0),
        ("Tech Park office lease", "under_contract", 180000, 5.0),
        ("Green Heights 3BHK resale", "offer", 6200000, 2.5),
        ("Marina Bay apartment – closed", "closed", 9800000, 2.0),
        ("Palm Grove plot – lost", "lost", 4500000, 2.5),
        ("City Center shop – under contract", "under_contract", 15000000, 3.0),
        ("Hillcrest bungalow offer", "offer", 22000000, 2.0),
        ("Riverside flat – negotiation", "negotiation", 5400000, 2.5),
        ("Sunrise Towers penthouse", "offer", 31000000, 1.5),
        ("Industrial warehouse lease", "negotiation", 90000, 4.0),
        ("Heritage villa – closed", "closed", 17500000, 2.0),
        ("Downtown studio – lost", "lost", 2800000, 2.5),
        ("Gardenia society 2BHK", "offer", 4100000, 2.5),
        ("Coastal retreat – under contract", "under_contract", 14000000, 2.0),
    ]

    created = []
    for i in range(count):
        title, stage, value, rate = samples[i % len(samples)]
        if i >= len(samples):
            title = f"{title} #{i + 1}"
        prop_id = props[i % len(props)]["id"] if props else None
        client_id = clients[i % len(clients)]["id"] if clients else None
        agent_id = agents[i % len(agents)]["id"] if agents else None
        cur = db.execute(
            """INSERT INTO deals
               (title, property_id, client_id, agent_id, stage, value, commission_rate, expected_close, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                title,
                prop_id,
                client_id,
                agent_id,
                stage,
                value,
                rate,
                None,
                "Demo deal seeded by Estacle AI",
            ),
        )
        created.append({
            "id": cur.lastrowid,
            "title": title,
            "stage": stage,
            "value": value,
        })
    db.commit()
    _audit(user_id, "AI seed_demo_deals", "deal", None, f"{len(created)} deals")
    return {"ok": True, "created_count": len(created), "deals": created}


def exec_create_deal(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title is required"}
    try:
        value = float(args.get("value") or 0)
    except Exception:
        value = 0.0
    try:
        rate = float(args.get("commission_rate") or 2.5)
    except Exception:
        rate = 2.5
    stage = _normalize_deal_stage(args.get("stage") or "offer")
    prop_id = args.get("property_id")
    client_id = args.get("client_id")
    agent_id = args.get("agent_id")
    try:
        prop_id = int(prop_id) if prop_id else None
        client_id = int(client_id) if client_id else None
        agent_id = int(agent_id) if agent_id else None
    except Exception:
        return {"ok": False, "error": "invalid property_id, client_id, or agent_id"}
    cur = db.execute(
        """INSERT INTO deals
           (title, property_id, client_id, agent_id, stage, value, commission_rate, expected_close, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            title,
            prop_id,
            client_id,
            agent_id,
            stage,
            value,
            rate,
            args.get("expected_close") or None,
            (args.get("notes") or "").strip() or None,
        ),
    )
    db.commit()
    did = cur.lastrowid
    _audit(user_id, "AI create_deal", "deal", did, title)
    return {"ok": True, "deal_id": did, "title": title, "stage": stage, "value": value}


def exec_update_deal(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    did = int(args["deal_id"])
    row = db.execute("SELECT id, title FROM deals WHERE id = ?", (did,)).fetchone()
    if not row:
        return {"ok": False, "error": "deal not found"}
    fields = []
    params: list = []
    mapping = {
        "title": "title",
        "value": "value",
        "commission_rate": "commission_rate",
        "property_id": "property_id",
        "client_id": "client_id",
        "agent_id": "agent_id",
        "expected_close": "expected_close",
        "notes": "notes",
    }
    for k, col in mapping.items():
        if k in args and args[k] is not None:
            fields.append(f"{col} = ?")
            params.append(args[k])
    if args.get("stage"):
        fields.append("stage = ?")
        params.append(_normalize_deal_stage(args["stage"]))
    if not fields:
        return {"ok": False, "error": "no fields to update"}
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(did)
    db.execute("UPDATE deals SET " + ", ".join(fields) + " WHERE id = ?", params)
    db.commit()
    _audit(user_id, "AI update_deal", "deal", did, row["title"])
    return {"ok": True, "deal_id": did, "updated": True}


def exec_delete_deal(args: dict, user_id: int) -> dict:
    db = get_db()
    migrate_db()
    did = int(args["deal_id"])
    row = db.execute("SELECT id, title FROM deals WHERE id = ?", (did,)).fetchone()
    if not row:
        return {"ok": False, "error": "deal not found"}
    db.execute("DELETE FROM deals WHERE id = ?", (did,))
    db.commit()
    _audit(user_id, "AI delete_deal", "deal", did, row["title"])
    return {"ok": True, "deleted_deal_id": did, "title": row["title"]}


def exec_bulk_delete(args: dict, user_id: int) -> dict:
    """Delete many records of one entity type in one confirmed call."""
    db = get_db()
    migrate_db()
    entity = (args.get("entity") or "").strip().lower()
    ids = args.get("ids") or []
    if not isinstance(ids, list):
        return {"ok": False, "error": "ids must be a list of integers"}
    try:
        ids = [int(x) for x in ids if x is not None and str(x).strip() != ""]
    except Exception:
        return {"ok": False, "error": "ids must be integers"}
    ids = list(dict.fromkeys(ids))  # dedupe, preserve order
    if not ids:
        return {"ok": False, "error": "no ids provided"}
    if len(ids) > 100:
        return {"ok": False, "error": "max 100 ids per bulk delete"}

    table_map = {
        "property": ("properties", "title"),
        "properties": ("properties", "title"),
        "client": ("clients", "full_name"),
        "clients": ("clients", "full_name"),
        "deal": ("deals", "title"),
        "deals": ("deals", "title"),
        "task": ("tasks", "title"),
        "tasks": ("tasks", "title"),
        "user": ("users", "email"),
        "users": ("users", "email"),
    }
    if entity not in table_map:
        return {"ok": False, "error": "entity must be property, client, deal, task, or user"}
    table, label_col = table_map[entity]
    if table == "users" and user_id in ids:
        return {"ok": False, "error": "cannot delete your own account in a bulk delete"}

    placeholders = ",".join("?" * len(ids))
    existing = _rows(
        db.execute(
            f"SELECT id, {label_col} AS label FROM {table} WHERE id IN ({placeholders})",
            ids,
        )
    )
    found_ids = {int(r["id"]) for r in existing}
    missing = [i for i in ids if i not in found_ids]
    deleted = []
    for r in existing:
        db.execute(f"DELETE FROM {table} WHERE id = ?", (int(r["id"]),))
        deleted.append({"id": int(r["id"]), "label": r["label"]})
    db.commit()
    _audit(
        user_id,
        "AI bulk_delete",
        entity,
        None,
        f"{len(deleted)} deleted: " + ", ".join(str(d["id"]) for d in deleted[:20]),
    )
    return {
        "ok": True,
        "entity": entity,
        "deleted_count": len(deleted),
        "deleted": deleted,
        "missing_ids": missing,
    }


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
    "create_deal": exec_create_deal,
    "fetch_url": exec_fetch_url,
    "attach_image": exec_attach_image,
    "attach_video": exec_attach_video,
    "seed_demo_deals": exec_seed_demo_deals,
    "update_deal": exec_update_deal,
    "delete_deal": exec_delete_deal,
    "bulk_delete": exec_bulk_delete,
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


SYSTEM_PROMPT = """You are Serenia AI — the official intelligent CRM operator for Estacle, the real-estate platform by TechSerenia.

OWNER & BRAND
- Product brand for this assistant is Serenia AI (by TechSerenia / Estacle).
- Estacle and TechSerenia are owned by Harshil Sandip Khandhar.
- Address yourself as Serenia AI; the CRM product remains Estacle.
- Tone: polished, concise, trustworthy SaaS CRM assistant — never casual slang.

HELPDESK
- You also act as an in-app helpdesk for Estacle.
- If the user asks how to do something in the UI, explain steps using Estacle admin navigation (Overview, Users, Properties, Clients, Leads, Deals, Tasks, Appointments, Documents, Settings, Serenia AI).
- You may both guide the user and offer to perform the action via tools when appropriate.

HOW TO USE ESTACLE (product knowledge)
- Overview → Dashboard: KPIs and recent activity. Analytics: charts and performance. Global Search: find users, properties, clients, deals, leads, tasks across the CRM.
- Users: list, add, edit agents/admins/customers; avatars; activate/deactivate.
- Properties: All Properties, Display Properties, Compare. Fields include title, price, type, status, address, city, beds/baths, area, listing type, photos, videos, agent.
- Clients: full client records and pipeline statuses (lead, contacted, qualified, negotiation, closed, lost). Leads board is driven by client status.
- Deals: All Deals, Pipeline (offer → negotiation → under_contract → closed → lost), Overall Summary. Cascade delete can remove deal + linked property without breaking live stats.
- Appointments: schedule viewings/meetings with client, property, agent, status (scheduled/completed/cancelled/no_show).
- Tasks, Documents, Inquiries, Activity log, Commission, Reports are available under their menu sections.
- Settings → General: company profile, currency, Dawn mode appearance.
- Settings → Serenia AI: Groq API key, model, Prompt library, Company knowledge (RAG) documents.
- Serenia AI chat: natural language CRM operations, media attach (image/video/PDF), prompt library, saved chats.
- Agent mode is scoped to the signed-in agent’s records; admin sees everything.
- Prefer tools to read/write data; when explaining UI paths, be specific and ordered.

DEALS
- Deals pipeline stages: offer → negotiation → under_contract → closed → lost.
- Use get_deals / create_deal / update_deal / delete_deal for normal deal work.
- If the user asks for several demo/sample/test deals (e.g. “add 10 demo deals”), call seed_demo_deals once with count — never call create_deal in a loop or narrate multiple tool calls in plain text.
- Prefer stage (not status) when filtering or updating deals.
- When listing deals, show title, stage, value, client, property in plain language.

IMAGES, VIDEOS, AND DOCUMENTS
- Users may upload images, screenshots, videos, and PDF/text brochures in chat.
- When media is already attached in the same conversation, NEVER ask the user to upload it again.
- If the user says "add her/him as a client" (or similar) with an image attached:
  create the client immediately with best-effort fields (use a sensible full_name from context or "New client from photo"),
  attach the image, and report what you created. Do not interrogate for every optional field first.
- Missing email/phone is fine — leave them empty rather than blocking.
- Use attach_image for photos/screenshots (property|user|client) and attach_video for property videos.
- When extracted brochure text is provided, parse fields and call create/update tools without extra confirmation loops.
- When the user says "fetch details from the image" and a file is attached but no OCR text is available,
  explain briefly that the current model cannot read pixels, then still proceed with the user's stated intent
  (e.g. create the client/property and attach the file). Offer optional fields only once, not repeatedly.
- Confirm what was attached or created in plain language; never show server paths.

COMPANY KNOWLEDGE
- When company knowledge context is provided in the system message, prefer those documents for policy, process, and product answers.
- Do not invent company rules that are not in the knowledge context or CRM data.

TOOL CALLING
- Emit only valid structured tool calls. Never write plans like “I will call create_deal 10 times” as the model output.
- For batch create of demos, one seed_demo_deals call is enough.

MULTI DELETE
- When the user asks to delete several items (e.g. “delete properties 3, 5 and 9” or “remove these clients”), use bulk_delete once with entity + ids — do not call delete_* many times unless only one id.
- Still rely on confirmation; never claim deletes finished until tools return ok:true after confirm.
- Summarize what will be deleted in business language (names/titles), not raw JSON.

WEB LINKS
- You CAN access public web pages via the fetch_url tool and via server-fetched page content included in the user message.
- NEVER say you cannot scrape, browse, or retrieve external websites.
- When page content is already included under "Content fetched from …", use that text immediately.
- Otherwise call fetch_url with the user URL, then summarize or create CRM records from the result.
- Never invent page content; only use fetched text.
- Refuse local/private network addresses only.

OPERATIONS
- You execute real database operations only through the provided tools.
- Never invent IDs, counts, prices, or claim success unless a tool result confirms it.
- Prefer tools for every CRM read or write.
- For delete_* and bulk_delete tools, emit the tool call; the app will require user confirmation.
- If a tool returns ok:false, explain the error clearly and suggest the next step.

PRIVACY OF INTERNALS (critical)
- Never show SQL, database queries, table schemas, tool names, function arguments, JSON payloads, stack traces, or API request details to the user.
- Never quote SELECT/INSERT/UPDATE/DELETE or “running query…”.
- Translate every tool result into short business language only.
- Confirmation prompts should describe the business action (e.g. “Delete 3 clients: A, B, C?”) without exposing internal tool payload format.

REPLY FORMAT (important)
- Lead with a one-line outcome (e.g. “Created deal #12.” / “Here is the deals pipeline.”).
- Use clean markdown: short headings (##), bullet lists, and compact tables for multi-row data.
- Table headers must be plain text; keep columns few (id, name/title, stage/status, key metric).
- Format money with the workspace currency when known (e.g. ₹ for INR).
- End with a brief optional “Next step” only when helpful.
- Do not dump raw JSON to the user.
- Never ask for or expose API keys.
- Only use the provided tools — never invent SQL or shell commands.
"""
