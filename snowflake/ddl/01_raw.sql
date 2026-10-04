-- =============================================================================
-- SentinelAML Copilot — RAW schema (synthetic source data)
-- Spec: aml-regulatory-copilot  |  Task 3.2  |  Requirements: 2.1, 1.3
-- =============================================================================
-- Synthetic Entities, Transaction_Events, Alerts, and policy documents (Req 2.1).
-- Column shapes mirror the pydantic domain models in design.md "Data Models"
-- (Entity, TransactionEvent, Alert) so the Snowflake connector round-trips
-- cleanly into the Python records.
--
-- Run AFTER 00_databases_schemas.sql. Assumes USE DATABASE is already set by the
-- CoCo CLI connection / prior script.
-- =============================================================================

USE SCHEMA RAW;

-- -----------------------------------------------------------------------------
-- RAW.ENTITY  — synthetic customer/account under investigation
--   pydantic: Entity(entity_id, display_name_masked, attributes)
-- display_name_masked is masked per role via a Snowflake masking policy applied
-- in snowflake/policies/ (Req 9.3, task 8.2); the base column stores the
-- synthetic raw value.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ENTITY (
    entity_id            STRING        NOT NULL,
    display_name_masked  STRING,
    attributes           VARIANT,                 -- free-form synthetic attributes (dict)
    CONSTRAINT pk_entity PRIMARY KEY (entity_id)
)
COMMENT = 'Synthetic entities under investigation (Req 2.1). display_name_masked is masked per role (Req 9.3).';

-- -----------------------------------------------------------------------------
-- RAW.TRANSACTION_EVENT  — synthetic financial transaction
--   pydantic: TransactionEvent(event_id, entity_id, amount, currency,
--             occurred_at, ingested_at, source, is_duplicate_suppressed)
-- event_id is the dedup key (Req 2.5); ingested_at + source capture ingestion
-- provenance (Req 2.4). Intentional data-quality defects (missing field,
-- duplicate, stale/late record) are injected by the seed generator (Req 2.3),
-- so nullable columns beyond the key are permitted here by design.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS TRANSACTION_EVENT (
    event_id                STRING          NOT NULL,   -- dedup key (Req 2.5)
    entity_id               STRING          NOT NULL,
    amount                  NUMBER(38, 2),              -- Decimal
    currency                STRING,
    occurred_at             TIMESTAMP_NTZ,              -- business time
    ingested_at             TIMESTAMP_NTZ,              -- ingestion timestamp (Req 2.4)
    source                  STRING,                     -- ingestion source (Req 2.4)
    is_duplicate_suppressed BOOLEAN         DEFAULT FALSE,  -- Req 2.5
    CONSTRAINT pk_transaction_event PRIMARY KEY (event_id)
)
COMMENT = 'Synthetic transaction events (Req 2.1). event_id = dedup key (Req 2.5); ingested_at/source = provenance (Req 2.4).';

-- -----------------------------------------------------------------------------
-- RAW.ALERT  — suspicious-activity signal over transactions
--   pydantic: Alert(alert_id, entity_id, typology, raised_at, correlation_key)
-- correlation_key drives correlation/dedup into a single case (Req 4.5).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ALERT (
    alert_id         STRING         NOT NULL,
    entity_id        STRING         NOT NULL,
    typology         STRING,                       -- e.g. "structuring"
    raised_at        TIMESTAMP_NTZ,
    correlation_key  STRING,                        -- correlation/dedup key (Req 4.5)
    CONSTRAINT pk_alert PRIMARY KEY (alert_id)
)
COMMENT = 'Synthetic suspicious-activity alerts (Req 2.1). correlation_key groups alerts into one case (Req 4.5).';

-- -----------------------------------------------------------------------------
-- RAW.POLICY_DOC  — synthetic AML policy / regulatory source document
-- Source for textual grounding. Chunked + embedded into VEC.POLICY_CHUNK
-- (02_vec.sql) for Cortex retrieval; stored here as the authoritative passage
-- text that textual claims cite (Req 5.4 dual-grounding).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS POLICY_DOC (
    policy_doc_id   STRING        NOT NULL,
    title           STRING,
    body            STRING,                        -- full synthetic policy text
    source          STRING,                        -- synthetic provenance label
    version         STRING,
    ingested_at     TIMESTAMP_NTZ,
    CONSTRAINT pk_policy_doc PRIMARY KEY (policy_doc_id)
)
COMMENT = 'Synthetic AML policy / regulatory documents (Req 2.1); authoritative passages for textual grounding (Req 5.4).';

-- -----------------------------------------------------------------------------
-- Grants to the application runtime role.
-- RAW is read-mostly for the app (ingestion writes via streams/tasks in task 5).
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT ON TABLE ENTITY            TO ROLE AML_APP_ROLE;
GRANT SELECT, INSERT ON TABLE TRANSACTION_EVENT TO ROLE AML_APP_ROLE;
GRANT SELECT, INSERT ON TABLE ALERT             TO ROLE AML_APP_ROLE;
GRANT SELECT, INSERT ON TABLE POLICY_DOC        TO ROLE AML_APP_ROLE;
