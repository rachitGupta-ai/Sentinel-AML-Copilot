-- =============================================================================
-- SentinelAML Copilot — RAW.TRANSACTION_STREAM (stream-based ingestion source)
-- Spec: aml-regulatory-copilot  |  Task 5.2  |  Requirements: 2.4, 2.5, 2.6
-- =============================================================================
-- Stream/task ingestion for Transaction_Events (design.md "Snowflake physical
-- model": RAW.TRANSACTION_STREAM + ingest task; dedup; record ingested_at/source;
-- freshness indicator). This script provisions the LANDING table and the STREAM;
-- the ingest TASK lives in snowflake/tasks/30_ingest_transaction_task.sql and the
-- freshness view in snowflake/streams/31_data_freshness.sql.
--
-- Design intent (why a landing table + stream, not loading straight into
-- RAW.TRANSACTION_EVENT):
--   * RAW.TRANSACTION_EVENT is the governed, deduplicated store the semantic view
--     reads from (snowflake/semantic/20_entity_risk_metrics.sql filters on
--     is_duplicate_suppressed = FALSE). We must not let unprocessed duplicates or
--     un-provenanced rows into it.
--   * New rows land in RAW.TRANSACTION_LANDING (raw arrivals, exactly as received).
--   * A STREAM on the landing table (RAW.TRANSACTION_STREAM) exposes only the
--     newly-arrived rows as a change set (Req 2.4 "ingest ... via a stream/task").
--   * The ingest TASK consumes the stream, stamps ingestion provenance
--     (ingested_at + source — Req 2.4), and MERGEs into RAW.TRANSACTION_EVENT,
--     deduplicating by event_id and recording each suppression (Req 2.5).
--
-- Multi-replica safety: the stream + task pattern is a single Snowflake-side
-- consumer, so concurrent backend replicas never double-ingest — Snowflake
-- advances the stream offset transactionally when the task MERGE commits.
--
-- Run AFTER:
--   * snowflake/ddl/00_databases_schemas.sql  (RAW schema, AML_APP_ROLE)
--   * snowflake/ddl/01_raw.sql                (RAW.TRANSACTION_EVENT)
-- Then run, in order:
--   * snowflake/streams/31_data_freshness.sql
--   * snowflake/tasks/30_ingest_transaction_task.sql
-- =============================================================================

-- Keep database-name agnostic, consistent with snowflake/ddl/00_databases_schemas.sql.
SET db = 'GOVERNED_AML';
USE DATABASE IDENTIFIER($db);
USE SCHEMA RAW;

-- -----------------------------------------------------------------------------
-- RAW.TRANSACTION_LANDING — raw arrival buffer for incoming Transaction_Events.
-- Rows are inserted here by the loader / connector exactly as received (the seed
-- loader can also target this table for a streamed demo). Columns mirror the
-- inbound shape; ingestion provenance and dedup are applied downstream by the
-- task when rows move into RAW.TRANSACTION_EVENT, so provenance columns here are
-- optional and may be NULL on arrival.
--
-- A landing-side arrival timestamp (landed_at) defaults to the load time so even
-- a row that arrives without an explicit source is attributable; the task still
-- stamps the governed ingested_at/source on the RAW.TRANSACTION_EVENT row.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS TRANSACTION_LANDING (
    event_id      STRING          NOT NULL,   -- dedup key (Req 2.5)
    entity_id     STRING          NOT NULL,
    amount        NUMBER(38, 2),              -- Decimal
    currency      STRING,
    occurred_at   TIMESTAMP_NTZ,              -- business time
    source        STRING,                     -- optional inbound source hint (Req 2.4)
    landed_at     TIMESTAMP_NTZ   DEFAULT CURRENT_TIMESTAMP()  -- arrival time (fallback provenance)
)
COMMENT = 'Raw arrival buffer for Transaction_Events; the ingest task consumes the stream over this table into RAW.TRANSACTION_EVENT with provenance + dedup (Req 2.4, 2.5).';

-- -----------------------------------------------------------------------------
-- RAW.TRANSACTION_STREAM — stream over the landing table (Req 2.4).
-- An append-only stream (INSERT-only landing) yields the set of newly-arrived
-- rows since the last task run. SHOW_INITIAL_ROWS = TRUE means the first task run
-- also processes rows already present when the stream was created, so a one-shot
-- seed load is ingested too.
-- -----------------------------------------------------------------------------
CREATE STREAM IF NOT EXISTS TRANSACTION_STREAM
    ON TABLE TRANSACTION_LANDING
    APPEND_ONLY = TRUE
    SHOW_INITIAL_ROWS = TRUE
    COMMENT = 'Change stream of newly-landed Transaction_Events consumed by the ingest task (Req 2.4). Append-only; advances transactionally on task MERGE.';

-- -----------------------------------------------------------------------------
-- Grants to the application runtime role.
-- The app may land rows (INSERT) and read the stream/landing for diagnostics.
-- Writes into RAW.TRANSACTION_EVENT happen inside the task (owner's rights).
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT ON TABLE  TRANSACTION_LANDING TO ROLE AML_APP_ROLE;
GRANT SELECT        ON STREAM  TRANSACTION_STREAM  TO ROLE AML_APP_ROLE;
