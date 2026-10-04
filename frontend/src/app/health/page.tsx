/**
 * System-health & AI-quality view (Req 11.1, Req 1.4, Req 10.4, Req 11.5).
 *
 * Three sections:
 *
 * 1. **System health** — `GET /health` (not under `/api`, not RBAC-gated):
 *    the Snowflake-aware state (healthy / misconfigured / unavailable), the
 *    classified failure kind, whether services are wired, live client count, and
 *    any missing settings. A non-healthy state is shown as a degradation banner
 *    rather than hidden (Req 1.4, Req 13.2).
 * 2. **AI quality / KPIs** — reuses {@link KpiPanel}: demonstrated vs intended
 *    KPIs shown in visibly distinct columns so a measured result is never
 *    mistaken for a target (Req 10.4).
 * 3. **Same answer, provably** — the identical governed question (a case's risk
 *    aggregation) is fetched under TWO roles via the role-keyed api client, and
 *    the governed value + `metric_definition_version` + lineage are shown side by
 *    side. Equality across roles is the role-invariance property (Req 11.5,
 *    Property 7).
 *
 * Every section defines the five states (Req 11.2).
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import { Citation } from "@/components/evidence-drawer";
import { KpiPanel } from "@/components/kpi-panel";
import { ViewState } from "@/components/states";
import { api, isUnauthorised } from "@/lib/api";
import { formatFigure } from "@/lib/format";
import { useAsync } from "@/lib/use-async";
import { ROLES, type GovernedMetricValue, type HealthStatus, type Role } from "@/lib/types";

// ---------------------------------------------------------------------------
// 1. System health
// ---------------------------------------------------------------------------

function healthBadgeClass(status: HealthStatus["status"]): string {
  switch (status) {
    case "healthy":
      return "governed";
    case "misconfigured":
      return "warn";
    case "unavailable":
      return "danger";
  }
}

function SystemHealthPanel() {
  // /health is not RBAC-gated, so this fetch never yields the unauthorised
  // state; it degrades to the error state if the backend is unreachable.
  const { phase, data, error, reload } = useAsync((role, signal) => api.getHealth(role, signal));

  return (
    <section className="panel" aria-label="System health">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>System Health</h2>
        <button className="secondary" onClick={reload}>
          Refresh
        </button>
      </div>
      <ViewState
        phase={phase}
        data={data}
        error={error}
        onRetry={reload}
        loadingLabel="Checking health…"
      >
        {(h) => (
          <>
            <div className="row small" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
              <span className={`badge ${healthBadgeClass(h.status)}`}>{h.status}</span>
              {h.failure_kind !== "none" ? (
                <span className="badge warn">failure: {h.failure_kind}</span>
              ) : null}
              <span className="muted">
                {h.service} v{h.version}
              </span>
            </div>

            {h.status !== "healthy" ? (
              <div className="stale-banner" role="status" style={{ marginTop: "0.75rem" }}>
                <span aria-hidden>⚠</span>
                <span>
                  Backend is degraded ({h.status}). {h.detail || "See details below."}
                </span>
              </div>
            ) : null}

            <table className="data" style={{ marginTop: "0.75rem" }}>
              <tbody>
                <tr>
                  <th>Operation</th>
                  <td>
                    <code>{h.operation}</code>
                  </td>
                </tr>
                <tr>
                  <th>Detail</th>
                  <td>{h.detail || <span className="muted">—</span>}</td>
                </tr>
                <tr>
                  <th>Services wired</th>
                  <td>{h.services_wired ? "yes" : "no"}</td>
                </tr>
                <tr>
                  <th>Live clients</th>
                  <td className="mono">{h.live_clients}</td>
                </tr>
                {h.missing_settings && h.missing_settings.length ? (
                  <tr>
                    <th>Missing settings</th>
                    <td>
                      {h.missing_settings.map((s) => (
                        <code key={s} style={{ marginRight: "0.4rem" }}>
                          {s}
                        </code>
                      ))}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </>
        )}
      </ViewState>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 3. "Same answer, provably" — same governed question under two roles.
// ---------------------------------------------------------------------------

interface RoleFetch {
  phase: "idle" | "loading" | "ready" | "unauthorised" | "error";
  metrics: GovernedMetricValue[] | null;
  aggregate: string | null;
  error?: string;
}

const EMPTY_FETCH: RoleFetch = { phase: "idle", metrics: null, aggregate: null };

/** Deterministically serialise a metric's value + version + lineage for comparison. */
function fingerprint(m: GovernedMetricValue): string {
  return JSON.stringify({
    name: m.metric_name,
    value: m.value,
    version: m.metric_definition_version,
    lineage: m.query_lineage,
  });
}

function provablyIdentical(a: GovernedMetricValue[], b: GovernedMetricValue[]): boolean {
  if (a.length !== b.length) return false;
  const fa = a.map(fingerprint).sort();
  const fb = b.map(fingerprint).sort();
  return fa.every((v, i) => v === fb[i]);
}

function RoleColumn({ role, fetch }: { role: Role; fetch: RoleFetch }) {
  return (
    <div>
      <div className="block-header">
        <span className="badge muted">as {role}</span>
      </div>
      {fetch.phase === "loading" ? (
        <p className="small muted">
          <span className="spinner" aria-hidden /> Fetching…
        </p>
      ) : fetch.phase === "unauthorised" ? (
        <div className="state unauthorised" role="alert">
          <div className="state-title">Not authorised</div>
          <div className="small">This role is not entitled to view the case.</div>
        </div>
      ) : fetch.phase === "error" ? (
        <div className="state error" role="alert">
          <div className="small">{fetch.error}</div>
        </div>
      ) : fetch.metrics ? (
        <>
          <p className="small muted">
            Aggregate <span className="mono">{fetch.aggregate}</span>
          </p>
          <table className="data">
            <thead>
              <tr>
                <th>Metric</th>
                <th style={{ textAlign: "right" }}>Value</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              {fetch.metrics.map((m, i) => (
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
        </>
      ) : (
        <p className="small muted">Nothing fetched yet.</p>
      )}
    </div>
  );
}

function SameAnswerPanel() {
  const [caseId, setCaseId] = useState("");
  const [roleA, setRoleA] = useState<Role>("analyst");
  const [roleB, setRoleB] = useState<Role>("compliance_officer");
  const [fetchA, setFetchA] = useState<RoleFetch>(EMPTY_FETCH);
  const [fetchB, setFetchB] = useState<RoleFetch>(EMPTY_FETCH);
  const [ran, setRan] = useState(false);

  // Suggest a case id from the ranked list so the demo is one click away.
  const { phase: listPhase, data: cases } = useAsync((role, signal) =>
    api.listCases(role, signal),
  );
  useEffect(() => {
    if (!caseId && listPhase === "ready" && cases && cases.length) {
      setCaseId(cases[0].case_id);
    }
  }, [caseId, listPhase, cases]);

  const fetchAs = useCallback(
    async (role: Role, set: (f: RoleFetch) => void, id: string) => {
      set({ ...EMPTY_FETCH, phase: "loading" });
      try {
        const c = await api.getCase(role, id);
        set({
          phase: "ready",
          metrics: c.risk_aggregation.metrics,
          aggregate: formatFigure(c.risk_aggregation.aggregate_score),
        });
      } catch (err) {
        if (isUnauthorised(err)) {
          set({ ...EMPTY_FETCH, phase: "unauthorised" });
          return;
        }
        set({
          ...EMPTY_FETCH,
          phase: "error",
          error: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [],
  );

  async function run() {
    const id = caseId.trim();
    if (!id) return;
    setRan(true);
    // Fetch the SAME governed question under both roles, independently.
    await Promise.all([fetchAs(roleA, setFetchA, id), fetchAs(roleB, setFetchB, id)]);
  }

  const bothReady = fetchA.phase === "ready" && fetchB.phase === "ready";
  const identical =
    bothReady && fetchA.metrics && fetchB.metrics
      ? provablyIdentical(fetchA.metrics, fetchB.metrics) &&
        fetchA.aggregate === fetchB.aggregate
      : null;

  return (
    <section className="panel" aria-label="Same answer, provably">
      <h2>Same Answer, Provably</h2>
      <p className="small muted">
        The identical governed question — a case&apos;s risk aggregation — fetched
        under two roles. The governed value, metric-definition version, and lineage
        are role-invariant (Req 11.5).
      </p>

      <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
        <input
          type="text"
          value={caseId}
          placeholder="Case id, e.g. CASE-123"
          onChange={(e) => setCaseId(e.target.value)}
          style={{ maxWidth: "16rem" }}
        />
        <select value={roleA} onChange={(e) => setRoleA(e.target.value as Role)} style={{ maxWidth: "12rem" }}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <select value={roleB} onChange={(e) => setRoleB(e.target.value as Role)} style={{ maxWidth: "12rem" }}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <button onClick={run} disabled={!caseId.trim()}>
          Fetch under both roles
        </button>
      </div>

      {ran ? (
        <>
          {identical !== null ? (
            <p style={{ marginTop: "1rem" }}>
              {identical ? (
                <span className="badge governed">
                  Provably identical — same value, version &amp; lineage across roles
                </span>
              ) : (
                <span className="badge danger">
                  Values differ across roles — role-invariance violated
                </span>
              )}
            </p>
          ) : null}
          <div className="grid cols-2" style={{ marginTop: "0.75rem" }}>
            <RoleColumn role={roleA} fetch={fetchA} />
            <RoleColumn role={roleB} fetch={fetchB} />
          </div>
        </>
      ) : null}
    </section>
  );
}

export default function HealthPage() {
  return (
    <div>
      <h1>System Health &amp; AI Quality</h1>
      <p className="muted small">
        Backend health and degradation, AI-quality KPIs (demonstrated vs intended),
        and the role-invariance demonstration.
      </p>
      <SystemHealthPanel />
      <KpiPanel />
      <SameAnswerPanel />
    </div>
  );
}
