# Snowflake Semantic Layer — governed metrics (`SEM`)

Governed semantic layer for the **SentinelAML Copilot** (spec `aml-regulatory-copilot`, task 4.1).
These scripts provision the **only** source of regulatory/risk figures in the system: a versioned
metric catalog plus the governed semantic view that computes the figures. Per the design, the LLM
never computes or alters a regulatory number — it may only narrate around figures that these objects
have already computed (Req 3.2).

> All data read by these objects is **fully synthetic** — no real customer, account, or transaction
> data (Req 2.1).

## Scripts (run in order)

| # | File | Creates | Requirements |
|---|------|---------|--------------|
| 10 | `10_metric_definition_registry.sql` | `SEM.METRIC_DEFINITION_REGISTRY` — metric definitions, `Metric_Definition_Version`, glossary synonyms; seeds `exposure_90d`, `txn_velocity`, `structuring_score` at `v1` | 3.1, 3.4, 3.5 |
| 20 | `20_entity_risk_metrics.sql` | `SEM.ENTITY_RISK_METRICS` (wide) + `SEM.ENTITY_RISK_METRIC_VALUES` (long) governed views, backed by `SEM.ENTITY_RISK_METRICS_BASE` | 3.1, 3.2, 3.3, 3.4 |

Run **after** the base DDL in `snowflake/ddl/` (`00_databases_schemas.sql` creates the `SEM` schema
and `AML_APP_ROLE`; `01_raw.sql` creates `RAW.TRANSACTION_EVENT` and `RAW.ENTITY`, the inputs to the
view). Script `20` depends on `10` (it joins the registry to stamp each figure with its version).

## Governed metrics (MVP)

| `metric_name` | Definition (summary) | Unit | Version |
|---|---|---|---|
| `exposure_90d` | Total transacted amount over the trailing 90-day window (duplicate-suppressed events excluded). | amount | `v1` |
| `txn_velocity` | Average transactions/day over the trailing 90-day window (`count / 90`). | count_per_day | `v1` |
| `structuring_score` | Fraction of trailing-90-day txns in the just-below-threshold band (`9000 ≤ amount < 10000`), `0.0–1.0`. | score_0_1 | `v1` |

The full plain-language definition for each lives in `METRIC_DEFINITION_REGISTRY.definition` and is
the governed contract; the computation in `ENTITY_RISK_METRICS` must match it. The trailing-90-day
window is anchored on each entity's latest business-time event (data-state anchored, **not**
wall-clock), so a fixed data snapshot yields identical values and the same version for every caller
(Req 3.3).

## Versioning (Req 3.4)

Changing a metric definition is **append-only**: insert a new row for the same `metric_name` with a
new `metric_definition_version`, set `is_current = TRUE` on it, and set the prior row's `is_current`
to `FALSE`. The views join `is_current = TRUE`, so every value produced after the change is stamped
with the new version; prior version rows are retained for lineage. `AML_APP_ROLE` is granted
`SELECT`/`INSERT` only — never `UPDATE`/`DELETE` — so version history cannot be rewritten.

## Glossary synonyms (Req 3.5)

`METRIC_DEFINITION_REGISTRY.glossary_synonyms` holds the lower-cased business terms that resolve to
each canonical metric. The metric service's `resolve_term(term)` matches an input term against these
arrays and returns the canonical `metric_name`, or `Ambiguous` when a term maps to more than one
canonical metric. Example resolution:

```sql
-- resolve_term('smurfing') -> 'structuring_score'
SELECT metric_name
FROM SEM.METRIC_DEFINITION_REGISTRY
WHERE is_current = TRUE
  AND ARRAY_CONTAINS('smurfing'::VARIANT, glossary_synonyms);
```

## Prerequisites

- **Snowflake CoCo CLI** (`snow`) installed and on `PATH`.
- A configured CoCo CLI connection whose role can create views/tables in the `SEM` schema.
- The base DDL in `snowflake/ddl/` already provisioned (see `snowflake/ddl/README.md`). The scripts
  stay database-name agnostic via the `db` variable (default `GOVERNED_AML`), matching the base DDL.

## Exact CoCo CLI provisioning commands

Run from the repository root. `-c <connection>` selects your configured CoCo CLI connection;
`-D db=<DB>` sets the target database name (defaults to `GOVERNED_AML` if omitted). This continues
the ordered sequence documented in `snowflake/ddl/README.md` (scripts `00`–`04` first).

```bash
# provision the governed semantic layer (registry -> views)
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/semantic/10_metric_definition_registry.sql
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/semantic/20_entity_risk_metrics.sql
```

One-liner (ordered — `20` depends on `10`):

```bash
for f in 10_metric_definition_registry 20_entity_risk_metrics; do
  snow sql -c governed-aml -D db=GOVERNED_AML -f "snowflake/semantic/${f}.sql" || break
done
```

> **Variable substitution** behaves exactly as in `snowflake/ddl/README.md`: the scripts rely on the
> server-side `$db` session variable (`SET db = COALESCE($db, 'GOVERNED_AML')` + `IDENTIFIER($db)`),
> so they work whether or not `-D db=...` is passed. If your connection already pins a default
> database you can omit `-D db=...`.

## Idempotency & re-runs

- `METRIC_DEFINITION_REGISTRY` uses `CREATE TABLE IF NOT EXISTS` and a `MERGE` seed, so re-running
  `10` neither drops the table nor duplicates the seeded `v1` rows.
- The views use `CREATE OR REPLACE VIEW`, so re-running `20` is safe and refreshes the definitions.

## Verify after provisioning

```sql
-- every seeded metric present at a current version
SELECT metric_name, metric_definition_version, is_current
FROM SEM.METRIC_DEFINITION_REGISTRY ORDER BY metric_name;

-- governed figures per entity, each stamped with its definition version
SELECT * FROM SEM.ENTITY_RISK_METRIC_VALUES ORDER BY entity_id, metric_name;
```

## Downstream (not in this task)

- `app/services/metric` — Governed Metric Service: `get_metric`, `aggregate_entity_risk`,
  `resolve_term` read **only** these views/registry (task 4.2).
- `snowflake/streams/` + `snowflake/tasks/` — ingestion that populates `RAW.TRANSACTION_EVENT`
  (task 5), the input to these metrics.
