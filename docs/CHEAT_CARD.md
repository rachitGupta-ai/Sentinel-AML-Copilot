# Demo Cheat Card - keep open while recording

App: SentinelAML Copilot | Challenge 1 (Risk, Fraud & Regulatory Intelligence Copilot)

LIVE URLs (record against these - fully deployed):
- Frontend (demo)  : https://sentinel-aml-frontend.onrender.com
- Backend health   : https://sentinel-aml-backend-u6dr.onrender.com/health
- Snowsight        : https://app.snowflake.com/ulmsstp/vnb57096
- Case entity      : ENT-SMURF-001

Role (sidebar "Acting as role"): Analyst or Compliance Officer (both can view cases).

---

## 0. Pre-flight (2 min, before recording)
- Do Not Disturb ON. Browser zoom ~125%.
- Open https://sentinel-aml-frontend.onrender.com and HARD REFRESH (Cmd+Shift+R).
  First load may take ~30-50s (free tier wake) - do this BEFORE recording so it is warm.
- Confirm the Command Centre shows 3 ranked cases + KPIs, and the old "Connecting..."
  badge is gone (we hid it; it only shows "Live" when the WS connects).
- Click each sidebar screen once so you know what renders.
- Warm Cortex: open ENT-SMURF-001 -> Investigation, ask the question below once.

---

## 1. SNOWSIGHT - governed figures (hero shot #1)
Worksheet (role ACCOUNTADMIN):
```
SELECT entity_id, metric_name, metric_value, metric_definition_version
FROM SEM.ENTITY_RISK_METRIC_VALUES ORDER BY entity_id, metric_name;
```
Say: "Snowflake computes these figures. ENT-SMURF-001 structuring score 0.90, version v1.
Normal entities 0.0. The LLM never computes a number - it reads from here."

Append-only audit proof:
```
SHOW GRANTS ON TABLE AUDIT.AUDIT_RECORD;   -- only INSERT + SELECT
```

---

## 2. SQL FILES - "built natively on Snowflake" (open in editor)
Open these two (full table in VIDEO_PLAN.md):
- snowflake/semantic/20_entity_risk_metrics.sql -> "the governed view - deterministic figures"
- snowflake/ddl/04_audit.sql -> "immutable audit, enforced at the GRANT level"
Say: "One command - scripts/provision.sh - builds all of it on a clean account via the Snowflake CLI."

---

## 3. UI GOLDEN PATH (hero shot #2) - sidebar, in order
1. Command Centre - 3 cases RANKED by governed risk: ENT-SMURF-001 (0.90) #1, normals 0.0.
   Say: "Ranked by the semantic view, not the LLM. Each is a Risk Signal - a candidate, never
   auto-confirmed."
2. Investigation (open ENT-SMURF-001) - type EXACTLY:
     What is the structuring and total exposure for this entity?
   Say: "Semantic interpretation shown first, then governed figures with the AI narrative marked
   distinct. groundedness 1.0, dual-grounded." Open the evidence drawer -> click a figure ->
   shows metric + version v1 + the generated SQL lineage.
3. Draft SAR - "AI-generated decision support; marked incomplete / not-filing-ready because policy
   evidence is not grounded yet - it refuses to over-claim. That is the safety guarantee."
4. Approval Queue (Compliance Officer) - review -> Approve -> moves toward ACTION_COMPLETED
   (filing simulated only).
5. Audit Trail - replay the case: question -> metric -> query -> narrative -> approval.
6. Health & AI Quality - demonstrated KPIs, separated from intended targets.

---

## 4. CLOSE - evaluation harness (hero shot #3), run in terminal:
```
cd /Users/rachit.gupta/IdeaProjects/streamcontract-snowflake
PYTHONPATH="$PWD:$PWD/backend" backend/.venv/bin/python -m evaluation.harness
```
Say: "All metrics pass - groundedness, citation precision, refusal correctness, and prompt-injection
resistance 2/2. Demonstrated results, separated from intended targets. Not a chatbot - governed."

---

## Known-OK quirks (do not be thrown on camera)
- "Live" badge may not appear - expected; live push is best-effort over a free-tier WebSocket.
  All data loads via REST + the Refresh button works. Not a defect.
- Data freshness shows "stale / no data" - expected: seed loaded via direct COPY, not the stream.
  It is a correctly-working stale-data indicator.
- SAR = incomplete - correct and intentional (no policy embeddings ingested). A safety feature.
- First action after idle ~30-50s (free-tier wake) - warm it in pre-flight; trim waits in editing.

## Backup (if the UI stalls, show the terminal)
```
BE=https://sentinel-aml-backend-u6dr.onrender.com
curl -s -H "X-Role: compliance_officer" $BE/api/cases | python3 -m json.tool
curl -s -X POST -H "X-Role: analyst" -H "Content-Type: application/json" \
  $BE/api/investigate/8384900b-a122-49dc-94e0-1d4a3de0e36c \
  -d '{"question":"What is the structuring and total exposure for this entity?"}' | python3 -m json.tool
```
