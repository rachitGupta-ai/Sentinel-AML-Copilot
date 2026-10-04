-- =============================================================================
-- SentinelAML Copilot — RAW.DATA_FRESHNESS (data-freshness indicator for the UI)
-- Spec: aml-regulatory-copilot  |  Task 5.2  |  Requirements: 2.6
-- =============================================================================
-- Exposes the data-freshness indicator the UI consumes: the latest ingested
-- event time, with a defined "no data" state when nothing has been ingested
-- (Req 2.6, Property 5 — "the data-freshness indicator equals the maximum event
-- time over that set; for the empty set it reports the defined 'no data' state").
--
-- The view reads from the governed, deduplicated RAW.TRANSACTION_EVENT (the same
-- store the semantic view reads), counting only non-suppressed rows so suppressed
-- duplicates (Req 2.5) do not distort freshness.
--
-- Columns mirror the pydantic FreshnessIndicator model
-- (backend/app/models/governed.py):
--   * latest_event_time — MAX event time over ingested rows; NULL when no data.
--   * has_data          — FALSE reports the defined "no data" state (Req 2.6).
--   * is_stale          — left FALSE here; triage sets staleness against its
--                         configured freshness threshold at read time (Req 4.4,
--                         Property 13), which is a runtime policy, not a property
--                         of the data itself.
--
-- "latest ingested event time" is exposed two ways so both readings in the spec
-- are satisfied without ambiguity:
--   * latest_event_time        = MAX(occurred_at)  — latest business event time
--                                (Property 5 "maximum event time").
--   * latest_ingested_at       = MAX(ingested_at)  — latest ingestion stamp
--                                (Req 2.6 phrasing "latest ingested event time").
-- The UI binds latest_event_time to FreshnessIndicator.latest_event_time; the
-- extra ingestion column is available for an "ingested at" badge.
--
-- Run AFTER snowflake/ddl/01_raw.sql (RAW.TRANSACTION_EVENT). Database stays
-- name-agnostic via the $db session variable, matching snowflake/ddl/*.sql.
-- =============================================================================

-- Keep database-name agnostic, consistent with snowflake/ddl/00_databases_schemas.sql.
SET db = 'GOVERNED_AML';
USE DATABASE IDENTIFIER($db);
USE SCHEMA RAW;

-- -----------------------------------------------------------------------------
-- RAW.DATA_FRESHNESS — single-row freshness indicator.
-- Always returns exactly one row: on an empty table the aggregates are NULL, so
-- has_data is FALSE and the row reports the defined "no data" state (Req 2.6).
-- Non-suppressed rows only (COALESCE handles the pre-dedup default FALSE).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW DATA_FRESHNESS
COMMENT = 'Data-freshness indicator for the UI: latest ingested event time with a defined no-data state (Req 2.6, Property 5).'
AS
WITH ingested AS (
    SELECT occurred_at, ingested_at
    FROM TRANSACTION_EVENT
    WHERE COALESCE(is_duplicate_suppressed, FALSE) = FALSE
)
SELECT
    MAX(occurred_at)        AS latest_event_time,     -- max business event time (Property 5)
    MAX(ingested_at)        AS latest_ingested_at,    -- latest ingestion stamp (Req 2.6)
    (COUNT(occurred_at) > 0) AS has_data,             -- FALSE = defined "no data" state (Req 2.6)
    COUNT(*)                AS ingested_event_count,  -- distinct non-suppressed rows ingested
    FALSE                   AS is_stale               -- set by triage vs. its threshold (Req 4.4)
FROM ingested;

-- -----------------------------------------------------------------------------
-- Grant to the application runtime role. Read-only indicator surface.
-- -----------------------------------------------------------------------------
GRANT SELECT ON VIEW DATA_FRESHNESS TO ROLE AML_APP_ROLE;
