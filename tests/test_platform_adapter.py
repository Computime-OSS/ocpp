"""Targeted tests for platform adapter helper paths."""

# sonar:approved

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ocpp.const import DOMAIN
from custom_components.ocpp.platform_adapter import HomeAssistantAdapter, PlatformAdapter
from tests.const import MOCK_CONFIG_DATA


class DummyAdapter(PlatformAdapter):
    """Minimal adapter to exercise default async hook methods."""

    @property
    def unit_of_time_minutes(self) -> str:
        return "minutes"

    def get_unit_for_device_class(self, device_class: str) -> str:
        return device_class

    def ocpp_unit_to_platform_unit(self, ocpp_unit: str) -> str:
        return ocpp_unit

    def schedule_task(self, coro) -> None:
        return None

    def signal_state_changed(
        self, charge_point_id: str, entity_ids: set[str] | None = None
    ) -> None:
        return None

    async def notify_user(self, message: str, title: str = "OCPP") -> bool:
        return True

    def get_config(self) -> dict:
        return {}

    async def persist_charge_point_config(self, charge_point_id: str, data: dict) -> None:
        return None

    async def update_device_info(
        self,
        identifiers: set[tuple[str, str]],
        manufacturer: str,
        model: str,
        sw_version: str,
    ) -> None:
        return None

    def get_entity_ids_to_refresh(self, charge_point_id: str) -> set[str]:
        return set()

    def get_metric_fallback(
        self, charge_point_id: str, measurand: str, connector_id: int | None
    ) -> str | None:
        return None

    async def on_unknown_charge_point(self, cp_id: str) -> None:
        return None

    async def run_in_executor(self, func, *args, **kwargs):
        return func(*args, **kwargs)

    def register_service(
        self,
        domain: str,
        service_name: str,
        handler,
        schema=None,
        supports_response=None,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_platform_adapter_default_async_hooks_return_none():
    """Default optional hooks are awaitable and return None."""
    adapter = DummyAdapter()
    assert await adapter.get_tariff_horizon("CP_1") is None
    assert await adapter.get_grid_constraints("CP_1") is None
    assert await adapter.get_user_charging_preferences("CP_1") is None


@pytest.mark.asyncio
async def test_get_entity_ids_to_refresh_filters_disabled_and_missing_state(
    hass, monkeypatch
):
    """HomeAssistantAdapter collects active entities through child devices."""
    config = MOCK_CONFIG_DATA.copy()
    entry = MockConfigEntry(domain=DOMAIN, data=config)
    adapter = HomeAssistantAdapter(hass, entry)

    hass.states.async_set("sensor.root_active", "1")
    hass.states.async_set("sensor.child_active", "1")

    root_dev = SimpleNamespace(id="root")
    child_dev = SimpleNamespace(id="child", via_device_id="root")
    unrelated_dev = SimpleNamespace(id="other", via_device_id="other_parent")
    fake_device_registry = SimpleNamespace(
        async_get_device=lambda identifiers: root_dev
        if (DOMAIN, "CP_1") in identifiers
        else None,
        devices={"root": root_dev, "child": child_dev, "other": unrelated_dev},
    )

    def fake_entries_for_device(_registry, dev_id):
        if dev_id == "root":
            return [
                SimpleNamespace(
                    entity_id="sensor.root_active", disabled=False, disabled_by=None
                ),
                SimpleNamespace(
                    entity_id="sensor.root_disabled", disabled=True, disabled_by=None
                ),
                SimpleNamespace(
                    entity_id="sensor.root_missing", disabled=False, disabled_by=None
                ),
            ]
        if dev_id == "child":
            return [
                SimpleNamespace(
                    entity_id="sensor.child_active", disabled=False, disabled_by=None
                ),
                SimpleNamespace(
                    entity_id="sensor.child_disabled_by",
                    disabled=False,
                    disabled_by="integration",
                ),
            ]
        return []

    monkeypatch.setattr(
        "custom_components.ocpp.platform_adapter.device_registry.async_get",
        lambda _hass: fake_device_registry,
    )
    monkeypatch.setattr(
        "custom_components.ocpp.platform_adapter.entity_registry.async_get",
        lambda _hass: object(),
    )
    monkeypatch.setattr(
        "custom_components.ocpp.platform_adapter.entity_registry.async_entries_for_device",
        fake_entries_for_device,
    )

    refreshed = adapter.get_entity_ids_to_refresh("CP_1")
    assert refreshed == {"sensor.root_active", "sensor.child_active"}
