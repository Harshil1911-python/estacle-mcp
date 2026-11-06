"""
Estacle MCP server — Streamable HTTP, tools backed by core.crm_service.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:
    try:
        from mcp.server import MCPServer as _Server
    except ImportError:
        from mcp.server.fastmcp import FastMCP as _Server  # mcp v1 fallback

from mcp_estacle.context import get_user
from core import crm_service as crm

mcp = _Server(
    "Estacle CRM",
    instructions=(
        "You are connected to Estacle, a real-estate CRM by TechSerenia. "
        "Use tools to create, read, and update CRM records in the live database. "
        "Never claim success unless a tool returns ok:true. "
        "For delete_property and delete_client, you must pass confirm=true only after the user explicitly confirms."
    ),
)


def _ok(result: Any) -> str:
    return json.dumps(result, default=str, indent=2)


def _err(e: Exception) -> str:
    return json.dumps({"ok": False, "error": str(e)})


# ----- Properties -----

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
    """Create a property in Estacle. Required: title, price. Example: 3BHK in Vesu Surat at 8500000."""
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


# ----- Clients -----

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


# ----- Leads -----

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


# ----- Tasks -----

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


# ----- Analytics -----

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


def create_mcp_asgi_app():
    """Return Starlette ASGI app for Streamable HTTP MCP (mcp v1 + v2)."""
    for name in ("streamable_http_app", "http_app", "sse_app"):
        fn = getattr(mcp, name, None)
        if callable(fn):
            try:
                return fn()
            except TypeError:
                # some versions require path/transport kwargs with defaults
                try:
                    return fn(transport="streamable-http")
                except TypeError:
                    return fn
    raise RuntimeError(
        "Installed mcp package has no streamable_http_app/http_app/sse_app. "
        "Install mcp>=2.0 or mcp<2 with FastMCP."
    )
