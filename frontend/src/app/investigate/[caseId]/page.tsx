/**
 * Investigation workspace for one case (Req 11.1, Req 5).
 *
 * Layout:
 * - a case header with the governed risk aggregation (each metric clickable to
 *   its lineage via the evidence drawer) and the freshness/stale state;
 * - an NL question box. On ask, the workspace first fetches and shows the
 *   semantic interpretation (Req 5.1), then the answer / clarification / refusal,
 *   rendered with AI narrative kept visibly distinct from governed facts
 *   (Req 11.4) by {@link InvestigationResultView}.
 *
 * Every data region defines the five states (Req 11.2): the case load has
 * loading/error/unauthorised/empty via {@link ViewState}; a stale case shows the
 * {@link StaleBanner}; the ask flow has its own loading/error/unauthorised
 * handling.
 */

"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { InvestigationResultView } from "@/components/answer-view";
import { SemanticInterpretationView } from "@/components/interpretation-view";
import { Citation } from "@/components/evidence-drawer";
import { StaleBanner, ViewState } from "@/components/states";
import { api, isUnauthorised } from "@/lib/api";
import { formatFigure, formatState, formatTime } from "@/lib/format";
import { useRole } from "@/lib/role-context";
import { useAsync } from "@/lib/use-async";
import type {
  Case,
  InvestigationResult,
  SemanticInterpretation,
} from "@/lib/types";

function CaseHeader({ c }: { c: Case }) {
  return (
    <section className="panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>
            Case <code>{c.case_id}</code>
          </h1>
          <p className="muted small">
            Entity <code>{c.entity_id}</code> · state{" "}
            <span className="badge muted">{formatState(c.state)}</span> ·{" "}
            <span className="badge warn">
              {c.classification === "risk_signal" ? "Risk Signal" : "Risk Event"}
            </span>
          </p>
        </div>
        <Link href="/">← Command Centre</Link>
      </div>

      {(c.freshness.is_stale || !c.freshness.has_data) && (
        <StaleBanner
          latestEventTime={formatTime(c.freshness.latest_event_time)}
          hasData={c.freshness.has_data}
        />
      )}

      <h2 style={{ marginTop: "0.5rem" }}>Governed risk</h2>
      <p className="small muted">
        Aggregate score{" "}
        <span className="mono">{formatFigure(c.risk_aggregation.aggregate_score)}</span>. Each
        figure is a governed metric — click it for its version and lineage.
      </p>
      {c.risk_aggregation.metrics.length ? (
        <table className="data">
          <thead>
            <tr>
              <th>Metric</th>
              <th style={{ textAlign: "right" }}>Value</th>
              <th>Version</th>
            </tr>
          </thead>
          <tbody>
            {c.risk_aggregation.metrics.map((m) => (
              <tr key={m.metric_name}>
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
        <p className="small muted">No governed metrics aggregated for this case yet.</p>
      )}
    </section>
  );
}

type AskPhase = "idle" | "interpreting" | "answering" | "done" | "error" | "unauthorised";

function QuestionWorkspace({ caseId }: { caseId: string }) {
  const { role } = useRole();
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<AskPhase>("idle");
  const [interpretation, setInterpretation] = useState<SemanticInterpretation | null>(null);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [error, setError] = useState<string>();

  const busy = phase === "interpreting" || phase === "answering";

  async function ask() {
    const q = question.trim();
    if (!q) return;
    setResult(null);
    setInterpretation(null);
    setError(undefined);

    // Step 1: show the semantic interpretation before answering (Req 5.1).
    setPhase("interpreting");
    try {
      const interp = await api.interpret(role, caseId, q);
      setInterpretation(interp);
    } catch (err) {
      if (isUnauthorised(err)) {
        setPhase("unauthorised");
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
      setPhase("error");
      return;
    }

    // Step 2: fetch the answer / clarification / refusal.
    setPhase("answering");
    try {
      const res = await api.investigate(role, caseId, q);
      setResult(res);
      setPhase("done");
    } catch (err) {
      if (isUnauthorised(err)) {
        setPhase("unauthorised");
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }

  return (
    <section className="panel">
      <h2>Ask about this case</h2>
      <p className="small muted">
        Natural-language question. The interpretation is shown first; the answer
        keeps AI narrative separate from governed facts.
      </p>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <textarea
          rows={2}
          value={question}
          placeholder="e.g. Why is this entity high risk?"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask();
          }}
        />
        <button onClick={ask} disabled={busy || !question.trim()}>
          {busy ? "Working…" : "Ask"}
        </button>
      </div>

      {phase === "interpreting" && (
        <p className="small muted" style={{ marginTop: "1rem" }}>
          <span className="spinner" aria-hidden /> Interpreting your question…
        </p>
      )}

      {/* Interpretation is shown before the answer (Req 5.1). While step 2 runs
          we render it standalone; once the result arrives InvestigationResultView
          renders the (same) interpretation above the answer, so we drop the
          standalone copy to avoid duplication. */}
      {interpretation && phase === "answering" && (
        <SemanticInterpretationView interpretation={interpretation} />
      )}

      {phase === "answering" && (
        <p className="small muted">
          <span className="spinner" aria-hidden /> Grounding and composing the answer…
        </p>
      )}

      {phase === "unauthorised" && (
        <div className="state unauthorised" role="alert" style={{ marginTop: "1rem" }}>
          <div className="state-title">Not authorised</div>
          <div className="small">Your role is not entitled to investigate this case.</div>
        </div>
      )}

      {phase === "error" && (
        <div className="state error" role="alert" style={{ marginTop: "1rem" }}>
          <div className="state-title">Investigation failed</div>
          <div className="small">{error}</div>
          <div style={{ marginTop: "0.75rem" }}>
            <button className="secondary" onClick={ask}>
              Retry
            </button>
          </div>
        </div>
      )}

      {phase === "done" && result && (
        <div style={{ marginTop: "1rem" }}>
          <InvestigationResultView result={result} />
        </div>
      )}
    </section>
  );
}

export default function InvestigationWorkspacePage() {
  const params = useParams<{ caseId: string }>();
  const caseId = decodeURIComponent(params.caseId);

  const { phase, data, error, forbidden, reload } = useAsync(
    (role, signal) => api.getCase(role, caseId, signal),
    [caseId],
  );

  return (
    <div>
      <ViewState
        phase={phase}
        data={data}
        error={error}
        forbidden={forbidden}
        onRetry={reload}
        loadingLabel="Loading case…"
        emptyTitle="Case not found"
      >
        {(c) => (
          <>
            <CaseHeader c={c} />
            <QuestionWorkspace caseId={c.case_id} />
          </>
        )}
      </ViewState>
    </div>
  );
}
