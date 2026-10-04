# SentinelAML Copilot — Backend

AML suspicious-activity investigation and audit-ready SAR (Suspicious Activity
Report) reporting copilot, built natively on the **Snowflake AI Data Cloud**
(Python 3.11 + FastAPI). See the full spec under
`.kiro/specs/aml-regulatory-copilot/`.

## Layout

```
backend/
  app/
    api/          # FastAPI routers (ingest, cases, investigate, sar, approval, audit, kpi, auth)
    services/     # interface + Impl services (triage, metric, investigation, grounding, ...)
    snowflake/    # session factory, startup guard, semantic-view accessors, Cortex adapter, policies
    models/       # pydantic domain + document models
    config/       # pydantic-settings Settings + startup credential guard
    main.py       # app factory + startup guard wiring
  tests/
    property/     # hypothesis property tests (min 100 iterations)
    unit/ integration/ smoke/
```

## Setup

Requires **Python 3.11**.

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt   # or: pip install -e ".[dev]"
```

## Run

```bash
uvicorn app.main:app --reload
```

Connection settings are read from environment/configuration only — no secrets
are embedded in source, prompts, or logs (Req 1.1). Startup fails loudly if any
required setting is missing (Req 1.2); this guard is implemented in tasks 1.2
and 2.1.

## Test

```bash
pytest                 # all tests
pytest -m property     # property-based tests only
```
