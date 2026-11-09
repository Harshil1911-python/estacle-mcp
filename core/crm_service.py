"""Shared CRM operations for MCP (and internal tools). Uses existing SQLite schema."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# Resolve DB path without Flask app context when possible
_ROOT = Path(__file__).resolve().parent.parent


def default_db_path() -> str:
    import os
    env = os.environ.get("DATABASE_PATH")
    if env:
        return env
    inst = _ROOT / "instance" / "estacle.db"
    return str(inst)


@contextmanager
def db_conn(db_path: Optional[str] = None):
    path = db_path or default_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rows(cur) -> List[dict]:
    return [dict(r) for r in cur.fetchall()]


def _valid_email(email: str) -> bool:
    if not email:
        return True
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))


def require_staff(user: dict):
    if not user or user.get("role") not in ("admin", "agent"):
        raise PermissionError("Only admin or agent accounts can use Estacle MCP.")


def agent_scope_clause(user: dict, agent_col: str = "agent_id") -> tuple:
    """Agents only see/modify their own records; admins see all."""
    if user.get("role") == "admin":
        return "", []
    return f" AND {agent_col} = ?", [user["id"]]


# ----- Properties -----

def add_property(user: dict, **kwargs) -> dict:
    require_staff(user)
    title = (kwargs.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    try:
        price = float(kwargs.get("price"))
    except (TypeError, ValueError):
        raise ValueError("price is required and must be a number")
    city = kwargs.get("city")
    address = kwargs.get("address")
    property_type = (kwargs.get("property_type") or "Apartment").strip()
    status = (kwargs.get("status") or "available").strip().lower()
    bedrooms = kwargs.get("bedrooms")
    bathrooms = kwargs.get("bathrooms")
    area_sqft = kwargs.get("area_sqft")
    listing_type = (kwargs.get("listing_type") or "sale").strip().lower()
    description = kwargs.get("description")
    agent_id = user["id"] if user["role"] == "agent" else kwargs.get("agent_id")

    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO properties
               (title, description, price, property_type, status, address, city,
                bedrooms, bathrooms, area_sqft, listing_type, agent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title, description, price, property_type, status, address, city,
                bedrooms, bathrooms, area_sqft, listing_type, agent_id,
            ),
        )
        pid = cur.lastrowid
        row = conn.execute(
            "SELECT id, title, price, city, status, bedrooms, property_type FROM properties WHERE id = ?",
            (pid,),
        ).fetchone()
    return {"ok": True, "property": dict(row)}


def search_properties(user: dict, q: str = None, city: str = None, status: str = None,
                      bedrooms: int = None, limit: int = 20) -> dict:
    require_staff(user)
    limit = min(int(limit or 20), 50)
    sql = "SELECT id, title, price, city, status, bedrooms, property_type, listing_type, address, agent_id FROM properties WHERE 1=1"
    params: list = []
    scope, sp = agent_scope_clause(user)
    sql += scope
    params.extend(sp)
    if q:
        sql += " AND (title LIKE ? OR IFNULL(city,'') LIKE ? OR IFNULL(address,'') LIKE ?)"
        qq = f"%{q}%"
        params.extend([qq, qq, qq])
    if city:
        sql += " AND lower(IFNULL(city,'')) LIKE ?"
        params.append(f"%{city.lower()}%")
    if status:
        sql += " AND lower(status) = ?"
        params.append(status.lower())
    if bedrooms is not None:
        sql += " AND bedrooms = ?"
        params.append(int(bedrooms))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        items = _rows(conn.execute(sql, params))
    return {"ok": True, "count": len(items), "properties": items}


def get_property_details(user: dict, property_id: int) -> dict:
    require_staff(user)
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (int(property_id),)).fetchone()
        if not row:
            raise ValueError(f"Property {property_id} not found")
        d = dict(row)
        if user["role"] == "agent" and d.get("agent_id") not in (None, user["id"]):
            # allow read of unassigned; block others' listings
            if d.get("agent_id") and d.get("agent_id") != user["id"]:
                raise PermissionError("You do not have access to this property.")
    return {"ok": True, "property": d}


def update_property(user: dict, property_id: int, **kwargs) -> dict:
    require_staff(user)
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (int(property_id),)).fetchone()
        if not row:
            raise ValueError(f"Property {property_id} not found")
        d = dict(row)
        if user["role"] == "agent" and d.get("agent_id") and d.get("agent_id") != user["id"]:
            raise PermissionError("You cannot update another agent's property.")
        fields, params = [], []
        for col in ("title", "price", "status", "city", "address", "bedrooms", "bathrooms",
                    "area_sqft", "description", "property_type", "listing_type"):
            if col in kwargs and kwargs[col] is not None:
                fields.append(f"{col} = ?")
                params.append(kwargs[col])
        if not fields:
            raise ValueError("No fields to update")
        params.append(int(property_id))
        conn.execute(f"UPDATE properties SET {', '.join(fields)} WHERE id = ?", params)
        updated = dict(conn.execute("SELECT id, title, price, city, status FROM properties WHERE id = ?",
                                    (int(property_id),)).fetchone())
    return {"ok": True, "property": updated}


def delete_property(user: dict, property_id: int, confirm: bool = False) -> dict:
    require_staff(user)
    if not confirm:
        return {
            "ok": False,
            "needs_confirmation": True,
            "message": f"Set confirm=true to permanently delete property {property_id}.",
        }
    if user["role"] != "admin":
        raise PermissionError("Only admins can delete properties.")
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, title FROM properties WHERE id = ?", (int(property_id),)
        ).fetchone()
        if not row:
            raise ValueError(f"Property {property_id} not found")

        # Remove deals that point at this property (and their payments)
        deal_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM deals WHERE property_id = ?", (int(property_id),)
            ).fetchall()
        ]
        for did in deal_ids:
            for table in ("deal_payments", "deal_brokerage_payments"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE deal_id = ?", (did,))
                except Exception:
                    pass
            conn.execute("DELETE FROM deals WHERE id = ?", (did,))

        for sql in (
            "DELETE FROM favorites WHERE property_id = ?",
            "DELETE FROM client_properties WHERE property_id = ?",
            "DELETE FROM inquiries WHERE property_id = ?",
        ):
            try:
                conn.execute(sql, (int(property_id),))
            except Exception:
                pass
        try:
            conn.execute(
                "UPDATE appointments SET property_id = NULL WHERE property_id = ?",
                (int(property_id),),
            )
        except Exception:
            pass
        try:
            conn.execute(
                "UPDATE tasks SET related_property_id = NULL WHERE related_property_id = ?",
                (int(property_id),),
            )
        except Exception:
            pass

        conn.execute("DELETE FROM properties WHERE id = ?", (int(property_id),))
    return {
        "ok": True,
        "deleted_property_id": int(property_id),
        "title": row["title"],
        "deleted_related_deals": len(deal_ids),
        "message": "Property and related deals removed. Live stats stay consistent.",
    }


# ----- Clients / Leads (clients table is pipeline source of truth) -----

def add_client(user: dict, **kwargs) -> dict:
    require_staff(user)
    name = (kwargs.get("full_name") or "").strip()
    if not name:
        raise ValueError("full_name is required")
    email = kwargs.get("email")
    if email and not _valid_email(email):
        raise ValueError("Invalid email address")
    status = (kwargs.get("status") or "lead").strip().lower()
    agent_id = user["id"] if user["role"] == "agent" else kwargs.get("agent_id")
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO clients (full_name, email, phone, status, notes, agent_id, budget, preferred_city)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, email, kwargs.get("phone"), status, kwargs.get("notes"),
                agent_id, kwargs.get("budget"), kwargs.get("city") or kwargs.get("preferred_city"),
            ),
        )
        cid = cur.lastrowid
        row = conn.execute(
            "SELECT id, full_name, email, phone, status, preferred_city, agent_id FROM clients WHERE id = ?",
            (cid,),
        ).fetchone()
    return {"ok": True, "client": dict(row)}


def search_clients(user: dict, q: str = None, status: str = None, city: str = None,
                   unassigned: bool = False, limit: int = 30) -> dict:
    require_staff(user)
    limit = min(int(limit or 30), 50)
    sql = "SELECT id, full_name, email, phone, status, preferred_city, agent_id, budget FROM clients WHERE 1=1"
    params: list = []
    scope, sp = agent_scope_clause(user)
    sql += scope
    params.extend(sp)
    if q:
        sql += " AND (full_name LIKE ? OR IFNULL(email,'') LIKE ? OR IFNULL(phone,'') LIKE ?)"
        qq = f"%{q}%"
        params.extend([qq, qq, qq])
    if status:
        sql += " AND lower(status) = ?"
        params.append(status.lower())
    if city:
        sql += " AND lower(IFNULL(preferred_city,'')) LIKE ?"
        params.append(f"%{city.lower()}%")
    if unassigned:
        sql += " AND (agent_id IS NULL OR agent_id = 0)"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        items = _rows(conn.execute(sql, params))
    return {"ok": True, "count": len(items), "clients": items}


def get_client_details(user: dict, client_id: int) -> dict:
    require_staff(user)
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (int(client_id),)).fetchone()
        if not row:
            raise ValueError(f"Client {client_id} not found")
        d = dict(row)
        if user["role"] == "agent" and d.get("agent_id") and d.get("agent_id") != user["id"]:
            raise PermissionError("You do not have access to this client.")
    return {"ok": True, "client": d}


def update_client(user: dict, client_id: int, **kwargs) -> dict:
    require_staff(user)
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (int(client_id),)).fetchone()
        if not row:
            raise ValueError(f"Client {client_id} not found")
        d = dict(row)
        if user["role"] == "agent" and d.get("agent_id") and d.get("agent_id") != user["id"]:
            raise PermissionError("You cannot update another agent's client.")
        fields, params = [], []
        mapping = {
            "full_name": "full_name", "email": "email", "phone": "phone",
            "status": "status", "notes": "notes", "budget": "budget",
            "preferred_city": "preferred_city", "city": "preferred_city",
            "agent_id": "agent_id",
        }
        for k, col in mapping.items():
            if k in kwargs and kwargs[k] is not None:
                if k == "email" and kwargs[k] and not _valid_email(str(kwargs[k])):
                    raise ValueError("Invalid email address")
                if user["role"] != "admin" and col == "agent_id":
                    continue
                fields.append(f"{col} = ?")
                params.append(kwargs[k])
        if not fields:
            raise ValueError("No fields to update")
        fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(client_id))
        conn.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", params)
        updated = dict(conn.execute(
            "SELECT id, full_name, email, phone, status, agent_id FROM clients WHERE id = ?",
            (int(client_id),),
        ).fetchone())
    return {"ok": True, "client": updated}


def delete_client(user: dict, client_id: int, confirm: bool = False) -> dict:
    require_staff(user)
    if not confirm:
        return {
            "ok": False,
            "needs_confirmation": True,
            "message": f"Set confirm=true to permanently delete client {client_id}.",
        }
    if user["role"] != "admin":
        raise PermissionError("Only admins can delete clients.")
    with db_conn() as conn:
        row = conn.execute("SELECT id, full_name FROM clients WHERE id = ?", (int(client_id),)).fetchone()
        if not row:
            raise ValueError(f"Client {client_id} not found")
        conn.execute("DELETE FROM clients WHERE id = ?", (int(client_id),))
    return {"ok": True, "deleted_client_id": int(client_id), "full_name": row["full_name"]}


# Leads map onto clients pipeline
def add_lead(user: dict, **kwargs) -> dict:
    kwargs.setdefault("status", "lead")
    return add_client(user, **kwargs)


def search_leads(user: dict, **kwargs) -> dict:
    # default exclude closed unless specified
    if not kwargs.get("status"):
        kwargs = dict(kwargs)
    result = search_clients(user, **kwargs)
    result["leads"] = result.pop("clients", [])
    return result


def update_lead(user: dict, lead_id: int, **kwargs) -> dict:
    r = update_client(user, lead_id, **kwargs)
    if "client" in r:
        r["lead"] = r.pop("client")
    return r


def assign_lead(user: dict, lead_id: int, agent_id: int) -> dict:
    require_staff(user)
    if user["role"] != "admin" and int(agent_id) != user["id"]:
        raise PermissionError("Agents can only assign leads to themselves.")
    return update_client(user, lead_id, agent_id=int(agent_id))


def change_lead_status(user: dict, lead_id: int, status: str) -> dict:
    if not status:
        raise ValueError("status is required")
    return update_client(user, lead_id, status=status.strip().lower())


# ----- Tasks / follow-ups -----

def create_followup(user: dict, title: str, description: str = None, due_date: str = None,
                    priority: str = "medium", assigned_to: int = None,
                    related_client_id: int = None) -> dict:
    require_staff(user)
    if not (title or "").strip():
        raise ValueError("title is required")
    assignee = assigned_to or user["id"]
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (title, description, due_date, status, priority, assigned_to,
                                  related_client_id, created_by)
               VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (title.strip(), description, due_date, priority or "medium", assignee,
             related_client_id, user["id"]),
        )
        tid = cur.lastrowid
        row = conn.execute(
            "SELECT id, title, status, priority, due_date, assigned_to FROM tasks WHERE id = ?",
            (tid,),
        ).fetchone()
    return {"ok": True, "task": dict(row)}


def get_pending_followups(user: dict, limit: int = 30) -> dict:
    require_staff(user)
    limit = min(int(limit or 30), 50)
    sql = """SELECT id, title, status, priority, due_date, assigned_to, related_client_id
             FROM tasks WHERE lower(IFNULL(status,'pending')) IN ('pending','open','todo')"""
    params: list = []
    if user["role"] == "agent":
        sql += " AND assigned_to = ?"
        params.append(user["id"])
    sql += " ORDER BY due_date IS NULL, due_date ASC, id DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        items = _rows(conn.execute(sql, params))
    return {"ok": True, "count": len(items), "tasks": items}


def update_followup(user: dict, task_id: int, **kwargs) -> dict:
    require_staff(user)
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
        if not row:
            raise ValueError(f"Task {task_id} not found")
        d = dict(row)
        if user["role"] == "agent" and d.get("assigned_to") not in (None, user["id"]):
            raise PermissionError("You cannot update another user's task.")
        fields, params = [], []
        for col in ("title", "description", "status", "priority", "due_date", "assigned_to"):
            if col in kwargs and kwargs[col] is not None:
                fields.append(f"{col} = ?")
                params.append(kwargs[col])
        if not fields:
            raise ValueError("No fields to update")
        params.append(int(task_id))
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", params)
        updated = dict(conn.execute(
            "SELECT id, title, status, priority, due_date FROM tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone())
    return {"ok": True, "task": updated}


def mark_followup_complete(user: dict, task_id: int) -> dict:
    return update_followup(user, task_id, status="completed")


# ----- Deals -----

_DEAL_STAGES = ("offer", "negotiation", "under_contract", "closed", "lost")


def _normalize_deal_stage(st: str) -> str:
    st = (st or "offer").strip().lower().replace(" ", "_")
    aliases = {
        "under contract": "under_contract",
        "under-contract": "under_contract",
        "won": "closed",
        "sold": "closed",
    }
    st = aliases.get(st, st)
    if st not in _DEAL_STAGES:
        st = "offer"
    return st


def add_deal(
    user: dict,
    title: str,
    value: float = 0,
    commission_rate: float = 2.5,
    stage: str = "offer",
    property_id: int = None,
    client_id: int = None,
    agent_id: int = None,
    expected_close: str = None,
    notes: str = None,
) -> dict:
    require_staff(user)
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    stage = _normalize_deal_stage(stage)
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO deals
               (title, property_id, client_id, agent_id, stage, value, commission_rate, expected_close, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                title,
                int(property_id) if property_id else None,
                int(client_id) if client_id else None,
                int(agent_id) if agent_id else None,
                stage,
                float(value or 0),
                float(commission_rate or 2.5),
                expected_close or None,
                notes,
            ),
        )
        did = cur.lastrowid
        row = dict(conn.execute(
            "SELECT id, title, stage, value, commission_rate, expected_close FROM deals WHERE id = ?",
            (did,),
        ).fetchone())
    return {"ok": True, "deal": row}


def search_deals(
    user: dict,
    q: str = None,
    stage: str = None,
    limit: int = 30,
) -> dict:
    require_staff(user)
    limit = min(int(limit or 30), 100)
    sql = (
        "SELECT d.id, d.title, d.stage, d.value, d.commission_rate, d.expected_close, "
        "d.property_id, d.client_id, d.agent_id, "
        "p.title as property_title, c.full_name as client_name, u.full_name as agent_name "
        "FROM deals d "
        "LEFT JOIN properties p ON d.property_id = p.id "
        "LEFT JOIN clients c ON d.client_id = c.id "
        "LEFT JOIN users u ON d.agent_id = u.id WHERE 1=1"
    )
    params: list = []
    if stage:
        sql += " AND lower(IFNULL(d.stage,'')) = ?"
        params.append(_normalize_deal_stage(stage))
    if q:
        sql += (
            " AND (d.title LIKE ? OR IFNULL(p.title,'') LIKE ? OR IFNULL(c.full_name,'') LIKE ?)"
        )
        params.extend(["%" + q + "%"] * 3)
    scope_sql, scope_params = agent_scope_clause(user, "d.agent_id")
    if scope_sql:
        sql += scope_sql  # already includes leading AND
        params.extend(scope_params)
    sql += " ORDER BY d.updated_at DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        rows = _rows(conn.execute(sql, params))
    return {"ok": True, "deals": rows, "count": len(rows)}


def get_deal_details(user: dict, deal_id: int) -> dict:
    require_staff(user)
    with db_conn() as conn:
        row = conn.execute(
            """SELECT d.*, p.title as property_title, c.full_name as client_name, u.full_name as agent_name
               FROM deals d
               LEFT JOIN properties p ON d.property_id = p.id
               LEFT JOIN clients c ON d.client_id = c.id
               LEFT JOIN users u ON d.agent_id = u.id
               WHERE d.id = ?""",
            (int(deal_id),),
        ).fetchone()
        if not row:
            raise ValueError("deal not found")
    return {"ok": True, "deal": dict(row)}


def update_deal(user: dict, deal_id: int, **kwargs) -> dict:
    require_staff(user)
    allowed = {
        "title", "value", "commission_rate", "stage", "property_id",
        "client_id", "agent_id", "expected_close", "notes",
    }
    fields = []
    params: list = []
    for k, v in kwargs.items():
        if k not in allowed or v is None:
            continue
        if k == "stage":
            v = _normalize_deal_stage(v)
        fields.append(f"{k} = ?")
        params.append(v)
    if not fields:
        raise ValueError("No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(int(deal_id))
    with db_conn() as conn:
        exists = conn.execute("SELECT id FROM deals WHERE id = ?", (int(deal_id),)).fetchone()
        if not exists:
            raise ValueError("deal not found")
        conn.execute(f"UPDATE deals SET {', '.join(fields)} WHERE id = ?", params)
        row = dict(conn.execute(
            "SELECT id, title, stage, value, commission_rate, expected_close FROM deals WHERE id = ?",
            (int(deal_id),),
        ).fetchone())
    return {"ok": True, "deal": row}


def _cascade_delete_deal_and_property(conn, deal_id: int, delete_property: bool = True):
    """
    Remove a deal and optionally its linked property.
    Cleans payments / favorites / inquiries so analytics and agent views
    only ever count remaining live rows (no orphaned FK noise).
    """
    deal = conn.execute(
        "SELECT id, title, property_id FROM deals WHERE id = ?", (int(deal_id),)
    ).fetchone()
    if not deal:
        return None, None

    prop_id = deal["property_id"]
    title = deal["title"]

    for table in ("deal_payments", "deal_brokerage_payments"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE deal_id = ?", (int(deal_id),))
        except Exception:
            pass

    conn.execute("DELETE FROM deals WHERE id = ?", (int(deal_id),))

    prop_title = None
    if delete_property and prop_id:
        prop = conn.execute(
            "SELECT id, title FROM properties WHERE id = ?", (int(prop_id),)
        ).fetchone()
        if prop:
            prop_title = prop["title"]
            other = conn.execute(
                "SELECT id FROM deals WHERE property_id = ?", (int(prop_id),)
            ).fetchall()
            for od in other:
                for table in ("deal_payments", "deal_brokerage_payments"):
                    try:
                        conn.execute(f"DELETE FROM {table} WHERE deal_id = ?", (od["id"],))
                    except Exception:
                        pass
                conn.execute("DELETE FROM deals WHERE id = ?", (od["id"],))

            for sql in (
                "DELETE FROM favorites WHERE property_id = ?",
                "DELETE FROM client_properties WHERE property_id = ?",
                "DELETE FROM inquiries WHERE property_id = ?",
            ):
                try:
                    conn.execute(sql, (int(prop_id),))
                except Exception:
                    pass
            try:
                conn.execute(
                    "UPDATE appointments SET property_id = NULL WHERE property_id = ?",
                    (int(prop_id),),
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "UPDATE tasks SET related_property_id = NULL WHERE related_property_id = ?",
                    (int(prop_id),),
                )
            except Exception:
                pass

            conn.execute("DELETE FROM properties WHERE id = ?", (int(prop_id),))

    return title, prop_title


def delete_deal(
    user: dict,
    deal_id: int,
    confirm: bool = False,
    also_property: bool = False,
) -> dict:
    """
    Delete a deal. Admin may pass also_property=true to cascade-delete the
    linked property (and related live rows) so the whole CRM stays in sync.
    Agents may only delete their own deals and never cascade the property.
    """
    require_staff(user)
    if not confirm:
        return {
            "ok": False,
            "needs_confirmation": True,
            "message": "Pass confirm=true to delete. Admin may also pass also_property=true to remove the linked property.",
        }

    with db_conn() as conn:
        row = conn.execute(
            "SELECT id, title, property_id, agent_id FROM deals WHERE id = ?",
            (int(deal_id),),
        ).fetchone()
        if not row:
            raise ValueError("deal not found")

        if user.get("role") == "agent":
            if row["agent_id"] and int(row["agent_id"]) != int(user["id"]):
                raise PermissionError("You can only delete your own deals.")
            # Agents never cascade-delete inventory
            also_property = False
        elif user.get("role") != "admin" and also_property:
            raise PermissionError("Only admins can cascade-delete the property.")

        title, prop_title = _cascade_delete_deal_and_property(
            conn, int(deal_id), delete_property=bool(also_property)
        )

    out = {"ok": True, "deleted_deal_id": int(deal_id), "title": title}
    if prop_title:
        out["deleted_property_title"] = prop_title
        out["message"] = "Deal and linked property removed. Live stats and agent views updated."
    return out



def change_deal_stage(user: dict, deal_id: int, stage: str) -> dict:
    return update_deal(user, deal_id, stage=_normalize_deal_stage(stage))


# ----- Analytics -----

def get_dashboard_analytics(user: dict) -> dict:
    require_staff(user)
    with db_conn() as conn:
        out = {
            "ok": True,
            "properties_total": conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0],
            "properties_available": conn.execute(
                "SELECT COUNT(*) FROM properties WHERE lower(status) IN ('available','active')"
            ).fetchone()[0],
            "clients_total": conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
            "tasks_pending": conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE lower(IFNULL(status,'pending')) IN ('pending','open','todo')"
            ).fetchone()[0],
        }
        try:
            out["deals_total"] = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
            out["deals_value"] = conn.execute("SELECT COALESCE(SUM(value),0) FROM deals").fetchone()[0]
        except Exception:
            out["deals_total"] = 0
            out["deals_value"] = 0
    return out


def get_sales_analytics(user: dict) -> dict:
    require_staff(user)
    with db_conn() as conn:
        sold = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(price),0) FROM properties WHERE lower(status)='sold'"
        ).fetchone()
        month_props = conn.execute(
            "SELECT COUNT(*) FROM properties WHERE created_at >= date('now','start of month')"
        ).fetchone()[0]
        try:
            deals = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(value),0) FROM deals"
            ).fetchone()
        except Exception:
            deals = (0, 0)
    return {
        "ok": True,
        "properties_sold": sold[0],
        "sold_value": sold[1],
        "properties_added_this_month": month_props,
        "deals_count": deals[0],
        "deals_value": deals[1],
    }


def get_agent_performance(user: dict) -> dict:
    require_staff(user)
    with db_conn() as conn:
        agents = _rows(conn.execute(
            """SELECT u.id, u.full_name,
                      (SELECT COUNT(*) FROM clients c WHERE c.agent_id = u.id) AS clients,
                      (SELECT COUNT(*) FROM properties p WHERE p.agent_id = u.id) AS properties
               FROM users u WHERE u.role = 'agent' AND u.is_active = 1
               ORDER BY clients DESC"""
        ))
    return {"ok": True, "agents": agents}


def get_lead_conversion_report(user: dict) -> dict:
    require_staff(user)
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        closed = conn.execute(
            "SELECT COUNT(*) FROM clients WHERE lower(status) IN ('closed','won')"
        ).fetchone()[0]
        by_status = _rows(conn.execute(
            "SELECT lower(IFNULL(status,'lead')) AS status, COUNT(*) AS cnt FROM clients GROUP BY 1 ORDER BY cnt DESC"
        ))
    rate = round(100.0 * closed / total, 1) if total else 0.0
    return {"ok": True, "total_clients": total, "closed": closed, "conversion_rate_pct": rate, "by_status": by_status}


def get_property_inventory_report(user: dict) -> dict:
    require_staff(user)
    with db_conn() as conn:
        by_status = _rows(conn.execute(
            "SELECT lower(IFNULL(status,'available')) AS status, COUNT(*) AS cnt, COALESCE(SUM(price),0) AS value FROM properties GROUP BY 1"
        ))
        by_city = _rows(conn.execute(
            """SELECT city AS name, COUNT(*) AS cnt FROM properties
               WHERE city IS NOT NULL AND city != '' GROUP BY city ORDER BY cnt DESC LIMIT 15"""
        ))
        inventory_value = conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM properties WHERE lower(IFNULL(status,'')) NOT IN ('sold','rented')"
        ).fetchone()[0]
    return {"ok": True, "by_status": by_status, "by_city": by_city, "active_inventory_value": inventory_value}


def add_appointment(
    user: dict,
    appointment_date: str,
    client_id: int = None,
    property_id: int = None,
    agent_id: int = None,
    status: str = "scheduled",
    notes: str = None,
) -> dict:
    """Create an appointment. appointment_date: ISO-like YYYY-MM-DDTHH:MM or YYYY-MM-DD HH:MM."""
    require_staff(user)
    appointment_date = (appointment_date or "").strip().replace(" ", "T")
    if not appointment_date:
        raise ValueError("appointment_date is required")
    status = (status or "scheduled").strip().lower().replace(" ", "_")
    if status not in ("scheduled", "completed", "cancelled", "no_show"):
        status = "scheduled"
    if agent_id is None and user.get("role") == "agent":
        agent_id = user.get("id")
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO appointments
               (client_id, property_id, agent_id, appointment_date, status, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (client_id, property_id, agent_id, appointment_date, status, notes),
        )
        aid = cur.lastrowid
        conn.commit()
        row = conn.execute(
            """SELECT a.*, c.full_name as client_name, p.title as property_title, u.full_name as agent_name
               FROM appointments a
               LEFT JOIN clients c ON a.client_id = c.id
               LEFT JOIN properties p ON a.property_id = p.id
               LEFT JOIN users u ON a.agent_id = u.id
               WHERE a.id = ?""",
            (aid,),
        ).fetchone()
    return {"ok": True, "appointment": dict(row) if row else {"id": aid}}


def search_appointments(
    user: dict,
    q: str = None,
    status: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50,
) -> dict:
    require_staff(user)
    sql = (
        "SELECT a.*, c.full_name as client_name, p.title as property_title, u.full_name as agent_name "
        "FROM appointments a "
        "LEFT JOIN clients c ON a.client_id = c.id "
        "LEFT JOIN properties p ON a.property_id = p.id "
        "LEFT JOIN users u ON a.agent_id = u.id WHERE 1=1"
    )
    params = []
    if q:
        sql += (
            " AND (IFNULL(c.full_name,'') LIKE ? OR IFNULL(p.title,'') LIKE ? "
            "OR IFNULL(u.full_name,'') LIKE ? OR IFNULL(a.notes,'') LIKE ?)"
        )
        qq = "%" + q + "%"
        params.extend([qq, qq, qq, qq])
    if status:
        sql += " AND lower(IFNULL(a.status,'')) = ?"
        params.append(status.strip().lower())
    if date_from:
        sql += " AND date(a.appointment_date) >= date(?)"
        params.append(date_from)
    if date_to:
        sql += " AND date(a.appointment_date) <= date(?)"
        params.append(date_to)
    scope, sp = agent_scope_clause(user, "a.agent_id")
    if scope:
        sql += " AND " + scope
        params.extend(sp)
    sql += " ORDER BY a.appointment_date DESC LIMIT ?"
    params.append(int(limit or 50))
    with db_conn() as conn:
        rows = _rows(conn.execute(sql, params))
    return {"ok": True, "count": len(rows), "appointments": rows}


def get_appointment_details(user: dict, appointment_id: int) -> dict:
    require_staff(user)
    with db_conn() as conn:
        row = conn.execute(
            """SELECT a.*, c.full_name as client_name, p.title as property_title, u.full_name as agent_name
               FROM appointments a
               LEFT JOIN clients c ON a.client_id = c.id
               LEFT JOIN properties p ON a.property_id = p.id
               LEFT JOIN users u ON a.agent_id = u.id
               WHERE a.id = ?""",
            (appointment_id,),
        ).fetchone()
    if not row:
        raise ValueError("Appointment not found")
    return {"ok": True, "appointment": dict(row)}


def update_appointment_status(
    user: dict,
    appointment_id: int,
    status: str,
    minutes_of_meeting: str = None,
) -> dict:
    require_staff(user)
    status = (status or "").strip().lower().replace(" ", "_")
    if status not in ("scheduled", "completed", "cancelled", "no_show"):
        raise ValueError("Invalid status")
    with db_conn() as conn:
        if status == "completed" and minutes_of_meeting is not None:
            conn.execute(
                "UPDATE appointments SET status = ?, minutes_of_meeting = ? WHERE id = ?",
                (status, minutes_of_meeting, appointment_id),
            )
        else:
            conn.execute(
                "UPDATE appointments SET status = ? WHERE id = ?",
                (status, appointment_id),
            )
        conn.commit()
    return {"ok": True, "id": appointment_id, "status": status}
