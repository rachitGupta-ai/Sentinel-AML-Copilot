# Snowflake Seed — synthetic AML dataset generator

Spec `aml-regulatory-copilot`, **task 5.1**. Generates the **fully synthetic** demo dataset
(Req 2.1 — no real customer, account, or transaction data anywhere) and emits it as CSVs plus a
`COPY INTO` loader that targets the RAW tables from `snowflake/ddl/01_raw.sql`.

> Loading via streams/tasks (dedup + freshness) is **task 5.2**. This task generates the data and a
> plain bulk loader so the output is immediately loadable.

## Generate

```bash
# writes CSVs + load.sql + manifest.json into snowflake/seed/data/
python snowflake/seed/generate_seed.py

# custom location / seed
python snowflake/seed/generate_seed.py --out /tmp/seed --seed 42
```

The generator uses only the Python standard library and a single seeded RNG, so output is
**deterministic** — the demo and its KPIs reproduce identically on every run.

## What it produces

| File | Target table | Columns (mirror `01_raw.sql`) |
|------|--------------|-------------------------------|
| `entity.csv` | `RAW.ENTITY` | `entity_id, display_name_masked, attributes` |
| `transaction_event.csv` | `RAW.TRANSACTION_EVENT` | `event_id, entity_id, amount, currency, occurred_at, ingested_at, source, is_duplicate_suppressed` |
| `alert.csv` | `RAW.ALERT` | `alert_id, entity_id, typology, raised_at, correlation_key` |
| `policy_doc.csv` | `RAW.POLICY_DOC` | `policy_doc_id, title, body, source, version, ingested_at` |
| `manifest.json` | — | counts + structuring summary + injected-defect provenance |
| `load.sql` | — | `COPY INTO` loader for the four tables |

All ids/names are obviously fake (`ENT-...`, `TXN-...`, `Synthetic ... (FAKE)`) so the
synthetic-only contract (Req 2.1) is self-evident and checkable (task 5.4 smoke test).

## Structuring / smurfing typology (Req 2.2)

`SEM.ENTITY_RISK_METRICS.structuring_score` is the fraction of a trailing-90-day window's
transactions in the just-below-threshold band `9000 <= amount < 10000`, anchored on the entity's
latest `occurred_at`. The seed makes **`ENT-SMURF-001`** deposit ~18 amounts in that band over
< 60 days, so its `structuring_score ≈ 0.9` and clearly flags — enough to raise `ALERT-STRUCT-001`
and support a SAR. Normal entities (`ENT-NORMAL-002/003`) avoid the band, so their score is `0.0`.

Two structuring alerts on the same entity share a `correlation_key`, exercising
correlation/dedup into one case (Req 4.5) downstream.

## Injected data-quality defects (Req 2.3)

Recorded in `manifest.json → injected_defects`:

| Defect | Event id | Detail |
|--------|----------|--------|
| Missing field | `TXN-DEFECT-MISSING-CURRENCY` | empty `currency` |
| Duplicate event | `TXN-SMURF-000` (second copy) | re-uses an existing `event_id` (the dedup key, Req 2.5) so ingestion can suppress it |
| Stale / late record | `TXN-DEFECT-STALE-LATE` | `occurred_at` ~400 days old; `ingested_at` lags far behind (late arrival) |

The duplicate is emitted with `is_duplicate_suppressed = FALSE` on purpose: the ingestion
stream/task (task 5.2) is what suppresses it and records the suppression.

## Load into Snowflake

Run after the RAW DDL (`snowflake/ddl/01_raw.sql`). From the directory holding the generated files:

```bash
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/seed/data/load.sql
```

`load.sql` creates an internal stage + CSV file format, `PUT`s the CSVs, and `COPY INTO` each RAW
table. `attributes` is parsed into the `VARIANT` column via `TRY_PARSE_JSON`; empty fields load as
NULL so the injected missing-field defect round-trips.
