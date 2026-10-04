/**
 * Investigation index — choose a case to investigate. Reuses the ranked open
 * cases list (same governed ranking as the Command_Centre) and links each to its
 * investigation workspace. Defines the five states via {@link ViewState}.
 */

"use client";

import Link from "next/link";

import { ViewState } from "@/components/states";
import { api } from "@/lib/api";
import { formatFigure } from "@/lib/format";
import { useAsync } from "@/lib/use-async";

export default function InvestigateIndexPage() {
  const { phase, data, error, forbidden, reload } = useAsync((role, signal) =>
    api.listCases(role, signal),
  );

  return (
    <div>
      <h1>Investigation</h1>
      <p className="muted small">Pick a case to open its investigation workspace.</p>

      <section className="panel">
        <ViewState
          phase={phase}
          data={data}
          error={error}
          forbidden={forbidden}
          onRetry={reload}
          loadingLabel="Loading cases…"
          isEmpty={(rows) => rows.length === 0}
          emptyTitle="No open cases to investigate"
          emptyHint="Cases appear here once alerts are triaged."
        >
          {(rows) => (
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Case</th>
                  <th>Entity</th>
                  <th style={{ textAlign: "right" }}>Governed risk</th>
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
                    <td style={{ textAlign: "right" }}>
                      <Link href={`/investigate/${encodeURIComponent(row.case_id)}`}>
                        Open →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ViewState>
      </section>
    </div>
  );
}
