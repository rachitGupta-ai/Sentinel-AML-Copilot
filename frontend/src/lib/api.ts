/**
 * Typed REST client for the SentinelAML FastAPI backend (task 18.1).
 *
 * Every call sends the caller's role in the `X-Role` header (the backend's
 * fail-closed RBAC seam — an absent/unknown role is entitled to nothing). The
 * client classifies failures so views can render the five mandated states
 * (Req 11.2): a 403 surfaces as an {@link UnauthorisedError} carrying the
 * backend's `{error, action, reason, audit_id}` detail so the UI can show the
 * provably-audited denial; any other non-2xx becomes an {@link ApiError}.
 *
 * The base URL is configuration-driven via `NEXT_PUBLIC_API_BASE_URL` so the
 * same build points at local / e2e / staging backends without code changes.
 */

import type {
  ActionResult,
  ApprovalItem,
  Case,
  CaseRankingView,
  FreshnessIndicator,
  HealthStatus,
  InvestigationResult,
  KpiSnapshot,
  Lineage,
  Role,
  SemanticInterpretation,
} from "@/lib/types";

/** Configured backend base URL (no trailing slash), defaulting to localhost. */
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

/** The 403 detail body the backend returns for an unauthorised request. */
export interface ForbiddenDetail {
  error: string;
  action: string;
  reason: string | null;
  audit_id: string | null;
}

/** A non-2xx response that is not a 403. Views render this as the error state. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** A 403. Views render this as the dedicated unauthorised state (Req 11.2). */
export class UnauthorisedError extends ApiError {
  readonly detail: ForbiddenDetail;

  constructor(detail: ForbiddenDetail, body: unknown) {
    super(403, detail.reason ?? "Forbidden", body);
    this.name = "UnauthorisedError";
    this.detail = detail;
  }
}

/** Narrowing helper so view code can branch on the unauthorised state cleanly. */
export function isUnauthorised(err: unknown): err is UnauthorisedError {
  return err instanceof UnauthorisedError;
}

interface RequestOptions {
  role: Role;
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
}

/** FastAPI nests handler-raised detail under `detail`; unwrap it when present. */
function extractForbiddenDetail(body: unknown): ForbiddenDetail {
  const detail = (body as { detail?: unknown } | null)?.detail ?? body;
  const d = (detail ?? {}) as Partial<ForbiddenDetail>;
  return {
    error: d.error ?? "forbidden",
    action: d.action ?? "",
    reason: d.reason ?? null,
    audit_id: d.audit_id ?? null,
  };
}

async function request<T>(path: string, opts: RequestOptions): Promise<T> {
  const { role, method = "GET", body, signal } = opts;

  const headers: Record<string, string> = { "X-Role": role };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
      cache: "no-store",
    });
  } catch (cause) {
    // Network/connection failure — surface as a 0-status ApiError so the view
    // shows the error state rather than crashing (Req 11.2, Req 13.2).
    throw new ApiError(0, "Unable to reach the SentinelAML backend.", cause);
  }

  // Parse the body once; tolerate empty/non-JSON bodies.
  let parsed: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (res.status === 403) {
    throw new UnauthorisedError(extractForbiddenDetail(parsed), parsed);
  }
  if (!res.ok) {
    const message =
      (parsed as { detail?: string } | null)?.detail ??
      `Request to ${path} failed (${res.status}).`;
    throw new ApiError(res.status, String(message), parsed);
  }

  return parsed as T;
}

// ---------------------------------------------------------------------------
// Endpoint wrappers (one per backend route this task consumes).
// ---------------------------------------------------------------------------

export const api = {
  /** Command_Centre ranked open cases (Req 4.2). */
  listCases(role: Role, signal?: AbortSignal): Promise<CaseRankingView[]> {
    return request<CaseRankingView[]>("/api/cases", { role, signal });
  },

  /** A single case with governed risk aggregation + freshness (Req 11.1). */
  getCase(role: Role, caseId: string, signal?: AbortSignal): Promise<Case> {
    return request<Case>(`/api/cases/${encodeURIComponent(caseId)}`, { role, signal });
  },

  /** The stale/incomplete freshness indicator for a case (Req 4.4). */
  getFreshness(role: Role, caseId: string, signal?: AbortSignal): Promise<FreshnessIndicator> {
    return request<FreshnessIndicator>(
      `/api/cases/${encodeURIComponent(caseId)}/freshness`,
      { role, signal },
    );
  },

  /** Show the semantic interpretation before answering (Req 5.1). */
  interpret(
    role: Role,
    caseId: string,
    question: string,
    signal?: AbortSignal,
  ): Promise<SemanticInterpretation> {
    return request<SemanticInterpretation>(
      `/api/investigate/${encodeURIComponent(caseId)}/interpret`,
      { role, method: "POST", body: { question }, signal },
    );
  },

  /** Answer an NL question — returns Answer | Clarification | Refusal (Req 5). */
  investigate(
    role: Role,
    caseId: string,
    question: string,
    signal?: AbortSignal,
  ): Promise<InvestigationResult> {
    return request<InvestigationResult>(
      `/api/investigate/${encodeURIComponent(caseId)}`,
      { role, method: "POST", body: { question }, signal },
    );
  },

  /** Operational + AI-quality KPIs; demonstrated vs intended (Req 10.3, 10.4). */
  getKpis(role: Role, signal?: AbortSignal): Promise<KpiSnapshot> {
    return request<KpiSnapshot>("/api/kpi", { role, signal });
  },

  // -------------------------------------------------------------------------
  // Approval / HITL (Req 7). Submit needs VIEW_CASE; approve/reject/action
  // require the compliance_officer's APPROVE_ACTION — a lesser role gets an
  // audited 403 surfaced as UnauthorisedError (Req 9.2, Req 11.2).
  // -------------------------------------------------------------------------

  /** Submit a recommendation-ready case for approval -> review payload (Req 7.2). */
  submitForApproval(role: Role, caseId: string, signal?: AbortSignal): Promise<ApprovalItem> {
    return request<ApprovalItem>(
      `/api/approval/${encodeURIComponent(caseId)}/submit`,
      { role, method: "POST", signal },
    );
  },

  /** Approve an awaiting case, recording the reviewer (compliance_officer, Req 7.3). */
  approveCase(
    role: Role,
    caseId: string,
    reviewerId: string,
    signal?: AbortSignal,
  ): Promise<Case> {
    return request<Case>(`/api/approval/${encodeURIComponent(caseId)}/approve`, {
      role,
      method: "POST",
      body: { reviewer_id: reviewerId },
      signal,
    });
  },

  /** Reject an awaiting case with a reason (compliance_officer, Req 7.4). */
  rejectCase(
    role: Role,
    caseId: string,
    reviewerId: string,
    reason: string,
    signal?: AbortSignal,
  ): Promise<Case> {
    return request<Case>(`/api/approval/${encodeURIComponent(caseId)}/reject`, {
      role,
      method: "POST",
      body: { reviewer_id: reviewerId, reason },
      signal,
    });
  },

  /** Execute the approved material action, idempotently (compliance_officer, Req 7.3, 9.6). */
  executeAction(role: Role, caseId: string, signal?: AbortSignal): Promise<ActionResult> {
    return request<ActionResult>(
      `/api/approval/${encodeURIComponent(caseId)}/action`,
      { role, method: "POST", signal },
    );
  },

  /** Replay a case's immutable lineage from audit records alone (Req 8.4). */
  getAudit(role: Role, caseId: string, signal?: AbortSignal): Promise<Lineage> {
    return request<Lineage>(`/api/audit/${encodeURIComponent(caseId)}`, { role, signal });
  },

  /**
   * System-health probe (Req 1.4). NOTE: `/health` is NOT under `/api` and is
   * not RBAC-gated, so this never raises an {@link UnauthorisedError}; the
   * `X-Role` header is sent for consistency but ignored by the backend.
   */
  getHealth(role: Role, signal?: AbortSignal): Promise<HealthStatus> {
    return request<HealthStatus>("/health", { role, signal });
  },
};
