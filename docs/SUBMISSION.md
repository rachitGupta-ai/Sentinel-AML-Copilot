# SentinelAML Copilot - Submission Reply

Snowflake CoCo CLI Hackathon 2026 - Challenge 1 (Risk, Fraud & Regulatory Intelligence Copilot)

The evaluation email asked for three things: Prototype/Demo Link, Test Login Credentials,
Special Instructions. It wants a LIVE, testable URL (no YouTube required). We have one.

===============================================================================
  COPY-PASTE EMAIL REPLY (everything between the lines)
===============================================================================

Hi team,

Please find the requested details for our prototype below.

PROTOTYPE / DEMO LINK (live, interactive - open and test directly):
  https://sentinel-aml-frontend.onrender.com

  Supporting references:
  - Backend API health: https://sentinel-aml-backend-u6dr.onrender.com/health
  - Source repository:  https://github.com/rachitGupta-ai/Sentinel-AML-Copilot
  - Recorded walkthrough (fallback): 
    https://github.com/rachitGupta-ai/Sentinel-AML-Copilot/blob/main/demo-sentinel-aml-copilot.mov

TEST LOGIN CREDENTIALS:
  There is no username/password login. Access is via an in-app role switcher in the
  left sidebar ("Acting as role"). Please select a role to explore:
    - Analyst            : view cases, run investigations
    - Compliance Officer : also approve actions in the Approval Queue
    - Metric Steward     : also change metric definitions
    - Viewer             : read-only (shows an audited 403 on restricted actions)
  No token or API key is needed. Snowflake credentials are server-side only and are
  never exposed to the client.

SPECIAL INSTRUCTIONS:
  - Free hosting tier: the FIRST request may take ~30-50 seconds to wake the service,
    then it is responsive. Please retry once if the first load is slow.
  - Suggested evaluation path:
    1) Command Centre - cases ranked by governed risk (ENT-SMURF-001 is #1 at ~0.90).
       Each case is a "Risk Signal" (candidate), never an auto-confirmed finding.
    2) Open ENT-SMURF-001 -> Investigation -> ask:
       "What is the structuring and total exposure for this entity?"
       You will see the semantic interpretation first, then governed figures (stamped
       with metric definition version v1), the AI narrative marked distinct, and an
       evidence drawer that traces each figure to its metric + version + query lineage.
    3) Draft SAR -> AI-generated decision support; marked incomplete / not-filing-ready
       when any element is ungrounded (a safety guarantee).
    4) Approval Queue (as Compliance Officer) -> review groundedness + lineage -> approve.
    5) Audit Trail -> replay the full case lineage from immutable audit records.
    6) Health & AI Quality -> demonstrated KPIs, separated from intended targets.
  - Core principle: every regulatory NUMBER comes from a governed, versioned Snowflake
    semantic view (deterministic - same answer for everyone). Snowflake Cortex only
    narrates around figures Snowflake computed; it never invents a figure. Nothing is
    actionable unless dual-grounded (numeric claim cites metric+lineage, textual claim
    cites a policy passage).

Thank you - happy to provide anything else needed for scoring.

Best regards,
Rachit Gupta

===============================================================================
  END OF EMAIL REPLY
===============================================================================


## Live environment (reference - not part of the email)

| Component | URL / value |
|---|---|
| Frontend (demo) | https://sentinel-aml-frontend.onrender.com |
| Backend API | https://sentinel-aml-backend-u6dr.onrender.com |
| Snowflake account | ULMSSTP-VNB57096 (CoCo trial) |
| Cortex completion model | claude-sonnet-5-5 |
| Cortex embedding model | snowflake-arctic-embed-m-v1.5 (768-dim) |

Verified live end-to-end: Command Centre (3 ranked cases), Investigation (groundedness 1.0,
dual-grounded, cited lineage), Case detail, Audit Trail, KPIs, RBAC, health (Snowflake connected).

## Notes
- No YouTube link is required by the email - it asks for a live testable link, which the
  Render URL provides. The .mov in the repo is included only as a fallback walkthrough.
- Keep the Snowflake trial active during judging (expires Nov 3, 2026). A card is on file;
  cancel/downgrade in Snowsight -> Admin -> Billing & Payments AFTER judging if you do not
  want the $20/month charge.
