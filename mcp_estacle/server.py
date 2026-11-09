"""
Estacle MCP server — Streamable HTTP, tools backed by core.crm_service.
Compatible with MCP Python SDK v2 (MCPServer) and v1 (FastMCP).
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import urlparse

try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:
    try:
        from mcp.server import MCPServer as _Server
    except ImportError:
        from mcp.server.fastmcp import FastMCP as _Server

from mcp_estacle.context import get_user
from core import crm_service as crm


def _transport_security():
    """Allow Render / public host so MCP does not return 421 Invalid Host header."""
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError:
        return None

    hosts = [
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "estaclenew.onrender.com",
        "estaclenew.onrender.com:*",
    ]
    base = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    if base:
        host = urlparse(base).hostname or base.replace("https://", "").replace("http://", "").split("/")[0]
        if host:
            hosts.append(host)
            hosts.append(f"{host}:*")

    # Render terminates TLS; default empty allowlist causes HTTP 421.
    # On Render, relax host check unless MCP_RELAX_HOST_CHECK=0.
    relax = os.environ.get("MCP_RELAX_HOST_CHECK", "").lower() in ("1", "true", "yes")
    if os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL"):
        if os.environ.get("MCP_RELAX_HOST_CHECK", "1") != "0":
            relax = True

    if relax:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=["*"],
    )


_ts = _transport_security()
_server_kwargs = {
    "instructions": (
        "You are connected to Estacle, a real-estate CRM by TechSerenia. "
        "Use tools to create, read, and update CRM records in the live database. "
        "Never claim success unless a tool returns ok:true. "
        "Never show SQL, queries, or raw tool payloads to the user — speak in business language only. "
        "Deals stages: offer, negotiation, under_contract, closed, lost. "
        "For delete_property, delete_client, and delete_deal, pass confirm=true only after the user explicitly confirms. "
        "Admin may pass also_property=true on delete_deal to remove the linked property across the CRM; analytics then count only remaining live rows."
    ),
}
if _ts is not None:
    _server_kwargs["transport_security"] = _ts

try:
    mcp = _Server("Serenia AI · Estacle CRM", **_server_kwargs)
except TypeError:
    mcp = _Server(
        "Serenia AI · Estacle CRM",
        instructions=_server_kwargs["instructions"],
    )


def _ok(result: Any) -> str:
    return json.dumps(result, default=str, indent=2)


def _err(e: Exception) -> str:
    return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def add_property(
    title: str,
    price: float,
    city: Optional[str] = None,
    address: Optional[str] = None,
    property_type: Optional[str] = "Apartment",
    bedrooms: Optional[int] = None,
    bathrooms: Optional[float] = None,
    listing_type: Optional[str] = "sale",
    status: Optional[str] = "available",
    description: Optional[str] = None,
) -> str:
    """Create a property in Estacle. Required: title, price."""
    try:
        return _ok(crm.add_property(
            get_user(), title=title, price=price, city=city, address=address,
            property_type=property_type, bedrooms=bedrooms, bathrooms=bathrooms,
            listing_type=listing_type, status=status, description=description,
        ))
    except Exception as e:
        return _err(e)


@mcp.tool()
def search_properties(
    q: Optional[str] = None,
    city: Optional[str] = None,
    status: Optional[str] = None,
    bedrooms: Optional[int] = None,
    limit: Optional[int] = 20,
) -> str:
    """Search properties by text, city, status, or bedrooms."""
    try:
        return _ok(crm.search_properties(get_user(), q=q, city=city, status=status, bedrooms=bedrooms, limit=limit or 20))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_property_details(property_id: int) -> str:
    """Get full details for one property by id."""
    try:
        return _ok(crm.get_property_details(get_user(), property_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
def update_property(
    property_id: int,
    title: Optional[str] = None,
    price: Optional[float] = None,
    status: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    bedrooms: Optional[int] = None,
) -> str:
    """Update fields on an existing property."""
    try:
        kw = {k: v for k, v in dict(title=title, price=price, status=status, city=city, address=address, bedrooms=bedrooms).items() if v is not None}
        return _ok(crm.update_property(get_user(), property_id, **kw))
    except Exception as e:
        return _err(e)


@mcp.tool()
def delete_property(property_id: int, confirm: bool = False) -> str:
    """Delete a property. Requires confirm=true after explicit user confirmation. Admin only."""
    try:
        return _ok(crm.delete_property(get_user(), property_id, confirm=confirm))
    except Exception as e:
        return _err(e)


@mcp.tool()
def add_client(
    full_name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    status: Optional[str] = "lead",
    city: Optional[str] = None,
    notes: Optional[str] = None,
    budget: Optional[float] = None,
) -> str:
    """Create a client/lead in Estacle."""
    try:
        return _ok(crm.add_client(
            get_user(), full_name=full_name, email=email, phone=phone,
            status=status, city=city, notes=notes, budget=budget,
        ))
    except Exception as e:
        return _err(e)


@mcp.tool()
def search_clients(
    q: Optional[str] = None,
    status: Optional[str] = None,
    city: Optional[str] = None,
    unassigned: Optional[bool] = False,
    limit: Optional[int] = 30,
) -> str:
    """Search clients/leads."""
    try:
        return _ok(crm.search_clients(get_user(), q=q, status=status, city=city, unassigned=bool(unassigned), limit=limit or 30))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_client_details(client_id: int) -> str:
    """Get one client by id."""
    try:
        return _ok(crm.get_client_details(get_user(), client_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
def update_client(
    client_id: int,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    agent_id: Optional[int] = None,
) -> str:
    """Update client fields."""
    try:
        kw = {k: v for k, v in dict(full_name=full_name, email=email, phone=phone, status=status, notes=notes, agent_id=agent_id).items() if v is not None}
        return _ok(crm.update_client(get_user(), client_id, **kw))
    except Exception as e:
        return _err(e)


@mcp.tool()
def delete_client(client_id: int, confirm: bool = False) -> str:
    """Delete a client. Requires confirm=true. Admin only."""
    try:
        return _ok(crm.delete_client(get_user(), client_id, confirm=confirm))
    except Exception as e:
        return _err(e)


@mcp.tool()
def add_lead(
    full_name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    city: Optional[str] = None,
    notes: Optional[str] = None,
    budget: Optional[float] = None,
) -> str:
    """Add a new lead (stored as client with status=lead)."""
    try:
        return _ok(crm.add_lead(get_user(), full_name=full_name, email=email, phone=phone, city=city, notes=notes, budget=budget, status="lead"))
    except Exception as e:
        return _err(e)


@mcp.tool()
def search_leads(q: Optional[str] = None, status: Optional[str] = None, limit: Optional[int] = 30) -> str:
    """Search leads/clients in the pipeline."""
    try:
        return _ok(crm.search_leads(get_user(), q=q, status=status, limit=limit or 30))
    except Exception as e:
        return _err(e)


@mcp.tool()
def update_lead(lead_id: int, full_name: Optional[str] = None, phone: Optional[str] = None, notes: Optional[str] = None) -> str:
    """Update lead contact fields."""
    try:
        kw = {k: v for k, v in dict(full_name=full_name, phone=phone, notes=notes).items() if v is not None}
        return _ok(crm.update_lead(get_user(), lead_id, **kw))
    except Exception as e:
        return _err(e)


@mcp.tool()
def assign_lead(lead_id: int, agent_id: int) -> str:
    """Assign a lead to an agent user id."""
    try:
        return _ok(crm.assign_lead(get_user(), lead_id, agent_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
def change_lead_status(lead_id: int, status: str) -> str:
    """Change lead pipeline status (lead, contacted, nurturing, closed, unqualified, etc.)."""
    try:
        return _ok(crm.change_lead_status(get_user(), lead_id, status))
    except Exception as e:
        return _err(e)


@mcp.tool()
def create_followup(
    title: str,
    description: Optional[str] = None,
    due_date: Optional[str] = None,
    priority: Optional[str] = "medium",
    related_client_id: Optional[int] = None,
) -> str:
    """Create a follow-up task."""
    try:
        return _ok(crm.create_followup(
            get_user(), title=title, description=description, due_date=due_date,
            priority=priority or "medium", related_client_id=related_client_id,
        ))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_pending_followups(limit: Optional[int] = 30) -> str:
    """List pending follow-up tasks."""
    try:
        return _ok(crm.get_pending_followups(get_user(), limit=limit or 30))
    except Exception as e:
        return _err(e)


@mcp.tool()
def update_followup(
    task_id: int,
    title: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    due_date: Optional[str] = None,
) -> str:
    """Update a follow-up task."""
    try:
        kw = {k: v for k, v in dict(title=title, status=status, priority=priority, due_date=due_date).items() if v is not None}
        return _ok(crm.update_followup(get_user(), task_id, **kw))
    except Exception as e:
        return _err(e)


@mcp.tool()
def mark_followup_complete(task_id: int) -> str:
    """Mark a follow-up task as completed."""
    try:
        return _ok(crm.mark_followup_complete(get_user(), task_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
def add_deal(
    title: str,
    value: Optional[float] = 0,
    commission_rate: Optional[float] = 2.5,
    stage: Optional[str] = "offer",
    property_id: Optional[int] = None,
    client_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    expected_close: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Create a deal in the Estacle pipeline."""
    try:
        return _ok(crm.add_deal(
            get_user(),
            title=title,
            value=value or 0,
            commission_rate=commission_rate or 2.5,
            stage=stage or "offer",
            property_id=property_id,
            client_id=client_id,
            agent_id=agent_id,
            expected_close=expected_close,
            notes=notes,
        ))
    except Exception as e:
        return _err(e)


@mcp.tool()
def search_deals(
    q: Optional[str] = None,
    stage: Optional[str] = None,
    limit: Optional[int] = 30,
) -> str:
    """Search/list deals. stage: offer|negotiation|under_contract|closed|lost."""
    try:
        return _ok(crm.search_deals(get_user(), q=q, stage=stage, limit=limit or 30))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_deal_details(deal_id: int) -> str:
    """Get one deal by id with property, client, and agent names."""
    try:
        return _ok(crm.get_deal_details(get_user(), deal_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
def update_deal(
    deal_id: int,
    title: Optional[str] = None,
    value: Optional[float] = None,
    commission_rate: Optional[float] = None,
    stage: Optional[str] = None,
    property_id: Optional[int] = None,
    client_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    expected_close: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Update fields on a deal."""
    try:
        kwargs = {}
        for k, v in {
            "title": title, "value": value, "commission_rate": commission_rate,
            "stage": stage, "property_id": property_id, "client_id": client_id,
            "agent_id": agent_id, "expected_close": expected_close, "notes": notes,
        }.items():
            if v is not None:
                kwargs[k] = v
        return _ok(crm.update_deal(get_user(), deal_id, **kwargs))
    except Exception as e:
        return _err(e)


@mcp.tool()
def change_deal_stage(deal_id: int, stage: str) -> str:
    """Move a deal to a pipeline stage: offer, negotiation, under_contract, closed, lost."""
    try:
        return _ok(crm.change_deal_stage(get_user(), deal_id, stage))
    except Exception as e:
        return _err(e)


@mcp.tool()
def delete_deal(
    deal_id: int,
    confirm: bool = False,
    also_property: bool = False,
) -> str:
    """Permanently delete a deal. Requires confirm=true after user approval.
    Admin may set also_property=true to also remove the linked property across the CRM
    (payments, favorites, inquiries cleaned). Live analytics then only count remaining rows.
    Agents can only delete their own deals and never cascade the property."""
    try:
        return _ok(
            crm.delete_deal(
                get_user(),
                deal_id,
                confirm=confirm,
                also_property=also_property,
            )
        )
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_dashboard_analytics() -> str:
    """CRM dashboard counts from the live database."""
    try:
        return _ok(crm.get_dashboard_analytics(get_user()))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_sales_analytics() -> str:
    """Sales-oriented metrics (sold properties, deals)."""
    try:
        return _ok(crm.get_sales_analytics(get_user()))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_agent_performance() -> str:
    """Per-agent client and property counts."""
    try:
        return _ok(crm.get_agent_performance(get_user()))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_lead_conversion_report() -> str:
    """Lead conversion rates and status breakdown."""
    try:
        return _ok(crm.get_lead_conversion_report(get_user()))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_property_inventory_report() -> str:
    """Property inventory by status and city."""
    try:
        return _ok(crm.get_property_inventory_report(get_user()))
    except Exception as e:
        return _err(e)


@mcp.tool()
def add_appointment(
    appointment_date: str,
    client_id: Optional[int] = None,
    property_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    status: Optional[str] = "scheduled",
    notes: Optional[str] = None,
) -> str:
    """Schedule an appointment. appointment_date format: YYYY-MM-DDTHH:MM (e.g. 2026-11-10T14:30)."""
    try:
        return _ok(crm.add_appointment(
            get_user(),
            appointment_date=appointment_date,
            client_id=client_id,
            property_id=property_id,
            agent_id=agent_id,
            status=status or "scheduled",
            notes=notes,
        ))
    except Exception as e:
        return _err(e)


@mcp.tool()
def search_appointments(
    q: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: Optional[int] = 50,
) -> str:
    """Search appointments by text, status, or date range."""
    try:
        return _ok(crm.search_appointments(
            get_user(), q=q, status=status, date_from=date_from, date_to=date_to, limit=limit or 50,
        ))
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_appointment_details(appointment_id: int) -> str:
    """Get one appointment by id."""
    try:
        return _ok(crm.get_appointment_details(get_user(), appointment_id))
    except Exception as e:
        return _err(e)


@mcp.tool()
def update_appointment_status(
    appointment_id: int,
    status: str,
    minutes_of_meeting: Optional[str] = None,
) -> str:
    """Update appointment status (scheduled, completed, cancelled, no_show). Optional minutes_of_meeting when completed."""
    try:
        return _ok(crm.update_appointment_status(
            get_user(), appointment_id, status, minutes_of_meeting=minutes_of_meeting,
        ))
    except Exception as e:
        return _err(e)


def create_mcp_asgi_app(path: str = "/mcp"):
    """Streamable HTTP ASGI app. Default path /mcp for Claude."""
    errors = []
    ts = _transport_security()
    base_attempts = [
        {"streamable_http_path": path},
        {"path": path},
        {"transport": "streamable-http", "path": path},
        {},
    ]
    attempts = []
    for kwargs in base_attempts:
        if ts is not None:
            attempts.append(("streamable_http_app", {**kwargs, "transport_security": ts}))
            attempts.append(("http_app", {**kwargs, "transport_security": ts}))
        attempts.append(("streamable_http_app", kwargs))
        attempts.append(("http_app", kwargs))
    attempts.append(("sse_app", {}))

    for attr, kwargs in attempts:
        fn = getattr(mcp, attr, None)
        if not callable(fn):
            continue
        try:
            return fn(**kwargs)
        except TypeError as e:
            errors.append(f"{attr}{kwargs}: {e}")
            try:
                return fn()
            except Exception as e2:
                errors.append(f"{attr}(): {e2}")
        except Exception as e:
            errors.append(f"{attr}{kwargs}: {e}")
    raise RuntimeError("Could not create MCP HTTP app. Tried: " + "; ".join(errors))
