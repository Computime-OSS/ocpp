"""Unit tests for OCPP 2.0.1 set_charge_rate helper paths."""

# sonar:approved

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ocpp.v201 import call
from ocpp.v201.enums import ChargingProfileStatusEnumType

from custom_components.ocpp.core_errors import OcppError
from custom_components.ocpp.ocppv201 import ChargePoint


class FakeChargePoint:
    """Small fake object that reuses real ChargePoint methods."""

    _resolve_evse_target = ChargePoint._resolve_evse_target
    _get_limit_spec = ChargePoint._get_limit_spec
    _raise_for_rejected_profile = ChargePoint._raise_for_rejected_profile
    _apply_charging_profile = ChargePoint._apply_charging_profile
    set_charge_rate = ChargePoint.set_charge_rate

    def __init__(self):
        self.calls: list[call.SetChargingProfile] = []
        self.clear_count = 0
        self.response = SimpleNamespace(status=ChargingProfileStatusEnumType.accepted)
        self.raise_pair_error = False

    def _global_to_pair(self, _global_idx: int) -> tuple[int, int]:
        if self.raise_pair_error:
            raise RuntimeError("mapping failed")
        return 3, 1

    async def call(self, req):
        self.calls.append(req)
        return self.response

    async def clear_profile(self) -> None:
        self.clear_count += 1


@pytest.mark.asyncio
async def test_set_charge_rate_uses_conn_id_for_custom_profile():
    """Custom profile path uses mapped EVSE target and succeeds."""
    cp = FakeChargePoint()
    profile = {"id": 11, "charging_schedule": []}

    await cp.set_charge_rate(profile=profile, conn_id=2)

    assert len(cp.calls) == 1
    assert cp.calls[0].evse_id == 3
    assert cp.calls[0].charging_profile == profile


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected_clear"),
    [
        ({"limit_watts": 22000}, 1),
        ({"limit_amps": 32}, 1),
        ({}, 1),
        ({"limit_watts": 5000}, 0),
    ],
)
async def test_set_charge_rate_limit_threshold_paths(kwargs, expected_clear):
    """Limit branch clears profile only for threshold or missing limits."""
    cp = FakeChargePoint()

    await cp.set_charge_rate(**kwargs)

    assert cp.clear_count == expected_clear


@pytest.mark.asyncio
async def test_set_charge_rate_suppresses_evse_mapping_failure():
    """Invalid connector mapping falls back to station-level profile."""
    cp = FakeChargePoint()
    cp.raise_pair_error = True

    await cp.set_charge_rate(limit_amps=16, conn_id=9)

    assert len(cp.calls) == 1
    assert cp.calls[0].evse_id == 0


@pytest.mark.asyncio
async def test_set_charge_rate_raises_on_rejected_profile_with_status_info():
    """Rejected profile raises OcppError including status_info details."""
    cp = FakeChargePoint()
    cp.response = SimpleNamespace(
        status=ChargingProfileStatusEnumType.rejected,
        status_info="minimum current is 6A",
    )

    with pytest.raises(OcppError, match="minimum current is 6A"):
        await cp.set_charge_rate(limit_amps=10)
