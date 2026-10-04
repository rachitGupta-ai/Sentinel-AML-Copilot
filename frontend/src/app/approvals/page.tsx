/**
 * Human-approval queue (Req 11.1, Req 7).
 *
 * The queue lists cases that are ready for the Approval_Gate — those in
 * `recommendation_ready` or `awaiting_approval`. `/api/cases` returns only the
 * governed-risk ranking (no state), so we resolve each ranked case's detail and
 * filter by state client-side (the detail fetch itself is RBAC-gated, so the
 * five states still flow through {@link ViewState}).
 *
 * Selecting a case loads its review payload via `POST .../submit` (which also
 * advances a `recommendation_ready` case into `awaiting_approval` and returns the
 * recommendation, groundedness score, full lineage, and evidence — Req 7.2).
 * A compliance officer can then Approve or Reject (Req 7.3, 7.4); an
 * analyst/viewer attempting either sees the audited **unauthorised** state
 * (Req 9.2). The queue live-updates over `/api/stream` (Req 10.5): any approval
 * transition refetches the list.
 *
 * Every surface defines the five states (Req 11.2): empty / loading / error /
 * unauthorised / stale (a stale case row is flagged).
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { Citation } from "@/components/evidence-drawer";
import { StaleBanner, ViewState } from "@/components/states";
import { api, isUnauthorised } from "@/lib/api";
import { formatFigure, formatState, formatTime } from "@/lib/format";
import { useLiveEvents } from "@/lib/live";
import { useRole } from "@/lib/role-context";
import { useAsync } from "@/lib/use-async";
import type { ApprovalItem, Case, Role, SARDraft } from "@/lib/types";

/** Case states that belong in the approval queue (Req 7.2). */
const QUEUE_STATES = new Set<Case["state"]>(["recommendation_ready", "awaiting_approval"]);

/**
 * Fetch the ranked open cases and resolve each to its detail, keeping only the
 * cases that are at or heading into the Approval_Gate. Returns the full detail so
 * the row can show state + freshness without a second round-trip.
 */
async function fetchQueue(role: Role, signal: AbortSignal): Promise<Case[]> {
  const ranked = await api.listCases(role, signal);
  const detailed = await Promise.all(
    ranked.map((r) => api.getCase(role, r.case_id, signal)),
  );
  return detailed.filter((c) => QUEUE_STATES.has(c.state));
}

function SarRecommendation({ sar }: { sar: SARDraft }) {
  return (
    <div className="block block-ai">
      <div className="block-header">
        <span className="badge ai">AI-generated decision support</span>
        <span className="title">SAR recommendation — never a filing</span>
      </div>
      <p className="small muted">Entity summary</p>
      <p>{sar.entity_summary}</p>
      <p className="small muted" style={{ marginTop: "0.5rem" }}>
        Suspicious pattern
      </p>
      <p>{sar.suspicious_pattern}</p>
      <div className="row small" style={{ marginTop: "0.5rem", flexWrap: "wrap" }}>
        <span className={`badge ${sar.completeness === "complete" ? "governed" : "warn"}`}>
          {sar.completeness === "complete" ? "Complete" : "Incomplete — not filing-ready"}
        </span>
        {sar.ungrounded_elements.length ? (
          <span className="muted">
            Ungrounded: {sar.ungrounded_elements.join(", ")}
          </span>
        ) : null}
      </div>

      {/* Governed figures — each clickable to its metric + version + lineage. */}
      <div className="block block-governed" style={{ marginTop: "0.75rem" }}>
        <div className="block-header">
          <span className="badge governed">Governed figures</span>
          <span className="title">From the semantic view only</span>
        </div>
        {sar.governed_figures.length ? (
          <table className="data">
            <thead>
              <tr>
                <th>Metric</th>
                <th style={{ textAlign: "right" }}>Value</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              {sar.governed_figures.map((m, i) => (
                <tr key={`${m.metric_name}-${i}`}>
                  <td>
                    <code>{m.metric_name}</code>
                  </td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    <Citation reference={{ kind: "metric", metric: m }} title="Show grounding">
                      {formatFigure(m.value)}
                    </Citation>
                  </td>
                  <td>
                    <code>{m.metric_definition_version}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="small muted">No governed figures in this draft.</p>
        )}
      </div>

      {/* Cited policy basis — each clickable to its passage. */}
      <div className="block block-governed">
        <div className="block-header">
          <span className="badge governed">Policy citations</span>
        </div>
        {sar.policy_citations.length ? (
          <ul>
            {sar.policy_citations.map((ev, i) => (
              <li key={`${ev.ref}-${i}`}>
                <Citation reference={{ kind: "evidence", evidence: ev }}>
                  {ev.kind}: <code>{ev.ref}</code>
                </Citation>
              </li>
            ))}
          </ul>
        ) : (
          <p className="small muted">No policy citations in this draft.</p>
        )}
      </div>
    </div>
  );
}

type DecisionPhase = "idle" | "working" | "unauthorised" | "error" | "done";

/** The review payload + Approve/Reject controls for a selected case. */
function ReviewPanel({
  caseId,
  onDecided,
}: {
  caseId: string;
  onDecided: () => void;
}) {
  const { role } = useRole();
  const [item, setItem] = useState<ApprovalItem | null>(null);
  const [loadPhase, setLoadPhase] = useState<
    "loading" | "ready" | "unauthorised" | "error"
  >("loading");
  const [loadError, setLoadError] = useState<string>();

  const [reviewerId, setReviewerId] = useState("");
  const [reason, setReason] = useState("");
  const [decision, setDecision] = useState<DecisionPhase>("idle");
  const [decisionError, setDecisionError] = useState<string>();
  const [outcome, setOutcome] = useState<string>();

  const loadPayload = useCallback(() => {
    setLoadPhase("loading");
    setLoadError(undefined);
    const controller = new AbortController();
    api
      .submitForApproval(role, caseId, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        setItem(payload);
        setLoadPhase("ready");
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (isUnauthorised(err)) {
          setLoadPhase("unauthorised");
          return;
        }
        setLoadError(err instanceof Error ? err.message : String(err));
        setLoadPhase("error");
      });
    return () => controller.abort();
  }, [role, caseId]);

  // (Re)load the review payload whenever the selected case or role changes.
  useEffect(() => loadPayload(), [loadPayload]);

  async function decide(kind: "approve" | "reject") {
    if (!reviewerId.trim()) return;
    if (kind === "reject" && !reason.trim()) return;
    setDecision("working");
    setDecisionError(undefined);
    try {
      const updated =
        kind === "approve"
          ? await api.approveCase(role, caseId, reviewerId.trim())
          : await api.rejectCase(role, caseId, reviewerId.trim(), reason.trim());
      setOutcome(
        `Case ${updated.case_id} ${kind === "approve" ? "approved" : "rejected"} — now ${formatState(
          updated.state,
        )}.`,
      );
      setDecision("done");
      onDecided();
    } catch (err) {
      if (isUnauthorised(err)) {
        setDecision("unauthorised");
        return;
      }
      setDecisionError(err instanceof Error ? err.message : String(err));
      setDecision("error");
    }
  }

  if (loadPhase === "loading") {
    return (
      <p className="small muted">
        <span className="spinner" aria-hidden /> Loading review payload…
      </p>
    );
  }
  if (loadPhase === "unauthorised") {
    return (
      <div className="state unauthorised" role="alert">
        <div className="state-title">Not authorised</div>
        <div className="small">Your role is not entitled to open this case for review.</div>
      </div>
    );
  }
  if (loadPhase === "error" || !item) {
    return (
      <div className="state error" role="alert">
        <div className="state-title">Could not load the review payload</div>
        <div className="small">{loadError ?? "Unknown error."}</div>
        <div style={{ marginTop: "0.75rem" }}>
          <button className="secondary" onClick={loadPayload}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="row small" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
        <span className={`badge ${item.groundedness_score >= 0.7 ? "governed" : "warn"}`}>
          Groundedness {(item.groundedness_score * 100).toFixed(0)}%
        </span>
        <span className="badge muted">Action on approval: {item.material_action}</span>
        <span className="muted">Lineage records: {item.lineage.records.length}</span>
      </div>

      <SarRecommendation sar={item.recommendation} />

      {item.evidence_items.length ? (
        <div className="block block-governed">
          <div className="block-header">
            <span className="badge governed">Supporting evidence</span>
          </div>
          <ul>
            {item.evidence_items.map((ev, i) => (
              <li key={`${ev.ref}-${i}`}>
                <Citation reference={{ kind: "evidence", evidence: ev }}>
                  {ev.kind}: <code>{ev.ref}</code>
                </Citation>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Decision controls (compliance_officer only; others get an audited 403). */}
      <section className="panel" aria-label="Decision">
        <h3>Decision</h3>
        <p className="small muted">
          Approve or reject this recommendation. A reviewer identity is required on
          every decision; a rejection also requires a reason (Req 7.4, 7.5).
        </p>
        <label htmlFor="reviewer-id" className="small muted">
          Reviewer identity
        </label>
        <input
          id="reviewer-id"
          type="text"
          value={reviewerId}
          placeholder="e.g. officer.jane"
          onChange={(e) => setReviewerId(e.target.value)}
        />
        <label htmlFor="reject-reason" className="small muted" style={{ marginTop: "0.5rem", display: "block" }}>
          Rejection reason (required to reject)
        </label>
        <textarea
          id="reject-reason"
          rows={2}
          value={reason}
          placeholder="Why is this being rejected?"
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="row" style={{ marginTop: "0.75rem" }}>
          <button
            onClick={() => decide("approve")}
            disabled={decision === "working" || !reviewerId.trim()}
          >
            {decision === "working" ? "Working…" : "Approve"}
          </button>
          <button
            className="secondary"
            onClick={() => decide("reject")}
            disabled={decision === "working" || !reviewerId.trim() || !reason.trim()}
          >
            Reject
          </button>
        </div>

        {decision === "unauthorised" && (
          <div className="state unauthorised" role="alert" style={{ marginTop: "1rem" }}>
            <div className="state-title">Not authorised</div>
            <div className="small">
              Approving or rejecting requires the Compliance Officer role. This
              denial was audited.
            </div>
          </div>
        )}
        {decision === "error" && (
          <div className="state error" role="alert" style={{ marginTop: "1rem" }}>
            <div className="state-title">Decision failed</div>
            <div className="small">{decisionError}</div>
          </div>
        )}
        {decision === "done" && outcome && (
          <p className="small" style={{ marginTop: "1rem" }}>
            <span className="badge governed">Recorded</span> {outcome}{" "}
            <Link href={`/audit?case=${encodeURIComponent(caseId)}`}>View audit trail →</Link>
          </p>
        )}
      </section>
    </div>
  );
}

export default function ApprovalsPage() {
  const { latest } = useLiveEvents();
  const [reloadKey, setReloadKey] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

  // Any live approval-queue transition refetches the queue (Req 10.5).
  useEffect(() => {
    if (latest) setReloadKey((k) => k + 1);
  }, [latest]);

  const { phase, data, error, forbidden, reload } = useAsync(
    (role, signal) => fetchQueue(role, signal),
    [reloadKey],
  );

  const refetch = useMemo(
    () => () => {
      setReloadKey((k) => k + 1);
      reload();
    },
    [reload],
  );

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: "1rem" }}>
        <div>
          <h1>Approval Queue</h1>
          <p className="muted small">
            Cases at the Approval_Gate. Approving or rejecting requires the{" "}
            <span className="badge muted">Compliance Officer</span> role — switch
            roles in the sidebar to see the audited 403 as an analyst/viewer.
          </p>
        </div>
        <button className="secondary" onClick={refetch}>
          Refresh
        </button>
      </div>

      <section className="panel" aria-label="Awaiting approval">
        <h2>Awaiting review</h2>
        <ViewState
          phase={phase}
          data={data}
          error={error}
          forbidden={forbidden}
          onRetry={reload}
          loadingLabel="Loading approval queue…"
          isEmpty={(rows) => rows.length === 0}
          emptyTitle="Nothing awaiting approval"
          emptyHint="Cases appear here once a dual-grounded recommendation is ready."
        >
          {(rows) => (
            <table className="data">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Entity</th>
                  <th style={{ textAlign: "right" }}>Governed risk</th>
                  <th>State</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.case_id} className={selected === c.case_id ? "" : "clickable"}>
                    <td>
                      <code>{c.case_id}</code>
                      {c.freshness.is_stale || !c.freshness.has_data ? (
                        <span className="badge warn" style={{ marginLeft: "0.4rem" }}>
                          stale
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <code>{c.entity_id}</code>
                    </td>
                    <td className="mono" style={{ textAlign: "right" }}>
                      {formatFigure(c.risk_aggregation.aggregate_score)}
                    </td>
                    <td>
                      <span className="badge muted">{formatState(c.state)}</span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="secondary"
                        onClick={() => setSelected(c.case_id)}
                        disabled={selected === c.case_id}
                      >
                        {selected === c.case_id ? "Reviewing" : "Review"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ViewState>
      </section>

      {selected ? (
        <section className="panel" aria-label="Review">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>
              Review <code>{selected}</code>
            </h2>
            <button className="secondary" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
          {/* A selected stale case keeps its banner visible during review. */}
          {data?.find((c) => c.case_id === selected)?.freshness.is_stale ? (
            <StaleBanner
              latestEventTime={formatTime(
                data.find((c) => c.case_id === selected)?.freshness.latest_event_time ?? null,
              )}
              hasData={data.find((c) => c.case_id === selected)?.freshness.has_data}
            />
          ) : null}
          <ReviewPanel caseId={selected} onDecided={refetch} />
        </section>
      ) : null}
    </div>
  );
}
