/**
 * Command_Centre (Req 11.1): the ranked alerts/cases list plus the KPI panel,
 * subscribed to the live `/api/stream` so new/correlated/approved cases refresh
 * the list in place (Req 10.5). Cases are labelled Risk_Signal (candidate) and
 * never shown as confirmed Risk_Events (Req 4.3). Every surface defines the five
 * states (Req 11.2) via {@link ViewState}; a stale case row is flagged.
 */

"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { KpiPanel } from "@/components/kpi-panel";
import { ViewState } from "@/components/states";
import { api } from "@/lib/api";
import { formatFigure, formatTime } from "@/lib/format";
import { useLiveEvents } from "@/lib/live";
import { useAsync } from "@/lib/use-async";

function LiveStatusBadge() {
  const { status, latest } = useLiveEvents();
  const label =
    status === "open" ? "Live" : status === "connecting" ? "Connecting…" : "Offline";
  const cls = status === "open" ? "governed" : status === "connecting" ? "warn" : "danger";
  return (
    <span className="row small">
      <span className={`badge ${cls}`}>{label}</span>
      {latest ? (
        <span className="muted">
          last: {latest.type.replace(/_/g, " ")} {formatTime(latest.occurred_at)}
        </span>
      ) : null}
    </span>
  );
}

export default function CommandCentrePage() {
  // Live events drive a reload counter: each new event refetches the ranked list
  // and the KPIs so the Command_Centre reflects the latest workflow state.
  const { latest } = useLiveEvents();
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (latest) setReloadKey((k) => k + 1);
  }, [latest]);

  const { phase, data, error, forbidden, reload } = useAsync(
    (role, signal) => api.listCases(role, signal),
    [reloadKey],
  );

  const refreshAll = useMemo(
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
          <h1>Command Centre</h1>
          <p className="muted small">
            Open cases ranked by governed risk. Each case is a{" "}
            <span className="badge warn">Risk Signal</span> — a candidate, not a
            confirmed finding.
          </p>
        </div>
        <div className="row">
          <LiveStatusBadge />
          <button className="secondary" onClick={refreshAll}>
            Refresh
          </button>
        </div>
      </div>

      <section className="panel" aria-label="Ranked cases">
        <h2>Ranked Cases</h2>
        <ViewState
          phase={phase}
          data={data}
          error={error}
          forbidden={forbidden}
          onRetry={reload}
          loadingLabel="Loading cases…"
          isEmpty={(rows) => rows.length === 0}
          emptyTitle="No open cases"
          emptyHint="Cases appear here as alerts are triaged. Ingest an alert to begin."
        >
          {(rows) => (
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Case</th>
                  <th>Entity</th>
                  <th style={{ textAlign: "right" }}>Governed risk</th>
                  <th>Classification</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.case_id}>
                    <td>{row.rank}</td>
                    <td>
                      <code>{row.case_id}</code>
                    </td>
                    <td>
                      <code>{row.entity_id}</code>
                    </td>
                    <td className="mono" style={{ textAlign: "right" }}>
                      {formatFigure(row.aggregate_score)}
                    </td>
                    <td>
                      <span className="badge warn">Risk Signal</span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Link href={`/investigate/${encodeURIComponent(row.case_id)}`}>
                        Investigate →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ViewState>
      </section>

      <KpiPanel reloadKey={reloadKey} />
    </div>
  );
}
