<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# Glossary

This glossary defines common terms used in this repository.
It aligns local wording with interoperability deliverable terminology.

| **Term** | **Meaning in this repository** | **Usage / notes** |
|----------|--------------------------------|-------------------|
| **Asset** | A controllable EV charging device managed through OCPP | Used as a neutral term for charger/charge point in system-level docs |
| **Charge point** | OCPP endpoint representing the charger system | May include one or more connectors (EVSE outputs) |
| **Connector / EVSE** | Physical charging outlet exposed by a charge point | Multi-connector devices create per-connector state and controls |
| **CSMS** | Charging Station Management System role in OCPP | In this integration, CSMS behavior is implemented by connector runtime |
| **Control signal** | Command sent from runtime to charger | Examples: start/stop, availability change, profile update |
| **Setpoint** | Target value for charging behavior | Typically current or power limits used in smart charging |
| **Telemetry** | Runtime data reported by the charger | Includes measurands, status updates, and session information |
| **Measurand** | OCPP-defined measurement key | Example: `Power.Active.Import`, `Energy.Active.Import.Register` |
| **Profile** | Charging control profile applied through OCPP | Includes `ChargePointMaxProfile`, `TxProfile`, `TxDefaultProfile` |
| **Platform adapter** | Abstraction layer that connects protocol core to a host runtime | Enables reuse outside Home Assistant |

---

## Notes

- Use terms consistently across all docs, issues, and pull requests
- Prefer "asset", "control signal", and "setpoint" in cross-platform documentation
- Keep OCPP-specific names exact when referencing protocol fields or actions

