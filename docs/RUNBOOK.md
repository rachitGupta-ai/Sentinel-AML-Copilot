# SentinelAML Copilot — Runbook

Spec: `aml-regulatory-copilot` | Task **21.1** | Requirements: **1.3, 14.1, 14.3**

This runbook takes you from a **clean Snowflake account** to a running end-to-end demo of the
**SentinelAML Copilot** — an AML suspicious-activity investigation and audit-ready SAR reporting
copilot built natively on the Snowflake AI Data Cloud with the **Snowflake CoCo CLI** developer
workflow.

> **Responsible-data constraint.** All data is **fully synthetic** (Req 2.1). No real customer,
> account, or transaction data appears in the repository, prompts, logs, screenshots, or generated
> reports.
>
> **Config-only credentials (Req 1.1).** No secret is ever embedded in source, scripts, prompts, or
> logs. The Snowflake connection is supplied entirely by your configured CoCo CLI connection and by
> environment variables read at backend startup. Startup fails loudly, naming every missing setting,
> if any required value is absent (Req 1.2).

---

## 1. Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| **Snowflake CoCo CLI** (`snow`) | current | Provision Snowflake objects, run seed loader (Req 1.3) |
| **Python** | **3.11** | Backend (FastAPI), seed generator, evaluation harness |
| **Node.js + npm** | 18+ (LTS) | Next.js dashboard (`frontend/`) |
| **Snowflake account** | — | A clean account whose provisioning role can `CREATE DATABASE` / `CREATE ROLE` (ACCOUNTADMIN / SECURITYADMIN or a delegated role) for the first run |

**LLM / embeddings note.** Generation, embeddings, and retrieval use **Snowflake Cortex**
(`SNOWFLAKE.CORTEX.COMPLETE`, `EMBED_TEXT_*`, `VECTOR_COSINE_SIMILARITY`) — in-account, so no data
leaves Snowflake. The backend's Cortex adapter is config-gated (`CORTEX_*`, see §2) with safe
defaults; if Cortex is unavailable the pipeline **fails safe** (no ungrounded output) rather than
emitting an unsupported answer (Req 13.1). The offline **evaluation harness** needs neither live
Snowflake nor Cortex — it runs the real service layer against deterministic fakes (§7).

### Create and test the CoCo CLI connection (one-time)

```bash
snow connection add --connection-name governed-aml
snow connection test --connection governed-aml
```

The connection supplies account / user / role / warehouse. The SQL scripts stay database-name
agnostic via the `db` variable (default `GOVERNED_AML`).

---

## 2. Required configuration keys

The backend reads **all** Snowflake connection settings from the environment (prefix `SNOWFLAKE_`).
There are intentionally no secret-bearing defaults — a missing value aborts startup (Req 1.2).

| Env key | Setting | Required | Notes |
|---|---|:---:|---|
| `SNOWFLAKE_ACCOUNT` | account identifier | ✅ | e.g. `orgname-accountname` |
| `SNOWFLAKE_USER` | login user | ✅ | |
| `SNOWFLAKE_AUTHENTICATION` | auth secret | ✅ | password / key-pair passphrase / token; held as a secret, never logged |
| `SNOWFLAKE_ROLE` | role | ✅ | role assumed for all AI/semantic/persistence ops |
| `SNOWFLAKE_WAREHOUSE` | warehouse | ✅ | compute warehouse |
| `SNOWFLAKE_DATABASE` | database | ✅ | match the provisioned DB (default `GOVERNED_AML`) |
| `SNOWFLAKE_SCHEMA` | schema | ✅ | default working schema |

Optional, non-secret operational tuning (safe defaults; never required):

| Env key | Default | Purpose |
|---|---|---|
| `CORTEX_TIMEOUT_SECONDS` | `30.0` | Per-attempt Cortex call budget (Req 13.1) |
| `CORTEX_MAX_RETRIES` | `2` | Retries after the first attempt (total = 1 + retries) |
| `CORTEX_EMBEDDING_MODEL` | `snowflake-arctic-embed-m-v1.5` | 768-dim model matching `VEC.*` columns |
| `CORTEX_COMPLETE_MODEL` | `claude-sonnet-5-5` | Narrative generation model (use `claude-opus-5` for a max-quality showcase run) |
| `CORTEX_RETRIEVAL_TOP_K` | `5` | Default chunks per retrieval |
| `TRIAGE_CORRELATION_WINDOW_SECONDS` | `3600` | Alert correlation/dedup window (Req 4.5) |
| `TRIAGE_FRESHNESS_THRESHOLD_SECONDS` | `86400` | Case staleness threshold (Req 4.4) |

Provide these via your shell, a secrets manager, or a `backend/.env` file (git-ignored). **Never**
commit real secret values.

```bash
# Example (values illustrative — supply your own; do not commit):
export SNOWFLAKE_ACCOUNT="orgname-accountname"
export SNOWFLAKE_USER="demo_user"
export SNOWFLAKE_AUTHENTICATION="********"
export SNOWFLAKE_ROLE="AML_APP_ROLE"
export SNOWFLAKE_WAREHOUSE="COMPUTE_WH"
export SNOWFLAKE_DATABASE="GOVERNED_AML"
export SNOWFLAKE_SCHEMA="APP"
```

---

## 3. Provision + seed on a clean account (one command)

The wrapper `scripts/provision.sh` runs every Snowflake script **in the order the per-directory
READMEs document** and then generates + loads the synthetic dataset. It is **idempotent** and
parameterised; it embeds **no secrets** (connection comes from `-c`).

```bash
# Full provision + seed with defaults (connection governed-aml, DB GOVERNED_AML)
scripts/provision.sh

# Preview every command without executing (works even without `snow` installed)
scripts/provision.sh --dry-run

# Custom connection / database / ingest warehouse / seed
scripts/provision.sh -c my-conn -d DEMO_DB -w DEMO_WH --seed 42

# Provision objects only (no data), or load via the stream/task path (dedup+freshness)
scripts/provision.sh --skip-seed
scripts/provision.sh --load-stream
```

**What it runs, in order** (each step is a documented `snow sql -c <conn> -D db=<DB> -f ...` call):

1. `snowflake/ddl/00_databases_schemas.sql` → `01_raw.sql` → `02_vec.sql` → `03_app.sql` → `04_audit.sql`
2. `snowflake/semantic/10_metric_definition_registry.sql` → `20_entity_risk_metrics.sql`
3. `snowflake/streams/30_transaction_stream.sql` → `31_data_freshness.sql`
4. `snowflake/tasks/30_ingest_transaction_task.sql` (`-D ingest_warehouse=<WH>`)
5. `snowflake/policies/05_policies.sql`
6. `python3 snowflake/seed/generate_seed.py --out snowflake/seed/data --seed <N>`
7. seed load — **direct** (`COPY INTO RAW.*` from `snowflake/seed/data/load.sql`, default) or
   **stream** (`--load-stream`: land + `EXECUTE TASK RAW.INGEST_TRANSACTION_EVENTS`)

### Manual equivalent (CoCo CLI commands)

If you prefer to run the steps by hand (identical to the per-directory READMEs):

```bash
# 1) DDL
for f in 00_databases_schemas 01_raw 02_vec 03_app 04_audit; do
  snow sql -c governed-aml -D db=GOVERNED_AML -f "snowflake/ddl/${f}.sql" || break
done
# 2) Semantic layer
for f in 10_metric_definition_registry 20_entity_risk_metrics; do
  snow sql -c governed-aml -D db=GOVERNED_AML -f "snowflake/semantic/${f}.sql" || break
done
# 3) Streams + freshness + ingest task
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/streams/30_transaction_stream.sql
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/streams/31_data_freshness.sql
snow sql -c governed-aml -D db=GOVERNED_AML -D ingest_warehouse=COMPUTE_WH \
  -f snowflake/tasks/30_ingest_transaction_task.sql
# 4) Policies
snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/policies/05_policies.sql
# 5) Seed — generate, then load from the data directory
python3 snowflake/seed/generate_seed.py
( cd snowflake/seed/data && snow sql -c governed-aml -D db=GOVERNED_AML -f load.sql )
```

### Verify provisioning

```bash
# governed figures per entity, each stamped with its definition version
snow sql -c governed-aml -D db=GOVERNED_AML \
  -q 'SELECT * FROM SEM.ENTITY_RISK_METRIC_VALUES ORDER BY entity_id, metric_name;'

# data-freshness indicator (one row; no-data state when empty)
snow sql -c governed-aml -D db=GOVERNED_AML -q 'SELECT * FROM RAW.DATA_FRESHNESS;'

# audit table is append-only (expect only INSERT/SELECT grants)
snow sql -c governed-aml -D db=GOVERNED_AML -q 'SHOW GRANTS ON TABLE AUDIT.AUDIT_RECORD;'
```

---

## 4. Run the backend (FastAPI)

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt   # or: pip install -e ".[dev]"

# Ensure the SNOWFLAKE_* env keys from §2 are exported (or present in backend/.env)
uvicorn app.main:app --reload
```

Startup aborts and names every missing setting if the config is incomplete (Req 1.2); it never
echoes secret values. The API serves the Command Centre, investigation, SAR, approval, audit, and
KPI endpoints plus a WebSocket live feed.

---

## 5. Run the frontend (Next.js dashboard)

```bash
cd frontend
npm install
npm run dev        # dev server (default http://localhost:3000)
# production:
# npm run build && npm run start
```

Point the dashboard at the backend via its documented API base-URL env var (e.g.
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`). The dashboard provides the Command Centre
(alerts/cases + KPIs), investigation workspace, evidence/citation drawer, recommendation view,
human-approval queue, action-status, audit-trail, and system-health & AI-quality views.

---

## 6. Run the scripted demo

See **[docs/DEMO.md](./DEMO.md)** for the golden-path script (ingest → triage → investigate → SAR
draft → approval → audit → KPIs) and the ≥ 4 non-happy-path scenarios, each with exact steps,
expected UI state, expected metrics, and recovery/graceful-degradation behaviour (Req 14.2, 14.4).

---

## 7. Run the evaluation harness (no live Snowflake needed)

From the repository root — both the repo root and `backend/` must be on `PYTHONPATH`:

```bash
# text report (default)
PYTHONPATH="$PWD:$PWD/backend" python3 -m evaluation.harness

# machine-readable report
PYTHONPATH="$PWD:$PWD/backend" python3 -m evaluation.harness --format json

# fail the process (exit 1) if any executed metric is below its acceptable threshold
PYTHONPATH="$PWD:$PWD/backend" python3 -m evaluation.harness --strict
```

The harness measures groundedness, citation precision/coverage, unsupported-claim rate, refusal
correctness, metric-consistency, and prompt-injection resistance. It separates **demonstrated**
results from **intended** targets and omits unexecuted tests (Req 12.4).

---

## 8. Idempotency, re-runs, and teardown

- All provisioning scripts use `CREATE ... IF NOT EXISTS` / `CREATE OR REPLACE` and idempotent
  `MERGE`/`REVOKE`, so `scripts/provision.sh` is safe to re-run on an existing account.
- The seed generator is deterministic (fixed RNG seed), so the demo and its KPIs reproduce
  identically on every run.
- Audit records are retained unaltered for the demo lifetime (Req 8.5); a full teardown is out of
  scope for the MVP. To reset a demo account, drop the database manually
  (`DROP DATABASE IF EXISTS GOVERNED_AML;`) — this is destructive and removes audit history, so do
  it only on a disposable demo account.

---

## 9. Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| Backend aborts at startup listing settings | A `SNOWFLAKE_*` key is missing/empty (Req 1.2) | Export the named keys (§2); re-start |
| `tool 'snow' not found` | CoCo CLI not installed / not on `PATH` | Install the CoCo CLI; `--dry-run` previews without it |
| Seed `PUT` cannot find the CSVs | Loader run from the wrong directory | Run `load.sql` from `snowflake/seed/data/` (the wrapper does this) |
| Ingest task does no work | Stream empty / task on wrong warehouse | Land rows, then `EXECUTE TASK RAW.INGEST_TRANSACTION_EVENTS;`; pass `-w <WH>` |
| Answer flagged / refused | Groundedness below threshold or dual-grounding unmet | Expected safety behaviour (Req 5.5); see docs/DEMO.md non-happy paths |
