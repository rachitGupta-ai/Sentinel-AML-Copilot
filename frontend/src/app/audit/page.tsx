/**
 * Audit-trail view (Req 11.1, Req 8.4).
 *
 * `GET /api/audit/{case_id}` reconstructs a case's full {@link Lineage} from
 * append-only audit records alone. This view renders that ordered chain —
 * question → metric → generated query → source rows → narrative → human approval
 * → outcome — so the whole decision history is replayable and verifiable after
 * the fact (Req 8.4). Each record shows its UTC timestamp, actor, action,
 * input/output refs, and the `metric_definition_version` where a governed figure
 * was involved.
 *
 * A case id is required; it can arrive via the `?case=` query param (e.g. a link
 * from the approvals/actions views) or be picked from the ranked case list. Every
 * surface defines the five states (Req 11.2): an empty lineage ("no records yet")
 * is the honest answer for a case with no auditable operations, and the record
 * fetch flows loading / error / unauthorised through {@link ViewState}.
 */

"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ViewState } from "@/components/states";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { useAsync } from "@/lib/use-async";
import type { AuditRecord } from "@/lib/types";

function CasePicker({
  value,
  onPick,
}: {
  value: string;
  onPick: (caseId: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const { phase, data } = useAsync((role, signal) => api.listCases(role, signal));

  useEffect(() => setDraft(value), [value]);

  return (
    <section className="panel" aria-label="Pick a case">
      <h2>Replay a case&apos;s lineage</h2>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <input
          type="text"
          value={draft}
          placeholder="Case id, e.g. CASE-123"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft.trim()) onPick(draft.trim());
          }}
        />
        <button onClick={() => draft.trim() && onPick(draft.trim())} disabled={!draft.trim()}>
          Replay
        </button>
      </div>
      {phase === "ready" && data && data.length ? (
        <p className="small muted" style={{ marginTop: "0.5rem" }}>
          Open cases:{" "}
          {data.map((c) => (
            <button
              key={c.case_id}
              className="cite"
              style={{ marginRight: "0.6rem" }}
              onClick={() => onPick(c.case_id)}
            >
              {c.case_id}
            </button>
          ))}
        </p>
      ) : null}
    </section>
  );
}

function AuditRecordRow({ record, index }: { record: AuditRecord; index: number }) {
  return (
    <tr>
      <td className="mono">{index + 1}</td>
      <td>{formatTime(record.timestamp_utc)}</td>
      <td>
        <code>{record.action}</code>
      </td>
      <td>
        <code>{record.actor_id}</code>
      </td>
      <td className="small">
        {record.input_refs.length ? (
          record.input_refs.map((r) => (
            <div key={r}>
              <code>{r}</code>
            </div>
          ))
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="small">
        {record.output_refs.length ? (
          record.output_refs.map((r) => (
            <div key={r}>
              <code>{r}</code>
            </div>
          ))
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>
        {record.metric_definition_version ? (
          <code>{record.metric_definition_version}</code>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
    </tr>
  );
}

function AuditTrail({ caseId }: { caseId: string }) {
  const { phase, data, error, forbidden, reload } = useAsync(
    (role, signal) => api.getAudit(role, caseId, signal),
    [caseId],
  );

  return (
    <section className="panel" aria-label="Audit trail">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>
          Lineage for <code>{caseId}</code>
        </h2>
        <button className="secondary" onClick={reload}>
          Refresh
        </button>
      </div>
      <p className="small muted">
        Reconstructed from append-only audit records alone — the full, replayable
        decision chain (Req 8.4).
      </p>
      <ViewState
        phase={phase}
        data={data}
        error={error}
        forbidden={forbidden}
        onRetry={reload}
        loadingLabel="Replaying lineage…"
        isEmpty={(lineage) => lineage.records.length === 0}
        emptyTitle="No audit records yet"
        emptyHint="This case has produced no auditable operations. 'No records' is itself the honest answer."
      >
        {(lineage) => (
          <table className="data">
            <thead>
              <tr>
                <th>#</th>
                <th>Timestamp (UTC)</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Inputs</th>
                <th>Outputs</th>
                <th>Metric ver.</th>
              </tr>
            </thead>
            <tbody>
              {lineage.records.map((record, i) => (
                <AuditRecordRow key={record.audit_id} record={record} index={i} />
              ))}
            </tbody>
          </table>
        )}
      </ViewState>
    </section>
  );
}

function AuditPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const caseId = params.get("case") ?? "";

  // Keep the URL as the single source of truth so links (and reloads) deep-link
  // straight to a case's lineage.
  const pick = (next: string) => {
    router.replace(`/audit?case=${encodeURIComponent(next)}`);
  };

  return (
    <div>
      <h1>Audit Trail</h1>
      <p className="muted small">
        Replayable, immutable lineage per case — reading it requires the view-case
        entitlement; a non-entitled role sees the audited 403.
      </p>
      <CasePicker value={caseId} onPick={pick} />
      {caseId ? <AuditTrail caseId={caseId} /> : null}
    </div>
  );
}

export default function AuditPage() {
  // useSearchParams requires a Suspense boundary in the App Router.
  return (
    <Suspense fallback={<div className="state">Loading…</div>}>
      <AuditPageInner />
    </Suspense>
  );
}
