"""
Admin blueprint package.
Split by feature for maintainability and future offline support.
"""
from flask import Blueprint

admin = Blueprint('admin', __name__)

# Import all feature modules so their routes register on the blueprint
from routes.admin import (
    dashboard,
    users,
    properties,
    clients,
    appointments,
    analytics,
    reports,
    settings,
    tasks,
    deals,
    activity,
    documents,
    search,
    leads,
    commission,
    compare,
    inquiries,
    ai,
)

__all__ = ['admin']
