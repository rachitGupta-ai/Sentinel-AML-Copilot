# Snowflake Streams & Tasks — ingestion, dedup, freshness

Stream/task ingestion for the **SentinelAML Copilot** (spec `aml-regulatory-copilot`, task 5.2).
These scripts land incoming Transaction_Events, consume them with a stream + task that stamps
ingestion provenance and deduplicates by `event_id`, and expose a data-freshness indicator for the
UI (Req 2.4, 2.5, 2.6).

> All data flowing through these objects is **fully synthetic** — no real customer, account, or
> transaction data (Req 2.1).

## Why a landing table + stream (not a direct load)

`RAW.TRANSACTION_EVENT` is the governed, **deduplicated** store the semantic view reads from
(`snowflake/semantic/20_entity_risk_metrics.sql` filters `is_duplicate_suppressed = FALSE`). So raw
arrivals never go straight into it. Instead:

1. New rows land in `RAW.TRANSACTION_LANDING` (exactly as received).
2. `RAW.TRANSACTION_STREAM` (append-only stream over the landing table) exposes only the newly
   arrived rows (Req 2.4).
3. The task `RAW.INGEST_TRANSACTION_EVENTS` consumes the stream, stamps `ingested_at` + `source`
   (Req 2.4), deduplicates by `event_id`, records each suppression in
   `RAW.TRANSACTION_SUPPRESSION_LOG` (Req 2.5), and `MERGE`s the keepers into
   `RAW.TRANSACTION_EVENT`.

The stream + task is a **single Snowflake-side consumer**, so concurrent backend replicas never
double-ingest — Snowflake advances the stream offset transactionally when the task `MERGE` commits.

## Scripts (run in order)

| # | File | Dir | Creates | Requirements |
|---|------|-----|---------|--------------|
| 30 | `30_transaction_stream.sql` | `streams/` | `RAW.TRANSACTION_LANDING` + `RAW.TRANSACTION_STREAM` | 2.4, 2.5 |
| 31 | `31_data_freshness.sql` | `streams/` | `RAW.DATA_FRESHNESS` view (latest ingested event time + no-data state) | 2.6 |
| 30 | `30_ingest_transaction_task.sql` | `tasks/` | `RAW.TRANSACTION_SUPPRESSION_LOG` + `RAW.INGEST_TRANSACTION_EVENTS` task | 2.4, 2.5 |

Run **after** the base DDL in `snowflake/ddl/` (`00_databases_schemas.sql` creates the `RAW` schema
and `AML_APP_ROLE`; `01_raw.sql` creates `RAW.TRANSACTION_EVENT`). The task script depends on the
stream script (it reads `RAW.TRANSACTION_STREAM`).

## Dedup & provenance semantics (Req 2.4, 2.5 — Properties 3, 4)

- **Provenance (Property 3):** every row the task writes into `RAW.TRANSACTION_EVENT` gets a non-null
  `ingested_at` (task run time) and a populated `source` (the inbound hint, else
  `stream-ingest-task`).
- **Dedup by event id (Property 4):** the governed store keeps exactly one row per `event_id`.
  - In-batch duplicates: the first arrival by `(landed_at, occurred_at, entity_id)` is kept; the rest
    are suppressed with reason `duplicate_in_batch`.
  - Already-stored ids: a new arrival of an existing `event_id` is suppressed with reason
    `duplicate_event_id` and not re-inserted.
  - Therefore **distinct stored ids == input ids** and **suppression count = total − distinct**.
- The kept governed row keeps `is_duplicate_suppressed = FALSE`; suppression is recorded in
  `RAW.TRANSACTION_SUPPRESSION_LOG`, not by mutating the governed row. The duplicate-suppression KPI
  (task 17) counts rows in that log.

## Data-freshness indicator (Req 2.6 — Property 5)

`RAW.DATA_FRESHNESS` always returns exactly one row:

| Column | Meaning |
|---|---|
| `latest_event_time` | `MAX(occurred_at)` over non-suppressed ingested rows — the maximum event time (Property 5). `NULL` when no data. |
| `latest_ingested_at` | `MAX(ingested_at)` — latest ingestion stamp ("latest ingested event time" phrasing of Req 2.6). |
| `has_data` | `FALSE` reports the defined **no-data** state (Property 5). |
| `ingested_event_count` | Count of non-suppressed ingested rows. |
| `is_stale` | Left `FALSE` here; triage sets staleness against its configured freshness threshold (Req 4.4). |

The UI binds `latest_event_time`/`has_data`/`is_stale` to the `FreshnessIndicator` model via the
Python helper `backend/app/snowflake/freshness.py` (`read_freshness(session)`), which degrades to the
defined no-data state on any read error.

## Prerequisites

- **Snowflake CoCo CLI** (`snow`) installed and on `PATH`.
- A configured CoCo CLI connection whose role can create streams/tasks and `EXECUTE TASK` on the
  account (to resume the task). The base DDL in `snowflake/ddl/` already provisioned.
- A warehouse for the task to run on. The task binds `$ingest_warehouse` (default `COMPUTE_WH`);
  override with `-D ingest_warehouse=<WH>` to match your connection's warehouse.

## Exact CoCo CLI provisioning commands

Run from the repository root. Continues the ordered sequence in `snowflake/ddl/README.md`
(scripts `00`–`04`) and `snowflake/semantic/README.md` (`10`–`20`).

```bash
# 1) landing table + stream
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/streams/30_transaction_stream.sql

# 2) freshness indicator view
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/streams/31_data_freshness.sql

# 3) suppression log + ingest task (binds the task warehouse; override as needed)
snow sql -c governed-aml -D db=GOVERNED_AML -D ingest_warehouse=COMPUTE_WH \
  -f snowflake/tasks/30_ingest_transaction_task.sql
```

The task script `RESUME`s the task so it begins honouring its 1-minute schedule and only does work
when `SYSTEM$STREAM_HAS_DATA` is true (cost-safe). For the demo you can land seed rows and trigger
ingestion immediately rather than waiting for the next tick:

```sql
EXECUTE TASK RAW.INGEST_TRANSACTION_EVENTS;   -- one-shot manual run
```

> **Variable substitution** behaves exactly as in `snowflake/ddl/README.md`: the scripts rely on the
> server-side `$db` session variable, so they work whether or not `-D db=...` is passed.

## Loading data into the stream (demo)

The seed loader (`snowflake/seed/data/load.sql`) currently `COPY INTO RAW.TRANSACTION_EVENT`
directly. To exercise the stream/task path instead, land the synthetic CSV into
`RAW.TRANSACTION_LANDING` (same columns minus the governed provenance/dedup, which the task adds),
then run the task. The duplicate and stale/late defects injected by `generate_seed.py` (Req 2.3)
then flow through dedup + freshness exactly as a real feed would.

## Idempotency & re-runs

- `TRANSACTION_LANDING`, `TRANSACTION_STREAM`, `TRANSACTION_SUPPRESSION_LOG`, and the task all use
  `CREATE ... IF NOT EXISTS`; `DATA_FRESHNESS` uses `CREATE OR REPLACE VIEW`. Safe to re-run.
- `ALTER TASK ... RESUME` is idempotent. The `MERGE` is `WHEN NOT MATCHED`-only, so re-processing a
  row that is already stored suppresses it rather than inserting a duplicate (dedup holds across
  runs).

## Verify after provisioning

```sql
-- stream has pending rows?
SELECT SYSTEM$STREAM_HAS_DATA('RAW.TRANSACTION_STREAM');

-- distinct governed ids == what you loaded; suppressions logged separately
SELECT COUNT(*) AS distinct_events FROM RAW.TRANSACTION_EVENT;
SELECT COUNT(*) AS suppressed      FROM RAW.TRANSACTION_SUPPRESSION_LOG;

-- freshness indicator (one row; no-data state when empty)
SELECT * FROM RAW.DATA_FRESHNESS;
```

## Downstream (not in this task)

- `app/services/triage` — reads `RAW.DATA_FRESHNESS` for case staleness (Req 4.4, task 9).
- `app/services/kpi` — counts `RAW.TRANSACTION_SUPPRESSION_LOG` for the duplicate-suppression KPI
  (Req 10.3, task 17).
