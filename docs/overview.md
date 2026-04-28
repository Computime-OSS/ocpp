<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# Project overview

This document provides background and scope for the OCPP connector repository.
It aligns repository documentation with the interoperability reference structure while describing this implementation's goals.

---

## Purpose of this repository

This repository contains an open-source OCPP connector used to integrate EV charging assets with Home Assistant and other platform runtimes through an adapter model.

Key goals:

- Enable interoperable control and telemetry exchange between platform logic and OCPP-compliant chargers
- Preserve a portable protocol core that can be reused across runtime environments
- Provide clear implementation and porting documentation for contributors
- Keep documentation aligned with shared interoperability templates and terminology

---

## Scope of the connector

This project focuses on OCPP-based communication and control for EV charging infrastructure, including:

- OCPP 1.6
- OCPP 2.0.1
- OCPP 2.1 (experimental in this codebase)

The repository includes protocol handlers, platform integration logic, and supporting documentation.
It does not include a generic HEMS implementation or non-OCPP protocol connectors.

---

## System context

In a residential flexibility setup, the platform runtime (for example Home Assistant) acts as the local orchestrator for devices.
The OCPP connector operates as the communication boundary between the runtime and EV chargers over WebSocket transport.

The connector receives charger telemetry and status updates, translates them into platform-level entities, and applies control signals back to chargers through supported OCPP actions.

---

## Relationship to connector deliverables

This repository documentation is organized so that:

- Shared deliverables (`documentation-index`, `overview`, `use-cases`, `glossary`, `style-guide`) provide baseline interoperability alignment
- Implementation deliverables describe connector architecture, configuration, operation, and troubleshooting
- Versioned changes are tracked in `changelog.md` and should stay synchronized with integration release metadata
