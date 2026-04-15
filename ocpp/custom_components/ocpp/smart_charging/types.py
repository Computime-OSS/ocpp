"""Data types for platform-agnostic smart charging policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto


@dataclass
class TariffPricePoint:
    """Single price sample (e.g. EUR/kWh)."""

    valid_from: datetime
    price_per_kwh: float


@dataclass
class TariffHorizon:
    """Upcoming tariff / price information for optimization."""

    currency: str | None
    points: list[TariffPricePoint] = field(default_factory=list)


@dataclass(frozen=True)
class GridConstraints:
    """Site or grid limits (from e.g. IAMMETER, main fuse, DSO signal)."""

    max_charge_current_amps: float | None = None
    max_import_power_watts: float | None = None


@dataclass
class UserChargingPreferences:
    """User/session intent (HA helpers, UI, or future OEM bridge)."""

    departure_time: datetime | None = None
    target_energy_kwh: float | None = None
    max_price_per_kwh: float | None = None


@dataclass
class EvseSmartChargingSnapshot:
    """Latest charger→CSMS smart-charging payloads per EVSE."""

    evse_id: int
    notify_ev_charging_needs: dict | None = None
    notify_charging_limit: dict | None = None
    notify_ev_charging_schedule: dict | None = None
    updated_at: datetime | None = None


class SmartChargingOutcomeKind(Enum):
    """What the policy layer decided to do on the charger."""

    NOOP = auto()
    APPLY_STATION_CURRENT_CAP = auto()
    CLEAR_STATION_PROFILE = auto()


@dataclass(frozen=True)
class SmartChargingOutcome:
    """Result of running the smart charging engine."""

    kind: SmartChargingOutcomeKind
    limit_amps: int | None = None
