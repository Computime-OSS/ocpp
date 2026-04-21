#!/usr/bin/env python3
"""Standalone OCPP CSMS capability test script (multi-protocol).

Acts as a charge point client against your Central System. Runs separate suites for
OCPP 1.6 (`ocpp1.6`), 2.0.1 (`ocpp2.0.1`), and 2.1 (`ocpp2.1`) so you can verify the
server accepts chargers on each subprotocol.

**1.6:** Authorize, BootNotification, Heartbeat, StatusNotification, MeterValues,
Start/StopTransaction, plus server-initiated GetConfiguration, ChangeConfiguration,
TriggerMessage, SetChargingProfile, ClearChargingProfile, RemoteStart/Stop,
ChangeAvailability (prompted waits where applicable).

**2.0.1 / 2.1:** Same logical coverage using OCPP 2.x message names — GetVariables
fulfills the role of GetConfiguration; RequestStart/StopTransaction map to
RemoteStart/Stop. Client sends smart-charging-related NotifyEVChargingNeeds,
NotifyChargingLimit, NotifyEVChargingSchedule (OCPP 2.0.1 smart charging), plus
TransactionEvent, MeterValues, TriggerMessage (CP-initiated), etc.

**Smart-charging “capability” vs HA:** On **1.6**, when the CSMS requests ``SupportedFeatureProfiles`` via GetConfiguration, this script now answers ``Core,SmartCharging`` (unless overridden by a prior ChangeConfiguration). On **2.x**, the Home Assistant integration sets ``smart_charging_available`` from **NotifyReport** inventory (``SmartChargingCtrlr`` / ``Available``) after **GetBaseReport**. This script answers **GetBaseReport** and sends a minimal **NotifyReport** so that flow can complete; separate Notify* client tests still exercise CSMS handling of those messages.

No Home Assistant dependencies; uses only `ocpp` and `websockets`.

Usage:
    python tests/ocpp_server_capability_test.py

Optional environment:
    OCPP_TARGET_WS_URL       — WebSocket URL; last path segment is replaced per suite unless
                               you use ``{id}`` / ``{cp_id}`` (see below).
    OCPP_CHARGE_POINT_ID     — stem for identities (default TEST-001); each suite uses a
                               distinct id by default: ``<stem>-v16``, ``<stem>-v201``, ``<stem>-v21``
                               so the CSMS does not treat every run as the same protocol.
    OCPP_CHARGE_POINT_ID_16 / OCPP_CHARGE_POINT_ID_201 / OCPP_CHARGE_POINT_ID_21 — optional
                               overrides for a suite’s charge point id (and URL path segment).
    OCPP_RUN_SUITES          — comma list: 1.6, 2.0.1, 2.1 (default: all three)
    OCPP_REPORT_ORGANIZATION, OCPP_REPORT_PRODUCT — report header

Results: tests/ocpp_capability_test_results.json and tests/ocpp_capability_test_report.html.
"""

from __future__ import annotations

import site
import sys
from pathlib import Path


def _ensure_pypi_ocpp_first() -> None:
    """Make ``import ocpp`` resolve to the official PyPI package, not ``custom_components/ocpp``.
    """
    candidates: list[Path] = []
    try:
        candidates.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    candidates.extend(Path(p) for p in site.getsitepackages())
    for root in candidates:
        ocpp_pkg = root / "ocpp"
        if not (ocpp_pkg / "v16").is_dir():
            continue
        if not (ocpp_pkg / "routing.py").is_file():
            continue
        root_s = str(root.resolve())
        while root_s in sys.path:
            sys.path.remove(root_s)
        sys.path.insert(0, root_s)
        return


_ensure_pypi_ocpp_first()

import asyncio
import html
import importlib
import json
import logging
import os
import socket
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP16Base, call, call_result
from ocpp.v16.enums import (
    Action,
    AuthorizationStatus,
    AvailabilityStatus,
    ChargePointErrorCode,
    ChargePointStatus,
    ChargingProfileStatus,
    ClearChargingProfileStatus,
    ConfigurationStatus,
    RegistrationStatus,
    RemoteStartStopStatus,
    TriggerMessageStatus,
)

# -----------------------------------------------------------------------------
# Configuration (change here or via OCPP_* env vars)
# -----------------------------------------------------------------------------
TARGET_WS_URL = os.environ.get("OCPP_TARGET_WS_URL", "ws://192.168.0.174:80/TEST-001")
# Base identity; each protocol suite uses a distinct id by default so the CSMS does not
# treat every connection as the same negotiated OCPP version (see charge_point_id_for_suite).
CHARGE_POINT_ID = os.environ.get("OCPP_CHARGE_POINT_ID", "TEST")
DEFAULT_SUBPROTOCOLS_16 = ["ocpp1.6"]
DEFAULT_SUBPROTOCOLS_201 = ["ocpp2.0.1"]
DEFAULT_SUBPROTOCOLS_21 = ["ocpp2.1"]


def _websocket_url_for_charge_point(ws_url: str, charge_point_identity: str) -> str:
    """Build WebSocket URL whose last path segment is the charge point identity.

    If ``OCPP_TARGET_WS_URL`` contains ``{id}`` or ``{cp_id}``, those are substituted.
    Otherwise the last path segment is replaced (e.g. ``.../TEST-001`` → ``.../TEST-v201``).
    """
    if "{id}" in ws_url or "{cp_id}" in ws_url:
        return ws_url.replace("{id}", charge_point_identity).replace("{cp_id}", charge_point_identity)
    parts = urlsplit(ws_url)
    segments = [s for s in (parts.path or "").split("/") if s]
    if segments:
        segments[-1] = charge_point_identity
        new_path = "/" + "/".join(segments)
    else:
        new_path = f"/{charge_point_identity}"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def charge_point_id_for_suite(suite_key: str) -> str:
    """Return charge point id for suite ``1.6`` | ``2.0.1`` | ``2.1``.

    Override with ``OCPP_CHARGE_POINT_ID_16``, ``OCPP_CHARGE_POINT_ID_201``, ``OCPP_CHARGE_POINT_ID_21``.
    Defaults: ``<stem>-v16``, ``<stem>-v201``, ``<stem>-v21`` where stem is ``OCPP_CHARGE_POINT_ID``.
    """
    stem = CHARGE_POINT_ID.strip() or "TEST-001"
    env_names = {
        "1.6": "OCPP_CHARGE_POINT_ID_16",
        "2.0.1": "OCPP_CHARGE_POINT_ID_201",
        "2.1": "OCPP_CHARGE_POINT_ID_21",
    }
    env_key = env_names.get(suite_key)
    if env_key:
        override = os.environ.get(env_key, "").strip()
        if override:
            return override
    defaults = {
        "1.6": f"{stem}-v16",
        "2.0.1": f"{stem}-v201",
        "2.1": f"{stem}-v21",
    }
    return defaults.get(suite_key, stem)


def target_ws_url_for_suite(suite_key: str) -> str:
    """WebSocket URL for this suite (path segment matches ``charge_point_id_for_suite``)."""
    return _websocket_url_for_charge_point(TARGET_WS_URL, charge_point_id_for_suite(suite_key))


def _parse_run_suites() -> list[str]:
    """Return suite keys to run: '1.6', '2.0.1', '2.1' (default: all three, order 1.6 → 2.0.1 → 2.1)."""
    default_order = "1.6,2.0.1,2.1"
    raw = os.environ.get("OCPP_RUN_SUITES", default_order).strip().lower()
    allowed = {"1.6", "2.0.1", "2.1"}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out = [p for p in parts if p in allowed]
    return out if out else ["1.6", "2.0.1", "2.1"]


RUN_SUITES = _parse_run_suites()

RESULTS_FILE = Path(__file__).resolve().parent / "ocpp_capability_test_results.json"
REPORT_HTML_FILE = Path(__file__).resolve().parent / "ocpp_capability_test_report.html"
# Optional: set OCPP_REPORT_ORGANIZATION / OCPP_REPORT_PRODUCT for the HTML/PDF-style header.
REPORT_ORGANIZATION = os.environ.get("OCPP_REPORT_ORGANIZATION", "Computime")
REPORT_PRODUCT_UNDER_TEST = os.environ.get(
    "OCPP_REPORT_PRODUCT",
    "Central System (CSMS) — capability verification",
)
TEST_TIMEOUT_SECONDS = 60
# Max wait after each prompt for the server to send that OCPP action (seconds).
SERVER_ACTION_WAIT_SECONDS = TEST_TIMEOUT_SECONDS

# After client tests: prompt and wait (in order) for these server-initiated calls.
# GetConfiguration is not listed here — the CSMS usually sends it during post_connect
# (same window as ChangeConfiguration); the handler still records it when received.
# Actions already received earlier are skipped.
SERVER_PROMPT_SEQUENCE: list[str] = [
    "RemoteStartTransaction",
    "RemoteStopTransaction",
    "ClearChargingProfile",
    "ChangeAvailability",
    "SetChargingProfile",
]

SERVER_ACTION_USER_INSTRUCTIONS: dict[str, str] = {
    "RemoteStartTransaction": (
        "In your CSMS (e.g. Home Assistant OCPP), trigger remote start / "
        "RemoteStartTransaction for this charge point (connector 1 if asked). "
        "After Accepted, this script emulates an active charging session "
        "(OCPP 1.6: StartTransaction + StatusNotification charging; "
        "OCPP 2.x: TransactionEvent Started with charging_state=Charging) so the CSMS "
        "shows an active session (e.g. Home Assistant Charge Control switch on)."
    ),
    "RemoteStopTransaction": (
        "After Remote Start above, trigger remote stop / RemoteStopTransaction for "
        "that session. The script then ends the session "
        "(1.6: StopTransaction + StatusNotification available; "
        "2.x: TransactionEvent Ended + idle)."
    ),
    "ClearChargingProfile": (
        "In your CSMS, send ClearChargingProfile for this charge point (connector 1 "
        "or as your UI allows)."
    ),
    "ChangeAvailability": (
        "In your CSMS, send ChangeAvailability (e.g. Operative/Inoperative) for "
        "this charge point or a connector."
    ),
    "SetChargingProfile": (
        "In your CSMS, set a charging profile / send SetChargingProfile for this "
        "charge point."
    ),
}

# -----------------------------------------------------------------------------
# Logging: prefix each line with [TEST: <action>] when testing that action
# -----------------------------------------------------------------------------
LOG_ACTION: str | None = None


class TestFilter(logging.Filter):
    """Add current test action to log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if LOG_ACTION:
            record.msg = f"[TEST: {LOG_ACTION}] {record.msg}"
        return True


def setup_logging() -> logging.Logger:
    """Configure logger with test-action prefix support."""
    log = logging.getLogger("ocpp_capability_test")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setLevel(logging.INFO)
        h.addFilter(TestFilter())
        log.addHandler(h)
    return log


LOGGER = setup_logging()

# Set before each suite run so record_result can attach the correct protocol section.
CURRENT_SUITE: str = "OCPP 1.6"


# -----------------------------------------------------------------------------
# Test result storage
# -----------------------------------------------------------------------------
def record_result(
    results: list[dict],
    action: str,
    passed: bool,
    message: str = "",
    category: str = "client_sent",
    suite: str | None = None,
) -> None:
    """Record or merge one test result by (suite, action).

    If the same OCPP action is exercised more than once (e.g. server sends
    GetConfiguration twice), update the existing row instead of appending so
    totals and the JSON report do not double-count. Pass status is merged with
    OR: any successful attempt marks the action passed. On first failure then
    success, message updates to the success text; a later failure does not
    clear a prior pass.
    category: 'client_sent' | 'server_sent' | 'smart_charging' (kept from first record).
    """
    s = suite if suite is not None else CURRENT_SUITE
    for row in results:
        if row["action"] == action and row.get("suite") == s:
            prev_passed = row["passed"]
            row["passed"] = prev_passed or passed
            if passed and not prev_passed:
                row["message"] = message
            elif passed and prev_passed:
                pass  # keep first success message
            elif not row["passed"]:
                row["message"] = message
            return
    results.append({
        "suite": s,
        "action": action,
        "passed": passed,
        "message": message,
        "category": category,
    })


# -----------------------------------------------------------------------------
# OCPP 1.6 Charge Point client (test stub)
# -----------------------------------------------------------------------------
class TestChargePoint(CP16Base):
    """OCPP 1.6 charge point client that records server capability test results."""

    def __init__(self, charge_point_id: str, websocket: websockets.WebSocketClientProtocol, results: list[dict]):
        super().__init__(charge_point_id, websocket)
        self.results = results
        self.active_transaction_id: int = 0
        # Persist keys set via ChangeConfiguration so GetConfiguration matches (OCPP post_connect).
        self._config: dict[str, str] = {}
        self._server_action_events: dict[str, asyncio.Event] = {
            k: asyncio.Event() for k in SERVER_ACTION_USER_INSTRUCTIONS
        }
        self._remote_session_connector_id: int = 1
        self._follow_up_tasks: list[asyncio.Task[Any]] = []

    def _get_configuration_value(self, key: str) -> str:
        """Return config value for GetConfiguration; persisted ChangeConfiguration overrides.

        ``SupportedFeatureProfiles`` defaults to Core+SmartCharging so the CSMS (e.g. this
        integration) detects smart-charging support like a real charger; see ocppv16
        ``get_supported_features`` and ``feature_profile_smart`` = ``SmartCharging``.
        """
        if key in self._config:
            return self._config[key]
        if key == "SupportedFeatureProfiles":
            return "Core,SmartCharging"
        return "test_value"

    def _schedule_coro(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run async follow-up from synchronous @on handlers (RemoteStart/RemoteStop)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            LOGGER.warning("No running event loop; cannot run async OCPP follow-up.")
            return
        task = loop.create_task(coro)
        self._follow_up_tasks.append(task)

    def _notify_server_action(self, action: str) -> None:
        """Signal waiters that this server-initiated action was handled."""
        ev = self._server_action_events.get(action)
        if ev is not None:
            ev.set()

    async def _begin_session_from_remote_start(self, id_tag: str, connector_id: int) -> None:
        """After RemoteStart Accepted: send StartTransaction so CSMS tracks a session for RemoteStop."""
        global LOG_ACTION
        LOG_ACTION = "RemoteStart→StartTransaction"
        self._remote_session_connector_id = connector_id
        LOGGER.info(
            "Fake session: sending StartTransaction after RemoteStart (id_tag=%s, connector=%s).",
            id_tag,
            connector_id,
        )
        try:
            req = call.StartTransaction(
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=1000,
                timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            resp = await self.call(req)
            self.active_transaction_id = resp.transaction_id
            status = resp.id_tag_info.get("status") if resp.id_tag_info else None
            if status == AuthorizationStatus.accepted:
                LOGGER.info(
                    "Fake session active: transaction_id=%s — use RemoteStop in CSMS to end.",
                    self.active_transaction_id,
                )
                await self.call(
                    call.StatusNotification(
                        connector_id=connector_id,
                        error_code=ChargePointErrorCode.no_error,
                        status=ChargePointStatus.charging,
                        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                )
            else:
                LOGGER.warning(
                    "StartTransaction after RemoteStart not accepted: %s",
                    status,
                )
        except Exception:
            LOGGER.exception("Begin fake session after RemoteStart failed")
        finally:
            LOG_ACTION = None

    async def _end_session_from_remote_stop(self, transaction_id: int | None) -> None:
        """After RemoteStop Accepted: send StopTransaction so CSMS clears the session."""
        global LOG_ACTION
        LOG_ACTION = "RemoteStop→StopTransaction"
        tid: int | None = transaction_id
        if tid in (None, 0) and self.active_transaction_id:
            tid = self.active_transaction_id
        if not tid:
            LOGGER.warning("RemoteStop received but no transaction_id to stop.")
            LOG_ACTION = None
            return
        LOGGER.info(
            "Fake session: sending StopTransaction after RemoteStop (transaction_id=%s).",
            tid,
        )
        try:
            await self.call(
                call.StopTransaction(
                    meter_stop=2000,
                    timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    transaction_id=int(tid),
                    reason="Remote",
                    id_tag="test_tag",
                )
            )
            self.active_transaction_id = 0
            await self.call(
                call.StatusNotification(
                    connector_id=self._remote_session_connector_id,
                    error_code=ChargePointErrorCode.no_error,
                    status=ChargePointStatus.available,
                    timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
            LOGGER.info("Fake session ended after RemoteStop.")
        except Exception:
            LOGGER.exception("End fake session after RemoteStop failed")
        finally:
            LOG_ACTION = None

    # ----- Server-initiated: record when we receive and respond -----
    @on(Action.get_configuration)
    def on_get_configuration(self, key: list[str] | None = None, **kwargs) -> call_result.GetConfiguration:
        global LOG_ACTION
        LOG_ACTION = "GetConfiguration"
        LOGGER.info("Received GetConfiguration from server; responding.")
        keys = key or []
        if not keys:
            out = call_result.GetConfiguration(configuration_key=[])
        else:
            config_list = [
                {
                    "key": k,
                    "readonly": False,
                    "value": self._get_configuration_value(k),
                }
                for k in keys
            ]
            out = call_result.GetConfiguration(configuration_key=config_list)
        record_result(self.results, "GetConfiguration", True, "Received and responded", "server_sent")
        self._notify_server_action("GetConfiguration")
        LOG_ACTION = None
        return out

    @on(Action.change_configuration)
    def on_change_configuration(self, key: str, value: str, **kwargs) -> call_result.ChangeConfiguration:
        """OCPP post_connect calls ChangeConfiguration for measurands and meter intervals."""
        global LOG_ACTION
        LOG_ACTION = "ChangeConfiguration"
        self._config[key] = value
        LOGGER.info(
            "Received ChangeConfiguration from server; key=%s — responding Accepted.",
            key,
        )
        record_result(self.results, "ChangeConfiguration", True, "Accepted", "server_sent")
        LOG_ACTION = None
        return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)

    @on(Action.trigger_message)
    def on_trigger_message(self, requested_message, **kwargs) -> call_result.TriggerMessage:
        """OCPP may send TriggerMessage after connect (BootNotification / StatusNotification)."""
        global LOG_ACTION
        LOG_ACTION = "TriggerMessage"
        LOGGER.info(
            "Received TriggerMessage from server; requested=%s — responding Accepted.",
            requested_message,
        )
        record_result(self.results, "TriggerMessage", True, "Accepted", "server_sent")
        LOG_ACTION = None
        return call_result.TriggerMessage(status=TriggerMessageStatus.accepted)

    @on(Action.set_charging_profile)
    def on_set_charging_profile(self, **kwargs) -> call_result.SetChargingProfile:
        global LOG_ACTION
        LOG_ACTION = "SetChargingProfile"
        LOGGER.info("Received SetChargingProfile from server; responding Accepted.")
        record_result(self.results, "SetChargingProfile", True, "Received and responded", "server_sent")
        self._notify_server_action("SetChargingProfile")
        LOG_ACTION = None
        return call_result.SetChargingProfile(ChargingProfileStatus.accepted)

    @on(Action.clear_charging_profile)
    def on_clear_charging_profile(self, **kwargs) -> call_result.ClearChargingProfile:
        global LOG_ACTION
        LOG_ACTION = "ClearChargingProfile"
        LOGGER.info("Received ClearChargingProfile from server; responding Accepted.")
        record_result(self.results, "ClearChargingProfile", True, "Received and responded", "server_sent")
        self._notify_server_action("ClearChargingProfile")
        LOG_ACTION = None
        return call_result.ClearChargingProfile(ClearChargingProfileStatus.accepted)

    @on(Action.remote_start_transaction)
    def on_remote_start_transaction(self, id_tag: str | None = None, connector_id: int | None = None, **kwargs) -> call_result.RemoteStartTransaction:
        global LOG_ACTION
        LOG_ACTION = "RemoteStartTransaction"
        LOGGER.info("Received RemoteStartTransaction from server; responding Accepted.")
        record_result(self.results, "RemoteStartTransaction", True, "Received and responded", "server_sent")
        self._notify_server_action("RemoteStartTransaction")
        id_resolved = id_tag or "remote_start"
        cid = connector_id if connector_id is not None else 1
        self._schedule_coro(self._begin_session_from_remote_start(id_resolved, cid))
        LOG_ACTION = None
        return call_result.RemoteStartTransaction(RemoteStartStopStatus.accepted)

    @on(Action.remote_stop_transaction)
    def on_remote_stop_transaction(self, transaction_id: int | None = None, **kwargs) -> call_result.RemoteStopTransaction:
        global LOG_ACTION
        LOG_ACTION = "RemoteStopTransaction"
        LOGGER.info("Received RemoteStopTransaction from server; responding Accepted.")
        record_result(self.results, "RemoteStopTransaction", True, "Received and responded", "server_sent")
        self._notify_server_action("RemoteStopTransaction")
        self._schedule_coro(self._end_session_from_remote_stop(transaction_id))
        LOG_ACTION = None
        return call_result.RemoteStopTransaction(RemoteStartStopStatus.accepted)

    @on(Action.change_availability)
    def on_change_availability(self, connector_id: int | None = None, type: str | None = None, **kwargs) -> call_result.ChangeAvailability:
        global LOG_ACTION
        LOG_ACTION = "ChangeAvailability"
        LOGGER.info("Received ChangeAvailability from server; responding Accepted.")
        record_result(self.results, "ChangeAvailability", True, "Received and responded", "server_sent")
        self._notify_server_action("ChangeAvailability")
        LOG_ACTION = None
        return call_result.ChangeAvailability(AvailabilityStatus.accepted)

    # ----- Client-initiated: send and verify response -----
    async def test_boot_notification(self) -> None:
        global LOG_ACTION
        LOG_ACTION = "BootNotification"
        LOGGER.info("Sending BootNotification.")
        try:
            req = call.BootNotification(
                charge_point_vendor="CapabilityTest",
                charge_point_model="Script",
            )
            resp = await self.call(req)
            if resp.status == RegistrationStatus.accepted:
                record_result(self.results, "BootNotification", True, "Server accepted")
            else:
                record_result(self.results, "BootNotification", False, f"Server status: {resp.status}")
        except Exception as e:
            LOGGER.exception("BootNotification failed")
            record_result(self.results, "BootNotification", False, str(e))
        LOG_ACTION = None

    async def test_authorize(self) -> None:
        global LOG_ACTION
        LOG_ACTION = "Authorize"
        LOGGER.info("Sending Authorize.")
        try:
            req = call.Authorize(id_tag="test_tag")
            resp = await self.call(req)
            status = resp.id_tag_info.get("status") if resp.id_tag_info else None
            if status == AuthorizationStatus.accepted:
                record_result(self.results, "Authorize", True, "Server accepted")
            else:
                record_result(self.results, "Authorize", False, f"id_tag_info status: {status}")
        except Exception as e:
            LOGGER.exception("Authorize failed")
            record_result(self.results, "Authorize", False, str(e))
        LOG_ACTION = None

    async def test_heartbeat(self) -> None:
        global LOG_ACTION
        LOG_ACTION = "Heartbeat"
        LOGGER.info("Sending Heartbeat.")
        try:
            req = call.Heartbeat()
            resp = await self.call(req)
            if resp.current_time:
                record_result(self.results, "Heartbeat", True, "Server returned currentTime")
            else:
                record_result(self.results, "Heartbeat", False, "Missing currentTime")
        except Exception as e:
            LOGGER.exception("Heartbeat failed")
            record_result(self.results, "Heartbeat", False, str(e))
        LOG_ACTION = None

    async def test_status_notification(self) -> None:
        global LOG_ACTION
        LOG_ACTION = "StatusNotification"
        LOGGER.info("Sending StatusNotification.")
        try:
            req = call.StatusNotification(
                connector_id=1,
                error_code=ChargePointErrorCode.no_error,
                status=ChargePointStatus.available,
                timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            resp = await self.call(req)
            record_result(self.results, "StatusNotification", True, "Server responded")
        except Exception as e:
            LOGGER.exception("StatusNotification failed")
            record_result(self.results, "StatusNotification", False, str(e))
        LOG_ACTION = None

    async def test_meter_values(self) -> None:
        global LOG_ACTION
        LOG_ACTION = "MeterValues"
        LOGGER.info("Sending MeterValues.")
        try:
            req = call.MeterValues(
                connector_id=1,
                transaction_id=0,
                meter_value=[
                    {
                        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "sampledValue": [
                            {"value": "1000", "context": "Sample.Periodic", "measurand": "Energy.Active.Import.Register", "unit": "Wh"},
                            {"value": "0", "context": "Sample.Periodic", "measurand": "Current.Import", "unit": "A"},
                        ],
                    }
                ],
            )
            resp = await self.call(req)
            record_result(self.results, "MeterValues", True, "Server responded")
        except Exception as e:
            LOGGER.exception("MeterValues failed")
            record_result(self.results, "MeterValues", False, str(e))
        LOG_ACTION = None

    async def test_start_transaction(self) -> None:
        """Send StartTransaction (enables server to track transaction for RemoteStop)."""
        global LOG_ACTION
        LOG_ACTION = "StartTransaction"
        LOGGER.info("Sending StartTransaction.")
        try:
            req = call.StartTransaction(
                connector_id=1,
                id_tag="test_tag",
                meter_start=1000,
                timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            resp = await self.call(req)
            self.active_transaction_id = resp.transaction_id
            status = resp.id_tag_info.get("status") if resp.id_tag_info else None
            if status == AuthorizationStatus.accepted:
                record_result(self.results, "StartTransaction", True, f"transaction_id={resp.transaction_id}")
            else:
                record_result(self.results, "StartTransaction", False, f"id_tag_info status: {status}")
        except Exception as e:
            LOGGER.exception("StartTransaction failed")
            record_result(self.results, "StartTransaction", False, str(e))
        LOG_ACTION = None

    async def test_stop_transaction(self) -> None:
        """Send StopTransaction."""
        global LOG_ACTION
        LOG_ACTION = "StopTransaction"
        LOGGER.info("Sending StopTransaction.")
        try:
            req = call.StopTransaction(
                meter_stop=2000,
                timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                transaction_id=self.active_transaction_id,
                reason="Local",
                id_tag="test_tag",
            )
            resp = await self.call(req)
            status = resp.id_tag_info.get("status") if resp.id_tag_info else None
            if status == AuthorizationStatus.accepted:
                record_result(self.results, "StopTransaction", True, "Server accepted")
                self.active_transaction_id = 0
            else:
                record_result(self.results, "StopTransaction", False, f"id_tag_info status: {status}")
        except Exception as e:
            LOGGER.exception("StopTransaction failed")
            record_result(self.results, "StopTransaction", False, str(e))
        LOG_ACTION = None


def _ocpp2_module_for_suite(suite_label: str) -> ModuleType:
    """Load ocpp.v201 or ocpp.v21 package for the given suite label."""
    if suite_label == "OCPP 2.0.1":
        return importlib.import_module("ocpp.v201")
    if suite_label == "OCPP 2.1":
        return importlib.import_module("ocpp.v21")
    raise ValueError(f"Unknown OCPP 2.x suite: {suite_label}")


def build_ocpp2_test_charge_point_class(pkg: ModuleType) -> type:
    """Build a ChargePoint test client subclass for OCPP 2.0.1 or 2.1 (shared logic)."""
    call2 = pkg.call
    cr2 = pkg.call_result
    dt = pkg.datatypes
    en = pkg.enums
    Action = en.Action

    def _payload_field(obj: Any, snake: str, camel: str | None = None) -> Any:
        """Read a field from an OCPP payload that may be a dict (camelCase) or dataclass."""
        if isinstance(obj, dict):
            if snake in obj:
                return obj[snake]
            if camel and camel in obj:
                return obj[camel]
            return None
        out = getattr(obj, snake, None)
        if out is not None or hasattr(obj, snake):
            return out
        return getattr(obj, camel, None) if camel else None

    # Home Assistant OCPP (ocppv201.on_authorize) only runs get_authorization_status for
    # ISO14443 / ISO15693 / Central. Use ISO14443 so default_authorization_status applies.
    # OCPP 2.1: IdToken.type is a plain string (no IdTokenEnumType in v21.enums).
    id_token_type_authorize: Any = (
        en.IdTokenEnumType.iso14443 if hasattr(en, "IdTokenEnumType") else "ISO14443"
    )

    def _parse_component_variable(
        item: Any,
    ) -> tuple[dt.ComponentType, dt.VariableType] | None:
        """Build ``ComponentType`` / ``VariableType`` from dict or dataclass (library may pass either)."""
        comp_raw = _payload_field(item, "component")
        var_raw = _payload_field(item, "variable")
        if comp_raw is None or var_raw is None:
            return None
        comp_name = _payload_field(comp_raw, "name")
        var_name = _payload_field(var_raw, "name")
        if comp_name is None or var_name is None:
            return None
        comp_inst = _payload_field(comp_raw, "instance")
        var_inst = _payload_field(var_raw, "instance")
        return (
            dt.ComponentType(name=str(comp_name), instance=comp_inst),
            dt.VariableType(name=str(var_name), instance=var_inst),
        )

    class TestChargePointOcpp2(pkg.ChargePoint):  # type: ignore[misc, name-defined]
        """OCPP 2.x charge point client; records CSMS capability results."""

        def __init__(
            self,
            charge_point_id: str,
            websocket: websockets.WebSocketClientProtocol,
            results: list[dict],
            suite_label: str,
        ) -> None:
            super().__init__(charge_point_id, websocket)
            self.results = results
            self._suite = suite_label
            self.active_transaction_id: str = ""
            self._variables: dict[tuple[str, str | None, str | None], str] = {}
            self._server_action_events: dict[str, asyncio.Event] = {
                k: asyncio.Event() for k in SERVER_ACTION_USER_INSTRUCTIONS
            }
            self._remote_evse_id: int = 1
            self._follow_up_tasks: list[asyncio.Task[Any]] = []

        def _schedule_coro(self, coro: Coroutine[Any, Any, None]) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                LOGGER.warning("No running event loop; cannot run async OCPP follow-up.")
                return
            self._follow_up_tasks.append(loop.create_task(coro))

        async def _notify_report_after_get_base(self, request_id: int) -> None:
            """Send minimal inventory so HA can mark SmartChargingCtrlr / Available after GetBaseReport."""
            await asyncio.sleep(0.05)
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                await self.call(
                    call2.NotifyReport(
                        request_id=request_id,
                        generated_at=ts,
                        seq_no=0,
                        report_data=[
                            dt.ReportDataType(
                                component=dt.ComponentType(name="SmartChargingCtrlr"),
                                variable=dt.VariableType(name="Available"),
                                variable_attribute=[
                                    dt.VariableAttributeType(
                                        type=en.AttributeEnumType.actual,
                                        value="true",
                                    )
                                ],
                            )
                        ],
                        tbc=False,
                    )
                )
            except Exception:
                LOGGER.exception("NotifyReport after GetBaseReport failed")

        def _notify_server_action(self, action: str) -> None:
            ev = self._server_action_events.get(action)
            if ev is not None:
                ev.set()

        async def _transaction_started_after_request_start(
            self,
            id_token: dt.IdTokenType,
            remote_start_id: int,
            evse_id: int,
            transaction_id: str | None,
        ) -> None:
            global LOG_ACTION
            LOG_ACTION = "RequestStartTransaction→TransactionEvent"
            tid = transaction_id or "cap-tx-1"
            self.active_transaction_id = tid
            self._remote_evse_id = evse_id
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                # HA Charge Control uses connector status; it only turns on for
                # charging / suspended_* (see switch.py). ev_connected maps to Preparing.
                await self.call(
                    call2.TransactionEvent(
                        event_type=en.TransactionEventEnumType.started,
                        timestamp=ts,
                        trigger_reason=en.TriggerReasonEnumType.remote_start,
                        seq_no=1,
                        transaction_info=dt.TransactionType(
                            transaction_id=tid,
                            charging_state=en.ChargingStateEnumType.charging,
                        ),
                        evse=dt.EVSEType(id=evse_id, connector_id=1),
                        id_token=id_token,
                    )
                )
            except Exception:
                LOGGER.exception("TransactionEvent(Started) after RequestStart failed")
            finally:
                LOG_ACTION = None

        async def _transaction_ended_after_request_stop(self, transaction_id: str | None) -> None:
            global LOG_ACTION
            LOG_ACTION = "RequestStopTransaction→TransactionEvent"
            tid = transaction_id or self.active_transaction_id
            if not tid:
                LOG_ACTION = None
                return
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                await self.call(
                    call2.TransactionEvent(
                        event_type=en.TransactionEventEnumType.ended,
                        timestamp=ts,
                        trigger_reason=en.TriggerReasonEnumType.remote_stop,
                        seq_no=2,
                        transaction_info=dt.TransactionType(
                            transaction_id=tid,
                            charging_state=en.ChargingStateEnumType.idle,
                        ),
                        evse=dt.EVSEType(id=self._remote_evse_id, connector_id=1),
                    )
                )
                self.active_transaction_id = ""
            except Exception:
                LOGGER.exception("TransactionEvent(Ended) after RequestStop failed")
            finally:
                LOG_ACTION = None

        @on(Action.get_base_report)
        def on_get_base_report(self, **kwargs) -> cr2.GetBaseReport:
            rid = int(kwargs.get("request_id") or 0)
            self._schedule_coro(self._notify_report_after_get_base(rid))
            return cr2.GetBaseReport(status=en.GenericDeviceModelStatusEnumType.accepted)

        @on(Action.update_firmware)
        def on_update_firmware(self, **kwargs) -> cr2.UpdateFirmware:
            LOGGER.info("Received UpdateFirmware from server; responding Rejected (dummy URL probe).")
            return cr2.UpdateFirmware(status=en.UpdateFirmwareStatusEnumType.rejected)

        @on(Action.get_variables)
        def on_get_variables(self, **kwargs) -> cr2.GetVariables:
            global LOG_ACTION
            LOG_ACTION = "GetVariables"
            get_variable_data = kwargs.get("get_variable_data") or []
            gvr: list[dt.GetVariableResultType] = []
            for gvd in get_variable_data:
                parsed = _parse_component_variable(gvd)
                if parsed is None:
                    continue
                comp, var = parsed
                key = (comp.name, comp.instance, var.name)
                val = self._variables.get(key, "test_value")
                gvr.append(
                    dt.GetVariableResultType(
                        attribute_status=en.GetVariableStatusEnumType.accepted,
                        component=comp,
                        variable=var,
                        attribute_value=val,
                    )
                )
            LOGGER.info("Received GetVariables from server; responding (%s result(s)).", len(gvr))
            record_result(
                self.results,
                "GetConfiguration",
                True,
                "OCPP 2.x: GetVariables received and responded",
                "server_sent",
                suite=self._suite,
            )
            self._notify_server_action("GetConfiguration")
            LOG_ACTION = None
            return cr2.GetVariables(get_variable_result=gvr)

        @on(Action.set_variables)
        def on_set_variables(self, **kwargs) -> cr2.SetVariables:
            global LOG_ACTION
            LOG_ACTION = "SetVariables"
            set_variable_data = kwargs.get("set_variable_data") or []
            results: list[dt.SetVariableResultType] = []
            for svd in set_variable_data:
                parsed = _parse_component_variable(svd)
                if parsed is None:
                    continue
                comp, var = parsed
                av = _payload_field(svd, "attribute_value", "attributeValue")
                if av is None:
                    av = ""
                key = (comp.name, comp.instance, var.name)
                self._variables[key] = str(av)
                results.append(
                    dt.SetVariableResultType(
                        attribute_status=en.SetVariableStatusEnumType.accepted,
                        component=comp,
                        variable=var,
                    )
                )
            LOGGER.info("Received SetVariables from server; %s variable(s).", len(results))
            record_result(
                self.results,
                "ChangeConfiguration",
                True,
                "OCPP 2.x: SetVariables accepted",
                "server_sent",
                suite=self._suite,
            )
            LOG_ACTION = None
            return cr2.SetVariables(set_variable_result=results)

        @on(Action.trigger_message)
        def on_trigger_message(self, **kwargs) -> cr2.TriggerMessage:
            global LOG_ACTION
            LOG_ACTION = "TriggerMessage"
            rm = kwargs.get("requested_message")
            LOGGER.info("Received TriggerMessage from server; requested=%s — responding Accepted.", rm)
            record_result(
                self.results,
                "TriggerMessage",
                True,
                "Accepted",
                "server_sent",
                suite=self._suite,
            )
            LOG_ACTION = None
            return cr2.TriggerMessage(status=en.TriggerMessageStatusEnumType.accepted)

        @on(Action.set_charging_profile)
        def on_set_charging_profile(self, **kwargs) -> cr2.SetChargingProfile:
            global LOG_ACTION
            LOG_ACTION = "SetChargingProfile"
            LOGGER.info("Received SetChargingProfile from server; responding Accepted.")
            record_result(
                self.results,
                "SetChargingProfile",
                True,
                "Received and responded",
                "server_sent",
                suite=self._suite,
            )
            self._notify_server_action("SetChargingProfile")
            LOG_ACTION = None
            return cr2.SetChargingProfile(status=en.ChargingProfileStatusEnumType.accepted)

        @on(Action.clear_charging_profile)
        def on_clear_charging_profile(self, **kwargs) -> cr2.ClearChargingProfile:
            global LOG_ACTION
            LOG_ACTION = "ClearChargingProfile"
            LOGGER.info("Received ClearChargingProfile from server; responding Accepted.")
            record_result(
                self.results,
                "ClearChargingProfile",
                True,
                "Received and responded",
                "server_sent",
                suite=self._suite,
            )
            self._notify_server_action("ClearChargingProfile")
            LOG_ACTION = None
            return cr2.ClearChargingProfile(status=en.ClearChargingProfileStatusEnumType.accepted)

        @on(Action.request_start_transaction)
        def on_request_start_transaction(self, **kwargs) -> cr2.RequestStartTransaction:
            global LOG_ACTION
            LOG_ACTION = "RequestStartTransaction"
            id_token = kwargs.get("id_token")
            remote_start_id = int(kwargs.get("remote_start_id") or 0)
            evse_id = kwargs.get("evse_id")
            if evse_id is None:
                evse_id = 1
            LOGGER.info("Received RequestStartTransaction from server; responding Accepted.")
            record_result(
                self.results,
                "RemoteStartTransaction",
                True,
                "OCPP 2.x: RequestStartTransaction received and responded",
                "server_sent",
                suite=self._suite,
            )
            self._notify_server_action("RemoteStartTransaction")
            tid = str(remote_start_id or 9001)
            start_id_token: dt.IdTokenType
            if isinstance(id_token, dt.IdTokenType):
                start_id_token = id_token
            elif isinstance(id_token, dict):
                token_value = (
                    id_token.get("id_token")
                    or id_token.get("idToken")
                    or id_token.get("value")
                    or "remote_start"
                )
                token_type = id_token.get("type") or "Central"
                start_id_token = dt.IdTokenType(
                    id_token=str(token_value),
                    type=str(token_type),
                )
            else:
                # Keep the fake charging-session flow robust even if payload shape differs.
                start_id_token = dt.IdTokenType(id_token="remote_start", type="Central")
            self._schedule_coro(
                self._transaction_started_after_request_start(
                    start_id_token,
                    remote_start_id,
                    int(evse_id),
                    tid,
                )
            )
            LOG_ACTION = None
            return cr2.RequestStartTransaction(
                status=en.RequestStartStopStatusEnumType.accepted,
                transaction_id=tid,
            )

        @on(Action.request_stop_transaction)
        def on_request_stop_transaction(self, **kwargs) -> cr2.RequestStopTransaction:
            global LOG_ACTION
            LOG_ACTION = "RequestStopTransaction"
            tid = kwargs.get("transaction_id")
            LOGGER.info("Received RequestStopTransaction from server; responding Accepted.")
            record_result(
                self.results,
                "RemoteStopTransaction",
                True,
                "OCPP 2.x: RequestStopTransaction received and responded",
                "server_sent",
                suite=self._suite,
            )
            self._notify_server_action("RemoteStopTransaction")
            self._schedule_coro(self._transaction_ended_after_request_stop(str(tid) if tid else None))
            LOG_ACTION = None
            return cr2.RequestStopTransaction(status=en.RequestStartStopStatusEnumType.accepted)

        @on(Action.change_availability)
        def on_change_availability(self, **kwargs) -> cr2.ChangeAvailability:
            global LOG_ACTION
            LOG_ACTION = "ChangeAvailability"
            LOGGER.info("Received ChangeAvailability from server; responding Accepted.")
            record_result(
                self.results,
                "ChangeAvailability",
                True,
                "Received and responded",
                "server_sent",
                suite=self._suite,
            )
            self._notify_server_action("ChangeAvailability")
            LOG_ACTION = None
            return cr2.ChangeAvailability(status=en.ChangeAvailabilityStatusEnumType.accepted)

        async def test_boot_notification(self) -> None:
            global LOG_ACTION
            LOG_ACTION = "BootNotification"
            LOGGER.info("Sending BootNotification (OCPP 2.x).")
            try:
                req = call2.BootNotification(
                    charging_station=dt.ChargingStationType(
                        vendor_name="CapabilityTest",
                        model="Script",
                    ),
                    reason=en.BootReasonEnumType.power_up,
                )
                resp = await self.call(req)
                if resp is None:
                    record_result(
                        self.results,
                        "BootNotification",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        suite=self._suite,
                    )
                elif resp.status == en.RegistrationStatusEnumType.accepted:
                    record_result(self.results, "BootNotification", True, "Server accepted", suite=self._suite)
                else:
                    record_result(
                        self.results,
                        "BootNotification",
                        False,
                        f"Server status: {resp.status}",
                        suite=self._suite,
                    )
            except Exception as e:
                LOGGER.exception("BootNotification failed")
                record_result(self.results, "BootNotification", False, str(e), suite=self._suite)
            LOG_ACTION = None

        async def test_authorize(self) -> None:
            global LOG_ACTION
            LOG_ACTION = "Authorize"
            LOGGER.info("Sending Authorize (OCPP 2.x).")
            try:
                req = call2.Authorize(
                    id_token=dt.IdTokenType(
                        id_token="test_tag",
                        type=id_token_type_authorize,
                    )
                )
                resp = await self.call(req)
                if resp is None:
                    record_result(
                        self.results,
                        "Authorize",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        suite=self._suite,
                    )
                else:
                    iti = getattr(resp, "id_token_info", None)
                    if iti is None and isinstance(resp, dict):
                        iti = resp.get("id_token_info") or resp.get("idTokenInfo")
                    auth_status = None
                    if iti is not None:
                        auth_status = (
                            getattr(iti, "status", None)
                            if not isinstance(iti, dict)
                            else iti.get("status")
                        )
                    # Capability test: CSMS responded. With ISO14443 + HA defaults, expect Accepted.
                    if auth_status == en.AuthorizationStatusEnumType.accepted:
                        record_result(self.results, "Authorize", True, "Server accepted", suite=self._suite)
                    elif auth_status is not None:
                        record_result(
                            self.results,
                            "Authorize",
                            True,
                            f"Server responded (status={auth_status}; use a registered tag for Accepted)",
                            suite=self._suite,
                        )
                    else:
                        record_result(
                            self.results,
                            "Authorize",
                            False,
                            "Missing id_token_info in response",
                            suite=self._suite,
                        )
            except Exception as e:
                LOGGER.exception("Authorize failed")
                record_result(self.results, "Authorize", False, str(e), suite=self._suite)
            LOG_ACTION = None

        async def test_heartbeat(self) -> None:
            global LOG_ACTION
            LOG_ACTION = "Heartbeat"
            LOGGER.info("Sending Heartbeat (OCPP 2.x).")
            try:
                req = call2.Heartbeat()
                resp = await self.call(req)
                if resp is None:
                    record_result(
                        self.results,
                        "Heartbeat",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        suite=self._suite,
                    )
                elif resp.current_time:
                    record_result(self.results, "Heartbeat", True, "Server returned currentTime", suite=self._suite)
                else:
                    record_result(self.results, "Heartbeat", False, "Missing currentTime", suite=self._suite)
            except Exception as e:
                LOGGER.exception("Heartbeat failed")
                record_result(self.results, "Heartbeat", False, str(e), suite=self._suite)
            LOG_ACTION = None

        async def test_status_notification(self) -> None:
            global LOG_ACTION
            LOG_ACTION = "StatusNotification"
            LOGGER.info("Sending StatusNotification (OCPP 2.x).")
            try:
                req = call2.StatusNotification(
                    timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    connector_status=en.ConnectorStatusEnumType.available,
                    evse_id=1,
                    connector_id=1,
                )
                resp = await self.call(req)
                if resp is None:
                    record_result(
                        self.results,
                        "StatusNotification",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        suite=self._suite,
                    )
                else:
                    record_result(self.results, "StatusNotification", True, "Server responded", suite=self._suite)
            except Exception as e:
                LOGGER.exception("StatusNotification failed")
                record_result(self.results, "StatusNotification", False, str(e), suite=self._suite)
            LOG_ACTION = None

        async def test_meter_values(self) -> None:
            global LOG_ACTION
            LOG_ACTION = "MeterValues"
            LOGGER.info("Sending MeterValues (OCPP 2.x).")
            try:
                # PyPI ocpp validates outbound payloads: SampledValueType.value must be number (float).
                req = call2.MeterValues(
                    evse_id=1,
                    meter_value=[
                        dt.MeterValueType(
                            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            sampled_value=[
                                dt.SampledValueType(
                                    value=1000.0,
                                    context=en.ReadingContextEnumType.sample_periodic,
                                    measurand=en.MeasurandEnumType.energy_active_import_register,
                                ),
                                dt.SampledValueType(
                                    value=0.0,
                                    context=en.ReadingContextEnumType.sample_periodic,
                                    measurand=en.MeasurandEnumType.current_import,
                                ),
                            ],
                        )
                    ],
                )
                resp = await self.call(req)
                if resp is None:
                    record_result(
                        self.results,
                        "MeterValues",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        suite=self._suite,
                    )
                else:
                    record_result(self.results, "MeterValues", True, "Server responded", suite=self._suite)
            except Exception as e:
                LOGGER.exception("MeterValues failed")
                record_result(self.results, "MeterValues", False, str(e), suite=self._suite)
            LOG_ACTION = None

        async def test_transaction_event_updated(self) -> None:
            """Send TransactionEvent Updated (no active session required for basic CSMS check)."""
            global LOG_ACTION
            LOG_ACTION = "TransactionEvent"
            LOGGER.info("Sending TransactionEvent (Updated) (OCPP 2.x).")
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                resp = await self.call(
                    call2.TransactionEvent(
                        event_type=en.TransactionEventEnumType.updated,
                        timestamp=ts,
                        trigger_reason=en.TriggerReasonEnumType.meter_value_periodic,
                        seq_no=50,
                        transaction_info=dt.TransactionType(transaction_id="no-active-tx"),
                        meter_value=[
                            dt.MeterValueType(
                                timestamp=ts,
                                sampled_value=[
                                    dt.SampledValueType(
                                        value=50.0,
                                        measurand=en.MeasurandEnumType.energy_active_import_register,
                                    )
                                ],
                            )
                        ],
                    )
                )
                if resp is None:
                    record_result(
                        self.results,
                        "TransactionEvent",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        suite=self._suite,
                    )
                else:
                    record_result(self.results, "TransactionEvent", True, "Server responded", suite=self._suite)
            except Exception as e:
                LOGGER.exception("TransactionEvent failed")
                record_result(self.results, "TransactionEvent", False, str(e), suite=self._suite)
            LOG_ACTION = None

        async def test_trigger_message_cp(self) -> None:
            """Charge point initiates TriggerMessage (request CSMS to trigger a message type)."""
            global LOG_ACTION
            LOG_ACTION = "TriggerMessage"
            LOGGER.info("Sending TriggerMessage (CP-initiated) (OCPP 2.x).")
            try:
                resp = await self.call(
                    call2.TriggerMessage(
                        requested_message=en.MessageTriggerEnumType.heartbeat,
                    )
                )
                if resp is None:
                    record_result(
                        self.results,
                        "TriggerMessage",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        "client_sent",
                        suite=self._suite,
                    )
                else:
                    record_result(
                        self.results,
                        "TriggerMessage",
                        True,
                        "CP-initiated TriggerMessage accepted",
                        "client_sent",
                        suite=self._suite,
                    )
            except asyncio.TimeoutError as e:
                LOGGER.info("CP-initiated TriggerMessage: no response within timeout (%s).", e)
                record_result(
                    self.results,
                    "TriggerMessage",
                    True,
                    "CP→CS TriggerMessage not answered in time (many CSMS omit this handler)",
                    "client_sent",
                    suite=self._suite,
                )
            except Exception as e:
                err_s = str(e)
                if "NotImplemented" in err_s or "No handler for TriggerMessage" in err_s:
                    LOGGER.info(
                        "CP-initiated TriggerMessage: CSMS does not implement (%s).", err_s
                    )
                    record_result(
                        self.results,
                        "TriggerMessage",
                        True,
                        "NotImplemented (CP→CS TriggerMessage optional on CSMS)",
                        "client_sent",
                        suite=self._suite,
                    )
                else:
                    LOGGER.exception("TriggerMessage failed")
                    record_result(
                        self.results,
                        "TriggerMessage",
                        False,
                        err_s,
                        "client_sent",
                        suite=self._suite,
                    )
            LOG_ACTION = None

        async def test_smart_charging_notify(self) -> None:
            """OCPP 2.0.1 smart charging: needs, limit, schedule (charge point → CSMS)."""
            global LOG_ACTION
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            ru = en.ChargingRateUnitEnumType
            rate_unit_amps = getattr(ru, "amps", getattr(ru, "a", None))
            if rate_unit_amps is None:
                rate_unit_amps = ru.watts
            try:
                LOG_ACTION = "NotifyEVChargingNeeds"
                needs = dt.ChargingNeedsType(
                    requested_energy_transfer=en.EnergyTransferModeEnumType.ac_three_phase,
                    ac_charging_parameters=dt.ACChargingParametersType(
                        energy_amount=5000,
                        ev_min_current=6,
                        ev_max_current=32,
                        ev_max_voltage=230,
                    ),
                )
                resp = await self.call(call2.NotifyEVChargingNeeds(charging_needs=needs, evse_id=1))
                if resp is None:
                    record_result(
                        self.results,
                        "NotifyEVChargingNeeds",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        "smart_charging",
                        suite=self._suite,
                    )
                else:
                    record_result(
                        self.results,
                        "NotifyEVChargingNeeds",
                        True,
                        "Smart charging: needs notified",
                        "smart_charging",
                        suite=self._suite,
                    )
            except Exception as e:
                LOGGER.exception("NotifyEVChargingNeeds failed")
                record_result(
                    self.results,
                    "NotifyEVChargingNeeds",
                    False,
                    str(e),
                    "smart_charging",
                    suite=self._suite,
                )
            LOG_ACTION = None

            try:
                LOG_ACTION = "NotifyChargingLimit"
                cl_src = getattr(en, "ChargingLimitSourceEnumType", None)
                if cl_src is not None:
                    limit = dt.ChargingLimitType(charging_limit_source=cl_src.ems)
                else:
                    limit = dt.ChargingLimitType(charging_limit_source="EMS")
                sched = dt.ChargingScheduleType(
                    id=1,
                    charging_rate_unit=rate_unit_amps,
                    charging_schedule_period=[
                        dt.ChargingSchedulePeriodType(start_period=0, limit=16.0),
                    ],
                )
                resp = await self.call(
                    call2.NotifyChargingLimit(
                        charging_limit=limit,
                        charging_schedule=[sched],
                        evse_id=1,
                    )
                )
                if resp is None:
                    record_result(
                        self.results,
                        "NotifyChargingLimit",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        "smart_charging",
                        suite=self._suite,
                    )
                else:
                    record_result(
                        self.results,
                        "NotifyChargingLimit",
                        True,
                        "Smart charging: limit notified",
                        "smart_charging",
                        suite=self._suite,
                    )
            except Exception as e:
                LOGGER.exception("NotifyChargingLimit failed")
                record_result(
                    self.results,
                    "NotifyChargingLimit",
                    False,
                    str(e),
                    "smart_charging",
                    suite=self._suite,
                )
            LOG_ACTION = None

            try:
                LOG_ACTION = "NotifyEVChargingSchedule"
                sched2 = dt.ChargingScheduleType(
                    id=2,
                    charging_rate_unit=rate_unit_amps,
                    charging_schedule_period=[
                        dt.ChargingSchedulePeriodType(start_period=0, limit=10.0),
                    ],
                )
                resp = await self.call(
                    call2.NotifyEVChargingSchedule(
                        time_base=ts,
                        charging_schedule=sched2,
                        evse_id=1,
                    )
                )
                if resp is None:
                    record_result(
                        self.results,
                        "NotifyEVChargingSchedule",
                        False,
                        "No response (CSMS may have returned CallError — check logs)",
                        "smart_charging",
                        suite=self._suite,
                    )
                else:
                    record_result(
                        self.results,
                        "NotifyEVChargingSchedule",
                        True,
                        "Smart charging: schedule notified",
                        "smart_charging",
                        suite=self._suite,
                    )
            except Exception as e:
                LOGGER.exception("NotifyEVChargingSchedule failed")
                record_result(
                    self.results,
                    "NotifyEVChargingSchedule",
                    False,
                    str(e),
                    "smart_charging",
                    suite=self._suite,
                )
            LOG_ACTION = None

        async def run_client_tests(self) -> None:
            """Ordered client-side calls for OCPP 2.x suites."""
            await self.test_boot_notification()
            await asyncio.sleep(0.3)
            await self.test_authorize()
            await asyncio.sleep(0.2)
            await self.test_heartbeat()
            await asyncio.sleep(0.2)
            await self.test_status_notification()
            await asyncio.sleep(0.2)
            await self.test_meter_values()
            await asyncio.sleep(0.2)
            await self.test_transaction_event_updated()
            await asyncio.sleep(0.2)
            await self.test_trigger_message_cp()
            await asyncio.sleep(0.2)
            await self.test_smart_charging_notify()

    return TestChargePointOcpp2


# -----------------------------------------------------------------------------
# Expected actions (for summary: mark as "not tested" if never received/sent)
# -----------------------------------------------------------------------------
EXPECTED_ACTIONS_V16: list[str] = [
    "Authorize",
    "BootNotification",
    "ClearChargingProfile",
    "GetConfiguration",
    "MeterValues",
    "RemoteStartTransaction",
    "RemoteStopTransaction",
    "SetChargingProfile",
    "StatusNotification",
    "ChangeAvailability",
    "Heartbeat",
    "TriggerMessage",
]

EXPECTED_ACTIONS_V2: list[str] = [
    "Authorize",
    "BootNotification",
    "ClearChargingProfile",
    "GetConfiguration",
    "MeterValues",
    "NotifyEVChargingNeeds",
    "NotifyChargingLimit",
    "NotifyEVChargingSchedule",
    "RemoteStartTransaction",
    "RemoteStopTransaction",
    "SetChargingProfile",
    "StatusNotification",
    "ChangeAvailability",
    "Heartbeat",
    "TransactionEvent",
    "TriggerMessage",
]

# Row order in the HTML/JSON report (OCA-style test matrix).
REPORT_ROW_ORDER: list[str] = [
    "Connection",
    "BootNotification",
    "Authorize",
    "Heartbeat",
    "StatusNotification",
    "MeterValues",
    "TransactionEvent",
    "NotifyEVChargingNeeds",
    "NotifyChargingLimit",
    "NotifyEVChargingSchedule",
    "StartTransaction",
    "StopTransaction",
    "GetConfiguration",
    "ChangeConfiguration",
    "TriggerMessage",
    "RemoteStartTransaction",
    "RemoteStopTransaction",
    "ClearChargingProfile",
    "SetChargingProfile",
    "ChangeAvailability",
]


SUITE_ORDER: list[str] = ["OCPP 1.6", "OCPP 2.0.1", "OCPP 2.1"]


def _sort_results_for_report(rows: list[dict]) -> list[dict]:
    """Stable order: suite first, then matrix row order."""
    sidx = {name: i for i, name in enumerate(SUITE_ORDER)}
    idx = {name: i for i, name in enumerate(REPORT_ROW_ORDER)}
    return sorted(
        rows,
        key=lambda r: (
            sidx.get(str(r.get("suite", "")), 99),
            idx.get(r["action"], 900),
            r["action"],
        ),
    )


def _result_outcome(row: dict) -> str:
    """Human-readable outcome matching OCA-style summary columns."""
    if row.get("passed"):
        return "Pass"
    msg = str(row.get("message", ""))
    if "Not exercised" in msg:
        return "Not run"
    return "Fail"


def _direction_for_row(row: dict) -> str:
    """Arrow label for test matrix."""
    cat = row.get("category", "")
    if cat == "client_sent":
        return "Charge Point → CSMS"
    if cat == "server_sent":
        return "CSMS → Charge Point"
    if cat == "smart_charging":
        return "Charge Point → CSMS (smart charging)"
    return "—"


def build_report_payload(results: list[dict]) -> dict[str, Any]:
    """Build JSON payload including OCA-style report metadata."""
    ts = datetime.now(UTC).isoformat()
    n_pass = sum(1 for r in results if r.get("passed"))
    n_fail = sum(1 for r in results if not r.get("passed") and _result_outcome(r) == "Fail")
    n_not_run = sum(1 for r in results if not r.get("passed") and _result_outcome(r) == "Not run")
    total = len(results)
    pct = round(100.0 * n_pass / total, 1) if total else 0.0
    return {
        "report_metadata": {
            "document_title": "OCPP 1.6 — Central System Capability Test Report",
            "document_subtitle": "Internal verification",
            "standard_reference": "OCPP 1.6, JSON over WebSocket",
            "organization": REPORT_ORGANIZATION,
            "product_under_test": REPORT_PRODUCT_UNDER_TEST,
            "certificate_style_note": (
                "This report is conducted internally by Computime."
            ),
            "generated_at_utc": ts,
            "host": socket.gethostname(),
        },
        "test_configuration": {
            "target_url_template": TARGET_WS_URL,
            "charge_point_id_stem": CHARGE_POINT_ID,
            "connections_by_suite": {
                sk: {
                    "charge_point_id": charge_point_id_for_suite(sk),
                    "websocket_url": target_ws_url_for_suite(sk),
                }
                for sk in RUN_SUITES
            },
            "suites_run": RUN_SUITES,
            "server_action_wait_seconds_per_prompt": SERVER_ACTION_WAIT_SECONDS,
        },
        "timestamp_utc": ts,
        "target_url": TARGET_WS_URL,
        "charge_point_id": CHARGE_POINT_ID,
        "results": results,
        "summary": {
            "total": total,
            "passed": n_pass,
            "failed": n_fail,
            "not_run": n_not_run,
            "pass_rate_percent": pct,
        },
    }


def _render_html_report(payload: dict[str, Any]) -> str:
    """Generate self-contained HTML (print-friendly) in OCA certificate-style sections."""
    meta = payload["report_metadata"]
    cfg = payload["test_configuration"]
    summ = payload["summary"]
    rows = _sort_results_for_report(list(payload["results"]))

    def esc(s: Any) -> str:
        return html.escape(str(s), quote=True)

    conn_lines: list[str] = []
    for sk, info in cfg.get("connections_by_suite", {}).items():
        cid = info.get("charge_point_id", "")
        wurl = info.get("websocket_url", "")
        conn_lines.append(f"{sk}: <code>{esc(cid)}</code> → <code>{esc(wurl)}</code>")
    connections_html = "<br/>".join(conn_lines) if conn_lines else esc("(none)")

    # Test case IDs (TC-CAP-*) for matrix readability.
    tc_rows: list[tuple[str, dict]] = []
    for i, row in enumerate(rows, start=1):
        tc_id = f"TC-CAP-{i:03d}"
        tc_rows.append((tc_id, row))

    outcome_class = {"Pass": "pass", "Fail": "fail", "Not run": "not-run"}

    table_rows = "".join(
        f"<tr>"
        f"<td>{esc(tc_id)}</td>"
        f"<td>{esc(r.get('suite', ''))}</td>"
        f"<td><code>{esc(r['action'])}</code></td>"
        f"<td>{esc(_direction_for_row(r))}</td>"
        f"<td class=\"outcome-{outcome_class[_result_outcome(r)]}\">"
        f"{esc(_result_outcome(r))}</td>"
        f"<td>{esc(r.get('message', ''))}</td>"
        f"</tr>"
        for tc_id, r in tc_rows
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{esc(meta['document_title'])}</title>
  <style>
    :root {{
      --ink: #1a2b3c;
      --muted: #5a6b7c;
      --border: #c5d0d8;
      --ok: #0d6e3a;
      --bad: #a61b1b;
      --skip: #8a6d3b;
      --band: #0f3d5c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      line-height: 1.45;
      max-width: 900px;
      margin: 0 auto;
      padding: 24px 20px 48px;
      background: #f7f9fa;
    }}
    .sheet {{
      background: #fff;
      border: 1px solid var(--border);
      box-shadow: 0 2px 8px rgba(0,0,0,.06);
    }}
    .header-band {{
      background: var(--band);
      color: #fff;
      padding: 20px 24px;
    }}
    .header-band h1 {{
      margin: 0 0 8px;
      font-size: 1.35rem;
      font-weight: 600;
      letter-spacing: .02em;
    }}
    .header-band .sub {{
      margin: 0;
      font-size: 0.95rem;
      opacity: 0.92;
    }}
    .section {{
      padding: 18px 24px;
      border-bottom: 1px solid var(--border);
    }}
    .section:last-child {{ border-bottom: none; }}
    h2 {{
      font-size: 1.05rem;
      margin: 0 0 12px;
      color: var(--band);
      font-weight: 600;
      border-bottom: 2px solid var(--border);
      padding-bottom: 6px;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: 160px 1fr;
      gap: 6px 16px;
      font-size: 0.92rem;
    }}
    .meta-grid dt {{ color: var(--muted); margin: 0; }}
    .meta-grid dd {{ margin: 0; }}
    .summary-box {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px 28px;
      font-size: 0.95rem;
    }}
    .summary-box strong {{ color: var(--band); }}
    .outcome-pass {{ color: var(--ok); font-weight: 600; }}
    .outcome-fail {{ color: var(--bad); font-weight: 600; }}
    .outcome-not-run {{ color: var(--skip); font-weight: 600; }}
    table.matrix {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }}
    table.matrix th, table.matrix td {{
      border: 1px solid var(--border);
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
    }}
    table.matrix th {{
      background: #eef3f6;
      font-weight: 600;
    }}
    table.matrix code {{ font-size: 0.9em; }}
    .disclaimer {{
      font-size: 0.78rem;
      color: var(--muted);
      line-height: 1.5;
    }}
    .footer-line {{
      margin-top: 8px;
      font-size: 0.75rem;
      color: #8899aa;
    }}
    @media print {{
      body {{ background: #fff; }}
      .sheet {{ box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <div class="sheet">
    <header class="header-band">
      <h1>{esc(meta['document_title'])}</h1>
      <p class="sub">{esc(meta['document_subtitle'])}</p>
    </header>

    <section class="section">
      <h2>Abstract — test configuration</h2>
      <dl class="meta-grid">
        <dt>Standard / profile</dt>
        <dd>{esc(meta['standard_reference'])}</dd>
        <dt>Organization</dt>
        <dd>{esc(meta['organization'])}</dd>
        <dt>Product under test</dt>
        <dd>{esc(meta['product_under_test'])}</dd>
        <dt>Report generated (UTC)</dt>
        <dd>{esc(meta['generated_at_utc'])}</dd>
        <dt>Host</dt>
        <dd>{esc(meta['host'])}</dd>
        <dt>URL template / stem</dt>
        <dd><code>{esc(cfg.get('target_url_template', cfg.get('target_url', '')))}</code> &nbsp;|&nbsp; stem <code>{esc(cfg.get('charge_point_id_stem', cfg.get('charge_point_identity', '')))}</code></dd>
        <dt>Per-suite connection</dt>
        <dd>{connections_html}</dd>
        <dt>Suites run</dt>
        <dd>{esc(", ".join(cfg.get('suites_run', [])))}</dd>
        <dt>Server-action prompt timeout</dt>
        <dd>{esc(cfg['server_action_wait_seconds_per_prompt'])} s</dd>
      </dl>
    </section>

    <section class="section">
      <h2>Test result summary</h2>
      <div class="summary-box">
        <span><strong>Total</strong> {esc(summ['total'])}</span>
        <span class="outcome-pass"><strong>Pass</strong> {esc(summ['passed'])}</span>
        <span class="outcome-fail"><strong>Fail</strong> {esc(summ['failed'])}</span>
        <span class="outcome-not-run"><strong>Not run</strong> {esc(summ['not_run'])}</span>
        <span><strong>Pass rate</strong> {esc(summ['pass_rate_percent'])}%</span>
      </div>
    </section>

    <section class="section">
      <h2>Detailed test results</h2>
      <table class="matrix">
        <thead>
          <tr>
            <th>Test case</th>
            <th>Suite</th>
            <th>OCPP action</th>
            <th>Direction</th>
            <th>Result</th>
            <th>Remarks</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </section>

    <section class="section disclaimer">
      <p><strong>Note.</strong> {esc(meta['certificate_style_note'])}</p>
      <p class="footer-line">
        Layout inspired by Open Charge Alliance certificate abstracts for readability.
        This tool exercises OCPP 1.6 and 2.x flows configured under Suites run.
      </p>
    </section>
  </div>
</body>
</html>
"""


def write_html_report(payload: dict[str, Any], path: Path) -> None:
    """Write the OCA-style HTML report."""
    path.write_text(_render_html_report(payload) + "\n", encoding="utf-8")
    LOGGER.info("HTML report written to %s", path)


def _default_category_for_expected(action: str) -> str:
    client = {
        "Authorize",
        "BootNotification",
        "Heartbeat",
        "MeterValues",
        "StatusNotification",
        "TransactionEvent",
        "NotifyEVChargingNeeds",
        "NotifyChargingLimit",
        "NotifyEVChargingSchedule",
    }
    if action in client:
        return "client_sent"
    if action.startswith("Notify"):
        return "smart_charging"
    return "server_sent"


def ensure_results_for_expected(
    results: list[dict],
    suite: str,
    expected: list[str],
) -> None:
    """Add 'not_run' entries for expected actions that have no result in this suite."""
    seen = {r["action"] for r in results if r.get("suite") == suite}
    for action in expected:
        if action not in seen:
            results.append({
                "suite": suite,
                "action": action,
                "passed": False,
                "message": "Not exercised during test (server did not send or test did not run)",
                "category": _default_category_for_expected(action),
            })


def save_results(results: list[dict], path: Path) -> dict[str, Any]:
    """Write results and metadata to JSON file (OCA-style payload). Returns payload for HTML."""
    payload = build_report_payload(results)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Results written to %s", path)
    return payload


def print_summary(results: list[dict]) -> None:
    """Print human-readable summary to stdout."""
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    fail_only = [r for r in failed if _result_outcome(r) == "Fail"]
    not_run = [r for r in failed if _result_outcome(r) == "Not run"]
    print("\n" + "=" * 60)
    print("OCPP CSMS Capability Test Summary")
    print("=" * 60)
    print(f"Target: {TARGET_WS_URL}")
    print(f"Suites: {', '.join(RUN_SUITES)}")
    print(
        f"Total:  {len(results)}  |  Passed: {len(passed)}  |  "
        f"Failed: {len(fail_only)}  |  Not run: {len(not_run)}",
    )
    print(f"HTML report: {REPORT_HTML_FILE}")
    print("-" * 60)
    if passed:
        print("PASSED:")
        for r in passed:
            print(f"  - [{r.get('suite', '')}] {r['action']}: {r.get('message', 'OK')}")
    if fail_only:
        print("FAILED:")
        for r in fail_only:
            print(f"  - [{r.get('suite', '')}] {r['action']}: {r.get('message', 'Failed')}")
    if not_run:
        print("NOT RUN:")
        for r in not_run:
            print(f"  - [{r.get('suite', '')}] {r['action']}: {r.get('message', '')}")
    print("=" * 60)


def _has_passing_result(results: list[dict], action: str, suite: str) -> bool:
    """Return True if this action already has a passing row in this suite."""
    return any(
        r["action"] == action and r.get("suite") == suite and r["passed"]
        for r in results
    )


async def wait_for_prompted_server_action(
    cp: TestChargePoint | Any,
    action: str,
    results: list[dict],
    timeout_sec: float,
    suite: str,
) -> bool:
    """Wait up to timeout_sec for the CSMS to send this call; record failure on timeout."""
    if _has_passing_result(results, action, suite):
        LOGGER.info("Server action %s already completed earlier; skipping wait.", action)
        return True
    ev = cp._server_action_events.get(action)
    if ev is None:
        record_result(
            results,
            action,
            False,
            f"Internal: unknown server action {action}",
            "server_sent",
            suite=suite,
        )
        return False
    try:
        await asyncio.wait_for(ev.wait(), timeout=timeout_sec)
        return True
    except TimeoutError:
        record_result(
            results,
            action,
            False,
            f"No {action} from server within {timeout_sec:.0f}s",
            "server_sent",
            suite=suite,
        )
        return False


def print_server_action_prompt(action: str, index: int, total: int) -> None:
    """Print instructions for the operator before waiting for a server-initiated call."""
    line = SERVER_ACTION_USER_INSTRUCTIONS.get(
        action,
        f"In your CSMS, trigger {action} for this charge point.",
    )
    print()
    print("=" * 60)
    print(f"Server-initiated test {index}/{total}: {action}")
    print("-" * 60)
    print(line)
    print(
        f"You have up to {SERVER_ACTION_WAIT_SECONDS} seconds for the server to send this call.",
    )
    print("=" * 60)
    sys.stdout.flush()


async def run_prompted_server_tests(cp: TestChargePoint | Any, results: list[dict], suite: str) -> None:
    """After client tests: prompt and wait (sequentially) for each server-initiated action."""
    total = len(SERVER_PROMPT_SEQUENCE)
    for i, action in enumerate(SERVER_PROMPT_SEQUENCE, start=1):
        if _has_passing_result(results, action, suite):
            print()
            print(
                f"[{suite}][{action}] Already received from server earlier — skipping ({i}/{total}).",
            )
            sys.stdout.flush()
            LOGGER.info("Skipping prompted wait for %s (already passed).", action)
            continue
        print_server_action_prompt(action, i, total)
        await wait_for_prompted_server_action(
            cp,
            action,
            results,
            SERVER_ACTION_WAIT_SECONDS,
            suite,
        )


async def run_suite_v16() -> list[dict]:
    """OCPP 1.6: connect with ocpp1.6 subprotocol and run the 1.6 matrix."""
    global CURRENT_SUITE
    CURRENT_SUITE = "OCPP 1.6"
    results: list[dict] = []
    cp_id = charge_point_id_for_suite("1.6")
    ws_url = target_ws_url_for_suite("1.6")

    LOGGER.info(
        "Suite OCPP 1.6: connecting to %s (charge_point_id=%s)",
        ws_url,
        cp_id,
    )
    try:
        async with websockets.connect(
            ws_url,
            subprotocols=DEFAULT_SUBPROTOCOLS_16,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            cp = TestChargePoint(cp_id, ws, results)
            runner = asyncio.create_task(cp.start())

            await asyncio.sleep(0.5)

            await cp.test_boot_notification()
            await asyncio.sleep(0.3)
            await cp.test_authorize()
            await asyncio.sleep(0.2)
            await cp.test_heartbeat()
            await asyncio.sleep(0.2)
            await cp.test_status_notification()
            await asyncio.sleep(0.2)
            await cp.test_meter_values()
            await asyncio.sleep(0.2)
            await cp.test_start_transaction()
            await asyncio.sleep(0.2)
            await cp.test_stop_transaction()

            print()
            print("=" * 60)
            print(f"[{CURRENT_SUITE}] Client-side tests finished.")
            print(
                "Next: server-initiated tests — follow each prompt and use your CSMS within "
                f"{SERVER_ACTION_WAIT_SECONDS}s per step.",
            )
            print("=" * 60)
            sys.stdout.flush()

            await run_prompted_server_tests(cp, results, CURRENT_SUITE)

            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
    except Exception as e:
        LOGGER.exception("Connection or test run failed (OCPP 1.6)")
        record_result(results, "Connection", False, str(e), "client_sent", suite=CURRENT_SUITE)

    ensure_results_for_expected(results, CURRENT_SUITE, EXPECTED_ACTIONS_V16)
    return results


async def run_suite_ocpp2(suite_label: str, suite_key: str, subprotocols: list[str]) -> list[dict]:
    """OCPP 2.0.1 or 2.1: connect and run 2.x client matrix + prompted server actions."""
    global CURRENT_SUITE
    CURRENT_SUITE = suite_label
    results: list[dict] = []
    pkg = _ocpp2_module_for_suite(suite_label)
    cp_cls = build_ocpp2_test_charge_point_class(pkg)
    cp_id = charge_point_id_for_suite(suite_key)
    ws_url = target_ws_url_for_suite(suite_key)

    LOGGER.info(
        "Suite %s: connecting to %s (charge_point_id=%s, subprotocols=%s)",
        suite_label,
        ws_url,
        cp_id,
        subprotocols,
    )
    try:
        async with websockets.connect(
            ws_url,
            subprotocols=subprotocols,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            cp = cp_cls(cp_id, ws, results, suite_label)
            runner = asyncio.create_task(cp.start())

            await asyncio.sleep(0.5)

            await cp.run_client_tests()

            print()
            print("=" * 60)
            print(f"[{suite_label}] Client-side tests finished.")
            print(
                "Next: server-initiated tests — follow each prompt and use your CSMS within "
                f"{SERVER_ACTION_WAIT_SECONDS}s per step.",
            )
            print("=" * 60)
            sys.stdout.flush()

            await run_prompted_server_tests(cp, results, suite_label)

            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
    except Exception as e:
        LOGGER.exception("Connection or test run failed (%s)", suite_label)
        record_result(results, "Connection", False, str(e), "client_sent", suite=suite_label)

    ensure_results_for_expected(results, suite_label, EXPECTED_ACTIONS_V2)
    return results


async def run_all_suites() -> list[dict]:
    """Run selected protocol suites sequentially and concatenate results."""
    combined: list[dict] = []
    suite_runners: dict[str, Callable[[], Coroutine[Any, Any, list[dict]]]] = {
        "1.6": run_suite_v16,
        "2.0.1": lambda: run_suite_ocpp2("OCPP 2.0.1", "2.0.1", DEFAULT_SUBPROTOCOLS_201),
        "2.1": lambda: run_suite_ocpp2("OCPP 2.1", "2.1", DEFAULT_SUBPROTOCOLS_21),
    }
    for key in RUN_SUITES:
        run = suite_runners.get(key)
        if run is None:
            continue
        combined.extend(await run())
    return combined


def main() -> int:
    """Entry point: run tests, store results, print summary."""
    print("OCPP CSMS Capability Test (multi-protocol)")
    print("URL template:", TARGET_WS_URL)
    print("Charge point stem:", CHARGE_POINT_ID)
    for sk in RUN_SUITES:
        print(f"  [{sk}] id={charge_point_id_for_suite(sk)!r} url={target_ws_url_for_suite(sk)!r}")
    print("Suites (this run, in order):", ", ".join(RUN_SUITES))
    print("Each suite uses its own WebSocket connection; default is all three in one process.")
    print("JSON results:", RESULTS_FILE)
    print("HTML report:", REPORT_HTML_FILE)
    print()

    results = asyncio.run(run_all_suites())
    payload = save_results(results, RESULTS_FILE)
    write_html_report(payload, REPORT_HTML_FILE)
    print_summary(results)

    fail_count = sum(1 for r in results if not r["passed"] and _result_outcome(r) == "Fail")
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
