-- =============================================================================
-- SentinelAML Copilot — SEM.METRIC_DEFINITION_REGISTRY (governed metric catalog)
-- Spec: aml-regulatory-copilot  |  Task 4.1  |  Requirements: 3.1, 3.4, 3.5
-- =============================================================================
-- The registry is the single catalog of Governed_Metrics. Each row names a
-- canonical metric, carries its human-readable definition, and the exact
-- Metric_Definition_Version used to compute its value (Req 3.1). When a metric
-- definition changes, a NEW row is inserted with a new Metric_Definition_Version
-- and marked current; prior versions are retained (append-only history) so every
-- value and answer can be stamped with the version it was computed under
-- (Req 3.4). The glossary synonyms column backs resolve_term(): common business
-- terms resolve to the correct canonical Governed_Metric (Req 3.5).
--
-- This table is the authority the semantic view (20_entity_risk_metrics.sql)
-- joins against to stamp each computed value with name + definition + version.
-- The LLM never writes here and never computes a figure (Req 3.2) — definitions
-- are governed, versioned data.
--
-- Run AFTER snowflake/ddl/00_databases_schemas.sql (creates the SEM schema and
-- the AML_APP_ROLE). Database stays name-agnostic via the $db session variable,
-- matching snowflake/ddl/*.sql.
-- =============================================================================

-- Keep database-name agnostic, consistent with snowflake/ddl/00_databases_schemas.sql.
SET db = 'GOVERNED_AML';
USE DATABASE IDENTIFIER($db);
USE SCHEMA SEM;

-- -----------------------------------------------------------------------------
-- SEM.METRIC_DEFINITION_REGISTRY
--   One row per (metric_name, metric_definition_version). The CURRENT version of
--   a metric is the single row with is_current = TRUE for that metric_name.
--
--   glossary_synonyms is an ARRAY of lower-cased business terms that resolve to
--   this canonical metric (Req 3.5); resolve_term() matches an input term against
--   these arrays and returns the canonical metric_name (or Ambiguous if a term
--   maps to more than one canonical metric).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS METRIC_DEFINITION_REGISTRY (
    metric_name                STRING        NOT NULL,   -- canonical id, e.g. 'exposure_90d'
    display_name               STRING        NOT NULL,   -- human-readable name (Req 3.1)
    definition                 STRING        NOT NULL,   -- plain-language definition (Req 3.1)
    metric_definition_version  STRING        NOT NULL,   -- exact version id (Req 3.1, 3.4)
    unit                       STRING,                    -- e.g. 'amount', 'count_per_day', 'score_0_1'
    glossary_synonyms          ARRAY,                     -- business-term synonyms (Req 3.5)
    is_current                 BOOLEAN       NOT NULL DEFAULT TRUE,  -- the active version for this metric
    effective_at              TIMESTAMP_TZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),  -- when this version took effect (Req 3.4)
    CONSTRAINT pk_metric_definition_registry PRIMARY KEY (metric_name, metric_definition_version)
)
COMMENT = 'Governed metric catalog: definitions, Metric_Definition_Version, and glossary synonyms (Req 3.1, 3.4, 3.5). A new definition = a new version row marked current; prior versions retained.';

-- -----------------------------------------------------------------------------
-- Seed the three MVP Governed_Metrics at version v1 (Req 3.1).
-- Definitions here are the human-readable contract; the computation lives in the
-- semantic view (20_entity_risk_metrics.sql) and MUST match these definitions.
-- MERGE keeps this script idempotent / re-runnable (consistent with the
-- CREATE ... IF NOT EXISTS style in snowflake/ddl/).
-- -----------------------------------------------------------------------------
MERGE INTO METRIC_DEFINITION_REGISTRY AS tgt
USING (
    SELECT 'exposure_90d'      AS metric_name,
           'Exposure (90-day)'  AS display_name,
           'Total transacted amount for the entity over the trailing 90 days from the latest ingested event time. Deterministic sum of TRANSACTION_EVENT.amount for occurred_at within the trailing-90-day window; duplicate-suppressed events are excluded.' AS definition,
           'v1'                 AS metric_definition_version,
           'amount'             AS unit,
           ARRAY_CONSTRUCT('exposure', '90 day exposure', '90-day exposure', 'ninety day exposure', 'total exposure', 'transacted amount') AS glossary_synonyms,
           TRUE                 AS is_current
    UNION ALL
    SELECT 'txn_velocity',
           'Transaction Velocity',
           'Average number of transactions per day for the entity over the trailing 90-day window: count of duplicate-suppressed TRANSACTION_EVENT rows divided by 90. Deterministic.',
           'v1',
           'count_per_day',
           ARRAY_CONSTRUCT('velocity', 'transaction velocity', 'txn rate', 'transaction rate', 'transactions per day', 'activity rate'),
           TRUE
    UNION ALL
    SELECT 'structuring_score',
           'Structuring Score',
           'Normalised 0.0-1.0 indicator of structuring/smurfing: the fraction of the trailing-90-day transactions whose amount falls in the just-below-reporting-threshold band (9000 <= amount < 10000). Deterministic; higher means more consistent with structuring typology.',
           'v1',
           'score_0_1',
           ARRAY_CONSTRUCT('structuring', 'smurfing', 'structuring risk', 'structuring indicator', 'threshold avoidance'),
           TRUE
) AS src
ON  tgt.metric_name = src.metric_name
AND tgt.metric_definition_version = src.metric_definition_version
WHEN NOT MATCHED THEN INSERT (
    metric_name, display_name, definition, metric_definition_version,
    unit, glossary_synonyms, is_current
) VALUES (
    src.metric_name, src.display_name, src.definition, src.metric_definition_version,
    src.unit, src.glossary_synonyms, src.is_current
);

-- -----------------------------------------------------------------------------
-- Grants to the application runtime role.
-- The app reads definitions/versions/synonyms (metric service + resolve_term).
-- INSERT is granted so a governed definition change can append a new version row
-- (Req 3.4) under an authorised role; UPDATE/DELETE are intentionally NOT granted
-- so prior versions are not rewritten (version history is append-only).
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT ON TABLE METRIC_DEFINITION_REGISTRY TO ROLE AML_APP_ROLE;
