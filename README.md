[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/custom-components/hacs)
[![codecov](https://codecov.io/gh/lbbrhzn/ocpp/branch/main/graph/badge.svg?token=3FRJIF5KRW)](https://codecov.io/gh/lbbrhzn/ocpp)
[![Documentation Status](https://readthedocs.org/projects/home-assistant-ocpp/badge/?version=latest)](https://home-assistant-ocpp.readthedocs.io/en/latest/?badge=latest)
[![hacs_downloads](https://img.shields.io/github/downloads/lbbrhzn/ocpp/latest/total)](https://github.com/lbbrhzn/ocpp/releases/latest)

# OCPP integration repository

![OCPP](https://github.com/home-assistant/brands/raw/master/custom_integrations/ocpp/icon.png)

# ElaadNL Interoperability Reference Repository

This repository is part of the Residential Flexibility Interoperability RFP, initiated by ElaadNL and FAN under the Dutch ßNational Grid Congestion Action Program.

It serves as a shared documentation and collaboration baseline for all participating consortia working on open-source connectors between Home Energy Management Systems (HEMS) and flexible energy-intensive devices (FEIDs).

---

## What this repository includes

- A production integration for Home Assistant and OCPP chargers
- A reusable protocol-core architecture with platform adapter support
- Documentation for installation, operation, troubleshooting, and development
- Contributor guidance in `CONTRIBUTING.md`, `SECURITY.md`, and `SUPPORT.md`

---

## Protocol and runtime scope

- Protocols: OCPP 1.6j, OCPP 2.0.1, OCPP 2.1 (experimental)
- Runtime: Home Assistant
- Foundation package: [mobilityhouse/ocpp](https://github.com/mobilityhouse/ocpp)

---

## Documentation

- User and developer docs: [home-assistant-ocpp.readthedocs.io](https://home-assistant-ocpp.readthedocs.io)
- Repository docs index: [`docs/README.md`](./docs/README.md)
- Interoperability-aligned baseline docs:
  - [`docs/overview.md`](./docs/overview.md)
  - [`docs/use-cases.md`](./docs/use-cases.md)
  - [`docs/glossary.md`](./docs/glossary.md)

---

## Repository structure

```text
/
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
├── SUPPORT.md
├── custom_components/
│   └── ocpp/
│       ├── __init__.py            # Integration bootstrap and setup
│       ├── manifest.json          # Home Assistant integration metadata
│       ├── config_flow.py         # UI configuration flow
│       ├── api.py                 # Central system orchestration and services
│       ├── chargepoint.py         # Shared charge point behavior
│       ├── ocppv16.py             # OCPP 1.6 message handlers
│       ├── ocppv201.py            # OCPP 2.x message handlers
│       ├── platform_adapter.py    # Platform abstraction boundary
│       ├── smart_charging/        # Smart charging engine and types
│       └── translations/          # Localization files
├── tests/                         # Unit and integration-focused test suites
├── docs/                          # User and developer documentation source
├── scripts/                       # Local development and lint helper scripts
└── requirements.txt               # Python dependencies
```

---

## Support and contribution

- For support questions: see [`SUPPORT.md`](./SUPPORT.md)
- For bug reports and feature requests: use [GitHub issues](https://github.com/lbbrhzn/ocpp/issues)
- For contribution workflow: see [`CONTRIBUTING.md`](./CONTRIBUTING.md)
- For vulnerability reports: see [`SECURITY.md`](./SECURITY.md)

---
