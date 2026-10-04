/**
 * KPI panel for the Command_Centre (Req 10.3, 10.4).
 *
 * Renders the KPI snapshot with **demonstrated** (measured this run) and
 * **intended** (production target) values in visibly separate columns, each
 * labelled with its provenance badge, so a measured result is never mistaken for
 * a target (Req 10.4, Property 39). Owns its own five view states.
 */

"use client";

import { ViewState } from "@/components/states";
import { api } from "@/lib/api";
import { formatKpi, formatTime } from "@/lib/format";
import { useAsync } from "@/lib/use-async";
import type { KpiValue } from "@/lib/types";

function KpiList({ values, provenance }: { values: KpiValue[]; provenance: "demonstrated" | "intended" }) {
  if (!values.length) {
    return (
      <p className="small muted">
        {provenance === "demonstrated"
          ? "No measured KPIs yet — run an investigation to populate them."
          : "No intended targets declared."}
      </p>
    );
  }
  return (
    <table className="data">
      <tbody>
        {values.map((kpi) => (
          <tr key={kpi.name}>
            <td>
              {kpi.name.replace(/_/g, " ")}
              {kpi.detail ? <div className="small muted">{kpi.detail}</div> : null}
            </td>
            <td className="mono" style={{ textAlign: "right" }}>
              {formatKpi(kpi.value, kpi.unit)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function KpiPanel({ reloadKey }: { reloadKey?: number }) {
  const { phase, data, error, forbidden, reload } = useAsync(
    (role, signal) => api.getKpis(role, signal),
    [reloadKey],
  );

  return (
    <section className="panel" aria-label="KPIs">
      <h2>KPIs &amp; AI Quality</h2>
      <ViewState
        phase={phase}
        data={data}
        error={error}
        forbidden={forbidden}
        onRetry={reload}
        loadingLabel="Loading KPIs…"
        isEmpty={(d) => d.demonstrated.length === 0 && d.intended.length === 0}
        emptyTitle="No KPIs yet"
        emptyHint="KPIs populate as cases are triaged and investigated."
      >
        {(snapshot) => (
          <>
            <p className="small muted">
              Computed {formatTime(snapshot.computed_at)} · sample size{" "}
              {snapshot.sample_size}
            </p>
            <div className="grid cols-2">
              <div>
                <div className="block-header">
                  <span className="badge governed">Demonstrated</span>
                  <span className="small muted">measured this run</span>
                </div>
                <KpiList values={snapshot.demonstrated} provenance="demonstrated" />
              </div>
              <div>
                <div className="block-header">
                  <span className="badge muted">Intended</span>
                  <span className="small muted">production target — not measured</span>
                </div>
                <KpiList values={snapshot.intended} provenance="intended" />
              </div>
            </div>
          </>
        )}
      </ViewState>
    </section>
  );
}
