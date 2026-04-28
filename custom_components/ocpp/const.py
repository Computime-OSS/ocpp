"""Home Assistant–specific constants and unit mappings for the OCPP integration.

Platform-agnostic constants and dataclasses live in core_const.py. This module
re-exports them and adds HA-specific mappings (UNITS_OCCP_TO_HA,
DEFAULT_CLASS_UNITS_HA) used by the HA adapter and HA platforms (sensor,
number, config_flow). Core code (chargepoint, ocppv16, ocppv201, platform_adapter
abstract) must not import this module; they use core_const and core_errors.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
import homeassistant.const as ha
from ocpp.v16.enums import UnitOfMeasure

# Re-export everything from core so existing HA code (config_flow, __init__, etc.) keeps working
from .core_const import (  # noqa: F401
    CONF_AUTH_LIST,
    CONF_AUTH_STATUS,
    CONF_CPI,
    CONF_CPID,
    CONF_CPIDS,
    CONF_CSID,
    CONF_DEFAULT_AUTH_STATUS,
    CONF_FORCE_SMART_CHARGING,
    CONF_HOST,
    CONF_ICON,
    CONF_IDLE_INTERVAL,
    CONF_ID_TAG,
    CONF_MAX_CURRENT,
    CONF_METER_INTERVAL,
    CONF_MODE,
    CONF_MONITORED_VARIABLES,
    CONF_MONITORED_VARIABLES_AUTOCONFIG,
    CONF_NAME,
    CONF_NUM_CONNECTORS,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SKIP_SCHEMA_VALIDATION,
    CONF_SMART_CHARGING_GRID_MAX_AMPS_ENTITY,
    CONF_SMART_CHARGING_POLICY_ENABLED,
    CONF_SSL,
    CONF_SSL_CERTFILE_PATH,
    CONF_SSL_KEYFILE_PATH,
    CONF_STEP,
    CONF_SUBPROTOCOL,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_USERNAME,
    CONF_WEBSOCKET_CLOSE_TIMEOUT,
    CONF_WEBSOCKET_PING_INTERVAL,
    CONF_WEBSOCKET_PING_TIMEOUT,
    CONF_WEBSOCKET_PING_TRIES,
    CentralSystemSettings,
    ChargerSystemSettings,
    CONFIG,
    DATA_UPDATED,
    DEFAULT_CPID,
    DEFAULT_CSID,
    DEFAULT_ENERGY_UNIT,
    DEFAULT_FORCE_SMART_CHARGING,
    DEFAULT_HOST,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MEASURAND,
    DEFAULT_METER_INTERVAL,
    DEFAULT_MONITORED_VARIABLES,
    DEFAULT_MONITORED_VARIABLES_AUTOCONFIG,
    DEFAULT_NUM_CONNECTORS,
    DEFAULT_PORT,
    DEFAULT_POWER_UNIT,
    DEFAULT_SKIP_SCHEMA_VALIDATION,
    DEFAULT_SSL,
    DEFAULT_SSL_CERTFILE_PATH,
    DEFAULT_SSL_KEYFILE_PATH,
    DEFAULT_WEBSOCKET_CLOSE_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_INTERVAL,
    DEFAULT_WEBSOCKET_PING_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_TRIES,
    DOMAIN,
    HA_ENERGY_UNIT,
    HA_POWER_UNIT,
    ICON,
    MEASURANDS,
    Measurand,
    NUMBER,
    OCPP_2_0,
    PLATFORMS,
    SENSOR,
    SLEEP_TIME,
    SWITCH,
    BUTTON,
)

# HA-specific: OCPP unit -> HA unit mapping (used by HA adapter and sensor/number)
UNITS_OCCP_TO_HA = {
    UnitOfMeasure.wh: ha.UnitOfEnergy.WATT_HOUR,
    UnitOfMeasure.kwh: ha.UnitOfEnergy.KILO_WATT_HOUR,
    UnitOfMeasure.varh: UnitOfMeasure.varh,
    UnitOfMeasure.kvarh: UnitOfMeasure.kvarh,
    UnitOfMeasure.w: ha.UnitOfPower.WATT,
    UnitOfMeasure.kw: ha.UnitOfPower.KILO_WATT,
    UnitOfMeasure.va: ha.UnitOfApparentPower.VOLT_AMPERE,
    UnitOfMeasure.kva: UnitOfMeasure.kva,
    UnitOfMeasure.var: ha.UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
    UnitOfMeasure.kvar: UnitOfMeasure.kvar,
    UnitOfMeasure.a: ha.UnitOfElectricCurrent.AMPERE,
    UnitOfMeasure.v: ha.UnitOfElectricPotential.VOLT,
    UnitOfMeasure.celsius: ha.UnitOfTemperature.CELSIUS,
    UnitOfMeasure.fahrenheit: ha.UnitOfTemperature.FAHRENHEIT,
    UnitOfMeasure.k: ha.UnitOfTemperature.KELVIN,
    UnitOfMeasure.percent: ha.PERCENTAGE,
}

# HA-specific: device class -> HA unit (used by sensor platform)
DEFAULT_CLASS_UNITS_HA = {
    SensorDeviceClass.CURRENT: ha.UnitOfElectricCurrent.AMPERE,
    SensorDeviceClass.VOLTAGE: ha.UnitOfElectricPotential.VOLT,
    SensorDeviceClass.FREQUENCY: ha.UnitOfFrequency.HERTZ,
    SensorDeviceClass.BATTERY: ha.PERCENTAGE,
    SensorDeviceClass.POWER: ha.UnitOfPower.KILO_WATT,
    SensorDeviceClass.REACTIVE_POWER: ha.UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
    SensorDeviceClass.ENERGY: ha.UnitOfEnergy.KILO_WATT_HOUR,
    SensorDeviceClass.TEMPERATURE: ha.UnitOfTemperature.CELSIUS,
}
