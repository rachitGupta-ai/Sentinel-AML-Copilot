-- =============================================================================
-- SentinelAML Copilot — VEC schema (Cortex embedding stores)
-- Spec: aml-regulatory-copilot  |  Task 3.2  |  Requirements: 2.1, 1.3
-- =============================================================================
-- Chunk + embedding tables that back evidence retrieval. Embeddings are produced
-- by Snowflake Cortex EMBED_TEXT_* and queried with VECTOR_COSINE_SIMILARITY by
-- the Cortex adapter (design.md component 10, task 10.1). The VECTOR dimension
-- (768) matches the 768-dim Cortex embedding model
-- snowflake-arctic-embed-m-v1.5 (the configured default); change EMBED_DIM below if
-- you choose a model with a different dimension and keep the two tables consistent.
--
-- Retrieved chunk text is UNTRUSTED and must pass the Guard injection scan before
-- any model call (Req 9.1) — enforced in the service layer, not the schema.
--
-- Run AFTER 00_databases_schemas.sql / 01_raw.sql.
-- =============================================================================

USE SCHEMA VEC;

-- -----------------------------------------------------------------------------
-- VEC.POLICY_CHUNK  — embedded chunks of RAW.POLICY_DOC
-- Each row is a retrievable policy passage; chunk_text is the exact passage a
-- textual claim cites (Req 5.4 dual-grounding), and policy_doc_id/chunk_index
-- give the citeable lineage back to the source document.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS POLICY_CHUNK (
    chunk_id         STRING             NOT NULL,
    policy_doc_id    STRING             NOT NULL,   -- FK -> RAW.POLICY_DOC
    chunk_index      NUMBER(10, 0)      NOT NULL,   -- ordinal within the document
    chunk_text       STRING,                        -- passage text (citeable excerpt)
    embedding        VECTOR(FLOAT, 768),            -- Cortex EMBED_TEXT_768 output
    embedding_model  STRING,                        -- model name for lineage/reproducibility
    created_at       TIMESTAMP_NTZ,
    CONSTRAINT pk_policy_chunk PRIMARY KEY (chunk_id)
)
COMMENT = 'Embedded policy passages for Cortex retrieval (VECTOR_COSINE_SIMILARITY); citeable textual evidence (Req 5.4).';

-- -----------------------------------------------------------------------------
-- VEC.EVIDENCE_CHUNK  — embedded chunks of other evidence (e.g. transaction
-- narratives, case notes, synthetic supporting documents).
-- source_kind / source_ref let a retrieved chunk resolve back to its origin for
-- citation lineage (Property 17).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS EVIDENCE_CHUNK (
    chunk_id         STRING             NOT NULL,
    source_kind      STRING             NOT NULL,   -- e.g. "transaction_event", "case_note"
    source_ref       STRING             NOT NULL,   -- id of the originating record
    chunk_index      NUMBER(10, 0)      NOT NULL,
    chunk_text       STRING,
    embedding        VECTOR(FLOAT, 768),            -- Cortex EMBED_TEXT_768 output
    embedding_model  STRING,
    created_at       TIMESTAMP_NTZ,
    CONSTRAINT pk_evidence_chunk PRIMARY KEY (chunk_id)
)
COMMENT = 'Embedded non-policy evidence chunks for Cortex retrieval; resolves to source via source_kind/source_ref (Property 17).';

-- -----------------------------------------------------------------------------
-- Grants to the application runtime role.
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT ON TABLE POLICY_CHUNK   TO ROLE AML_APP_ROLE;
GRANT SELECT, INSERT ON TABLE EVIDENCE_CHUNK TO ROLE AML_APP_ROLE;
