#!/usr/bin/env python3
"""Standalone OCPP 1.6 server capability test script.

Tests the OCPP Central System (server) by acting as a charge point client.
Verifies: Authorize, BootNotification, ClearChargingProfile, GetConfiguration,
MeterValues, RemoteStartTransaction, RemoteStopTransaction, SetChargingProfile,
StatusNotification, ChangeAvailability, Heartbeat.

No Home Assistant dependencies; uses only the ocpp and websockets libraries.
Server-initiated actions (GetConfiguration, SetChargingProfile, etc.) are
marked passed when the server sends them and we respond successfully.
RemoteStartTransaction / RemoteStopTransaction are only tested if the server
sends them (e.g. when triggered from the HA UI).

Usage:
    python tests/ocpp_server_capability_test.py

Results are stored in tests/ocpp_capability_test_results.json and printed to stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
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
    RegistrationStatus,
    RemoteStartStopStatus,
)

# -----------------------------------------------------------------------------
# Configuration (hardcoded; change here for your server)
# -----------------------------------------------------------------------------
TARGET_WS_URL = "ws://127.0.0.1:9000/CP_001"
CHARGE_POINT_ID = "CP_001"
SUBPROTOCOLS = ["ocpp1.6"]
RESULTS_FILE = Path(__file__).resolve().parent / "ocpp_capability_test_results.json"
TEST_TIMEOUT_SECONDS = 60

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
    """Append one test result. category: 'client_sent' | 'server_sent'."""
    results.append(
        {
            "action": action,
            "passed": passed,
            "message": message,
            "category": category,
        }
    )


# -----------------------------------------------------------------------------
# OCPP 1.6 Charge Point client (test stub)
# -----------------------------------------------------------------------------
class TestChargePoint(CP16Base):
    """OCPP 1.6 charge point client that records server capability test results."""

    def __init__(
        self,
        charge_point_id: str,
        websocket: websockets.WebSocketClientProtocol,
        results: list[dict],
    ):
        super().__init__(charge_point_id, websocket)
        self.results = results
        self.active_transaction_id: int = 0

    # ----- Server-initiated: record when we receive and respond -----
    @on(Action.get_configuration)
    def on_get_configuration(
        self, key: list[str] | None = None, **kwargs
    ) -> call_result.GetConfiguration:
        global LOG_ACTION
        LOG_ACTION = "GetConfiguration"
        LOGGER.info("Received GetConfiguration from server; responding.")
        keys = key or []
        if not keys:
            out = call_result.GetConfiguration(configuration_key=[])
        else:
            config_list = [
                {"key": k, "readonly": False, "value": "test_value"} for k in keys
            ]
            out = call_result.GetConfiguration(configuration_key=config_list)
        record_result(
            self.results,
            "GetConfiguration",
            True,
            "Received and responded",
            "server_sent",
        )
        LOG_ACTION = None
        return out

    @on(Action.set_charging_profile)
    def on_set_charging_profile(self, **kwargs) -> call_result.SetChargingProfile:
        global LOG_ACTION
        LOG_ACTION = "SetChargingProfile"
        LOGGER.info("Received SetChargingProfile from server; responding Accepted.")
        record_result(
            self.results,
            "SetChargingProfile",
            True,
            "Received and responded",
            "server_sent",
        )
        LOG_ACTION = None
        return call_result.SetChargingProfile(ChargingProfileStatus.accepted)

    @on(Action.clear_charging_profile)
    def on_clear_charging_profile(self, **kwargs) -> call_result.ClearChargingProfile:
        global LOG_ACTION
        LOG_ACTION = "ClearChargingProfile"
        LOGGER.info("Received ClearChargingProfile from server; responding Accepted.")
        record_result(
            self.results,
            "ClearChargingProfile",
            True,
            "Received and responded",
            "server_sent",
        )
        LOG_ACTION = None
        return call_result.ClearChargingProfile(ClearChargingProfileStatus.accepted)

    @on(Action.remote_start_transaction)
    def on_remote_start_transaction(
        self, id_tag: str | None = None, connector_id: int | None = None, **kwargs
    ) -> call_result.RemoteStartTransaction:
        global LOG_ACTION
        LOG_ACTION = "RemoteStartTransaction"
        LOGGER.info("Received RemoteStartTransaction from server; responding Accepted.")
        record_result(
            self.results,
            "RemoteStartTransaction",
            True,
            "Received and responded",
            "server_sent",
        )
        LOG_ACTION = None
        return call_result.RemoteStartTransaction(RemoteStartStopStatus.accepted)

    @on(Action.remote_stop_transaction)
    def on_remote_stop_transaction(
        self, transaction_id: int | None = None, **kwargs
    ) -> call_result.RemoteStopTransaction:
        global LOG_ACTION
        LOG_ACTION = "RemoteStopTransaction"
        LOGGER.info("Received RemoteStopTransaction from server; responding Accepted.")
        record_result(
            self.results,
            "RemoteStopTransaction",
            True,
            "Received and responded",
            "server_sent",
        )
        LOG_ACTION = None
        return call_result.RemoteStopTransaction(RemoteStartStopStatus.accepted)

    @on(Action.change_availability)
    def on_change_availability(
        self, connector_id: int | None = None, type: str | None = None, **kwargs
    ) -> call_result.ChangeAvailability:
        global LOG_ACTION
        LOG_ACTION = "ChangeAvailability"
        LOGGER.info("Received ChangeAvailability from server; responding Accepted.")
        record_result(
            self.results,
            "ChangeAvailability",
            True,
            "Received and responded",
            "server_sent",
        )
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
                record_result(
                    self.results,
                    "BootNotification",
                    False,
                    f"Server status: {resp.status}",
                )
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
                record_result(
                    self.results, "Authorize", False, f"id_tag_info status: {status}"
                )
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
                record_result(
                    self.results, "Heartbeat", True, "Server returned currentTime"
                )
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
                            {
                                "value": "1000",
                                "context": "Sample.Periodic",
                                "measurand": "Energy.Active.Import.Register",
                                "unit": "Wh",
                            },
                            {
                                "value": "0",
                                "context": "Sample.Periodic",
                                "measurand": "Current.Import",
                                "unit": "A",
                            },
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
                record_result(
                    self.results,
                    "StartTransaction",
                    True,
                    f"transaction_id={resp.transaction_id}",
                )
            else:
                record_result(
                    self.results,
                    "StartTransaction",
                    False,
                    f"id_tag_info status: {status}",
                )
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
            else:
                record_result(
                    self.results,
                    "StopTransaction",
                    False,
                    f"id_tag_info status: {status}",
                )
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
            results.append(
                {
                    "action": action,
                    "passed": False,
                    "message": "Not exercised during test (server did not send or test did not run)",
                    "category": "client_sent"
                    if action
                    in (
                        "Authorize",
                        "BootNotification",
                        "Heartbeat",
                        "MeterValues",
                        "StatusNotification",
                    )
                    else "server_sent",
                }
            )


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
    """Print human-readable summary to stdout via logger."""
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    LOGGER.info("\n" + "=" * 60)
    LOGGER.info("OCPP 1.6 Server Capability Test Summary")
    LOGGER.info("=" * 60)
    LOGGER.info("Target: %s", TARGET_WS_URL)
    LOGGER.info("Total:  %s  |  Passed: %s  |  Failed: %s", len(results), len(passed), len(failed))
    LOGGER.info("-" * 60)
    if passed:
        LOGGER.info("PASSED:")
        for r in passed:
            LOGGER.info("  - %s: %s", r["action"], r.get("message", "OK"))
    if failed:
        LOGGER.info("FAILED / NOT RUN:")
        for r in failed:
            LOGGER.info("  - %s: %s", r["action"], r.get("message", "Failed"))
    LOGGER.info("=" * 60)


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

            # Allow more time for server-initiated messages (e.g. TriggerMessage, SetChargingProfile)
            await asyncio.sleep(2.0)

            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
    except Exception as e:
        LOGGER.exception("Connection or test run failed")
        record_result(results, "Connection", False, str(e), "client_sent")

    ensure_results_for_expected(results)
    return results


def main() -> int:
    """Entry point: run tests, store results, print summary."""
    LOGGER.info("OCPP 1.6 Server Capability Test")
    LOGGER.info("Target URL (hardcoded): %s", TARGET_WS_URL)
    LOGGER.info("Results file: %s", RESULTS_FILE)
    LOGGER.info("")

    results = asyncio.run(run_tests())
    save_results(results, RESULTS_FILE)
    print_summary(results)

    failed = sum(1 for r in results if not r["passed"])
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
