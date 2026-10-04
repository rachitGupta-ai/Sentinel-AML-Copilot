/**
 * The five mandated per-view states (Req 11.2): empty, loading, error,
 * unauthorised, and stale-data. Every view composes these so the five states
 * are defined and rendered consistently. {@link ViewState} ties a view's async
 * data to the right state component, and {@link StaleBanner} overlays the
 * stale-data state on top of otherwise-rendered content.
 */

"use client";

import type { ReactNode } from "react";

import { type ForbiddenDetail } from "@/lib/api";

/** Loading state — shown while a request is in flight (Req 11.2). */
export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <div>
        <span className="spinner" aria-hidden />
        {label}
      </div>
    </div>
  );
}

/** Empty state — the request succeeded but there is nothing to show (Req 11.2). */
export function EmptyState({
  title = "Nothing here yet",
  hint,
}: {
  title?: string;
  hint?: string;
}) {
  return (
    <div className="state">
      <div className="state-title">{title}</div>
      {hint ? <div className="small">{hint}</div> : null}
    </div>
  );
}

/** Error state — a non-403 failure (Req 11.2, Req 13.2). */
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state error" role="alert">
      <div className="state-title">Something went wrong</div>
      <div className="small">{message}</div>
      {onRetry ? (
        <div style={{ marginTop: "0.75rem" }}>
          <button className="secondary" onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Unauthorised state — a 403 (Req 11.2, Req 9.2). Shows the backend's audited
 * denial detail (action + reason + audit id) so the denial is visibly provable,
 * never a silent blank.
 */
export function UnauthorisedState({ detail }: { detail?: ForbiddenDetail }) {
  return (
    <div className="state unauthorised" role="alert">
      <div className="state-title">Not authorised</div>
      <div className="small">
        {detail?.reason ?? "Your role is not entitled to view this."}
      </div>
      {detail?.action ? (
        <div className="small muted" style={{ marginTop: "0.5rem" }}>
          Action: <code>{detail.action}</code>
          {detail.audit_id ? (
            <>
              {" · "}audited as <code>{detail.audit_id}</code>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Stale-data state — overlaid when a case/view's data is stale
 * (`freshness.is_stale`) (Req 11.2, Req 4.4). This is a banner rather than a
 * full-screen state because stale data is still shown, just flagged.
 */
export function StaleBanner({
  latestEventTime,
  hasData,
}: {
  latestEventTime?: string | null;
  hasData?: boolean;
}) {
  return (
    <div className="stale-banner" role="status">
      <span aria-hidden>⚠</span>
      <span>
        {hasData === false
          ? "No data has been ingested for this view yet."
          : "Underlying data is stale."}
        {latestEventTime ? (
          <span className="muted"> Latest event: {latestEventTime}.</span>
        ) : null}{" "}
        Figures may not reflect the latest activity.
      </span>
    </div>
  );
}

/** The async lifecycle a view's primary data can be in, mapped to a state. */
export type AsyncPhase = "loading" | "error" | "unauthorised" | "ready";

/**
 * Render the right state component for a view's async data, or `children` when
 * ready. Centralises the empty/loading/error/unauthorised branching so each view
 * only has to decide what "empty" and "ready" look like.
 */
export function ViewState<T>({
  phase,
  data,
  error,
  forbidden,
  isEmpty,
  emptyTitle,
  emptyHint,
  loadingLabel,
  onRetry,
  children,
}: {
  phase: AsyncPhase;
  data: T | null;
  error?: string;
  forbidden?: ForbiddenDetail;
  isEmpty?: (data: T) => boolean;
  emptyTitle?: string;
  emptyHint?: string;
  loadingLabel?: string;
  onRetry?: () => void;
  children: (data: T) => ReactNode;
}) {
  if (phase === "loading") return <LoadingState label={loadingLabel} />;
  if (phase === "unauthorised") return <UnauthorisedState detail={forbidden} />;
  if (phase === "error") return <ErrorState message={error ?? "Unknown error."} onRetry={onRetry} />;
  if (data === null) return <EmptyState title={emptyTitle} hint={emptyHint} />;
  if (isEmpty && isEmpty(data)) return <EmptyState title={emptyTitle} hint={emptyHint} />;
  return <>{children(data)}</>;
}
