/**
 * Renders the {@link SemanticInterpretation} the backend produces for a question
 * — shown *before* the answer (Req 5.1) so the analyst sees how the question was
 * understood (which canonical governed metrics and entities it resolved to, and
 * whether it was ambiguous).
 */

"use client";

import type { SemanticInterpretation } from "@/lib/types";

export function SemanticInterpretationView({
  interpretation,
  heading = "Semantic interpretation",
}: {
  interpretation: SemanticInterpretation;
  heading?: string;
}) {
  return (
    <div className="block" style={{ background: "var(--panel-2)" }}>
      <div className="block-header">
        <span className="badge muted">{heading}</span>
        <span className="small muted">how your question was understood</span>
      </div>
      <p className="small">“{interpretation.question}”</p>
      <div className="grid cols-2">
        <div>
          <div className="small muted">Resolved metrics</div>
          {interpretation.resolved_metrics.length ? (
            <ul className="small">
              {interpretation.resolved_metrics.map((m) => (
                <li key={m}>
                  <code>{m}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="small muted">none</p>
          )}
        </div>
        <div>
          <div className="small muted">Resolved entities</div>
          {interpretation.resolved_entities.length ? (
            <ul className="small">
              {interpretation.resolved_entities.map((e) => (
                <li key={e}>
                  <code>{e}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="small muted">none</p>
          )}
        </div>
      </div>
      {interpretation.ambiguous ? (
        <p className="small">
          <span className="badge warn">Ambiguous</span> candidates:{" "}
          {interpretation.candidate_terms.map((c) => (
            <code key={c} style={{ marginRight: "0.4rem" }}>
              {c}
            </code>
          ))}
        </p>
      ) : null}
    </div>
  );
}
