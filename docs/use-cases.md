<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# Key use cases

This document outlines three core residential flexibility use cases used as validation targets for the OCPP connector.

The use cases are protocol-agnostic, but this repository demonstrates them through OCPP charger control and telemetry.

---

## 1. Limiting peak grid demand

**Goal:** Keep household power usage within grid capacity limits.

**How it works:**

- A capacity constraint is provided to the local energy orchestration layer
- The platform translates that constraint into charger control signals
- The OCPP connector sends those control signals to charging assets and monitors compliance through meter values and status updates

---

## 2. Dynamic tariff optimization

**Goal:** Shift EV charging to lower-cost periods while respecting user and device constraints.

**How it works:**

- Time-varying price signals are evaluated in the platform runtime
- The runtime adjusts charge rates and charging windows
- The connector applies these decisions through OCPP control actions and reports resulting charger behavior

---

## 3. Maximizing self-consumption

**Goal:** Increase local use of on-site PV generation by charging EVs during surplus production windows.

**How it works:**

- The platform monitors solar production and household load
- Available surplus is converted to charging setpoints
- The connector continuously updates charger limits and verifies the applied charging behavior

---

## Implementation note

These use cases are baseline interoperability targets.
Repository documentation and examples should show that OCPP message handling, control actions, and telemetry paths support these outcomes.
