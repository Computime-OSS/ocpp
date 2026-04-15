"""Smart charging policy engine

It is basically the layer responsible to do the calculation for smart charging.
Only a simple calculation is done here, if you were to extend the functionality, you would need to extend the engine.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core_const import CONF_SMART_CHARGING_POLICY_ENABLED

from .types import (
    EvseSmartChargingSnapshot,
    GridConstraints,
    SmartChargingOutcome,
    SmartChargingOutcomeKind,
    TariffHorizon,
    UserChargingPreferences,
)

_LOGGER = logging.getLogger(__name__)


async def run_smart_charging_engine(
    adapter: Any,
    *,
    cpid: str,
    max_configured_amps: int,
    smart_charging_inventory_available: bool,
    snapshots: dict[int, EvseSmartChargingSnapshot],
) -> SmartChargingOutcome:
    """Compute whether to adjust charging limits using platform providers.

    v1 policy (safe default): only applies a station current cap when
    ``smart_charging_policy_enabled`` is true in YAML config, the charger
    reports SmartCharging available, and ``get_grid_constraints`` returns a
    max charge current. Tariff / departure / Notify* payload math can extend
    this function without touching OCPP handlers.
    """
    cfg: dict[str, Any] = adapter.get_config()
    if not cfg.get(CONF_SMART_CHARGING_POLICY_ENABLED):
        return SmartChargingOutcome(SmartChargingOutcomeKind.NOOP)

    if not smart_charging_inventory_available:
        _LOGGER.debug(
            "Smart charging policy skipped: inventory reports SmartCharging unavailable (%s)",
            cpid,
        )
        return SmartChargingOutcome(SmartChargingOutcomeKind.NOOP)

    grid: GridConstraints | None = await adapter.get_grid_constraints(cpid)
    tariff: TariffHorizon | None = await adapter.get_tariff_horizon(cpid)
    user: UserChargingPreferences | None = await adapter.get_user_charging_preferences(
        cpid
    )

    _LOGGER.debug(
        "Smart charging inputs cpid=%s grid=%s tariff_points=%s user_dep=%s snapshots=%s",
        cpid,
        grid,
        len(tariff.points) if tariff else 0,
        user.departure_time if user else None,
        list(snapshots.keys()),
    )

    if grid is not None and grid.max_charge_current_amps is not None:
        cap = float(grid.max_charge_current_amps)
        if cap <= 0:
            return SmartChargingOutcome(SmartChargingOutcomeKind.NOOP)
        limit = int(min(cap, float(max_configured_amps)))
        if limit < 1:
            return SmartChargingOutcome(SmartChargingOutcomeKind.NOOP)
        return SmartChargingOutcome(
            SmartChargingOutcomeKind.APPLY_STATION_CURRENT_CAP,
            limit_amps=limit,
        )

    if tariff and tariff.points and user and user.departure_time:
        _LOGGER.debug(
            "Tariff and departure present but v1 engine does not optimize them yet (%s)",
            cpid,
        )

    if snapshots:
        _LOGGER.debug(
            "Notify* snapshots stored for future policy use (%s): %s",
            cpid,
            {k: type(v).__name__ for k, v in snapshots.items()},
        )

    return SmartChargingOutcome(SmartChargingOutcomeKind.NOOP)
