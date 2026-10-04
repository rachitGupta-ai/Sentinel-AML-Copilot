/**
 * App-wide role context. The selected {@link Role} is sent on every backend
 * request via the `X-Role` header (the backend's RBAC seam). Keeping it in one
 * context lets the nav switch roles and lets task 19.2's "same answer, provably"
 * demo compare two roles against the identical governed value and lineage
 * (Req 11.5). The choice is persisted to localStorage so a reload keeps the
 * operator's role.
 */

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { Role } from "@/lib/types";

const STORAGE_KEY = "governed-aml.role";
const DEFAULT_ROLE: Role = "analyst";

interface RoleContextValue {
  role: Role;
  setRole: (role: Role) => void;
}

const RoleContext = createContext<RoleContextValue | null>(null);

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<Role>(DEFAULT_ROLE);

  // Hydrate from localStorage after mount (avoids SSR/client mismatch).
  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) as Role | null;
    if (stored) setRoleState(stored);
  }, []);

  const setRole = useCallback((next: Role) => {
    setRoleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const value = useMemo(() => ({ role, setRole }), [role, setRole]);
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

/** Access the current role and setter. Throws if used outside the provider. */
export function useRole(): RoleContextValue {
  const ctx = useContext(RoleContext);
  if (!ctx) throw new Error("useRole must be used within a RoleProvider.");
  return ctx;
}
