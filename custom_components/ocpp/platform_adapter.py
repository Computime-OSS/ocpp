"""Platform adapter abstractions for OCPP.

This module defines a small set of platform-agnostic helpers (a "platform adapter")
that the OCPP core code uses instead of directly importing Home Assistant.

In the Home Assistant integration, we provide a concrete implementation that
calls Home Assistant APIs.
"""

from __future__ import annotations

import logging

from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import Any

from .core_const import CONFIG, DATA_UPDATED, DOMAIN


STATE_OK = "ok"
STATE_UNAVAILABLE = "unavailable"
STATE_UNKNOWN = "unknown"


class PlatformAdapter(ABC):
    """Abstract interface for platform-specific behaviour."""

    @property
    @abstractmethod
    def unit_of_time_minutes(self) -> str:
        """Return the platform's unit for minutes."""

    @abstractmethod
    def get_unit_for_device_class(self, device_class: str) -> str:
        """Return the platform unit for a given device class."""

    @abstractmethod
    def ocpp_unit_to_platform_unit(self, ocpp_unit: str) -> str:
        """Convert an OCPP unit to the platform's equivalent unit."""

    @abstractmethod
    def schedule_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a coroutine to run on the platform event loop."""

    @abstractmethod
    def signal_state_changed(
        self, charge_point_id: str, entity_ids: set[str] | None = None
    ) -> None:
        """Signal that state has changed for a given charge point.

        The platform should refresh its view (e.g. update entities) when this is
        called. If `entity_ids` is provided, it should use that list; otherwise it
        can derive the affected entities itself.
        """

    @abstractmethod
    async def notify_user(self, message: str, title: str = "OCPP") -> bool:
        """Notify the user via the platform (e.g. persistent notification)."""

    @abstractmethod
    def get_config(self) -> dict[str, Any]:
        """Return global configuration for this integration."""

    @abstractmethod
    async def persist_charge_point_config(
        self, charge_point_id: str, data: dict[str, Any]
    ) -> None:
        """Persist updated configuration for a charge point."""

    @abstractmethod
    async def update_device_info(
        self,
        identifiers: set[tuple[str, str]],
        manufacturer: str,
        model: str,
        sw_version: str,
    ) -> None:
        """Update device information in the platform registry."""

    @abstractmethod
    def get_entity_ids_to_refresh(self, charge_point_id: str) -> set[str]:
        """Return entity IDs that should be refreshed for a charge point."""

    @abstractmethod
    def get_metric_fallback(
        self, charge_point_id: str, measurand: str, connector_id: int | None
    ) -> str | None:
        """Return a fallback value for a metric, if available from the platform."""

    @abstractmethod
    async def on_unknown_charge_point(self, cp_id: str) -> None:
        """Handle an unknown charge point (e.g. start discovery)."""

    @abstractmethod
    async def run_in_executor(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking function in the platform executor."""

    @abstractmethod
    def register_service(
        self,
        domain: str,
        service_name: str,
        handler: Any,
        schema: Any = None,
        supports_response: Any = None,
    ) -> None:
        """Register a service with the platform."""


# Keep homeassistant-specific implementation inside the integration layer.
try:
    from homeassistant.components.persistent_notification import DOMAIN as PN_DOMAIN
    from homeassistant.config_entries import ConfigEntry, SOURCE_INTEGRATION_DISCOVERY
    from homeassistant.core import HomeAssistant, SupportsResponse
    from homeassistant.helpers import device_registry, entity_registry
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    class HomeAssistantAdapter(PlatformAdapter):
        """Home Assistant implementation of the PlatformAdapter."""

        def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
            """Initialize the Home Assistant platform adapter."""
            self.hass = hass
            self.entry = entry

        @property
        def unit_of_time_minutes(self) -> str:
            """Return the unit of time for minutes."""
            from homeassistant.const import UnitOfTime

            return UnitOfTime.MINUTES

        def get_unit_for_device_class(self, device_class: str) -> str:
            """Return the unit for a given device class."""
            from homeassistant.components.sensor import SensorDeviceClass
            import homeassistant.const as ha

            # Mapping from device class to unit
            unit_map = {
                SensorDeviceClass.CURRENT: ha.UnitOfElectricCurrent.AMPERE,
                SensorDeviceClass.VOLTAGE: ha.UnitOfElectricPotential.VOLT,
                SensorDeviceClass.FREQUENCY: ha.UnitOfFrequency.HERTZ,
                SensorDeviceClass.BATTERY: ha.PERCENTAGE,
                SensorDeviceClass.POWER: ha.UnitOfPower.KILO_WATT,
                SensorDeviceClass.REACTIVE_POWER: ha.UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
                SensorDeviceClass.ENERGY: ha.UnitOfEnergy.KILO_WATT_HOUR,
                SensorDeviceClass.TEMPERATURE: ha.UnitOfTemperature.CELSIUS,
            }
            return unit_map.get(device_class, "")

        def ocpp_unit_to_platform_unit(self, ocpp_unit: str) -> str:
            """Convert OCPP unit to platform unit."""
            import homeassistant.const as ha
            from ocpp.v16.enums import UnitOfMeasure

            # Mapping from OCPP units to HA units
            unit_map = {
                UnitOfMeasure.wh.value: ha.UnitOfEnergy.WATT_HOUR,
                UnitOfMeasure.kwh.value: ha.UnitOfEnergy.KILO_WATT_HOUR,
                UnitOfMeasure.varh.value: UnitOfMeasure.varh.value,  # No HA equivalent
                UnitOfMeasure.kvarh.value: UnitOfMeasure.kvarh.value,  # No HA equivalent
                UnitOfMeasure.w.value: ha.UnitOfPower.WATT,
                UnitOfMeasure.kw.value: ha.UnitOfPower.KILO_WATT,
                UnitOfMeasure.va.value: ha.UnitOfApparentPower.VOLT_AMPERE,
                UnitOfMeasure.kva.value: UnitOfMeasure.kva.value,  # No HA equivalent
                UnitOfMeasure.var.value: ha.UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
                UnitOfMeasure.kvar.value: UnitOfMeasure.kvar.value,  # No HA equivalent
                UnitOfMeasure.a.value: ha.UnitOfElectricCurrent.AMPERE,
                UnitOfMeasure.v.value: ha.UnitOfElectricPotential.VOLT,
                UnitOfMeasure.celsius.value: ha.UnitOfTemperature.CELSIUS,
                UnitOfMeasure.fahrenheit.value: ha.UnitOfTemperature.FAHRENHEIT,
                UnitOfMeasure.k.value: ha.UnitOfTemperature.KELVIN,
                UnitOfMeasure.percent.value: ha.PERCENTAGE,
            }
            return unit_map.get(ocpp_unit, ocpp_unit)

        def schedule_task(self, coro: Coroutine[Any, Any, Any]) -> None:
            """Schedule a task."""
            self.hass.async_create_task(coro)

        def signal_state_changed(
            self, charge_point_id: str, entity_ids: set[str] | None = None
        ) -> None:
            """Signal that state has changed."""
            if entity_ids is None:
                entity_ids = self.get_entity_ids_to_refresh(charge_point_id)
            async_dispatcher_send(self.hass, DATA_UPDATED, entity_ids)

        async def notify_user(self, message: str, title: str = "OCPP") -> bool:
            """Notify the user."""
            await self.hass.services.async_call(
                PN_DOMAIN,
                "create",
                service_data={"title": title, "message": message},
                blocking=False,
            )
            return True

        def get_config(self) -> dict[str, Any]:
            """Get the configuration."""
            return self.hass.data[DOMAIN].get(CONFIG, {})

        async def persist_charge_point_config(
            self, charge_point_id: str, data: dict[str, Any]
        ) -> None:
            """Persist charge point configuration."""
            # `data` is expected to be the entire config entry data already modified.
            self.hass.config_entries.async_update_entry(self.entry, data=data)

        async def update_device_info(
            self,
            identifiers: set[tuple[str, str]],
            manufacturer: str,
            model: str,
            sw_version: str,
        ) -> None:
            """Update device info."""
            registry = device_registry.async_get(self.hass)
            registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers=identifiers,
                manufacturer=manufacturer,
                model=model,
                sw_version=sw_version,
            )

        def get_entity_ids_to_refresh(self, charge_point_id: str) -> set[str]:
            """Get entity IDs to refresh."""
            er = entity_registry.async_get(self.hass)
            dr = device_registry.async_get(self.hass)

            identifiers = {(DOMAIN, charge_point_id)}
            root_dev = dr.async_get_device(identifiers)
            if root_dev is None:
                return set()

            to_visit = [root_dev.id]
            visited: set[str] = set()
            active_entities: set[str] = set()

            while to_visit:
                dev_id = to_visit.pop(0)
                if dev_id in visited:
                    continue
                visited.add(dev_id)

                for ent in entity_registry.async_entries_for_device(er, dev_id):
                    if getattr(ent, "disabled", False) or getattr(
                        ent, "disabled_by", None
                    ):
                        continue
                    if self.hass.states.get(ent.entity_id) is None:
                        continue
                    active_entities.add(ent.entity_id)

                for dev in dr.devices.values():
                    if dev.via_device_id == dev_id and dev.id not in visited:
                        to_visit.append(dev.id)

            return active_entities

        def get_metric_fallback(
            self, charge_point_id: str, measurand: str, connector_id: int | None
        ) -> str | None:
            """Get metric fallback."""
            base = charge_point_id.lower()
            meas_slug = measurand.lower().replace(".", "_")

            candidates: list[str] = []
            if connector_id and connector_id > 0:
                candidates.append(f"sensor.{base}_connector_{connector_id}_{meas_slug}")
            candidates.append(f"sensor.{base}_{meas_slug}")

            for entity_id in candidates:
                try:
                    st = self.hass.states.get(entity_id)
                except Exception as e:
                    _LOGGER = logging.getLogger(__name__)
                    _LOGGER.debug("Error getting entity %s from HA: %s", entity_id, e)
                    st = None

                if st and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, None):
                    return st.state

            return None

        async def on_unknown_charge_point(self, cp_id: str) -> None:
            """Handle unknown charge point."""
            info = {"cp_id": cp_id, "entry": self.entry}
            await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data=info,
            )

        async def run_in_executor(self, func: Any, *args: Any, **kwargs: Any) -> Any:
            """Run in executor."""
            return await self.hass.async_add_executor_job(func, *args, **kwargs)

        def register_service(
            self,
            domain: str,
            service_name: str,
            handler: Any,
            schema: Any = None,
            supports_response: SupportsResponse = None,
        ) -> None:
            """Register service."""
            self.hass.services.async_register(
                domain, service_name, handler, schema, supports_response
            )

except ImportError:  # pragma: no cover
    # When Home Assistant is not available (e.g. in unit tests), the adapter is
    # not importable, but tests can still use a simple adapter stub.
    pass
