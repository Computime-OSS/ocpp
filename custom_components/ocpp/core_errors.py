"""Platform-agnostic exceptions for the OCPP integration.

Use these in chargepoint.py, ocppv16.py, ocppv201.py so the core has no
Home Assistant dependency. The HA layer (api.py) catches these and re-raises
as HomeAssistantError / ServiceValidationError for the UI.
"""

from __future__ import annotations


class OcppError(Exception):
    """Base exception for OCPP operations (e.g. charger rejected, call failed)."""


class OcppValidationError(OcppError):
    """Invalid request (e.g. malformed OCPP key). Platform can map to validation error."""
