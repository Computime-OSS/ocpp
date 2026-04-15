"""Unit tests for smart charging policy engine."""

import pytest

from custom_components.ocpp.core_const import CONF_SMART_CHARGING_POLICY_ENABLED
from custom_components.ocpp.smart_charging.engine import run_smart_charging_engine
from custom_components.ocpp.smart_charging.types import (
    GridConstraints,
    SmartChargingOutcomeKind,
)


class _FakeAdapter:
    """Minimal adapter stub for engine tests."""

    def __init__(self, cfg: dict, grid=None):
        self._cfg = cfg
        self._grid = grid

    def get_config(self):
        return self._cfg

    async def get_grid_constraints(self, _cpid: str):
        return self._grid

    async def get_tariff_horizon(self, _cpid: str):
        return None

    async def get_user_charging_preferences(self, _cpid: str):
        return None


@pytest.mark.asyncio
async def test_engine_disabled_returns_noop():
    """Policy flag off -> never applies."""
    ad = _FakeAdapter(
        {},
        grid=GridConstraints(max_charge_current_amps=8.0),
    )
    out = await run_smart_charging_engine(
        ad,
        cpid="c1",
        max_configured_amps=32,
        smart_charging_inventory_available=True,
        snapshots={},
    )
    assert out.kind == SmartChargingOutcomeKind.NOOP


@pytest.mark.asyncio
async def test_engine_applies_grid_cap_when_enabled():
    """Grid max amps caps station when policy enabled and SmartCharging available."""
    ad = _FakeAdapter(
        {CONF_SMART_CHARGING_POLICY_ENABLED: True},
        grid=GridConstraints(max_charge_current_amps=8.0),
    )
    out = await run_smart_charging_engine(
        ad,
        cpid="c1",
        max_configured_amps=32,
        smart_charging_inventory_available=True,
        snapshots={},
    )
    assert out.kind == SmartChargingOutcomeKind.APPLY_STATION_CURRENT_CAP
    assert out.limit_amps == 8


@pytest.mark.asyncio
async def test_engine_respects_configured_max_current():
    """Limit is min(grid, configured charger max)."""
    ad = _FakeAdapter(
        {CONF_SMART_CHARGING_POLICY_ENABLED: True},
        grid=GridConstraints(max_charge_current_amps=40.0),
    )
    out = await run_smart_charging_engine(
        ad,
        cpid="c1",
        max_configured_amps=16,
        smart_charging_inventory_available=True,
        snapshots={},
    )
    assert out.kind == SmartChargingOutcomeKind.APPLY_STATION_CURRENT_CAP
    assert out.limit_amps == 16


@pytest.mark.asyncio
async def test_engine_skips_without_smart_charging_inventory():
    """Inventory says SmartCharging unavailable -> noop even if grid set."""
    ad = _FakeAdapter(
        {CONF_SMART_CHARGING_POLICY_ENABLED: True},
        grid=GridConstraints(max_charge_current_amps=8.0),
    )
    out = await run_smart_charging_engine(
        ad,
        cpid="c1",
        max_configured_amps=32,
        smart_charging_inventory_available=False,
        snapshots={},
    )
    assert out.kind == SmartChargingOutcomeKind.NOOP
