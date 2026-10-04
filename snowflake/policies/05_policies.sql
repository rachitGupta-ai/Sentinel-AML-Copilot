-- =============================================================================
-- SentinelAML Copilot — Masking & Row-access policies (role-based data access)
-- Spec: aml-regulatory-copilot  |  Task 8.2  |  Requirements: 9.3, 9.2
-- =============================================================================
-- Snowflake-native enforcement of Req 9.3: sensitive synthetic fields are masked
-- for roles that are NOT entitled to see them, and non-entitled roles are limited
-- by a row-access policy. This complements the application-layer RBAC in
-- app/services/auth_service.py (Req 9.2) — together they satisfy Req 9.2/9.3.
--
-- Entitlement model (mirrors app.services.auth_service.Role):
--   * AML_COMPLIANCE_OFFICER — entitled to see sensitive fields (reviews lineage).
--   * AML_METRIC_STEWARD     — entitled (owns governed metric definitions).
--   * AML_ANALYST            — NOT entitled: sensitive fields are masked.
--   * AML_VIEWER             — NOT entitled: sensitive fields are masked.
-- The existing AML_APP_ROLE (DDL script 00) remains the runtime connection role;
-- the four roles above are secondary roles the app activates per request to let
-- Snowflake evaluate the policies by role (so masking is provable per role —
-- Property 34, Req 11.5 "same answer, provably").
--
-- Policies are deterministic: visibility depends ONLY on the current role, so the
-- governed numeric answer is role-invariant (Property 7) while the sensitive
-- display/identifier text is masked per entitlement (Property 34).
--
-- Config-only contract (Req 1.1): object names are fixed by the design; the
-- database is supplied by the CoCo CLI connection / environment, parameterised
-- via $db with GOVERNED_AML as the documented default (same pattern as the DDL).
--
-- Run AFTER snowflake/ddl/00_databases_schemas.sql and 01_raw.sql.
-- =============================================================================

SET db = 'GOVERNED_AML';
USE DATABASE IDENTIFIER($db);

-- -----------------------------------------------------------------------------
-- Entitlement roles (idempotent). These mirror the application Role enum so a
-- role name maps 1:1 between the app layer (Req 9.2) and Snowflake policies
-- (Req 9.3). Compliance officers and metric stewards are entitled to sensitive
-- fields; analysts and viewers are not.
-- -----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS AML_COMPLIANCE_OFFICER
    COMMENT = 'Entitled to view sensitive synthetic fields (reviews full lineage). SentinelAML (Req 9.3).';
CREATE ROLE IF NOT EXISTS AML_METRIC_STEWARD
    COMMENT = 'Entitled to view sensitive synthetic fields; owns governed metric definitions. SentinelAML (Req 9.3).';
CREATE ROLE IF NOT EXISTS AML_ANALYST
    COMMENT = 'NOT entitled to sensitive synthetic fields — masked per policy. SentinelAML (Req 9.3).';
CREATE ROLE IF NOT EXISTS AML_VIEWER
    COMMENT = 'Read-only; NOT entitled to sensitive synthetic fields — masked per policy. SentinelAML (Req 9.3).';

-- The runtime connection role may activate the entitlement roles as secondary
-- roles so Snowflake evaluates the policies by the request's effective role.
GRANT ROLE AML_COMPLIANCE_OFFICER TO ROLE AML_APP_ROLE;
GRANT ROLE AML_METRIC_STEWARD     TO ROLE AML_APP_ROLE;
GRANT ROLE AML_ANALYST            TO ROLE AML_APP_ROLE;
GRANT ROLE AML_VIEWER             TO ROLE AML_APP_ROLE;

-- All entitlement roles need to read RAW to be governed by the policies.
GRANT USAGE ON SCHEMA RAW TO ROLE AML_COMPLIANCE_OFFICER;
GRANT USAGE ON SCHEMA RAW TO ROLE AML_METRIC_STEWARD;
GRANT USAGE ON SCHEMA RAW TO ROLE AML_ANALYST;
GRANT USAGE ON SCHEMA RAW TO ROLE AML_VIEWER;
GRANT SELECT ON TABLE RAW.ENTITY            TO ROLE AML_COMPLIANCE_OFFICER;
GRANT SELECT ON TABLE RAW.ENTITY            TO ROLE AML_METRIC_STEWARD;
GRANT SELECT ON TABLE RAW.ENTITY            TO ROLE AML_ANALYST;
GRANT SELECT ON TABLE RAW.ENTITY            TO ROLE AML_VIEWER;
GRANT SELECT ON TABLE RAW.TRANSACTION_EVENT TO ROLE AML_COMPLIANCE_OFFICER;
GRANT SELECT ON TABLE RAW.TRANSACTION_EVENT TO ROLE AML_METRIC_STEWARD;
GRANT SELECT ON TABLE RAW.TRANSACTION_EVENT TO ROLE AML_ANALYST;
GRANT SELECT ON TABLE RAW.TRANSACTION_EVENT TO ROLE AML_VIEWER;

USE SCHEMA RAW;

-- =============================================================================
-- Masking policies (Req 9.3, Property 34)
-- =============================================================================
-- A masking policy returns the raw value for ENTITLED roles and a masked value
-- otherwise. Entitlement is checked with CURRENT_ROLE()/IS_ROLE_IN_SESSION so the
-- same stored synthetic value is shown raw to entitled roles and masked to
-- non-entitled roles — deterministic and role-only (Property 34).

-- display_name_masked on RAW.ENTITY (design "Data Models": "masked per role").
CREATE MASKING POLICY IF NOT EXISTS MASK_DISPLAY_NAME AS (val STRING) RETURNS STRING ->
    CASE
        WHEN IS_ROLE_IN_SESSION('AML_COMPLIANCE_OFFICER')
          OR IS_ROLE_IN_SESSION('AML_METRIC_STEWARD')
            THEN val
        ELSE '***MASKED***'
    END
    COMMENT = 'Entity display name: raw for entitled roles, masked for analysts/viewers (Req 9.3, Property 34).';

-- Account identifier (sensitive synthetic field, Req 9.3). Partial-reveal masking
-- keeps the last 4 characters for support/triage while hiding the identifier from
-- non-entitled roles.
CREATE MASKING POLICY IF NOT EXISTS MASK_ACCOUNT_IDENTIFIER AS (val STRING) RETURNS STRING ->
    CASE
        WHEN IS_ROLE_IN_SESSION('AML_COMPLIANCE_OFFICER')
          OR IS_ROLE_IN_SESSION('AML_METRIC_STEWARD')
            THEN val
        WHEN val IS NULL THEN NULL
        ELSE '****' || RIGHT(val, 4)
    END
    COMMENT = 'Account identifier: raw for entitled roles, last-4 only for non-entitled (Req 9.3, Property 34).';

-- Apply the masking policies to the sensitive columns.
--   * RAW.ENTITY.display_name_masked — the entity's display name.
--   * RAW.TRANSACTION_EVENT.entity_id — the account/entity identifier on a txn.
-- (ALTER ... SET MASKING POLICY is not IF-NOT-EXISTS-guarded; re-applying the
--  same policy to the same column is a no-op/benign error on re-run.)
ALTER TABLE RAW.ENTITY
    MODIFY COLUMN display_name_masked SET MASKING POLICY MASK_DISPLAY_NAME;
ALTER TABLE RAW.TRANSACTION_EVENT
    MODIFY COLUMN entity_id SET MASKING POLICY MASK_ACCOUNT_IDENTIFIER;

-- =============================================================================
-- Row-access policy (Req 9.3, Property 34)
-- =============================================================================
-- Non-entitled roles (analyst/viewer) are additionally limited at the ROW level:
-- rows flagged as restricted in the synthetic attributes are visible only to
-- entitled roles. Entitled roles see all rows; the runtime role used for
-- provisioning / system reads (AML_APP_ROLE acting directly) also sees all rows
-- so ingestion and governed aggregation are unaffected.
--
-- The predicate is deterministic and depends only on the current role plus a
-- row attribute, so it never changes a governed numeric figure for an entitled
-- role (Property 7) while restricting sensitive rows from non-entitled roles.
-- The policy takes the VARIANT `attributes` column directly and derives the
-- "restricted" flag inside the body. Snowflake's ADD ROW ACCESS POLICY ... ON
-- clause only accepts COLUMN NAMES (not arbitrary expressions), so the COALESCE
-- extraction must live here in the policy body, not in the ON mapping.
CREATE OR REPLACE ROW ACCESS POLICY RAP_ENTITY_RESTRICTED
    AS (attributes VARIANT) RETURNS BOOLEAN ->
        -- Entitled roles and the system runtime role: full visibility.
        IS_ROLE_IN_SESSION('AML_COMPLIANCE_OFFICER')
        OR IS_ROLE_IN_SESSION('AML_METRIC_STEWARD')
        OR IS_ROLE_IN_SESSION('AML_APP_ROLE')
        -- Non-entitled roles: only non-restricted rows (absent flag => visible).
        OR COALESCE(attributes:restricted::BOOLEAN, FALSE) = FALSE
    COMMENT = 'Row visibility: entitled/system roles see all; analysts/viewers see only non-restricted entities (Req 9.3).';

-- Bind the policy to RAW.ENTITY, mapping the VARIANT `attributes` column to the
-- policy's single argument. The "restricted" extraction happens in the body.
ALTER TABLE RAW.ENTITY
    ADD ROW ACCESS POLICY RAP_ENTITY_RESTRICTED ON (attributes);

-- =============================================================================
-- Verification helpers (optional). Run manually after provisioning.
-- =============================================================================
--   -- Confirm the policies are attached:
--   SELECT * FROM TABLE(INFORMATION_SCHEMA.POLICY_REFERENCES(
--       REF_ENTITY_NAME => 'RAW.ENTITY', REF_ENTITY_DOMAIN => 'TABLE'));
--   -- Prove per-role masking (Property 34 / Req 11.5 "same answer, provably"):
--   USE ROLE AML_COMPLIANCE_OFFICER; SELECT display_name_masked FROM RAW.ENTITY LIMIT 5;  -- raw
--   USE ROLE AML_ANALYST;            SELECT display_name_masked FROM RAW.ENTITY LIMIT 5;  -- masked
