/**
 * Evidence / citation drawer (Req 11.3).
 *
 * Any view can make a figure or claim clickable with {@link Citation}; clicking
 * it opens this drawer, which resolves the click to its grounding:
 *
 * - a **numeric** claim / governed metric → the governed metric name, value,
 *   `Metric_Definition_Version`, and full {@link QueryLineage} (generated query,
 *   source rows, semantic view, data-state hash);
 * - a **textual** claim / evidence item → the cited policy/transaction passage
 *   (kind, ref, verbatim excerpt).
 *
 * The grounding is always labelled as a **governed source fact** (Req 11.4) — the
 * drawer never shows AI narrative. State is held in a context so a single drawer
 * instance (mounted in the root layout) serves every view.
 */

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { formatFigure } from "@/lib/format";
import type {
  EvidenceItem,
  GroundingReference,
  NumericClaim,
  QueryLineage,
  TextualClaim,
  GovernedMetricValue,
} from "@/lib/types";

interface DrawerContextValue {
  open: (ref: GroundingReference) => void;
  close: () => void;
  current: GroundingReference | null;
}

const DrawerContext = createContext<DrawerContextValue | null>(null);

export function EvidenceDrawerProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<GroundingReference | null>(null);

  const open = useCallback((ref: GroundingReference) => setCurrent(ref), []);
  const close = useCallback(() => setCurrent(null), []);

  const value = useMemo(() => ({ open, close, current }), [open, close, current]);

  return (
    <DrawerContext.Provider value={value}>
      {children}
      <EvidenceDrawer />
    </DrawerContext.Provider>
  );
}

/** Access the drawer controls. Throws if used outside the provider. */
export function useEvidenceDrawer(): DrawerContextValue {
  const ctx = useContext(DrawerContext);
  if (!ctx) throw new Error("useEvidenceDrawer must be used within EvidenceDrawerProvider.");
  return ctx;
}

/**
 * A clickable citation. Renders `children` (the figure/claim text) as a button
 * that opens the evidence drawer resolved to `reference` (Req 11.3). Use this
 * anywhere a governed figure or cited claim appears.
 */
export function Citation({
  reference,
  children,
  title,
}: {
  reference: GroundingReference;
  children: ReactNode;
  title?: string;
}) {
  const { open } = useEvidenceDrawer();
  return (
    <button
      type="button"
      className="cite"
      title={title ?? "Show grounding"}
      onClick={() => open(reference)}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Drawer rendering.
// ---------------------------------------------------------------------------

function LineageView({ lineage }: { lineage: QueryLineage }) {
  return (
    <div>
      <h3 className="small muted">Query lineage</h3>
      {lineage.semantic_view ? (
        <p className="small">
          Semantic view: <code>{lineage.semantic_view}</code>
        </p>
      ) : null}
      {lineage.data_state_hash ? (
        <p className="small">
          Data-state hash: <code>{lineage.data_state_hash}</code>
        </p>
      ) : null}
      <div className="lineage mono" style={{ marginTop: "0.5rem" }}>
        {lineage.generated_query}
      </div>
      <p className="small muted" style={{ marginTop: "0.5rem" }}>
        Source rows ({lineage.source_row_refs.length})
      </p>
      {lineage.source_row_refs.length ? (
        <ul className="small">
          {lineage.source_row_refs.map((ref) => (
            <li key={ref}>
              <code>{ref}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small muted">No source rows recorded.</p>
      )}
    </div>
  );
}

function MetricGrounding({
  metricName,
  value,
  version,
  lineage,
}: {
  metricName: string;
  value: string;
  version: string;
  lineage: QueryLineage;
}) {
  return (
    <>
      <p>
        <span className="badge governed">Governed source fact</span>
      </p>
      <table className="data" style={{ marginBottom: "1rem" }}>
        <tbody>
          <tr>
            <th>Governed metric</th>
            <td>
              <code>{metricName}</code>
            </td>
          </tr>
          <tr>
            <th>Value</th>
            <td className="mono">{value}</td>
          </tr>
          <tr>
            <th>Definition version</th>
            <td>
              <code>{version}</code>
            </td>
          </tr>
        </tbody>
      </table>
      <LineageView lineage={lineage} />
    </>
  );
}

function numericGrounding(claim: NumericClaim) {
  return (
    <MetricGrounding
      metricName={claim.metric_name}
      value={formatFigure(claim.value)}
      version={claim.metric_definition_version}
      lineage={claim.lineage}
    />
  );
}

function metricValueGrounding(metric: GovernedMetricValue) {
  return (
    <MetricGrounding
      metricName={metric.metric_name}
      value={formatFigure(metric.value)}
      version={metric.metric_definition_version}
      lineage={metric.query_lineage}
    />
  );
}

function evidenceGrounding(evidence: EvidenceItem) {
  return (
    <>
      <p>
        <span className="badge governed">Cited source passage</span>{" "}
        <span className="badge muted">{evidence.kind}</span>
      </p>
      <table className="data" style={{ marginBottom: "1rem" }}>
        <tbody>
          <tr>
            <th>Reference</th>
            <td>
              <code>{evidence.ref}</code>
            </td>
          </tr>
        </tbody>
      </table>
      {evidence.excerpt ? (
        <div className="lineage" style={{ marginBottom: "1rem" }}>
          “{evidence.excerpt}”
        </div>
      ) : (
        <p className="small muted">No excerpt recorded for this passage.</p>
      )}
      {evidence.lineage ? <LineageView lineage={evidence.lineage} /> : null}
    </>
  );
}

function textualGrounding(claim: TextualClaim) {
  return (
    <>
      <p className="small">Claim: {claim.text}</p>
      {evidenceGrounding(claim.evidence)}
    </>
  );
}

function drawerTitle(ref: GroundingReference): string {
  switch (ref.kind) {
    case "numeric":
    case "metric":
      return "Figure grounding";
    case "textual":
      return "Claim grounding";
    case "evidence":
      return "Evidence";
  }
}

function drawerBody(ref: GroundingReference): ReactNode {
  switch (ref.kind) {
    case "numeric":
      return numericGrounding(ref.claim);
    case "metric":
      return metricValueGrounding(ref.metric);
    case "textual":
      return textualGrounding(ref.claim);
    case "evidence":
      return evidenceGrounding(ref.evidence);
  }
}

function EvidenceDrawer() {
  const { current, close } = useEvidenceDrawer();
  if (!current) return null;

  return (
    <>
      <div className="drawer-overlay" onClick={close} aria-hidden />
      <aside className="drawer" role="dialog" aria-modal aria-label="Evidence">
        <div className="drawer-header">
          <h2>{drawerTitle(current)}</h2>
          <button className="secondary" onClick={close} aria-label="Close evidence drawer">
            Close
          </button>
        </div>
        {drawerBody(current)}
      </aside>
    </>
  );
}
