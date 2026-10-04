/**
 * Action-status view (Req 11.1, Req 7.3, Req 9.6).
 *
 * Shows the outcome of simulated material actions. An action may only run after
 * the Approval_Gate has passed, so the list surfaces cases in `approved`,
 * `action_completed`, and `action_failed` (resolved from `/api/cases` + per-case
 * detail). For an `approved` case a compliance officer can trigger
 * `POST .../action`; the recorded {@link ActionResult} is shown with its
 * `final_state` (ACTION_COMPLETED / ACTION_FAILED) and the
 * `suppressed_duplicate_count` that demonstrates at-most-once idempotency —
 * re-running increments the count rather than re-executing (Req 9.6).
 *
 * An analyst/viewer triggering an action sees the audited **unauthorised** state
 * (Req 9.2). Every surface defines the five states (Req 11.2) and live-updates
 * over `/api/stream` (Req 10.5).
 */

"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { ViewState } from "@/components/states";
import { api, isUnauthorised } from "@/lib/api";
import { formatState, formatTime } from "@/lib/format";
import { useLiveEvents } from "@/lib/live";
import { useRole } from "@/lib/role-context";
import { useAsync } from "@/lib/use-async";
import type { ActionResult, Case, Role } from "@/lib/types";

/** Case states relevant to the action-status view (post-Approval_Gate). */
const ACTION_STATES = new Set<Case["state"]>([
  "approved",
  "action_completed",
  "action_failed",
]);

async function fetchActionable(role: Role, signal: AbortSignal): Promise<Case[]> {
  const ranked = await api.listCases(role, signal);
  const detailed = await Promise.all(
    ranked.map((r) => api.getCase(role, r.case_id, signal)),
  );
  return detailed.filter((c) => ACTION_STATES.has(c.state));
}

type ExecPhase = "idle" | "working" | "unauthorised" | "error" | "done";

function FinalStateBadge({ state }: { state: Case["state"] }) {
  if (state === "action_completed") return <span className="badge governed">Action completed</span>;
  if (state === "action_failed") return <span className="badge danger">Action failed</span>;
  return <span className="badge muted">{formatState(state)}</span>;
}

function ActionRow({ c, onExecuted }: { c: Case; onExecuted: () => void }) {
  const { role } = useRole();
  const [phase, setPhase] = useState<ExecPhase>("idle");
  const [result, setResult] = useState<ActionResult | null>(null);
  const [error, setError] = useState<string>();

  async function execute() {
    setPhase("working");
    setError(undefined);
    try {
      const res = await api.executeAction(role, c.case_id);
      setResult(res);
      setPhase("done");
      onExecuted();
    } catch (err) {
      if (isUnauthorised(err)) {
        setPhase("unauthorised");
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }

  // An approved (or previously-failed, recoverable) case can be executed; a
  // completed one is terminal and only shows its status.
  const canExecute = c.state === "approved" || c.state === "action_failed";

  return (
    <>
      <tr>
        <td>
          <code>{c.case_id}</code>
        </td>
        <td>
          <code>{c.entity_id}</code>
        </td>
        <td>
          <FinalStateBadge state={c.state} />
        </td>
        <td style={{ textAlign: "right" }}>
          {canExecute ? (
            <button onClick={execute} disabled={phase === "working"}>
              {phase === "working"
                ? "Executing…"
                : c.state === "action_failed"
                  ? "Retry action"
                  : "Execute action"}
            </button>
          ) : (
            <Link href={`/audit?case=${encodeURIComponent(c.case_id)}`}>Audit →</Link>
          )}
        </td>
      </tr>
      {phase !== "idle" ? (
        <tr>
          <td colSpan={4}>
            {phase === "unauthorised" && (
              <div className="state unauthorised" role="alert">
                <div className="state-title">Not authorised</div>
                <div className="small">
                  Executing a material action requires the Compliance Officer role.
                  This denial was audited.
                </div>
              </div>
            )}
            {phase === "error" && (
              <div className="state error" role="alert">
                <div className="state-title">Execution failed</div>
                <div className="small">{error}</div>
                <div style={{ marginTop: "0.75rem" }}>
                  <button className="secondary" onClick={execute}>
                    Retry
                  </button>
                </div>
              </div>
            )}
            {phase === "done" && result && (
              <div className={`block ${result.succeeded ? "block-governed" : ""}`}>
                <div className="block-header">
                  <FinalStateBadge state={result.final_state} />
                  <span className="title">{result.action}</span>
                </div>
                <table className="data">
                  <tbody>
                    <tr>
                      <th>Succeeded</th>
                      <td>{result.succeeded ? "yes" : "no"}</td>
                    </tr>
                    <tr>
                      <th>Final state</th>
                      <td>{formatState(result.final_state)}</td>
                    </tr>
                    <tr>
                      <th>Executed at</th>
                      <td>{formatTime(result.executed_at)}</td>
                    </tr>
                    <tr>
                      <th>Suppressed duplicates</th>
                      <td>
                        <span className="mono">{result.suppressed_duplicate_count}</span>{" "}
                        <span className="small muted">
                          at-most-once idempotency — re-running increments this
                          rather than re-executing (Req 9.6)
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
                {canExecute ? (
                  <div className="row" style={{ marginTop: "0.5rem" }}>
                    <button className="secondary" onClick={execute}>
                      Run again (demonstrate idempotency)
                    </button>
                  </div>
                ) : null}
              </div>
            )}
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default function ActionsPage() {
  const { latest } = useLiveEvents();
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (latest) setReloadKey((k) => k + 1);
  }, [latest]);

  const { phase, data, error, forbidden, reload } = useAsync(
    (role, signal) => fetchActionable(role, signal),
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
          <h1>Action Status</h1>
          <p className="muted small">
            Simulated material actions for approved cases. No action runs before
            the Approval_Gate passes; execution is idempotent.
          </p>
        </div>
        <button className="secondary" onClick={refetch}>
          Refresh
        </button>
      </div>

      <section className="panel" aria-label="Actionable cases">
        <h2>Approved &amp; executed actions</h2>
        <ViewState
          phase={phase}
          data={data}
          error={error}
          forbidden={forbidden}
          onRetry={reload}
          loadingLabel="Loading action status…"
          isEmpty={(rows) => rows.length === 0}
          emptyTitle="No approved actions"
          emptyHint="Approve a case in the Approval Queue to make its action executable."
        >
          {(rows) => (
            <table className="data">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Entity</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <ActionRow key={c.case_id} c={c} onExecuted={refetch} />
                ))}
              </tbody>
            </table>
          )}
        </ViewState>
      </section>
    </div>
  );
}
