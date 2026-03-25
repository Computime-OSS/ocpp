#!/usr/bin/env python3
"""Standalone OCPP 1.6 server capability test script.

Tests the OCPP Central System (server) by acting as a charge point client.
Verifies: Authorize, BootNotification, ClearChargingProfile, GetConfiguration,
MeterValues, RemoteStartTransaction, RemoteStopTransaction, SetChargingProfile,
StatusNotification, ChangeAvailability, Heartbeat.

No Home Assistant dependencies; uses only the ocpp and websockets libraries.
Server-initiated actions (GetConfiguration, ChangeConfiguration, TriggerMessage,
SetChargingProfile, etc.) are marked passed when the server sends them and we respond.
ChangeConfiguration and TriggerMessage are required for Home Assistant’s post_connect
flow (measurands, meter interval, triggers); without them the CSMS logs NotImplemented
and device setup may not finish (no entities).
RemoteStartTransaction / RemoteStopTransaction are only tested if the server
sends them (e.g. when triggered from the HA UI).

Usage:
    python tests/ocpp_server_capability_test.py

Results are stored in tests/ocpp_capability_test_results.json and printed to stdout.

After client-side tests, the script prints instructions for each remaining
server-initiated action (not GetConfiguration — the CSMS usually sends that
automatically during post_connect) and waits up to SERVER_ACTION_WAIT_SECONDS
per step for you to trigger it from the CSMS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Coroutine
from typing import Any
from datetime import datetime, UTC
from pathlib import Path

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
# Configuration (change here for your server)
# -----------------------------------------------------------------------------
TARGET_WS_URL = "ws://127.0.0.1:9000/TEST-002"
CHARGE_POINT_ID = "TEST-002"
SUBPROTOCOLS = ["ocpp1.6"]
RESULTS_FILE = Path(__file__).resolve().parent / "ocpp_capability_test_results.json"
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
        "After Accepted, this script sends StartTransaction + StatusNotification(charging) "
        "so the CSMS has an active session."
    ),
    "RemoteStopTransaction": (
        "After Remote Start above, trigger remote stop / RemoteStopTransaction for "
        "that session. The script then sends StopTransaction + StatusNotification(available)."
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


# -----------------------------------------------------------------------------
# Test result storage
# -----------------------------------------------------------------------------
def record_result(
    results: list[dict],
    action: str,
    passed: bool,
    message: str = "",
    category: str = "client_sent",
) -> None:
    """Record or merge one test result by action (one row per test type).

    If the same OCPP action is exercised more than once (e.g. server sends
    GetConfiguration twice), update the existing row instead of appending so
    totals and the JSON report do not double-count. Pass status is merged with
    OR: any successful attempt marks the action passed. On first failure then
    success, message updates to the success text; a later failure does not
    clear a prior pass.
    category: 'client_sent' | 'server_sent' (kept from first record for that action).
    """
    for row in results:
        if row["action"] == action:
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
                    "value": self._config.get(k, "test_value"),
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


# -----------------------------------------------------------------------------
# Expected actions (for summary: mark as "not tested" if never received/sent)
# -----------------------------------------------------------------------------
EXPECTED_ACTIONS = [
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
]


def ensure_results_for_expected(results: list[dict]) -> None:
    """Add 'not_run' entries for expected actions that have no result."""
    seen = {r["action"] for r in results}
    for action in EXPECTED_ACTIONS:
        if action not in seen:
            results.append({
                "action": action,
                "passed": False,
                "message": "Not exercised during test (server did not send or test did not run)",
                "category": "client_sent" if action in ("Authorize", "BootNotification", "Heartbeat", "MeterValues", "StatusNotification") else "server_sent",
            })


def save_results(results: list[dict], path: Path) -> None:
    """Write results and metadata to JSON file."""
    payload = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "target_url": TARGET_WS_URL,
        "charge_point_id": CHARGE_POINT_ID,
        "results": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    LOGGER.info("Results written to %s", path)


def print_summary(results: list[dict]) -> None:
    """Print human-readable summary to stdout."""
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    print("\n" + "=" * 60)
    print("OCPP 1.6 Server Capability Test Summary")
    print("=" * 60)
    print(f"Target: {TARGET_WS_URL}")
    print(f"Total:  {len(results)}  |  Passed: {len(passed)}  |  Failed: {len(failed)}")
    print("-" * 60)
    if passed:
        print("PASSED:")
        for r in passed:
            print(f"  - {r['action']}: {r.get('message', 'OK')}")
    if failed:
        print("FAILED / NOT RUN:")
        for r in failed:
            print(f"  - {r['action']}: {r.get('message', 'Failed')}")
    print("=" * 60)


def _has_passing_result(results: list[dict], action: str) -> bool:
    """Return True if this action already has a passing row."""
    return any(r["action"] == action and r["passed"] for r in results)


async def wait_for_prompted_server_action(
    cp: TestChargePoint,
    action: str,
    results: list[dict],
    timeout_sec: float,
) -> bool:
    """Wait up to timeout_sec for the CSMS to send this call; record failure on timeout."""
    if _has_passing_result(results, action):
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


async def run_prompted_server_tests(cp: TestChargePoint, results: list[dict]) -> None:
    """After client tests: prompt and wait (sequentially) for each server-initiated action."""
    total = len(SERVER_PROMPT_SEQUENCE)
    for i, action in enumerate(SERVER_PROMPT_SEQUENCE, start=1):
        if _has_passing_result(results, action):
            print()
            print(
                f"[{action}] Already received from server earlier — skipping ({i}/{total}).",
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
        )


async def run_tests() -> list[dict]:
    """Connect to server, run client and server tests, return results."""
    results: list[dict] = []

    LOGGER.info("Connecting to %s (charge_point_id=%s)", TARGET_WS_URL, CHARGE_POINT_ID)
    try:
        async with websockets.connect(
            TARGET_WS_URL,
            subprotocols=SUBPROTOCOLS,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            cp = TestChargePoint(CHARGE_POINT_ID, ws, results)
            runner = asyncio.create_task(cp.start())

            # Give server time to send GetConfiguration / SetChargingProfile / ChangeAvailability (post_connect)
            await asyncio.sleep(0.5)

            # ----- Client-initiated tests (we send, verify response) -----
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
            print("Client-side tests finished.")
            print(
                "Next: server-initiated tests — follow each prompt and use your CSMS within "
                f"{SERVER_ACTION_WAIT_SECONDS}s per step.",
            )
            print("=" * 60)
            sys.stdout.flush()

            await run_prompted_server_tests(cp, results)

            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
    except Exception as e:
        LOGGER.exception("Connection or test run failed")
        record_result(results, "Connection", False, str(e), "client_sent")

    ensure_results_for_expected(results)
    return results


def main() -> int:
    """Entry point: run tests, store results, print summary."""
    print("OCPP 1.6 Server Capability Test")
    print("Target URL (hardcoded):", TARGET_WS_URL)
    print("Results file:", RESULTS_FILE)
    print()

    results = asyncio.run(run_tests())
    save_results(results, RESULTS_FILE)
    print_summary(results)

    failed = sum(1 for r in results if not r["passed"])
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
