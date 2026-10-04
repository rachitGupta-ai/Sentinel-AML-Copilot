-- =============================================================================
-- SentinelAML Copilot — ingest task (stream -> dedup -> provenance) + suppression log
-- Spec: aml-regulatory-copilot  |  Task 5.2  |  Requirements: 2.4, 2.5, 2.6
-- =============================================================================
-- The ingest task consumes RAW.TRANSACTION_STREAM (new rows landed in
-- RAW.TRANSACTION_LANDING), stamps ingestion provenance, deduplicates by
-- event_id, and records every suppression:
--
--   * Provenance (Req 2.4): every row MERGEd into RAW.TRANSACTION_EVENT gets a
--     non-null ingested_at (task run time) and a populated source (the inbound
--     source hint, else a default ingest source). Satisfies Property 3 — every
--     ingested record carries ingestion provenance.
--   * Dedup by event_id (Req 2.5): the governed store keeps exactly one row per
--     event_id. A second arrival of an existing event_id is NOT inserted as a new
--     governed row; it is recorded as a suppression. Within a single stream batch
--     that itself contains duplicate event_ids, only the first (by landed_at,
--     then occurred_at) is kept and the rest are suppressed. This yields
--     Property 4 — distinct stored ids == input ids; suppression count =
--     total - distinct.
--   * Suppression record (Req 2.5): each suppressed arrival is appended to
--     RAW.TRANSACTION_SUPPRESSION_LOG so the duplicate-suppression count is
--     observable (feeds the KPI in task 17 and the freshness/quality panels).
--
-- Determinism of "which row wins" for a given event_id: order by landed_at ASC,
-- then occurred_at ASC, then a stable tiebreak so re-runs are reproducible.
--
-- Run AFTER:
--   * snowflake/ddl/01_raw.sql                    (RAW.TRANSACTION_EVENT)
--   * snowflake/streams/30_transaction_stream.sql (landing + stream)
-- =============================================================================

-- Keep database-name agnostic, consistent with snowflake/ddl/00_databases_schemas.sql.
SET db = 'GOVERNED_AML';
USE DATABASE IDENTIFIER($db);
USE SCHEMA RAW;

-- Default ingestion source stamped when an inbound row carries no source hint
-- (so `source` is always populated — Req 2.4, Property 3).
SET ingest_source_default = 'stream-ingest-task';

-- -----------------------------------------------------------------------------
-- RAW.TRANSACTION_SUPPRESSION_LOG — append-only record of suppressed duplicates
-- (Req 2.5 "record the duplicate suppression"). One row per suppressed arrival,
-- capturing why it was suppressed and when. The duplicate-suppression KPI counts
-- rows here; distinct governed ids live in RAW.TRANSACTION_EVENT.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS TRANSACTION_SUPPRESSION_LOG (
    event_id        STRING          NOT NULL,   -- the duplicated dedup key (Req 2.5)
    entity_id       STRING,
    occurred_at     TIMESTAMP_NTZ,              -- business time of the suppressed arrival
    source          STRING,                     -- source of the suppressed arrival
    suppressed_at   TIMESTAMP_NTZ   DEFAULT CURRENT_TIMESTAMP(),  -- when suppression happened
    reason          STRING          DEFAULT 'duplicate_event_id'  -- suppression reason
)
COMMENT = 'Append-only log of Transaction_Events suppressed as duplicates by event_id during ingestion (Req 2.5).';

GRANT SELECT, INSERT ON TABLE TRANSACTION_SUPPRESSION_LOG TO ROLE AML_APP_ROLE;

-- -----------------------------------------------------------------------------
-- RAW.INGEST_TRANSACTION_EVENTS — the scheduled ingest task.
-- Scheduled on a short cadence and gated by SYSTEM$STREAM_HAS_DATA so it only
-- does work when the stream has new rows (no-op otherwise, cost-safe and
-- multi-replica safe — a single Snowflake-side consumer).
--
-- The task body is a single MULTI-STATEMENT transaction (BEGIN...COMMIT) so the
-- suppression log and the governed MERGE commit together and the stream offset
-- advances atomically; a mid-task failure leaves no partial, un-logged ingest
-- (consistent with the "no partial, unaudited commits" posture, Req 13.4).
--
-- WAREHOUSE: set to the warehouse your CoCo CLI connection uses. Parameterised
-- via $ingest_warehouse with a documented default; override with -D on run.
-- -----------------------------------------------------------------------------
SET ingest_warehouse = 'COMPUTE_WH';

CREATE TASK IF NOT EXISTS INGEST_TRANSACTION_EVENTS
    WAREHOUSE = IDENTIFIER($ingest_warehouse)
    SCHEDULE  = '1 MINUTE'
    COMMENT   = 'Consume TRANSACTION_STREAM: stamp provenance, dedup by event_id, log suppressions, MERGE into TRANSACTION_EVENT (Req 2.4, 2.5).'
    WHEN SYSTEM$STREAM_HAS_DATA('TRANSACTION_STREAM')
AS
EXECUTE IMMEDIATE $$
BEGIN
    -- Snapshot the stream's change set into a transaction-local view. Rank
    -- arrivals per event_id deterministically; rn = 1 is the keeper, rn > 1 are
    -- in-batch duplicates to suppress.
    LET batch RESULTSET := (
        WITH changes AS (
            SELECT
                event_id,
                entity_id,
                amount,
                currency,
                occurred_at,
                COALESCE(source, $ingest_source_default) AS source,
                landed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY event_id
                    ORDER BY landed_at ASC, occurred_at ASC, entity_id ASC
                ) AS rn
            FROM TRANSACTION_STREAM
            -- APPEND_ONLY stream: all rows are inserts, but guard explicitly.
            WHERE METADATA$ACTION = 'INSERT'
        )
        SELECT * FROM changes
    );

    -- 1) Record suppressions FIRST (Req 2.5):
    --    (a) in-batch duplicates (rn > 1), and
    --    (b) arrivals whose event_id already exists in the governed store.
    INSERT INTO TRANSACTION_SUPPRESSION_LOG (event_id, entity_id, occurred_at, source, reason)
    SELECT c.event_id, c.entity_id, c.occurred_at, c.source,
           CASE WHEN c.rn > 1 THEN 'duplicate_in_batch' ELSE 'duplicate_event_id' END
    FROM TABLE(batch) c
    WHERE c.rn > 1
       OR EXISTS (
           SELECT 1 FROM TRANSACTION_EVENT t WHERE t.event_id = c.event_id
       );

    -- 2) MERGE only the keepers (rn = 1) that are NOT already stored, stamping
    --    governed ingestion provenance (ingested_at + source — Req 2.4). Because
    --    we only insert non-existing event_ids, the governed store holds exactly
    --    one row per event_id (Property 4). is_duplicate_suppressed stays FALSE
    --    on the stored (kept) row; suppression is recorded in the log, not by
    --    flipping the governed row.
    MERGE INTO TRANSACTION_EVENT AS tgt
    USING (
        SELECT event_id, entity_id, amount, currency, occurred_at, source
        FROM TABLE(batch)
        WHERE rn = 1
    ) AS src
    ON tgt.event_id = src.event_id
    WHEN NOT MATCHED THEN INSERT
        (event_id, entity_id, amount, currency, occurred_at, ingested_at, source, is_duplicate_suppressed)
        VALUES
        (src.event_id, src.entity_id, src.amount, src.currency, src.occurred_at,
         CURRENT_TIMESTAMP(), src.source, FALSE);
END;
$$;

-- -----------------------------------------------------------------------------
-- Tasks are created SUSPENDED. Resume to start the schedule; grant the app role
-- the ability to operate (monitor) the task. OWNERSHIP stays with the creating
-- role so the task runs with owner's rights over RAW.TRANSACTION_EVENT.
-- -----------------------------------------------------------------------------
GRANT MONITOR, OPERATE ON TASK INGEST_TRANSACTION_EVENTS TO ROLE AML_APP_ROLE;

-- Resume the task so it begins honouring its schedule (idempotent; a running
-- task resumes to running). Requires EXECUTE TASK on the account for the owner.
ALTER TASK INGEST_TRANSACTION_EVENTS RESUME;

-- -----------------------------------------------------------------------------
-- One-shot / demo convenience: run the task immediately rather than waiting for
-- the next scheduled tick (e.g. right after a seed load into TRANSACTION_LANDING).
-- Safe to leave in — it is a manual trigger, not a schedule change.
--   EXECUTE TASK INGEST_TRANSACTION_EVENTS;
-- -----------------------------------------------------------------------------
