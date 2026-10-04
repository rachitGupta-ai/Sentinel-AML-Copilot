# Snowflake policies — masking & row-access (role-based data access)

Physical, Snowflake-native enforcement of **Req 9.3** for the **SentinelAML Copilot**
(spec `aml-regulatory-copilot`, task 8.2). These policies mask sensitive synthetic fields for
roles that are **not** entitled to see them and restrict sensitive rows from non-entitled roles,
so visibility is governed by the effective role **inside Snowflake** at query time (Property 34).

This complements the **application-layer RBAC** on protected actions (view case / approve / change
metric definition) in `backend/app/services/auth_service.py` (**Req 9.2**). Together they satisfy
Req 9.2 / 9.3.

> All data governed by these policies is **fully synthetic** — no real customer, account, or
> transaction data (Req 2.1).

## Script

| # | File | Creates | Requirements |
|---|------|---------|--------------|
| 05 | `05_policies.sql` | Entitlement roles (`AML_COMPLIANCE_OFFICER`/`AML_METRIC_STEWARD`/`AML_ANALYST`/`AML_VIEWER`), masking policies `MASK_DISPLAY_NAME` + `MASK_ACCOUNT_IDENTIFIER`, row-access policy `RAP_ENTITY_RESTRICTED`, and their column/table bindings | **9.3**, 9.2 |

Run **after** the DDL scripts `00_databases_schemas.sql` and `01_raw.sql` (the policies attach to
`RAW.ENTITY` and `RAW.TRANSACTION_EVENT`).

## Entitlement model (mirrors `app.services.auth_service.Role`)

| App `Role` | Snowflake role | Sensitive fields | Row access |
|---|---|---|---|
| `compliance_officer` | `AML_COMPLIANCE_OFFICER` | **raw** (entitled) | all rows |
| `metric_steward` | `AML_METRIC_STEWARD` | **raw** (entitled) | all rows |
| `analyst` | `AML_ANALYST` | **masked** | non-restricted rows only |
| `viewer` | `AML_VIEWER` | **masked** | non-restricted rows only |

The runtime connection role `AML_APP_ROLE` (DDL script 00) activates these as **secondary roles**
so Snowflake evaluates the policies by the request's effective role — which is what makes masking
*provable per role* ("same answer, provably" — Req 11.5).

## What is masked / restricted (Req 9.3, Property 34)

- `RAW.ENTITY.display_name_masked` → `MASK_DISPLAY_NAME`: raw for entitled roles, `***MASKED***`
  otherwise (design "Data Models": *masked per role*).
- `RAW.TRANSACTION_EVENT.entity_id` (account identifier) → `MASK_ACCOUNT_IDENTIFIER`: raw for
  entitled roles, last-4 only otherwise.
- `RAW.ENTITY` rows flagged `attributes:restricted = true` → `RAP_ENTITY_RESTRICTED`: visible only
  to entitled / system roles; hidden from analysts and viewers.

Policies are **deterministic** — visibility depends only on the current role (plus a row flag for
row access) — so a governed numeric figure stays role-invariant (Property 7) while sensitive
display/identifier text is masked per entitlement (Property 34).

## Python helpers (`backend/app/snowflake/policies.py`)

- `snowflake_role_for(Role)` — the Snowflake role name backing an application `Role`.
- `is_entitled_to_sensitive(Role)` — predicts, consistent with the masking policy, whether a role
  sees sensitive fields unmasked (compliance officer / metric steward only; fail-closed otherwise).
- `apply_policies(session, db=...)` — (re)applies `05_policies.sql` through a Snowpark session
  (idempotent; never crashes, returns a `PolicyApplicationResult`).
- Object-name constants: `MASKING_POLICY_DISPLAY_NAME`, `MASKING_POLICY_ACCOUNT_IDENTIFIER`,
  `ROW_ACCESS_POLICY_ENTITY`.

## Exact CoCo CLI command

Run from the repository root (same `-c` / `-D db=` convention as the DDL):

```bash
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/policies/05_policies.sql
```

## Verify per-role behaviour (Property 34)

```sql
-- Confirm policies are attached:
SELECT * FROM TABLE(INFORMATION_SCHEMA.POLICY_REFERENCES(
    REF_ENTITY_NAME => 'RAW.ENTITY', REF_ENTITY_DOMAIN => 'TABLE'));

-- Entitled role sees raw; non-entitled sees masked (same stored value):
USE ROLE AML_COMPLIANCE_OFFICER; SELECT display_name_masked FROM RAW.ENTITY LIMIT 5;  -- raw
USE ROLE AML_ANALYST;            SELECT display_name_masked FROM RAW.ENTITY LIMIT 5;  -- ***MASKED***
```

## Idempotency & re-runs

Roles and policies use `CREATE ... IF NOT EXISTS`; re-binding a policy already attached to a column
is a benign no-op/error, so the script is safe to re-run on an existing account.
