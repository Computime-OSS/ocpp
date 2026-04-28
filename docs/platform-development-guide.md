# Platform Development Guide

## 1) Purpose

This guide explains what developers must understand and modify when adapting this OCPP connector from Home Assistant to another platform runtime.

## 2) Functional Component Map

### 2.1 Core Protocol Components (portable)

- `custom_components/ocpp/chargepoint.py`
  - Shared base behavior, metrics model, common control flow.
- `custom_components/ocpp/ocppv16.py`
  - OCPP 1.6 behavior and message handlers.
- `custom_components/ocpp/ocppv201.py`
  - OCPP 2.x behavior and message handlers.
- `custom_components/ocpp/core_const.py`
  - Platform-neutral constants and settings dataclasses.
- `custom_components/ocpp/core_errors.py`
  - Common connector exceptions.

### 2.2 Platform Integration Components (replace/adapt)

- `custom_components/ocpp/platform_adapter.py`
  - Main abstraction to implement for target platform.
- `custom_components/ocpp/api.py`
  - Service registration, central orchestration, runtime wiring.
- Home Assistant entity modules:
  - `sensor.py`, `switch.py`, `button.py`, `number.py`
  - Replace with target platform entity/exposure model.
- Config and setup flow:
  - `__init__.py`, `config_flow.py`, manifests/translations.

## 3) Required Integration Dependencies and Compatibility

### 3.1 Dependencies to Keep

- `ocpp`
- `websockets`
- Python asyncio runtime

### 3.2 Dependencies to Replace

- Home Assistant APIs (service registry, state machine, entities, config entries, dispatcher)

### 3.3 Compatibility Planning

- Define supported OCPP versions per target platform.
- Define target Python version matrix.
- Verify runtime behavior on Windows/Linux if both are required.
- Define asset model compatibility (single connector vs multi-EVSE devices).

## 4) Porting Steps

### Step 1: Implement Adapter Contract

Create a target-platform adapter equivalent to `HomeAssistantAdapter` with these responsibilities:

- Register services/actions
- Report state changes
- Fetch platform config
- Persist connector configuration
- Notify users (optional but recommended)
- Unit conversion hooks for telemetry units

### Step 2: Wire Connector Bootstrap

Implement platform-specific entry setup that:

1. Loads central and charger settings.
2. Creates `CentralSystem`.
3. Starts WebSocket server lifecycle.
4. Registers cleanup/unload hooks.

### Step 3: Replace Entity Exposure Layer

Map connector metrics/statuses to target platform primitives:

- Sensors/telemetry
- Switches/toggles
- Commands/buttons
- Numeric controls

### Step 4: Port Service/API Surface

Expose service equivalents for:

- Start/stop transaction
- Availability
- Set charge rate / clear profile
- Trigger message
- Configure/get configuration
- Firmware/diagnostics/data transfer (if supported in protocol module)

### Step 5: Validate Version-Specific Flows

For each supported OCPP version, verify:

- Boot and registration
- Session lifecycle (start/active/stop)
- Smart charging capability discovery and control
- Error mapping and retries/reconnect behavior
- control signal/setpoint propagation from platform API to charger behavior

## 5) Smart Charging Porting Notes

### OCPP 1.6

- Ensure configuration flow can surface `SupportedFeatureProfiles`.
- Ensure Set/ClearChargingProfile actions and outcomes are mapped to platform controls.

### OCPP 2.x

- Ensure `GetBaseReport`/`NotifyReport` capability flow is handled.
- Ensure `SetChargingProfile`, `RequestStartTransaction`, `RequestStopTransaction`, and variable operations are correctly mapped.
- Ensure connector status and transaction state update UI controls consistently.

Example capability payload (charger -> CSMS):

```json
{
  "requestId": 1,
  "generatedAt": "2026-04-21T12:00:00Z",
  "seqNo": 0,
  "reportData": [
    {
      "component": { "name": "SmartChargingCtrlr" },
      "variable": { "name": "Available" },
      "variableAttribute": [{ "type": "Actual", "value": "true" }]
    }
  ],
  "tbc": false
}
```

Expected behavior:

- The asset is recognized as smart-charging capable.
- Platform can apply setpoint control signals via charging profiles.

## 6) Usage and Local Development

### 6.1 Local Run Pattern

1. Setup Python venv.
2. Install requirements.
3. Start runtime or tests.

Example:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest tests -q --tb=short
```

If Windows socket policy blocks local socket tests, use:

```powershell
python -m pytest tests -q --tb=short --allow-hosts=127.0.0.1,localhost,::1
```

### 6.2 Example Port Skeleton

```text
platform_port/
  adapter.py               # implements platform adapter contract
  bootstrap.py             # setup/teardown lifecycle
  entity_mapper.py         # maps connector metrics to platform entities
  service_registry.py      # platform command/service handlers
  config_provider.py       # reads and validates runtime config
```

## 7) Troubleshooting for Porting

- **Message handled in one version only**
  - Check `@on(Action...)` handlers in `ocppv16.py` vs `ocppv201.py`.
- **Commands accepted but UI not changing**
  - Verify adapter state propagation and metric-to-entity mapping.
- **CallError/NotImplemented**
  - Missing handler on receiver side for that action.
- **Schema failures**
  - Validate enum values, required fields, and numeric/string types.
- **Cross-platform test differences**
  - Check socket policy, event loop policy, and Python version constraints.

## 8) Contribution and Governance for Platform Ports

### 8.1 Contribution Rules

- Isolate platform-specific code from protocol core.
- Add tests for each changed adapter/service behavior.
- Keep docs updated with compatibility matrix and known limitations.

### 8.2 Governance

- Assign code owners by layer:
  - Protocol core
  - Platform adapter(s)
  - Documentation and release engineering
- Require review from:
  - 1 protocol maintainer
  - 1 platform maintainer (for adapter/entity changes)
- Review/approval records remain transparent via repository PR history.

## 9) Versioning and Changelog Guidance

For platform port releases:

- Use semantic versioning.
- Initial release phase must stay in `0.x.y`.
- First major release is planned at end of initial release (`1.0.0` milestone).
- Keep explicit compatibility notes:
  - OCPP versions supported
  - Platform/runtime versions supported
- Changelog entry template:
  - Added / Changed / Fixed / Deprecated / Removed
  - Migration notes
  - Test evidence

## 10) Documentation Standards and Template Repository

### 10.1 Standards

- Use consistent terms: "asset", "setpoint", "control signal", "charger", "charge point", "connector", "EVSE", "CSMS".
- Keep fixed section order across docs:
  1. Overview
  2. Architecture
  3. Configuration
  4. Usage
  5. Troubleshooting
  6. Contribution/Governance
  7. Versioning/Changelog

### 10.2 Repository and Access Rules

- Store documentation in the same version-controlled repository as the source code.
- Use text-based Markdown files only (no binary documentation formats).
- Keep docs versioned and reviewed through standard PR workflow.
