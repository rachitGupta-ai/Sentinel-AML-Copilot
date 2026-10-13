# SentinelAML Copilot — How to Test

**Snowflake CoCo CLI Hackathon 2026 · Challenge 1 — Risk, Fraud & Regulatory Intelligence Copilot**

A governed AML suspicious-activity investigation & audit-ready SAR copilot, built natively on the
Snowflake AI Data Cloud (Cortex + governed semantic views). Every regulatory **figure** comes from a
governed, versioned Snowflake semantic view — deterministic, the same answer for everyone. The LLM
(Snowflake Cortex) only **narrates** around figures Snowflake already computed; it never invents a
number, and nothing is actionable unless **dual-grounded**.

---

## 1 · Open the prototype

| | |
|---|---|
| **Live link** | https://sentinel-aml-frontend.onrender.com |
| **Login** | None — access is via an in-app role switcher (see §2) |
| **First load** | Free hosting tier: the first request may take **30–50 seconds** to wake the service. If the first screen is slow or shows a retry, please wait and click **Refresh**. |

All data is **fully synthetic** — no real customer, account, or transaction data is used anywhere.

---

## 2 · Choose a role (this replaces login)

In the left sidebar under **"Acting as role"**, pick one:

| Role | Can do |
|---|---|
| **Analyst** | View cases, run investigations |
| **Compliance Officer** | Also approve actions in the Approval Queue |
| **Metric Steward** | Also change metric definitions |
| **Viewer** | Read-only — restricted actions return an audited 403 |

The role is sent as an `X-Role` header on every request; unentitled actions are denied and audited.
No username, password, API key, or token is required. Snowflake credentials are server-side only and
are never exposed to the client.

---

## 3 · Walk the golden path (5–7 minutes)

### Step 1 — Command Centre
Cases are ranked by **governed risk**. Entity **ENT-SMURF-001** is ranked **#1** with a structuring
score of ~**0.90**; normal entities are 0.0. Each case is labelled **"Risk Signal"** (a candidate) —
the system never auto-confirms a finding.

### Step 2 — Investigation
Open **ENT-SMURF-001** → **Investigate**. In the question box, type:

> **What is the structuring and total exposure for this entity?**

Observe:

- The **semantic interpretation** is shown first (which governed metrics the question mapped to).
- The answer shows **governed figures**, each stamped with **metric definition version v1**, with the
  AI narrative clearly marked as generated (distinct from the source figures).
- Open the **evidence drawer** and click a figure: it traces to the exact metric, its version, and the
  generated SQL query lineage — plus the cited policy passage.

**Try a refusal** — ask something out of scope, e.g. *"Will this customer default on a loan next year?"*
The system refuses rather than inventing an answer.

### Step 3 — Draft SAR (Suspicious Activity Report)
Request a SAR for the case. It is produced as **AI-generated decision support**, grounded in the
governed figures and cited policy. If any element cannot be grounded, the SAR is marked **incomplete /
not filing-ready** — a deliberate safety guarantee, not a defect.

### Step 4 — Approval Queue  *(switch role to Compliance Officer)*
Review the recommendation, groundedness score, and full lineage, then **Approve**. The case advances
toward **ACTION_COMPLETED**; any "filing" is **simulated only**.

> **Note:** once approved, the case state becomes *approved* and stays there — this is a real workflow,
> not a reset-per-visitor demo. If you find ENT-SMURF-001 already approved, a previous reviewer completed
> the flow; the full lineage is still replayable in the Audit Trail (Step 5), and Investigation / SAR /
> Audit can be re-run regardless of state.

### Step 5 — Audit Trail
Open the Audit Trail and **replay** the case: question → governed metric → generated query → narrative →
approval, reconstructed from **immutable, append-only** audit records.

### Step 6 — Health & AI Quality
Operational + AI-quality KPIs, with **demonstrated** values clearly separated from **intended**
production targets (measured results are never presented as targets).

---

## 4 · What to look for (why this is not just a chatbot)

- **Governed figures** — numbers come only from the Snowflake semantic view (deterministic; the same
  question yields the same number for every user).
- **Dual-grounding gate** — ungrounded or low-confidence output is refused or flagged, never shown as fact.
- **Human-in-the-loop** — no material action without an explicit approval.
- **Immutable audit** — every step is replayable from append-only audit records.
- **Role-based access + masking** — enforced via Snowflake policies.

---

## 5 · References

| | |
|---|---|
| Prototype | https://sentinel-aml-frontend.onrender.com |
| Backend health | https://sentinel-aml-backend-u6dr.onrender.com/health |
| Repository | https://github.com/rachitGupta-ai/Sentinel-AML-Copilot |
| Recorded demo | https://github.com/rachitGupta-ai/Sentinel-AML-Copilot/blob/main/demo-sentinel-aml-copilot.mov |
