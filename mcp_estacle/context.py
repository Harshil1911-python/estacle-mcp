"""Request-scoped authenticated user for MCP tool calls."""
from __future__ import annotations
from contextvars import ContextVar
from typing import Optional

_current_user: ContextVar[Optional[dict]] = ContextVar("mcp_user", default=None)


def set_user(user: Optional[dict]):
    _current_user.set(user)


def get_user() -> dict:
    user = _current_user.get()
    if not user:
        raise PermissionError(
            "Not authenticated. In Claude, reconnect the Estacle connector and complete sign-in "
            "with an admin or agent account, then retry."
        )
    return user
