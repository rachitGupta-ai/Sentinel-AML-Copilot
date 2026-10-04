-- =============================================================================
-- SentinelAML Copilot — APP schema (workflow state)
-- Spec: aml-regulatory-copilot  |  Task 3.2  |  Requirements: 1.3, 2.1
-- =============================================================================
-- Case / SAR draft / approval workflow state. Column shapes mirror the pydantic
-- domain models in design.md "Data Models" (Case, SARDraft, Approval) and the
-- case lifecycle state machine. Model invariants are encoded here as DDL defaults
-- so the stored state cannot silently violate the design:
--   * APP.CASE.classification      DEFAULT 'risk_signal' — never auto -> risk_event (Req 4.3)
--   * APP.SAR_DRAFT.is_ai_generated_decision_support DEFAULT TRUE — always true (Req 6.4)
--
-- These tables hold mutable workflow state (unlike AUDIT, which is append-only);
-- every transition still emits an append-only AUDIT.AUDIT_RECORD (Req 8.1).
--
-- Run AFTER 00_databases_schemas.sql.
-- =============================================================================

USE SCHEMA APP;

-- -----------------------------------------------------------------------------
-- APP.CASE  — investigation case + lifecycle state
--   pydantic: Case(case_id, entity_id, state, classification, risk_aggregation,
--             freshness, correlated_alert_ids)
-- state ∈ RECEIVED, TRIAGED, INVESTIGATING, RECOMMENDATION_READY,
--         AWAITING_APPROVAL, APPROVED, REJECTED, ACTION_COMPLETED, ACTION_FAILED
-- classification ∈ risk_signal | risk_event (defaults to the candidate signal;
-- promotion to risk_event requires human approval — Req 4.3, Property 12).
-- risk_aggregation / freshness are stored as VARIANT (EntityRiskView,
-- FreshnessIndicator); correlated_alert_ids as an ARRAY (Req 4.5).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "CASE" (
    case_id               STRING        NOT NULL,
    entity_id             STRING        NOT NULL,
    state                 STRING        NOT NULL DEFAULT 'RECEIVED',
    classification        STRING        NOT NULL DEFAULT 'risk_signal',  -- never auto -> risk_event (Req 4.3)
    risk_aggregation      VARIANT,                                       -- EntityRiskView
    freshness             VARIANT,                                       -- FreshnessIndicator (Req 4.4)
    correlated_alert_ids  ARRAY,                                         -- Req 4.5
    created_at            TIMESTAMP_NTZ,
    updated_at            TIMESTAMP_NTZ,
    CONSTRAINT pk_case PRIMARY KEY (case_id)
)
COMMENT = 'Investigation cases + lifecycle state. classification defaults risk_signal; never auto-advances to risk_event (Req 4.3).';

-- -----------------------------------------------------------------------------
-- APP.SAR_DRAFT  — audit-ready SAR draft
--   pydantic: SARDraft(case_id, entity_summary, suspicious_pattern,
--             governed_figures, policy_citations, completeness,
--             ungrounded_elements, is_ai_generated_decision_support)
-- governed_figures (list[GovernedMetricValue]) and policy_citations
-- (list[EvidenceItem]) are stored as VARIANT; ungrounded_elements as ARRAY
-- (Req 6.3). completeness ∈ complete | incomplete. is_ai_generated_decision_support
-- defaults TRUE and status is never 'filed' (Req 6.4, Property 24) — enforced by
-- the service; the status column has no 'filed' write path.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS SAR_DRAFT (
    sar_draft_id                     STRING    NOT NULL,
    case_id                          STRING    NOT NULL,
    entity_summary                   STRING,
    suspicious_pattern               STRING,
    governed_figures                 VARIANT,                 -- list[GovernedMetricValue] (Req 6.2)
    policy_citations                 VARIANT,                 -- list[EvidenceItem] (Req 6.2)
    completeness                     STRING    DEFAULT 'incomplete',  -- complete | incomplete (Req 6.3)
    ungrounded_elements              ARRAY,                   -- Req 6.3
    is_ai_generated_decision_support BOOLEAN   NOT NULL DEFAULT TRUE, -- always true (Req 6.4)
    status                           STRING    DEFAULT 'draft',       -- never 'filed' (Req 6.4, 6.5)
    created_at                       TIMESTAMP_NTZ,
    CONSTRAINT pk_sar_draft PRIMARY KEY (sar_draft_id)
)
COMMENT = 'AI-generated SAR drafts (decision support, Req 6.4). Ungrounded elements force incomplete/not-filing-ready (Req 6.3).';

-- -----------------------------------------------------------------------------
-- APP.APPROVAL  — human-in-the-loop approval decision
--   pydantic: Approval(case_id, reviewer_id, decision, reason, decided_at)
-- decision ∈ approved | rejected | overridden (Req 7.5). reviewer_id is required
-- (Req 7.6) — enforced NOT NULL here and re-checked in the service.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS APPROVAL (
    approval_id   STRING         NOT NULL,
    case_id       STRING         NOT NULL,
    reviewer_id   STRING         NOT NULL,          -- required reviewer identity (Req 7.6)
    decision      STRING         NOT NULL,          -- approved | rejected | overridden (Req 7.5)
    reason        STRING,                            -- required non-empty on reject (Req 7.4, service-enforced)
    decided_at    TIMESTAMP_NTZ  NOT NULL,
    CONSTRAINT pk_approval PRIMARY KEY (approval_id)
)
COMMENT = 'Approval decisions with reviewer identity + timestamp (Req 7.5, 7.6).';

-- -----------------------------------------------------------------------------
-- Grants to the application runtime role.
-- Workflow state is read/write for the app (mutable, unlike AUDIT).
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON TABLE "CASE"    TO ROLE AML_APP_ROLE;
GRANT SELECT, INSERT, UPDATE ON TABLE SAR_DRAFT TO ROLE AML_APP_ROLE;
GRANT SELECT, INSERT         ON TABLE APPROVAL  TO ROLE AML_APP_ROLE;  -- decisions are not edited in place
