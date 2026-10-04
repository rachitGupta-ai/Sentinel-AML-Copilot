# SentinelAML Copilot — Frontend

Next.js 14 (App Router, TypeScript) dashboard for the **SentinelAML Copilot**. This
package delivers the operator-facing views over the FastAPI backend
(`../backend`): the **Command Centre**, the **Investigation workspace**, the
shared **evidence/citation drawer**, and the **approval**, **action-status**,
**audit-trail**, and **system-health & AI-quality** views — all mounted into one
sidebar shell.

## What's here

### Command Centre & investigation (task 19.1)

| View | Route | Purpose |
|------|-------|---------|
| Command Centre | `/` | Ranked open cases + KPI panel (demonstrated vs intended), live-updating over the `/api/stream` WebSocket. |
| Investigation (index) | `/investigate` | Pick a case to investigate. |
| Investigation (workspace) | `/investigate/{caseId}` | Ask an NL question; shows the semantic interpretation first, then the Answer / Clarification / Refusal with **AI narrative kept visibly distinct from governed facts**. |
| Evidence drawer | (overlay, any view) | Click any figure or claim to resolve its grounding — governed metric + version + lineage, or a cited policy/transaction passage. |

### Approval, actions, audit & health (task 19.2)

| View | Route | Purpose |
|------|-------|---------|
| Approval Queue | `/approvals` | Cases at the Approval_Gate (`recommendation_ready` / `awaiting_approval`). Opening a case loads its review payload via `POST /api/approval/{id}/submit` — the SAR recommendation, groundedness score, full lineage, and evidence (each citation clickable). A **Compliance Officer** can Approve/Reject (reviewer identity required; rejection requires a reason). An analyst/viewer attempting a decision sees the audited **unauthorised** state. Live-updates over `/api/stream`. |
| Action Status | `/actions` | Post-gate cases (`approved` / `action_completed` / `action_failed`). Trigger `POST /api/approval/{id}/action` for an approved case; the `ActionResult` shows `final_state` and `suppressed_duplicate_count`. "Run again" demonstrates at-most-once **idempotency** (the count increments rather than re-executing). Compliance-officer-only; others get the audited 403. |
| Audit Trail | `/audit?case={id}` | `GET /api/audit/{id}` → the replayable `Lineage`: the ordered, immutable `AuditRecord` chain (timestamp, actor, action, input/output refs, metric-definition version). Deep-linked from the approval/action views. |
| Health & AI Quality | `/health` | `GET /health` (Snowflake-aware state, failure kind, missing settings, degradation banner — **not** RBAC-gated) + the KPI panel (demonstrated vs intended, shown distinctly) + the **"same answer, provably"** panel. |

### "Same answer, provably" (Req 11.5)

The `/health` view fetches the **same governed question** (a case's risk
aggregation) under **two selectable roles** via the role-keyed api client, then
compares the governed value, `metric_definition_version`, and lineage. Identical
results across roles render a green "provably identical" badge — the role
invariance property (Property 7). Each figure stays clickable to its grounding.

Design principles enforced in the UI:

- **AI narrative is marked distinct from governed source facts** (Req 11.4) — the
  purple "AI narrative" block versus the green "Governed facts" blocks.
- **Every view defines the five states** (Req 11.2): empty, loading, error,
  unauthorised (403), and stale-data (`freshness.is_stale`). See
  `src/components/states.tsx` and `src/lib/use-async.ts`.
- **Grounding is always one click away** (Req 11.3) — the shared `Citation`
  component opens the evidence drawer resolved to the exact grounding.
- **Figures are never computed client-side** — governed values are rendered
  exactly as the backend returns them.

## Architecture (extensible by design)

```
src/
  app/
    layout.tsx                     # RoleProvider + EvidenceDrawerProvider + sidebar shell
    globals.css                    # design tokens + shared classes
    page.tsx                       # Command Centre
    investigate/page.tsx           # investigation index
    investigate/[caseId]/page.tsx  # investigation workspace
    approvals/page.tsx             # human-approval queue + review/decide
    actions/page.tsx               # action-status + idempotent execution
    audit/page.tsx                 # replayable audit-trail / lineage per case
    health/page.tsx                # system health + KPIs + same-answer-provably
  components/
    nav.tsx                        # sidebar + role switcher (lists all views)
    states.tsx                     # the five view states + ViewState<T> dispatcher
    evidence-drawer.tsx            # drawer context + <Citation>
    kpi-panel.tsx                  # demonstrated vs intended KPIs
    answer-view.tsx                # Answer / Clarification / Refusal rendering
    interpretation-view.tsx        # semantic interpretation (shown before the answer)
  lib/
    types.ts                       # TS mirrors of backend models + DTOs
    api.ts                         # typed REST client (X-Role header, 403 -> UnauthorisedError)
    live.ts                        # /api/stream WebSocket hook (reconnect/backoff)
    use-async.ts                   # role-aware fetch -> five-phase result
    role-context.tsx               # app-wide role (persisted), drives X-Role
    format.ts                      # display-only formatting
```

To add a view: create `src/app/<view>/page.tsx`, add its typed endpoint wrapper
to `src/lib/api.ts`, fetch with `useAsync` (or the api client directly for an
action), wrap the result in `ViewState`, make figures/claims clickable with
`Citation`, and flip the view's `ready` flag in `src/components/nav.tsx`.

## Configuration

Copy the example env file and point it at your backend:

```bash
cp .env.local.example .env.local
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | FastAPI base URL (no trailing slash). |
| `NEXT_PUBLIC_WS_BASE_URL` | derived from the API URL | WebSocket base for `/api/stream`. |

The backend applies RBAC from the `X-Role` header; pick the acting role from the
sidebar switcher (`analyst`, `compliance_officer`, `metric_steward`, `viewer`).
An unentitled role renders the audited **unauthorised** state.

## Run

```bash
# from this directory (frontend/)
npm install            # or: yarn / pnpm install

npm run dev            # dev server at http://localhost:3000
npm run build          # production build
npm run start          # serve the production build
npm run typecheck      # tsc --noEmit
npm run lint           # next lint
```

The backend must be running (see `../backend/README`/runbook) for live data; the
views degrade to their error state when the backend is unreachable.
