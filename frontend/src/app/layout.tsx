/**
 * Root layout: wraps the whole dashboard in the {@link RoleProvider} (so every
 * view shares one role / `X-Role`) and the {@link EvidenceDrawerProvider} (so any
 * view can open the evidence drawer, Req 11.3), and renders the shared sidebar
 * shell the views slot into. This is the single, extensible frame task 19.2's
 * views also mount into.
 */

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Sidebar } from "@/components/nav";
import { EvidenceDrawerProvider } from "@/components/evidence-drawer";
import { RoleProvider } from "@/lib/role-context";
import "./globals.css";

export const metadata: Metadata = {
  title: "SentinelAML Copilot",
  description:
    "Audit-ready AML investigation copilot — governed figures, grounded narrative, full lineage.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <RoleProvider>
          <EvidenceDrawerProvider>
            <div className="app-shell">
              <Sidebar />
              <main className="main">{children}</main>
            </div>
          </EvidenceDrawerProvider>
        </RoleProvider>
      </body>
    </html>
  );
}
