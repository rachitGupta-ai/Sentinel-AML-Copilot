/**
 * `useAsync` — run a role-aware fetch and expose it as one of the five view
 * phases (Req 11.2). It maps a thrown {@link UnauthorisedError} to the
 * `unauthorised` phase and any other failure to `error`, so a view can hand the
 * result straight to {@link ViewState} without re-implementing the branching.
 *
 * The fetcher is keyed by the current role (and any extra deps) so switching
 * role re-runs the request with the new `X-Role` header — this is what powers
 * the "same answer, provably" role comparison in task 19.2.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import { isUnauthorised, type ForbiddenDetail } from "@/lib/api";
import { useRole } from "@/lib/role-context";
import type { Role } from "@/lib/types";
import type { AsyncPhase } from "@/components/states";

export interface AsyncResult<T> {
  phase: AsyncPhase;
  data: T | null;
  error?: string;
  forbidden?: ForbiddenDetail;
  /** Imperatively re-run the fetch (used by the error-state Retry button). */
  reload: () => void;
}

/**
 * Fetch `fetcher(role)` and track its phase. Re-runs whenever the role or any
 * entry in `deps` changes. Aborts the in-flight request on unmount/re-run so a
 * late response never overwrites fresher state.
 */
export function useAsync<T>(
  fetcher: (role: Role, signal: AbortSignal) => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
): AsyncResult<T> {
  const { role } = useRole();
  const [phase, setPhase] = useState<AsyncPhase>("loading");
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>();
  const [forbidden, setForbidden] = useState<ForbiddenDetail>();
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setError(undefined);
    setForbidden(undefined);

    fetcher(role, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setPhase("ready");
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (isUnauthorised(err)) {
          setForbidden(err.detail);
          setPhase("unauthorised");
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
        setPhase("error");
      });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role, nonce, ...deps]);

  return { phase, data, error, forbidden, reload };
}
