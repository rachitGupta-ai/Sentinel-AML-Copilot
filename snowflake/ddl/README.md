# Snowflake DDL — RAW / VEC / APP / AUDIT schemas

Physical model for the **SentinelAML Copilot** (spec `aml-regulatory-copilot`, task 3.2).
These scripts provision the source, embedding, workflow-state, and audit objects from the
design's *Snowflake physical model* table. The governed semantic layer (`SEM.ENTITY_RISK_METRICS`
+ `SEM.METRIC_DEFINITION_REGISTRY`) is provisioned separately in **task 4** under
`snowflake/semantic/`; streams/tasks, policies, and seed data come in tasks 5 and 8.

> All data loaded into these objects is **fully synthetic** — no real customer, account, or
> transaction data (Req 2.1).

## Scripts (run in order)

| # | File | Creates | Requirements |
|---|------|---------|--------------|
| 00 | `00_databases_schemas.sql` | Database, schemas `RAW/SEM/VEC/APP/AUDIT`, append-only writer role `AML_AUDIT_WRITER`, app role `AML_APP_ROLE` | 1.3, 8.3 |
| 01 | `01_raw.sql` | `RAW.TRANSACTION_EVENT`, `RAW.ALERT`, `RAW.ENTITY`, `RAW.POLICY_DOC` | 2.1 |
| 02 | `02_vec.sql` | `VEC.POLICY_CHUNK`, `VEC.EVIDENCE_CHUNK` (embeddings) | 2.1 |
| 03 | `03_app.sql` | `APP.CASE`, `APP.SAR_DRAFT`, `APP.APPROVAL` | 2.1 |
| 04 | `04_audit.sql` | `AUDIT.AUDIT_RECORD` (append-only; INSERT-only grants, UPDATE/DELETE denied) | 8.1, 8.2, **8.3** |

Column shapes mirror the pydantic domain models in `design.md` → *Data Models*
(`Entity`, `TransactionEvent`, `Alert`, `Case`, `SARDraft`, `Approval`, `AuditRecord`), so the
Snowflake connector round-trips cleanly into the Python records (task 3.1).

## Append-only audit guarantee (Req 8.3)

Immutability of `AUDIT.AUDIT_RECORD` is enforced **at the grant level**, not by application code:

- The `AML_AUDIT_WRITER` role is granted **`INSERT` + `SELECT` only**.
- `UPDATE`, `DELETE`, and `TRUNCATE` are **never granted** and are **explicitly revoked** (defensive).
- The application runtime role (`AML_APP_ROLE`) inherits `AML_AUDIT_WRITER`, so it can append and
  read audit rows but cannot mutate them.
- A mutation attempt therefore fails authorization; the service records that rejected attempt as a
  **new** audit row (Property 30), leaving replay (Property 31) intact.

Verify after provisioning:

```sql
SHOW GRANTS ON TABLE AUDIT.AUDIT_RECORD;
-- Expect only INSERT / SELECT for AML_AUDIT_WRITER (and via inheritance AML_APP_ROLE).
-- No UPDATE / DELETE / TRUNCATE should appear.
```

## Prerequisites

- **Snowflake CoCo CLI** (`snow`) installed and on `PATH`.
- A configured CoCo CLI connection whose role can `CREATE DATABASE` and `CREATE ROLE`
  (ACCOUNTADMIN / SECURITYADMIN or a delegated role) for the initial provisioning run.
- The connection supplies account / user / role / warehouse; the DDL stays database-name agnostic
  via the `db` variable (default `GOVERNED_AML`). These correspond to the config-only settings
  (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`,
  `SNOWFLAKE_DATABASE`) read by the backend (Req 1.1).

## Exact CoCo CLI provisioning commands

Run from the repository root. `-c <connection>` selects your configured CoCo CLI connection;
`-D db=<DB>` sets the target database name (defaults to `GOVERNED_AML` if omitted).

```bash
# 0) (one-time) create / verify a connection
snow connection add --connection-name governed-aml
snow connection test --connection governed-aml

# 1) provision in order (schemas -> raw -> vec -> app -> audit)
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/ddl/00_databases_schemas.sql
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/ddl/01_raw.sql
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/ddl/02_vec.sql
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/ddl/03_app.sql
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/ddl/04_audit.sql
```

> **Note on `-D` / variable substitution.** The scripts use a server-side session variable
> (`SET db = COALESCE($db, 'GOVERNED_AML')` + `IDENTIFIER($db)`), which `snow sql` resolves at
> execution time. On Snowflake CLI v2, `-D db=GOVERNED_AML` additionally supports client-side
> templating with the `&{ db }` form; the scripts here rely only on the server-side `$db` variable
> so they work on both. If your connection already pins a default database you can omit `-D db=...`
> and the default `GOVERNED_AML` is used.

One-liner (independent files, still ordered because each depends on the prior):

```bash
for f in 00_databases_schemas 01_raw 02_vec 03_app 04_audit; do
  snow sql -c governed-aml -D db=GOVERNED_AML -f "snowflake/ddl/${f}.sql" || break
done
```

## Idempotency & re-runs

Every object uses `CREATE ... IF NOT EXISTS`, so the scripts are safe to re-run on an existing
account; the `REVOKE` statements in `04_audit.sql` are idempotent (revoking an absent privilege is
a no-op). A full teardown is out of scope for the MVP (audit records are retained for the demo
lifetime unaltered — Req 8.5).

## Downstream (not in this task)

- `snowflake/semantic/` — `SEM.ENTITY_RISK_METRICS` semantic view + `SEM.METRIC_DEFINITION_REGISTRY` (task 4).
- `snowflake/streams/` + `snowflake/tasks/` — `RAW.TRANSACTION_STREAM` ingest, dedup, freshness (task 5).
- `snowflake/policies/` — masking + row-access policies on `RAW.*` for non-entitled roles (task 8.2); see `snowflake/policies/README.md`. Paired with application-layer RBAC in `backend/app/services/auth_service.py` (Req 9.2) and helpers in `backend/app/snowflake/policies.py` (Req 9.3).
- `snowflake/seed/` — synthetic dataset generator + loader (task 5, task 21).
