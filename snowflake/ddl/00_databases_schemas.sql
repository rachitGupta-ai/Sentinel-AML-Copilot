-- =============================================================================
-- SentinelAML Copilot — database, schemas, and append-only role scaffolding
-- Spec: aml-regulatory-copilot  |  Task 3.2  |  Requirements: 1.3, 2.1, 8.3
-- =============================================================================
-- This script is the first provisioning step. It creates the single application
-- database and the five schemas used by the physical model (design.md
-- "Snowflake physical model"), plus the dedicated append-only writer role that
-- the AUDIT schema relies on (Req 8.3).
--
-- Config-only contract (Req 1.1): object *names* are fixed by the design, but the
-- database and warehouse are supplied by the CoCo CLI connection / environment
-- (SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE). We parameterise the database name so
-- the same DDL runs against whatever database the operator configured, with
-- GOVERNED_AML as a documented default.
--
-- All data created under RAW is fully synthetic (Req 2.1); no real customer,
-- account, or transaction data is ever loaded here.
-- =============================================================================

-- The CoCo CLI connection supplies ACCOUNT / USER / ROLE / WAREHOUSE. These
-- SET statements let the rest of the scripts stay database-name agnostic.
-- Override on the command line with:  --variable db=<YOUR_DB>
SET db = 'GOVERNED_AML';

CREATE DATABASE IF NOT EXISTS IDENTIFIER($db)
    COMMENT = 'SentinelAML Copilot — synthetic AML investigation & SAR data cloud (aml-regulatory-copilot).';

USE DATABASE IDENTIFIER($db);

-- -----------------------------------------------------------------------------
-- Schemas (one per concern in the physical model)
-- -----------------------------------------------------------------------------
--   RAW   — synthetic source data (Req 2)
--   SEM   — governed semantic view + metric-definition registry (Req 3, task 4)
--   VEC   — Cortex embedding stores for policy/evidence retrieval
--   APP   — case / SAR / approval workflow state
--   AUDIT — immutable, append-only audit trail (Req 8)
-- -----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS RAW
    COMMENT = 'Synthetic source data: transaction events, alerts, entities, policy docs (Req 2).';
CREATE SCHEMA IF NOT EXISTS SEM
    COMMENT = 'Governed semantic layer: ENTITY_RISK_METRICS view + METRIC_DEFINITION_REGISTRY (Req 3; provisioned in task 4).';
CREATE SCHEMA IF NOT EXISTS VEC
    COMMENT = 'Cortex embedding stores for policy/evidence retrieval (VECTOR_COSINE_SIMILARITY).';
CREATE SCHEMA IF NOT EXISTS APP
    COMMENT = 'Workflow state: cases, SAR drafts, approvals.';
CREATE SCHEMA IF NOT EXISTS AUDIT
    COMMENT = 'Immutable, append-only audit trail (Req 8). INSERT-only; UPDATE/DELETE denied at the grant level (Req 8.3).';

-- -----------------------------------------------------------------------------
-- Append-only writer role for the audit trail (Req 8.3)
-- -----------------------------------------------------------------------------
-- Immutability is enforced at the *grant* level: the application writes audit
-- records through a role that is granted INSERT and SELECT only — never UPDATE or
-- DELETE — on AUDIT.AUDIT_RECORD (see 04_audit.sql). Because UPDATE/DELETE are
-- never granted, any mutation attempt fails authorization; the application layer
-- records that rejected attempt as a *new* audit row (Req 8.3, Property 30).
--
-- Requires a role with CREATE ROLE (ACCOUNTADMIN / SECURITYADMIN or a delegated
-- role). Adjust the grantee role name to match your CoCo CLI connection role.
CREATE ROLE IF NOT EXISTS AML_AUDIT_WRITER
    COMMENT = 'Append-only writer for AUDIT.AUDIT_RECORD — granted INSERT/SELECT only (Req 8.3).';

-- The application runtime role (the role the CoCo CLI connection assumes) inherits
-- the append-only writer so the service can append audit records. Replace
-- AML_APP_ROLE with your configured SNOWFLAKE_ROLE if different.
CREATE ROLE IF NOT EXISTS AML_APP_ROLE
    COMMENT = 'SentinelAML Copilot application runtime role.';
GRANT ROLE AML_AUDIT_WRITER TO ROLE AML_APP_ROLE;

-- Usage grants so the app role can reach the objects created by later scripts.
GRANT USAGE ON DATABASE IDENTIFIER($db) TO ROLE AML_APP_ROLE;
GRANT USAGE ON SCHEMA RAW   TO ROLE AML_APP_ROLE;
GRANT USAGE ON SCHEMA SEM   TO ROLE AML_APP_ROLE;
GRANT USAGE ON SCHEMA VEC   TO ROLE AML_APP_ROLE;
GRANT USAGE ON SCHEMA APP   TO ROLE AML_APP_ROLE;
GRANT USAGE ON SCHEMA AUDIT TO ROLE AML_AUDIT_WRITER;
