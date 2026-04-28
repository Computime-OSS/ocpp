"""Tests for OCPP 2.0.1 TriggerMessage mapping."""

from custom_components.ocpp.ocppv201 import _to_message_trigger_v201


def test_to_message_trigger_exact_enum_string():
    """Accept spec enum values as-is."""
    assert _to_message_trigger_v201("Heartbeat") == "Heartbeat"
    assert _to_message_trigger_v201("StatusNotification") == "StatusNotification"


def test_to_message_trigger_aliases():
    """Accept common aliases (v1.6-style and normalized)."""
    assert _to_message_trigger_v201("status_notification") == "StatusNotification"
    assert _to_message_trigger_v201("BootNotification") == "BootNotification"
    assert _to_message_trigger_v201("boot_notification") == "BootNotification"
    assert _to_message_trigger_v201("MeterValues") == "MeterValues"
    assert (
        _to_message_trigger_v201("diagnostics_status_notification")
        == "LogStatusNotification"
    )


def test_to_message_trigger_unknown():
    """Reject unknown names."""
    assert _to_message_trigger_v201("not_a_real_trigger") is None
    assert _to_message_trigger_v201("") is None
