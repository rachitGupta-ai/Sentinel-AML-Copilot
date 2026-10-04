/**
 * Renders an investigation result — {@link Answer}, {@link Clarification}, or
 * {@link Refusal} — with the non-negotiable separation of AI narrative from
 * governed source facts (Req 11.4, Req 5.3).
 *
 * An Answer is shown as two visibly distinct regions:
 * - an **AI narrative** block (purple, labelled "AI narrative"), carrying only
 *   the Cortex-generated prose and never a governed figure of its own;
 * - **Governed facts** blocks listing the numeric claims (each a governed metric
 *   + version, clickable to its lineage) and the textual claims (each a cited
 *   passage, clickable to its excerpt) via the shared {@link Citation}.
 *
 * The groundedness gate outcome (actionable / flagged_for_review / refused) and
 * dual-grounding status are surfaced as badges so an un-actionable answer is
 * never mistaken for a fact.
 */

"use client";

import { Citation } from "@/components/evidence-drawer";
import { SemanticInterpretationView } from "@/components/interpretation-view";
import { formatFigure } from "@/lib/format";
import {
  isAnswer,
  isClarification,
  isRefusal,
  type Answer,
  type EvidenceItem,
  type InvestigationResult,
} from "@/lib/types";

function StatusBadge({ answer }: { answer: Answer }) {
  const map: Record<Answer["status"], { cls: string; label: string }> = {
    actionable: { cls: "governed", label: "Actionable" },
    flagged_for_review: { cls: "warn", label: "Flagged for review" },
    refused: { cls: "danger", label: "Refused" },
  };
  const s = map[answer.status];
  return <span className={`badge ${s.cls}`}>{s.label}</span>;
}

function GroundingSummary({ answer }: { answer: Answer }) {
  return (
    <div className="row small" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
      <StatusBadge answer={answer} />
      <span className={`badge ${answer.dual_grounded ? "governed" : "warn"}`}>
        {answer.dual_grounded ? "Dual-grounded" : "Not dual-grounded"}
      </span>
      <span className="muted">
        Groundedness: {(answer.groundedness_score * 100).toFixed(0)}%
      </span>
      {answer.is_conflicted ? (
        <span className="badge danger">Conflicting evidence</span>
      ) : null}
    </div>
  );
}

function ConflictBlock({ items }: { items: EvidenceItem[] }) {
  if (!items.length) return null;
  return (
    <div className="block block-governed">
      <div className="block-header">
        <span className="badge danger">Conflicting evidence</span>
        <span className="title">Surfaced, not resolved</span>
      </div>
      <p className="small muted">
        The source records below conflict. They are presented in full rather than
        silently reconciled.
      </p>
      <ul>
        {items.map((ev, i) => (
          <li key={`${ev.ref}-${i}`}>
            <Citation reference={{ kind: "evidence", evidence: ev }}>
              {ev.kind}: <code>{ev.ref}</code>
            </Citation>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AnswerBlocks({ answer }: { answer: Answer }) {
  return (
    <div>
      <GroundingSummary answer={answer} />

      {/* AI narrative — visibly distinct, generated text only (Req 11.4). */}
      <div className="block block-ai" style={{ marginTop: "1rem" }}>
        <div className="block-header">
          <span className="badge ai">AI narrative</span>
          <span className="title">Generated — decision support, not a governed fact</span>
        </div>
        <p>{answer.narrative}</p>
      </div>

      {/* Governed numeric facts — each clickable to its metric + lineage. */}
      <div className="block block-governed">
        <div className="block-header">
          <span className="badge governed">Governed facts — figures</span>
          <span className="title">From the semantic view only</span>
        </div>
        {answer.numeric_claims.length ? (
          <table className="data">
            <thead>
              <tr>
                <th>Claim</th>
                <th>Metric</th>
                <th style={{ textAlign: "right" }}>Value</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              {answer.numeric_claims.map((claim, i) => (
                <tr key={`${claim.metric_name}-${i}`}>
                  <td>{claim.text}</td>
                  <td>
                    <code>{claim.metric_name}</code>
                  </td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    <Citation reference={{ kind: "numeric", claim }} title="Show figure grounding">
                      {formatFigure(claim.value)}
                    </Citation>
                  </td>
                  <td>
                    <code>{claim.metric_definition_version}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="small muted">No governed figures cited in this answer.</p>
        )}
      </div>

      {/* Governed textual facts — each clickable to its cited passage. */}
      <div className="block block-governed">
        <div className="block-header">
          <span className="badge governed">Governed facts — citations</span>
          <span className="title">Cited policy / transaction passages</span>
        </div>
        {answer.textual_claims.length ? (
          <ul>
            {answer.textual_claims.map((claim, i) => (
              <li key={i} style={{ marginBottom: "0.4rem" }}>
                {claim.text}{" "}
                <Citation reference={{ kind: "textual", claim }} title="Show cited passage">
                  [{claim.evidence.kind}: {claim.evidence.ref}]
                </Citation>
              </li>
            ))}
          </ul>
        ) : (
          <p className="small muted">No cited passages in this answer.</p>
        )}
      </div>

      <ConflictBlock items={answer.conflicting_evidence} />

      {answer.refusal_reason ? (
        <p className="small muted">Reason: {answer.refusal_reason}</p>
      ) : null}
    </div>
  );
}

/** Render whichever member of the investigation union the backend returned. */
export function InvestigationResultView({ result }: { result: InvestigationResult }) {
  if (isAnswer(result)) {
    return (
      <div>
        <SemanticInterpretationView interpretation={result.semantic_interpretation} />
        <AnswerBlocks answer={result} />
      </div>
    );
  }

  if (isClarification(result)) {
    return (
      <div>
        <SemanticInterpretationView interpretation={result.semantic_interpretation} />
        <div className="block block-ai">
          <div className="block-header">
            <span className="badge warn">Clarification needed</span>
          </div>
          <p>{result.message}</p>
          <p className="small">
            The term <code>{result.ambiguous_term}</code> maps to more than one
            governed metric:
          </p>
          <ul>
            {result.candidate_terms.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
          <p className="small muted">
            No answer was guessed — refine your question to one of these metrics.
          </p>
        </div>
      </div>
    );
  }

  if (isRefusal(result)) {
    return (
      <div>
        <SemanticInterpretationView interpretation={result.semantic_interpretation} />
        <div className="block block-ai">
          <div className="block-header">
            <span className="badge danger">Refused</span>
          </div>
          <p>{result.reason}</p>
          <p className="small muted">
            Out-of-scope or ungroundable — no numeric or textual claim is presented
            as fact.
          </p>
        </div>
      </div>
    );
  }

  return null;
}
