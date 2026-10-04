-- =============================================================================
-- SentinelAML Copilot — SEM.ENTITY_RISK_METRICS (governed semantic view)
-- Spec: aml-regulatory-copilot  |  Task 4.1  |  Requirements: 3.1, 3.4, 3.5
-- =============================================================================
-- SEM.ENTITY_RISK_METRICS is the governed Semantic_View: the ONLY source of
-- regulatory/risk figures (design.md "governed semantic layer is the source of
-- truth for all figures"; Req 3.2 enforced by the metric service reading here and
-- nowhere else). It computes the three MVP Governed_Metrics deterministically from
-- RAW.TRANSACTION_EVENT:
--
--   * exposure_90d       — total transacted amount over the trailing 90 days
--   * txn_velocity       — average transactions/day over the trailing 90 days
--   * structuring_score  — fraction of trailing-90-day txns in the just-below-
--                          threshold band (9000 <= amount < 10000), 0.0-1.0
--
-- Each value is stamped with the metric's name, definition, and the CURRENT
-- Metric_Definition_Version drawn from SEM.METRIC_DEFINITION_REGISTRY
-- (Req 3.1, 3.4), so the figure and the version it was computed under always
-- travel together. The computation is a pure function of the RAW data snapshot:
-- the same entity + same data state yields identical values and the same version
-- for every caller (Req 3.3), and the LLM has no write/compute path here (Req 3.2).
--
-- Two objects are provisioned:
--   1. ENTITY_RISK_METRICS         — WIDE view: one row per entity, one column per
--                                     metric. Convenient for aggregate_entity_risk().
--   2. ENTITY_RISK_METRIC_VALUES   — LONG view: one row per (entity, metric) with
--                                     value + display_name + definition + version.
--                                     This is the governed read surface get_metric()
--                                     uses; every row carries its definition version.
--
-- Determinism note: the trailing-90-day window is anchored on the latest ingested
-- event time per the registry definition. We anchor on MAX(occurred_at) over the
-- (non-suppressed) data so the window is a function of the data state alone and
-- never of wall-clock time at query execution.
--
-- Run AFTER:
--   * snowflake/ddl/00_databases_schemas.sql  (SEM schema, AML_APP_ROLE)
--   * snowflake/ddl/01_raw.sql                (RAW.TRANSACTION_EVENT, RAW.ENTITY)
--   * snowflake/semantic/10_metric_definition_registry.sql  (registry + versions)
-- =============================================================================

-- Keep database-name agnostic, consistent with snowflake/ddl/00_databases_schemas.sql.
SET db = 'GOVERNED_AML';
USE DATABASE IDENTIFIER($db);
USE SCHEMA SEM;

-- -----------------------------------------------------------------------------
-- Base computation: deterministic trailing-90-day aggregates per entity.
-- Duplicate-suppressed events are excluded (Req 2.5 semantics). The window end is
-- the entity's latest business-time event (data-state anchored, not wall-clock),
-- so values are reproducible for a fixed data snapshot (Req 3.3).
-- The structuring band (9000 <= amount < 10000) encodes the just-below-reporting-
-- threshold typology described in the registry definition.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW ENTITY_RISK_METRICS_BASE AS
WITH active_txn AS (
    SELECT
        entity_id,
        amount,
        occurred_at
    FROM RAW.TRANSACTION_EVENT
    WHERE COALESCE(is_duplicate_suppressed, FALSE) = FALSE
      AND occurred_at IS NOT NULL
      AND amount IS NOT NULL
),
anchored AS (
    SELECT
        entity_id,
        amount,
        occurred_at,
        MAX(occurred_at) OVER (PARTITION BY entity_id) AS window_end
    FROM active_txn
),
windowed AS (
    SELECT *
    FROM anchored
    WHERE occurred_at > DATEADD('day', -90, window_end)
)
SELECT
    entity_id,
    -- exposure_90d: total transacted amount in the trailing-90-day window.
    SUM(amount)                                                   AS exposure_90d,
    -- txn_velocity: average transactions per day across the 90-day window.
    (COUNT(*) / 90.0)                                             AS txn_velocity,
    -- structuring_score: fraction of windowed txns in the just-below-threshold band.
    CASE
        WHEN COUNT(*) = 0 THEN 0.0
        ELSE (COUNT_IF(amount >= 9000 AND amount < 10000) / COUNT(*)::FLOAT)
    END                                                           AS structuring_score,
    COUNT(*)                                                      AS txn_count_90d
FROM windowed
GROUP BY entity_id;

-- -----------------------------------------------------------------------------
-- 1) WIDE governed view: one row per entity, metrics as columns, stamped with the
--    single current Metric_Definition_Version shared by the metrics (all seeded at
--    v1). Entities with no qualifying transactions still appear with zeroed metrics
--    so downstream risk aggregation has a defined value (no silent NULL figures).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW ENTITY_RISK_METRICS
COMMENT = 'Governed Semantic_View (WIDE): one row per entity with exposure_90d, txn_velocity, structuring_score, stamped with current Metric_Definition_Version (Req 3.1, 3.2, 3.3, 3.4).'
AS
SELECT
    e.entity_id,
    COALESCE(b.exposure_90d, 0)                                   AS exposure_90d,
    COALESCE(b.txn_velocity, 0)                                   AS txn_velocity,
    COALESCE(b.structuring_score, 0.0)                            AS structuring_score,
    COALESCE(b.txn_count_90d, 0)                                  AS txn_count_90d,
    -- Current version is governed data in the registry; join the exposure_90d row
    -- (all three MVP metrics share version v1) to stamp the figures (Req 3.1, 3.4).
    (SELECT r.metric_definition_version
       FROM METRIC_DEFINITION_REGISTRY r
      WHERE r.metric_name = 'exposure_90d' AND r.is_current = TRUE)  AS metric_definition_version
FROM RAW.ENTITY e
LEFT JOIN ENTITY_RISK_METRICS_BASE b
       ON b.entity_id = e.entity_id;

-- -----------------------------------------------------------------------------
-- 2) LONG governed view: one row per (entity, metric). This is the read surface
--    the Governed Metric Service uses — every row carries the metric name,
--    human-readable definition, value, and the exact Metric_Definition_Version it
--    was computed under (Req 3.1, 3.4). UNPIVOT the wide view, then join the
--    registry (current version) for display_name + definition + version.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW ENTITY_RISK_METRIC_VALUES
COMMENT = 'Governed Semantic_View (LONG): one governed figure per (entity, metric) stamped with definition + Metric_Definition_Version (Req 3.1, 3.4). Read surface for get_metric().'
AS
WITH long AS (
    SELECT entity_id, 'exposure_90d'      AS metric_name, exposure_90d::FLOAT      AS metric_value FROM ENTITY_RISK_METRICS
    UNION ALL
    SELECT entity_id, 'txn_velocity',      txn_velocity::FLOAT      FROM ENTITY_RISK_METRICS
    UNION ALL
    SELECT entity_id, 'structuring_score', structuring_score::FLOAT FROM ENTITY_RISK_METRICS
)
SELECT
    l.entity_id,
    l.metric_name,
    r.display_name,
    r.definition,
    l.metric_value,
    r.unit,
    r.metric_definition_version
FROM long l
JOIN METRIC_DEFINITION_REGISTRY r
  ON r.metric_name = l.metric_name
 AND r.is_current = TRUE;

-- -----------------------------------------------------------------------------
-- Grants to the application runtime role. Views are read-only governed surfaces;
-- SELECT only (the app never writes figures — Req 3.2).
-- -----------------------------------------------------------------------------
GRANT SELECT ON VIEW ENTITY_RISK_METRICS_BASE   TO ROLE AML_APP_ROLE;
GRANT SELECT ON VIEW ENTITY_RISK_METRICS         TO ROLE AML_APP_ROLE;
GRANT SELECT ON VIEW ENTITY_RISK_METRIC_VALUES   TO ROLE AML_APP_ROLE;
