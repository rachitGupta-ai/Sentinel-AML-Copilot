/**
 * App sidebar: brand, primary navigation, and the role switcher. The role
 * switcher sets the context role that every request sends as `X-Role`, so an
 * operator can see RBAC and (in task 19.2) the "same answer, provably" property
 * by switching roles (Req 9.2, Req 11.5).
 *
 * The nav lists every view the UI provides (Req 11.1): the Command Centre and
 * Investigation workspace (task 19.1) plus the approval, action-status, audit,
 * and health views (task 19.2). An item marked `ready:false` renders as a
 * disabled "soon" placeholder.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useRole } from "@/lib/role-context";
import { ROLES, type Role } from "@/lib/types";

interface NavItem {
  href: string;
  label: string;
  ready: boolean;
}

const ITEMS: NavItem[] = [
  { href: "/", label: "Command Centre", ready: true },
  { href: "/investigate", label: "Investigation", ready: true },
  { href: "/approvals", label: "Approval Queue", ready: true },
  { href: "/actions", label: "Action Status", ready: true },
  { href: "/audit", label: "Audit Trail", ready: true },
  { href: "/health", label: "Health & AI Quality", ready: true },
];

const ROLE_LABELS: Record<Role, string> = {
  analyst: "Analyst",
  compliance_officer: "Compliance Officer",
  metric_steward: "Metric Steward",
  viewer: "Viewer",
};

export function Sidebar() {
  const pathname = usePathname();
  const { role, setRole } = useRole();

  return (
    <aside className="sidebar">
      <div className="brand">SentinelAML Copilot</div>

      <nav aria-label="Primary">
        {ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          if (!item.ready) {
            return (
              <a key={item.href} aria-disabled className="muted" style={{ cursor: "default" }}>
                {item.label} <span className="badge muted">soon</span>
              </a>
            );
          }
          return (
            <Link key={item.href} href={item.href} className={active ? "active" : ""}>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div style={{ marginTop: "1.5rem" }}>
        <label htmlFor="role-select" className="small muted">
          Acting as role
        </label>
        <select
          id="role-select"
          value={role}
          onChange={(e) => setRole(e.target.value as Role)}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
        <p className="small muted" style={{ marginTop: "0.5rem" }}>
          Sent as <code>X-Role</code> on every request. Unentitled roles see an
          audited 403.
        </p>
      </div>
    </aside>
  );
}
