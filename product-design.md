# SentinelAML Copilot — Product Design & Architecture

> Architectural deep-dive into SentinelAML Copilot: the Snowflake-native data fabric, the governed
> semantic layer, the dual-grounding engine, the trust boundaries that keep untrusted input out of the
> governed zone, the case lifecycle state machine, the human-in-the-loop approval flow, and the
> immutable audit trail. Diagrams are Mermaid; render them in any Markdown viewer that supports it.

---

## Table of contents

1. [Architecture at a glance](#1-architecture)
2. [Technology stack](#2-stack)
3. [Snowflake & CoCo CLI usage](#3-snowflake)
4. [The governed semantic layer](#4-semantic-layer)
5. [Trust boundaries](#5-trust-boundaries)
6. [Component & service map](#6-components)
7. [Case lifecycle state machine](#7-state-machine)
8. [Golden-path sequence](#8-golden-path)
9. [Dual-grounding & the groundedness gate](#9-grounding)
10. [Data architecture](#10-data)
11. [Security, governance & responsible AI](#11-security)
12. [Observability & evaluation](#12-observability)
13. [Failure & recovery](#13-failure)
14. [Correctness properties](#14-properties)
15. [Design principles & invariants](#15-principles)

---

## 1. Architecture at a glance {#1-architecture}

SentinelAML Copilot is a Snowflake-native application with a thin FastAPI application layer and a
Next.js experience layer. All figures and AI work happen inside Snowflake; the backend orchestrates,
grounds, gates, and audits.

```mermaid
graph TB
    subgraph Client[Experience Layer - Next.js]
        CC[Command Centre: alerts/cases + KPIs]
        INV[Investigation Workspace]
        EV[Evidence / Citation Drawer]
        REC[Recommendation Explanation]
        APQ[Human Approval Queue]
        AS[Action Status]
        AUD[Audit Trail View]
        HQ[System-Health & AI-Quality View]
    end

    subgraph API[Application Layer - FastAPI app/api]
        GW[Routers: ingest / cases / investigate / sar / approval / audit / kpi]
        WS[WebSocket live updates]
        AUTHZ[RBAC + purpose check]
    end

    subgraph Services[app/services - interface + Impl]
        TRIAGE[Alert Triage & Case Service]
        METRIC[Governed Metric Service<br/>semantic-view access only]
        NLQ[NL Investigation Service<br/>interpret → plan → ground]
        GROUND[Dual-Grounding + Groundedness Scorer]
        SAR[SAR Drafting Service]
        APPR[Approval / HITL Service]
        AUDIT[Immutable Audit Service]
        GUARD[Query Allow-list + Prompt-injection Guard]
        KPI[Observability / KPI Service]
    end

    subgraph Snowflake[Snowflake AI Data Cloud]
        RAW[(RAW: transaction_event, alert, entity, policy_doc)]
        STREAMS[Streams + Tasks: ingest / dedup]
        SEM[Semantic_View: Governed_Metrics + versions]
        CORTEX[Cortex: COMPLETE / EMBED_TEXT / VECTOR_COSINE_SIMILARITY]
        VEC[(Embedding store: policy & evidence chunks)]
        POL[Masking + Row-access policies]
        AUDITT[(AUDIT: audit_record append-only)]
        CASE[(CASE / SAR / APPROVAL state tables)]
    end

    CC --> GW
    INV --> GW
    APQ --> GW
    GW --> AUTHZ --> Services
    WS -.live.-> Client
    TRIAGE --> CASE
    TRIAGE --> METRIC
    METRIC --> SEM
    NLQ --> METRIC
    NLQ --> GUARD
    NLQ --> VEC
    GROUND --> CORTEX
    SAR --> CORTEX
    SAR --> METRIC
    APPR --> CASE
    STREAMS --> RAW
    RAW --> SEM
    Services --> AUDIT --> AUDITT
    POL -.enforce.-> RAW
    KPI --> AUDITT
```

**Repository layout (as built):**

```
backend/app/
  api/          # FastAPI routers: ingest, cases, investigate, sar, approval, audit, kpi, auth
  services/     # interface + Impl: triage, metric, investigation, grounding, sar, approval,
                #   audit, guard (injection + allow-list), kpi, glossary
  snowflake/    # session factory, startup credential guard, semantic-view accessors,
                #   stream/task defs, cortex adapter, policy helpers
  models/       # pydantic domain + document models
  config/       # pydantic-settings Settings + startup guard
  main.py       # app factory + startup guard
snowflake/      # CoCo CLI-provisioned objects
  ddl/ views/ policies/ tasks/ streams/ semantic/ seed/
frontend/       # Next.js dashboard
evaluation/     # ground-truth dataset + harness
```

---

## 2. Technology stack {#2-stack}

| Concern | Technology |
|---|---|
| Data cloud & compute | **Snowflake AI Data Cloud** — databases/schemas, tables, streams, tasks, semantic views, masking & row-access policies |
| AI (generation, embeddings, retrieval) | **Snowflake Cortex** — `SNOWFLAKE.CORTEX.COMPLETE`, `EMBED_TEXT_*`, `VECTOR_COSINE_SIMILARITY`; Cortex Search / Analyst where available |
| Developer workflow / provisioning | **Snowflake CoCo CLI** — provision objects, define Snowflake-native skills, documented commands |
| Governed metrics | **Semantic_View** with a versioned `Metric_Definition_Version` |
| Backend | **Python 3.11 + FastAPI**; Snowpark / Snowflake connector session |
| Config | **pydantic-settings** bound to env keys; startup credential guard |
| Frontend | **Next.js** dashboard |
| Property-based testing | **hypothesis** (`@given`, `@settings(max_examples=100)`) on **pytest** |

> **Reuse map.** Proven capabilities from the existing StreamContract.AI codebase are adapted rather
> than rebuilt: document parsing/OCR → policy & evidence ingestion; RAG/embedding/reranker → evidence
> retrieval re-pointed at Cortex `EMBED_TEXT_*` / `VECTOR_COSINE_SIMILARITY`; the citation/evidence
> model → dual-grounding; the LLM service → the Cortex generation adapter; the glossary service →
> business-term-to-canonical-metric mapping; audit patterns → the immutable audit store; the Next.js
> dashboard → the command-centre and investigation views. The streaming backbone is **not** reused —
> ingestion is Snowflake-native (streams/tasks).

---

## 3. Snowflake & CoCo CLI usage {#3-snowflake}

Snowflake features are selected for a justified role, not forced in.

| Snowflake feature | Why it's required | What it handles |
|---|---|---|
| Databases / schemas (RAW, SEM, VEC, APP, AUDIT) | Clean separation of raw data, governed metrics, embeddings, app state, audit | All persisted data |
| Tables | Synthetic source data + workflow state | transactions, alerts, entities, policy docs, cases, SAR drafts, approvals |
| **Streams + Tasks** | Snowflake-native ingestion with dedup; multi-replica-safe (dedup enforced server-side) | ingest transaction events, record `ingested_at`/`source`, suppress duplicates |
| **Semantic view** | Single governed, versioned source of all risk figures | `exposure_90d`, `txn_velocity`, `structuring_score` + definition versions |
| **Cortex** (`COMPLETE`, `EMBED_TEXT_*`, `VECTOR_COSINE_SIMILARITY`) | Generation + embedding/retrieval inside the data boundary | narrative drafting, policy/evidence embeddings and similarity retrieval |
| **Masking & row-access policies** | Role-based protection of sensitive synthetic fields | mask account identifiers for non-entitled roles; restrict row visibility |
| **Append-only audit table** | Immutable lineage; INSERT-only grants, UPDATE/DELETE denied | every transition, AI call, metric read, approval, action |
| **CoCo CLI** | Provision all objects + define Snowflake-native skills reproducibly | documented provisioning + seed on a clean account |

A key invariant: **Cortex is given numbers; it never produces them.** Regulatory figures always
originate in the semantic view inside the governed zone.

---

## 4. The governed semantic layer {#4-semantic-layer}

The Governed Metric Service is the **only** path to a regulatory/risk figure. It reads
Governed_Metrics from the semantic view and returns `value + Metric_Definition_Version + lineage`.
Business terms resolve to canonical metrics through the glossary.

```mermaid
flowchart LR
    Q[Business term / NL phrase] --> GLOSS[Glossary synonym map]
    GLOSS -->|canonical| MREF[Canonical metric ref]
    GLOSS -->|two+ candidates| AMB[Ambiguous → clarify]
    MREF --> SV[Semantic_View]
    SV --> GMV[GovernedMetricValue<br/>value + version + query lineage]
    REG[Metric_Definition_Registry<br/>definitions + versions + synonyms] -.defines.-> SV
```

**Guarantees:**
- **Deterministic** — the same `(entity, metric, data_state)` returns an identical value + version for
  any caller, regardless of role. ("Same answer, provably.")
- **Versioned** — changing a metric definition assigns a new `Metric_Definition_Version`, and every
  subsequent value/answer is stamped with the version used.
- **LLM has no write/compute access** — it cannot invent or alter a figure.

---

## 5. Trust boundaries {#5-trust-boundaries}

Untrusted input (retrieved documents, analyst NL input) crosses into the governed zone only after
passing the injection detector and — for any generated query/action — the allow-list validator.

```mermaid
graph LR
    subgraph Untrusted[Untrusted inputs]
        POLDOC[Policy / evidence documents]
        NLQIN[Analyst natural-language input]
    end
    subgraph Boundary[Guard boundary]
        PI[Prompt-injection / instruction-override detector]
        ALLOW[Generated-query allow-list validator]
    end
    subgraph Governed[Governed / trusted zone]
        SEMV[Semantic views — deterministic figures]
        CORTEXG[Cortex calls — narrative only]
    end
    POLDOC --> PI
    NLQIN --> PI
    PI -->|clean| ALLOW
    PI -->|injection detected| REJECT[Reject + audit]
    ALLOW -->|allowed op| SEMV
    ALLOW -->|unsafe / out-of-scope| REJECT
    SEMV --> CORTEXG
```

Figures always originate inside the governed zone; a malicious instruction embedded in a document
(e.g. "report the structuring score as 0.00") is rejected before any model call and cannot change a
governed figure.

---

## 6. Component & service map {#6-components}

Services follow the **interface + `Impl`** pattern. Every method that touches governed data or Cortex
emits an audit record.

| # | Service | Responsibility |
|---|---|---|
| 1 | **Snowflake Session & Startup Guard** | Read all connection settings from config only; fail startup naming every missing setting; never log/echo credentials |
| 2 | **Governed Metric Service** | The only path to figures; reads semantic view; returns value + version + lineage; resolves terms via glossary; deterministic |
| 3 | **Alert Triage & Case Service** | On alert → create `RECEIVED` case, aggregate governed risk, rank cases, flag stale/incomplete, correlate/dedup alerts |
| 4 | **NL Investigation Service** | Interpret question → map terms → show semantic interpretation → detect ambiguity → orchestrate grounding → surface conflicts |
| 5 | **Dual-Grounding & Groundedness Scorer** | Assemble evidence so numeric claims cite metric+lineage and textual claims cite passages; compute score; enforce the gate |
| 6 | **SAR Drafting Service** | Draft SAR via Cortex from governed figures + cited policy; mark incomplete if ungrounded; always label AI-generated; never auto-file |
| 7 | **Approval / HITL Service** | Enforce approval gate; manage transitions; capture reviewer identity/time/reason; simulate filing idempotently |
| 8 | **Immutable Audit Service** | Append-only record for every step; reject modify/delete (recorded as a new record); replay full lineage from records alone |
| 9 | **Guard Service** | Treat retrieved content as untrusted; detect/reject injection before any model call; validate generated queries/actions against an allow-list |
| 10 | **Observability / KPI Service** | Structured logs + workflow state; per-answer AI-quality telemetry; operational KPIs; separate demonstrated from intended values |
| — | **Authorization** | Role-based access (view / approve / change metric definition); deny + audit unauthorized; apply masking/row-access policies |

---

## 7. Case lifecycle state machine {#7-state-machine}

```mermaid
stateDiagram-v2
    [*] --> RECEIVED: Alert raised
    RECEIVED --> TRIAGED: governed risk aggregated
    TRIAGED --> INVESTIGATING: analyst opens case
    INVESTIGATING --> RECOMMENDATION_READY: dual-grounded SAR draft produced
    INVESTIGATING --> INVESTIGATING: more NL questions / clarifications
    RECOMMENDATION_READY --> AWAITING_APPROVAL: lineage + score presented
    AWAITING_APPROVAL --> APPROVED: reviewer approves
    AWAITING_APPROVAL --> REJECTED: reviewer rejects (+reason)
    APPROVED --> ACTION_COMPLETED: simulated filing succeeds
    APPROVED --> ACTION_FAILED: simulated failure → compensate
    ACTION_FAILED --> AWAITING_APPROVAL: recovery / retry
    REJECTED --> [*]
    ACTION_COMPLETED --> [*]
```

Every transition emits an append-only audit record. A **Risk_Signal never auto-advances to a confirmed
Risk_Event** — that transition requires human confirmation through the approval gate.

---

## 8. Golden-path sequence {#8-golden-path}

```mermaid
sequenceDiagram
    participant Str as Snowflake Stream/Task
    participant Tri as Triage Service
    participant Sem as Semantic View
    participant An as Analyst (NL)
    participant NLQ as Investigation Svc
    participant Gr as Grounding Scorer
    participant Cx as Cortex
    participant Sar as SAR Service
    participant Co as Compliance Officer
    participant Au as Audit Service

    Str->>Tri: Alert on structuring pattern
    Tri->>Sem: aggregate Governed_Metrics(entity)
    Sem-->>Tri: exposure_90d, txn_velocity, structuring_score (+versions)
    Tri->>Au: audit(case RECEIVED→TRIAGED)
    An->>NLQ: "Why is this entity high risk?"
    NLQ->>Sem: canonical metrics (via glossary)
    NLQ->>Gr: assemble numeric + textual evidence
    Gr->>Cx: COMPLETE(narrative grounded in evidence)
    Cx-->>Gr: narrative
    Gr->>Gr: compute Groundedness_Score + dual-grounding check
    Gr-->>An: answer OR refusal/flag (if below threshold)
    An->>Sar: request SAR
    Sar->>Cx: draft SAR from governed figures + cited policy
    Sar-->>Co: SAR draft (lineage attached) → AWAITING_APPROVAL
    Co->>Co: review lineage + score
    Co->>Au: approve → APPROVED
    Co->>Au: simulated filing → ACTION_COMPLETED, KPIs update
```

---

## 9. Dual-grounding & the groundedness gate {#9-grounding}

An answer is actionable **only** when both grounding conditions hold and the score clears the
threshold. Otherwise it is flagged for review or refused — never presented as fact.

```mermaid
flowchart TD
    DRAFT[Draft answer + evidence] --> NUM{Every numeric claim cites<br/>GovernedMetric + lineage?}
    DRAFT --> TXT{Every textual claim cites<br/>a policy/evidence passage?}
    NUM --> DG{Dual-grounded?}
    TXT --> DG
    DG -->|No| FLAG[Flagged for review / refused<br/>+ reason · route to human]
    DG -->|Yes| SCORE{Groundedness_Score ≥ threshold<br/>default 0.7?}
    SCORE -->|No| FLAG
    SCORE -->|Yes| ACT[Actionable answer<br/>figures governed · narrative cited]
    style FLAG fill:#ffd43b,color:#000
    style ACT fill:#51cf66,color:#fff
```

Generated narrative is always tagged as generated and kept disjoint from governed source facts — on
every surface, a user can tell what the model wrote from what the data says. Conflicting source records
are surfaced side-by-side, never silently resolved.

---

## 10. Data architecture {#10-data}

Domain models are pydantic records in the backend; the physical store is Snowflake. Audit tables are
append-only.

**Core domain entities:** `Entity`, `TransactionEvent` (with `event_id` dedup key, `occurred_at` vs
`ingested_at`), `Alert` (with `correlation_key`), `GovernedMetricValue` (value + version +
`data_state_hash` + query lineage — the only source of figures), `EvidenceItem`
(policy_passage / transaction_event / governed_metric), `Answer` (semantic interpretation + narrative +
numeric claims + textual claims + groundedness + status), `SARDraft`, `Case`, `Approval`,
`AuditRecord`.

**Snowflake physical model (CoCo CLI-provisioned):**

| Object | Type | Purpose |
|---|---|---|
| `RAW.TRANSACTION_EVENT`, `RAW.ALERT`, `RAW.ENTITY`, `RAW.POLICY_DOC` | tables | synthetic source data |
| `RAW.TRANSACTION_STREAM` + ingest task | stream + task | ingestion, dedup, record `ingested_at`/`source` |
| `SEM.ENTITY_RISK_METRICS` | **semantic view** | Governed_Metrics with name/definition/version |
| `SEM.METRIC_DEFINITION_REGISTRY` | table | metric definitions + versions + glossary synonyms |
| `VEC.POLICY_CHUNK`, `VEC.EVIDENCE_CHUNK` | tables w/ embeddings | Cortex `EMBED_TEXT_*` vectors for retrieval |
| `APP.CASE`, `APP.SAR_DRAFT`, `APP.APPROVAL` | tables | workflow state |
| `AUDIT.AUDIT_RECORD` | append-only table | immutable audit trail; INSERT-only, UPDATE/DELETE denied |
| masking policies, row-access policies | policies | role-based masking / row access |

Data-quality defects are intentionally injected (missing field, duplicate event, stale/late record) to
exercise non-happy paths; a freshness indicator exposes the latest ingested event time.

---

## 11. Security, governance & responsible AI {#11-security}

| Threat | Control |
|---|---|
| Prompt injection (incl. embedded in documents) | Treat retrieved content as untrusted; detect/reject instruction-override before any model call; record the rejection |
| Unsafe generated SQL / actions | Validate every generated query/action against an allow-list of governed operations; reject out-of-scope |
| Hallucinated figures / unsupported conclusions | Figures only from the semantic view; dual-grounding gate; refusal of ungroundable questions |
| Cross-role / sensitive-data access | RBAC (view / approve / change definition); Snowflake masking + row-access policies; deny + audit |
| Duplicate execution of an approved action | Idempotent action simulation; record suppressed duplicates |
| Tampered audit trail | Append-only store; modify/delete rejected and recorded as a new audit record |
| AI content mistaken for regulatory fact | Generated content visibly distinguished from governed facts on every surface; SAR always labelled AI-generated decision support, never "filed" |

Responsible-AI controls: evidence grounding, confidence presentation, source-vs-generated distinction,
human override, refusal for unsupported requests, full auditability, explanation of recommendations,
evaluation against ground truth, and feedback capture.

---

## 12. Observability & evaluation {#12-observability}

- **Per-answer AI-quality telemetry:** Groundedness_Score, dual-grounding (yes/no), refusal (yes/no),
  citation count.
- **Operational KPIs:** investigation time per case, % answers fully grounded, refusal-correctness
  count, duplicate-suppression count, alerts-to-cases correlation count.
- **Demonstrated vs intended** values are always separated — intended production targets are never
  presented as measured results.
- **Evaluation harness** (synthetic ground truth) measures groundedness, citation
  precision/coverage, unsupported-claim rate, refusal correctness, metric-consistency (same question →
  same answer), and prompt-injection resistance.

---

## 13. Failure & recovery {#13-failure}

```mermaid
flowchart TD
    CALL[Cortex / data call] --> OK{Success within timeout?}
    OK -->|Yes| CONT[Continue workflow]
    OK -->|No| RETRY{Retries left?<br/>default ≤ 2}
    RETRY -->|Yes| CALL
    RETRY -->|No| SAFE[Mark step failed · fail safe<br/>no actionable, ungrounded output]
    SAFE --> HUMAN[Route to human review]
    SAFE --> COMP[Compensate · leave case<br/>in consistent, auditable state]
    style SAFE fill:#ffd43b,color:#000
```

Low-confidence or ungrounded output is routed to human review rather than acted on; a mid-workflow
failure applies compensating behaviour and leaves no partial, unaudited commits.

---

## 14. Correctness properties {#14-properties}

The design is backed by universally-quantified properties, each implemented as a property-based test
(≥ 100 iterations). Representative examples:

- **Numeric figures originate only from governed metrics** — no numeric value comes from Cortex
  narrative.
- **Governed answers are role-invariant** — same question, same value + version + lineage, for any two
  roles ("same answer, provably").
- **Metric-definition changes produce a new version** stamped on all subsequent values.
- **Ambiguous questions yield a clarification, never a guess.**
- **Generated narrative is always distinguished from governed source facts** (disjoint, jointly
  covering the rendered content).
- **Dual-grounding citation completeness** — each cited claim resolves to its exact evidence.
- **Groundedness gate admits an answer iff grounded and above threshold.**
- **Out-of-scope / ungroundable questions are refused.**
- **Conflicting records are surfaced, never silently resolved.**
- **SAR drafts are structurally complete, fully cited, labelled AI-generated, and never auto-filed.**
- **Material actions require a passed approval gate; approved actions execute at most once
  (idempotent).**
- **Ingestion deduplicates by event id; freshness equals the latest event time.**
- **Secrets never appear in logs or prompts; the startup guard reports exactly the missing settings.**

---

## 15. Design principles & invariants {#15-principles}

1. **Governed figures are the source of truth** — the LLM never computes or alters a regulatory number.
2. **Dual-grounding gate** — actionable only when numeric + textual claims are both cited and the score
   clears the threshold.
3. **Human authority over material action** — nothing material happens without an approval gate.
4. **Immutable, replayable audit trail** — full lineage reconstructable from records alone; tamper
   attempts rejected and audited.
5. **Untrusted input is contained** — injection detection + allow-list validation before the governed
   zone.
6. **Risk_Signal never auto-becomes Risk_Event** — human confirmation required.
7. **Generated vs governed is always visible** — on every surface.
8. **Graceful degradation** — failures fail safe and route to human review; no partial, unaudited
   commits.
9. **Snowflake-native, config-only** — no credentials in source, prompts, or logs; startup fails loudly
   on a missing setting.
10. **Fully synthetic data only** — no real customer/account/transaction data anywhere.

> **Every figure governed. Every claim cited. Every decision auditable.**
