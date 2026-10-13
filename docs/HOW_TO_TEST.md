# SentinelAML Copilot - How to Test (Judge Guide)

Snowflake CoCo CLI Hackathon 2026 | Challenge 1 - Risk, Fraud & Regulatory Intelligence Copilot

A governed AML investigation & SAR-reporting copilot built natively on Snowflake (Cortex +
semantic views). Every regulatory figure comes from a governed, versioned Snowflake semantic
view; the LLM only narrates around figures Snowflake computed - it never invents a number.

-------------------------------------------------------------------------------
## 1. Open the prototype
-------------------------------------------------------------------------------

Live link:  https://sentinel-aml-frontend.onrender.com

- No install, no login. It opens straight to the Command Centre.
- NOTE: free hosting tier - the FIRST load may take 30-50 seconds to wake the
  service. If the first screen is slow or shows a retry, wait and click Refresh.

-------------------------------------------------------------------------------
## 2. Choose a role (this replaces login)
-------------------------------------------------------------------------------

In the left sidebar under "Acting as role", pick:
  - Analyst            - view cases, run investigations
  - Compliance Officer - also approve actions
  - Metric Steward     - also change metric definitions
  - Viewer             - read-only (restricted actions return an audited 403)

The role is sent as an X-Role header on every request; unentitled actions are
denied and audited. No username, password, or token is required.

-------------------------------------------------------------------------------
## 3. Walk the golden path (5-7 minutes)
-------------------------------------------------------------------------------

STEP 1 - Command Centre
  You will see cases ranked by GOVERNED risk. Entity ENT-SMURF-001 is ranked #1
  with a structuring score ~0.90; normal entities are 0.0. Each case is labelled
  "Risk Signal" (a candidate) - the system never auto-confirms a finding.

STEP 2 - Investigation
  Click "Investigate" on ENT-SMURF-001. In the question box, type:

      What is the structuring and total exposure for this entity?

  Observe:
  - The SEMANTIC INTERPRETATION is shown first (which governed metrics the
    question mapped to).
  - The answer shows GOVERNED FIGURES, each stamped with metric definition
    version v1, with the AI narrative clearly marked as generated (distinct from
    the source figures).
  - Open the EVIDENCE DRAWER and click a figure: it traces to the exact metric,
    its version, and the generated SQL query lineage.
  - A groundedness score is shown; answers are only "actionable" when
    dual-grounded (numeric claim -> metric+lineage; textual claim -> cited passage).

  Try a refusal: ask something out of scope, e.g.
      Will this customer default on a loan next year?
  The system refuses rather than inventing an answer.

STEP 3 - Draft SAR (Suspicious Activity Report)
  Request a SAR for the case. It is produced as AI-GENERATED DECISION SUPPORT and
  is marked incomplete / not-filing-ready when any element cannot be grounded -
  a deliberate safety guarantee, not a defect.

STEP 4 - Approval Queue  (switch role to Compliance Officer)
  Review the recommendation, groundedness, and full lineage, then Approve. The
  case advances toward ACTION_COMPLETED; any "filing" is simulated only.

  NOTE: once approved, the case state becomes "approved" and stays there (it is a
  real workflow, not a reset-per-visitor demo). If you find ENT-SMURF-001 already
  in an approved state, a previous reviewer completed the flow - the full lineage
  is still fully replayable in the Audit Trail (Step 5). The Investigation, SAR,
  and Audit steps can be re-run regardless of the case state.

STEP 5 - Audit Trail
  Open the Audit Trail for the case and replay it: question -> governed metric ->
  generated query -> narrative -> approval, reconstructed from immutable,
  append-only audit records.

STEP 6 - Health & AI Quality
  See operational + AI-quality KPIs, with DEMONSTRATED values clearly separated
  from INTENDED production targets (measured results are never presented as targets).

-------------------------------------------------------------------------------
## 4. What to look for (why this is not just a chatbot)
-------------------------------------------------------------------------------

  - Governed figures: numbers come only from the Snowflake semantic view
    (deterministic - the same question yields the same number for every user).
  - Dual-grounding gate: ungrounded or low-confidence output is refused or flagged,
    never presented as fact.
  - Human-in-the-loop: no material action without an explicit approval.
  - Immutable audit: every step is replayable from append-only audit records.
  - Role-based access + masking enforced via Snowflake policies.

-------------------------------------------------------------------------------
## 5. References
-------------------------------------------------------------------------------

  Prototype:        https://sentinel-aml-frontend.onrender.com
  Backend health:   https://sentinel-aml-backend-u6dr.onrender.com/health
  Repository:       https://github.com/rachitGupta-ai/Sentinel-AML-Copilot
  Recorded demo:    https://github.com/rachitGupta-ai/Sentinel-AML-Copilot/blob/main/demo-sentinel-aml-copilot.mov

All data is fully synthetic. No real customer, account, or transaction data is used.
