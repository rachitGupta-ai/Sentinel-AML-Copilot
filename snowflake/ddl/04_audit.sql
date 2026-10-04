-- =============================================================================
-- SentinelAML Copilot — AUDIT schema (immutable, append-only audit trail)
-- Spec: aml-regulatory-copilot  |  Task 3.2  |  Requirements: 8.3, 8.1, 8.2, 1.3
-- =============================================================================
-- AUDIT.AUDIT_RECORD is the immutable, append-only audit trail. Immutability is
-- enforced AT THE GRANT LEVEL (Req 8.3): the append-only writer role is granted
-- INSERT and SELECT only — UPDATE and DELETE are never granted and are explicitly
-- revoked below to defend against any inherited privilege. Because the writer role
-- cannot UPDATE/DELETE, any mutation attempt fails authorization; the application
-- then records that rejected attempt as a NEW audit row (Req 8.3, Property 30),
-- never altering the original (Property 31 replay stays intact).
--
-- Column shape mirrors the pydantic AuditRecord in design.md "Data Models"
-- (audit_id, timestamp_utc, case_ref, entity_ref, actor_id, action, input_refs,
-- output_refs, metric_definition_version), covering every required field in
-- Req 8.2.
--
-- Run AFTER 00_databases_schemas.sql (which creates the AUDIT schema and the
-- AML_AUDIT_WRITER / AML_APP_ROLE roles).
-- =============================================================================

USE SCHEMA AUDIT;

-- -----------------------------------------------------------------------------
-- AUDIT.AUDIT_RECORD  — one append-only row per auditable operation
--   pydantic: AuditRecord(audit_id, timestamp_utc, case_ref, entity_ref,
--             actor_id, action, input_refs, output_refs, metric_definition_version)
-- Written for every state transition, AI call, metric computation, evidence
-- retrieval, approval, and simulated action (Req 8.1). input_refs / output_refs
-- are ARRAYs of reference ids; metric_definition_version is populated where a
-- governed metric was involved (Req 8.2).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS AUDIT_RECORD (
    audit_id                   STRING         NOT NULL,
    timestamp_utc              TIMESTAMP_TZ   NOT NULL,     -- UTC timestamp (Req 8.2)
    case_ref                   STRING,                       -- nullable: not every op has a case
    entity_ref                 STRING,                       -- nullable
    actor_id                   STRING         NOT NULL,     -- user or system id (Req 8.2)
    action                     STRING         NOT NULL,     -- e.g. "case.transition", "metric.compute", "mutation.rejected"
    input_refs                 ARRAY,                        -- input reference ids (Req 8.2)
    output_refs                ARRAY,                        -- output reference ids (Req 8.2)
    metric_definition_version  STRING,                       -- where applicable (Req 8.2)
    CONSTRAINT pk_audit_record PRIMARY KEY (audit_id)
)
COMMENT = 'Immutable, append-only audit trail (Req 8). INSERT-only; UPDATE/DELETE denied at the grant level (Req 8.3).';

-- =============================================================================
-- Append-only enforcement at the GRANT level (Req 8.3)
-- =============================================================================
-- Grant ONLY INSERT + SELECT to the append-only writer role. We deliberately do
-- NOT grant UPDATE, DELETE, or TRUNCATE. The explicit REVOKEs below are defensive:
-- they strip any UPDATE/DELETE/TRUNCATE that might have been inherited or
-- previously granted, so the role is provably append-only.
GRANT INSERT ON TABLE AUDIT_RECORD TO ROLE AML_AUDIT_WRITER;
GRANT SELECT ON TABLE AUDIT_RECORD TO ROLE AML_AUDIT_WRITER;

-- Defensive revokes — guarantee no mutation privilege survives (Req 8.3).
REVOKE UPDATE   ON TABLE AUDIT_RECORD FROM ROLE AML_AUDIT_WRITER;
REVOKE DELETE   ON TABLE AUDIT_RECORD FROM ROLE AML_AUDIT_WRITER;
REVOKE TRUNCATE ON TABLE AUDIT_RECORD FROM ROLE AML_AUDIT_WRITER;

-- The application runtime role inherits AML_AUDIT_WRITER (granted in script 00),
-- so it can append and read audit records but cannot modify or delete them.
-- No UPDATE/DELETE grant on AUDIT_RECORD is ever issued to AML_APP_ROLE.

-- -----------------------------------------------------------------------------
-- Verification helper (optional): confirm no UPDATE/DELETE privilege exists on
-- the audit table. Run manually after provisioning; expect zero mutation rows.
--   SHOW GRANTS ON TABLE AUDIT.AUDIT_RECORD;
--   -- inspect the "privilege" column: only INSERT/SELECT should appear for
--   -- AML_AUDIT_WRITER / AML_APP_ROLE; no UPDATE / DELETE / TRUNCATE / OWNERSHIP
--   -- delegating mutation.
-- -----------------------------------------------------------------------------
