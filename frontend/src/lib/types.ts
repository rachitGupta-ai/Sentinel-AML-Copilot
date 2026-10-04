/**
 * TypeScript mirrors of the SentinelAML backend domain models and API DTOs.
 *
 * These types track the pydantic models in `backend/app/models/*` and the
 * request/response DTOs in `backend/app/api/*` (tasks 3.1, 18.1). Decimal values
 * are serialised by FastAPI as JSON numbers or strings depending on the encoder;
 * we type governed figures as `string | number` and format them defensively in
 * the UI so we never do arithmetic on an AI-adjacent figure (figures are
 * governed source facts, never computed in the client — Req 3.2, Req 11.4).
 */

/** Snowflake-style exact figures arrive as a number or a stringified decimal. */
export type GovernedNumber = number | string;

/** Roles the backend recognises; sent on every request via the `X-Role` header. */
export type Role = "analyst" | "compliance_officer" | "metric_steward" | "viewer";

export const ROLES: Role[] = [
  "analyst",
  "compliance_officer",
  "metric_steward",
  "viewer",
];

// ---------------------------------------------------------------------------
// Lineage, evidence, claims (backend app/models/common.py)
// ---------------------------------------------------------------------------

/** Traceable provenance for a governed figure (Req 3.2, Req 8.4). */
export interface QueryLineage {
  generated_query: string;
  source_row_refs: string[];
  semantic_view: string | null;
  data_state_hash: string | null;
}

export type EvidenceKind = "policy_passage" | "transaction_event" | "governed_metric";

/** A single citable piece of evidence (Req 5.4, Req 6.2). */
export interface EvidenceItem {
  kind: EvidenceKind;
  ref: string;
  excerpt: string | null;
  lineage: QueryLineage | null;
}

/** A numeric assertion grounded in a governed metric (never LLM-produced). */
export interface NumericClaim {
  text: string;
  metric_name: string;
  value: GovernedNumber;
  metric_definition_version: string;
  lineage: QueryLineage;
}

/** A textual/regulatory assertion grounded in a cited passage. */
export interface TextualClaim {
  text: string;
  evidence: EvidenceItem;
}

/** How an NL question was interpreted before answering (Req 5.1). */
export interface SemanticInterpretation {
  question: string;
  resolved_metrics: string[];
  resolved_entities: string[];
  ambiguous: boolean;
  candidate_terms: string[];
}

// ---------------------------------------------------------------------------
// Governed figures and freshness (backend app/models/governed.py)
// ---------------------------------------------------------------------------

/** A versioned, lineage-bearing governed figure — the only source of figures. */
export interface GovernedMetricValue {
  metric_name: string;
  entity_id: string;
  value: GovernedNumber;
  metric_definition_version: string;
  data_state_hash: string;
  query_lineage: QueryLineage;
}

/** Aggregated governed-risk view for an entity (Req 4.1). */
export interface EntityRiskView {
  entity_id: string;
  metrics: GovernedMetricValue[];
  aggregate_score: GovernedNumber;
}

/** Data-freshness state for the UI and triage staleness checks (Req 2.6, 4.4). */
export interface FreshnessIndicator {
  latest_event_time: string | null;
  has_data: boolean;
  is_stale: boolean;
}

// ---------------------------------------------------------------------------
// Case (backend app/models/case.py)
// ---------------------------------------------------------------------------

export type CaseState =
  | "received"
  | "triaged"
  | "investigating"
  | "recommendation_ready"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "action_completed"
  | "action_failed"
  | "closed";

export type Classification = "risk_signal" | "risk_event";

/** An investigation case over the lifecycle state machine (Req 4, Req 7). */
export interface Case {
  case_id: string;
  entity_id: string;
  state: CaseState;
  classification: Classification;
  risk_aggregation: EntityRiskView;
  freshness: FreshnessIndicator;
  correlated_alert_ids: string[];
}

/** One open case positioned in the governed-risk ranking (Req 4.2). */
export interface CaseRankingView {
  rank: number;
  case_id: string;
  entity_id: string;
  aggregate_score: GovernedNumber;
}

// ---------------------------------------------------------------------------
// Answer / Clarification / Refusal (backend app/models/answer.py + investigation_service.py)
// ---------------------------------------------------------------------------

export type AnswerStatus = "actionable" | "flagged_for_review" | "refused";

/** A grounded answer to an NL investigation question (Req 5). */
export interface Answer {
  case_id: string;
  semantic_interpretation: SemanticInterpretation;
  /** Cortex-generated narrative; MUST be rendered visibly as AI output (Req 11.4). */
  narrative: string;
  numeric_claims: NumericClaim[];
  textual_claims: TextualClaim[];
  conflicting_evidence: EvidenceItem[];
  is_conflicted: boolean;
  groundedness_score: number;
  dual_grounded: boolean;
  status: AnswerStatus;
  refusal_reason: string | null;
}

/** A request to disambiguate the question (Req 5.2). */
export interface Clarification {
  case_id: string;
  question: string;
  ambiguous_term: string;
  candidate_terms: string[];
  semantic_interpretation: SemanticInterpretation;
  message: string;
}

/** A refusal to answer an out-of-scope / ungroundable question (Req 5.6). */
export interface Refusal {
  case_id: string;
  question: string;
  reason: string;
  semantic_interpretation: SemanticInterpretation;
}

export type InvestigationResult = Answer | Clarification | Refusal;

/**
 * Discriminators for the investigation union. The backend returns the service's
 * union result serialised as-is (see investigate router), so the client
 * discriminates on shape: an Answer carries `status`, a Clarification
 * `candidate_terms` + `ambiguous_term`, a Refusal a bare `reason`.
 */
export function isAnswer(r: InvestigationResult): r is Answer {
  return (r as Answer).status !== undefined;
}

export function isClarification(r: InvestigationResult): r is Clarification {
  return (r as Clarification).ambiguous_term !== undefined;
}

export function isRefusal(r: InvestigationResult): r is Refusal {
  return !isAnswer(r) && !isClarification(r) && (r as Refusal).reason !== undefined;
}

// ---------------------------------------------------------------------------
// KPIs (backend app/models/telemetry.py)
// ---------------------------------------------------------------------------

export type KpiProvenance = "demonstrated" | "intended";

/** A single KPI value labelled with its provenance (Req 10.3, 10.4). */
export interface KpiValue {
  name: string;
  value: number;
  unit: string;
  provenance: KpiProvenance;
  detail: string | null;
}

/** A point-in-time snapshot of operational KPIs (Req 10.3, 10.4). */
export interface KpiSnapshot {
  computed_at: string;
  sample_size: number;
  /** Measured in this run — kept disjoint from `intended` (Req 10.4). */
  demonstrated: KpiValue[];
  /** Production targets, never presented as measured (Req 10.4). */
  intended: KpiValue[];
}

// ---------------------------------------------------------------------------
// SAR draft (backend app/models/answer.py)
// ---------------------------------------------------------------------------

export type SarCompleteness = "complete" | "incomplete";

/** An audit-ready SAR draft — the recommendation presented for approval (Req 6). */
export interface SARDraft {
  case_id: string;
  entity_summary: string;
  suspicious_pattern: string;
  governed_figures: GovernedMetricValue[];
  policy_citations: EvidenceItem[];
  completeness: SarCompleteness;
  ungrounded_elements: string[];
  /** Always true — the draft is decision support, never a filing (Req 6.4). */
  is_ai_generated_decision_support: boolean;
}

// ---------------------------------------------------------------------------
// Approval / HITL (backend app/services/approval_service.py)
// ---------------------------------------------------------------------------

/** The complete review payload presented when a case enters AWAITING_APPROVAL (Req 7.2). */
export interface ApprovalItem {
  case_id: string;
  recommendation: SARDraft;
  groundedness_score: number;
  lineage: Lineage;
  evidence_items: EvidenceItem[];
  material_action: string;
}

/** The recorded outcome of a (simulated) material action for a case (Req 7.3, 9.6). */
export interface ActionResult {
  case_id: string;
  action: string;
  succeeded: boolean;
  final_state: CaseState;
  executed_at: string;
  /** Suppressed duplicate executions; equals N-1 after N calls (idempotency, Req 9.6). */
  suppressed_duplicate_count: number;
}

// ---------------------------------------------------------------------------
// Audit trail (backend app/models/audit.py)
// ---------------------------------------------------------------------------

/** A single append-only, immutable audit-trail record (Req 8.1, 8.2). */
export interface AuditRecord {
  audit_id: string;
  timestamp_utc: string;
  case_ref: string | null;
  entity_ref: string | null;
  actor_id: string;
  action: string;
  input_refs: string[];
  output_refs: string[];
  metric_definition_version: string | null;
}

/** A case's full, replayable lineage reconstructed from audit records alone (Req 8.4). */
export interface Lineage {
  case_id: string;
  records: AuditRecord[];
}

// ---------------------------------------------------------------------------
// Health (backend /health — NOT under /api, not RBAC-gated)
// ---------------------------------------------------------------------------

export type HealthState = "healthy" | "misconfigured" | "unavailable";
export type FailureKind = "none" | "config" | "auth" | "connectivity" | "operational";

/** Snowflake-aware system-health probe payload (Req 1.4). */
export interface HealthStatus {
  status: HealthState;
  service: string;
  version: string;
  failure_kind: FailureKind;
  operation: string;
  detail: string;
  services_wired: boolean;
  live_clients: number;
  missing_settings?: string[];
}

// ---------------------------------------------------------------------------
// Live updates (backend app/api/live.py)
// ---------------------------------------------------------------------------

export type LiveEventType =
  | "case_opened"
  | "alert_correlated"
  | "case_submitted_for_approval"
  | "case_approved"
  | "case_rejected"
  | "action_completed"
  | "action_failed";

/** A lightweight, sensitive-data-free live update pushed over /api/stream. */
export interface LiveEvent {
  type: LiveEventType;
  case_id: string | null;
  entity_id: string | null;
  state: string | null;
  detail: string;
  occurred_at: string;
}

// ---------------------------------------------------------------------------
// Grounding reference — what the evidence drawer resolves a click to.
// ---------------------------------------------------------------------------

/**
 * A unified reference to the grounding behind a clicked figure or claim
 * (Req 11.3). The drawer renders a `numeric` reference as a governed metric +
 * version + lineage, and a `textual` reference as a cited policy/transaction
 * passage.
 */
export type GroundingReference =
  | { kind: "numeric"; claim: NumericClaim }
  | { kind: "textual"; claim: TextualClaim }
  | { kind: "metric"; metric: GovernedMetricValue }
  | { kind: "evidence"; evidence: EvidenceItem };
