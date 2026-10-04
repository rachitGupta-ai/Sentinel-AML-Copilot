# SentinelAML Copilot — Snowflake Connection & Integration Guide

Spec: `aml-regulatory-copilot` | Companion to [RUNBOOK.md](./RUNBOOK.md) and [DEMO.md](./DEMO.md)

This guide takes you from a **fresh Snowflake account** to a **working app where every feature
is wired to Snowflake**. It covers the portal (Snowsight) setup, the CoCo CLI connection, object
provisioning, backend wiring, and a feature-by-feature verification checklist.

> **Config-only credentials (Req 1.1).** No secret is embedded in source, scripts, prompts, or logs.
> Both the CLI and the backend read the connection from configuration / environment only.
> **All data is fully synthetic (Req 2.1).**

---

## ⚠️ Account type matters — Cortex is blocked on standard trials

The app's AI layer depends on **Cortex AI functions** (`COMPLETE`, `EMBED_TEXT_*`,
`VECTOR_COSINE_SIMILARITY`). Snowflake **disables these on standard self-service trial accounts**
(e.g. accounts from `trial.snowflake.com` / the "$400 credits" trial) until a credit card is added.
On such an account the gate query returns:

> `AI function COMPLETE is not available for trial accounts.`

**Use the dedicated Cortex / CoCo trial account instead**, which has AI functions enabled:

- Signup: **https://signup.snowflake.com/cortex-code/** (recommended region **AWS / US West (Oregon)**
  for best Cortex model availability). This trial includes a smaller inference budget (~$40) — plenty
  for building + demo runs; don't leave large batch jobs running.
- Reference: [Snowflake trial options](https://www.snowflake.com/en/snowflake-trial/),
  [trial account AI limitation](https://docs.snowflake.com/en/user-guide/admin-trial-account),
  [CoCo CLI trial note](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli).

### Two different CLIs — don't confuse them

| CLI | Executable | Package / install | Used by this repo? |
|---|---|---|---|
| **Snowflake CLI** | `snow` | `pipx install snowflake-cli` | ✅ Yes — `scripts/provision.sh` calls `snow sql ...` |
| **CoCo CLI** (Cortex Code — AI coding agent) | `cortex` | `curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh \| sh` | ✖ Not required for provisioning; it's the NL coding agent |

The provisioning path in this guide uses **`snow`**. The `cortex` agent is optional and separate.

> **Checkpoint:** installing a CLI does **not** change an account's Cortex entitlement — that is a
> property of the *account*. Always run the Step 1c gate query on the account the backend/`snow`
> connection will actually target.

---

## Mental model — how the app connects to Snowflake

The app does **not** use any marketplace integration or external connector. It connects **two ways**,
both with the same account credentials:

1. **CoCo CLI (`snow`)** — desktop CLI that runs the SQL in `snowflake/` to **create** all objects
   (databases, tables, semantic views, streams, tasks, policies). One-time provisioning.
2. **FastAPI backend** — connects at runtime via the Snowflake Python connector, reading the seven
   `SNOWFLAKE_*` env vars (`account, user, authentication, role, warehouse, database, schema`; see
   `backend/app/config/settings.py`). It runs queries and calls **Cortex** functions
   (`COMPLETE`, `EMBED_TEXT_*`, `VECTOR_COSINE_SIMILARITY`) directly in SQL — **Cortex needs no
   separate API key**; it is in-account SQL.

```
                    ┌──────────────────────────┐
  CoCo CLI (snow) ──┤  Snowflake account        │
   provisions SQL   │   • GOVERNED_AML database  │
                    │   • SEM / RAW / VEC / APP / │
  FastAPI backend ──┤     AUDIT schemas           │
   runtime queries  │   • Cortex (COMPLETE/EMBED) │
   + Cortex calls   └──────────────────────────┘
```

---

## Step 1 — Snowsight portal setup (run as ACCOUNTADMIN)

> **✅ Completed on this account (Cortex trial `VNB57096`, 2026-10-04):**
> - **1a/1b** — `COMPUTE_WH` warehouse + `AML_APP_ROLE` created and granted (user `RACHITGUPTA`).
> - **1c** — Cortex verified: `claude-sonnet-5-5` returns a word; `snowflake-arctic-embed-m-v1.5`
>   returns a 768-dim float vector. These are now the configured defaults (see Step 4 / `settings.py`).
>
> The steps below are kept for reproducibility on a clean account.

Step 1 is **SQL**, run inside Snowsight — not a terminal. Log into `https://app.snowflake.com`, open
a worksheet via **Workspaces → "SQL file"** (the Welcome panel) or **Projects → Worksheets →
+ Worksheet**, and confirm the role selector (top-left of the worksheet) shows **ACCOUNTADMIN**.

> **Run order matters.** The default ▶ Run button may execute only the statement under the cursor,
> which makes a later `GRANT ... TO ROLE AML_APP_ROLE` fail with *"Role 'AML_APP_ROLE' does not
> exist"* because `CREATE ROLE` had not run yet. Select all (Cmd+A) and use the **dropdown next to
> ▶ Run → "Run All"**, or run 1a and 1b as two separate executions so the role is created before it
> is granted to.

### 1a. Create the role first (so later grants can reference it)

```sql
USE ROLE ACCOUNTADMIN;
CREATE ROLE IF NOT EXISTS AML_APP_ROLE;
```

### 1b. Create the warehouse and grant to the role

```sql
USE ROLE ACCOUNTADMIN;

CREATE WAREHOUSE IF NOT EXISTS COMPUTE_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

GRANT ROLE AML_APP_ROLE TO USER RACHITGUPTA;             -- your login user
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE AML_APP_ROLE;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE AML_APP_ROLE;    -- lets this role provision the app DB
```

XSMALL is plenty for the demo and cheapest; `AUTO_SUSPEND = 60` sleeps the warehouse after 60s idle so
you don't burn credits.

> For the **first** provisioning run you may simply use `ACCOUNTADMIN` and skip the grants, but a
> dedicated role is cleaner and matches the spec's RBAC story (Req 9.2).

### 1c. Verify Cortex is available — **the most important check**

The entire AI layer depends on Cortex. Run:

```sql
-- Narrative generation model used by the backend (CortexSettings.complete_model).
-- Verified working on the Cortex trial account (returns a word).
SELECT SNOWFLAKE.CORTEX.COMPLETE('claude-sonnet-5-5', 'Say hello in one word.');

-- Embedding model (CortexSettings.embedding_model), 768-dim to match VEC.* columns.
-- NOTE: inspect the RETURNED VECTOR, not ARRAY_SIZE() — ARRAY_SIZE on a VECTOR type
-- returns NULL (a casting quirk), which does NOT mean the model failed. A row of
-- floats like [0.0269, -0.0113, ...] means it works.
SELECT SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m-v1.5', 'test') AS v;
```

- A word from `COMPLETE` + a float-vector from `EMBED_TEXT_768` → Cortex works. Proceed.
- `mistral-large2` / `e5-base-v2` are **legacy** — do not use them (COMPLETE on `mistral-large2`
  errors with *"model ... has been in legacy state"*).
- To see every model available to your account + lifecycle status: `SHOW CORTEX BASE MODELS;`
- Error about function/model not existing → Cortex or that model is **not enabled in your account's
  region**. Cortex is region-limited; see
  [Cortex LLM function availability](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions#availability).
  Pick an available model/region, then override `CORTEX_COMPLETE_MODEL` / `CORTEX_EMBEDDING_MODEL`
  (see Step 4). Keep the embedding model **768-dim** to match `snowflake/ddl/02_vec.sql`.

### 1d. Find your account identifier

```sql
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME();
```

The resulting `orgname-accountname` string is your `SNOWFLAKE_ACCOUNT`.

---

## Step 2 — Install & connect the CoCo CLI

> **Package name:** the current package is **`snowflake-cli`** (the old `snowflake-cli-labs` is
> deprecated and installs no `snow` app via pipx). The executable is `snow`.
>
> **On macOS with Homebrew Python, `pip` is not on PATH** — use `pip3`, `python3 -m pip`, or `pipx`.

```bash
# Recommended: pipx isolates the CLI and puts `snow` on PATH
pipx install snowflake-cli
#   if `snow` is still not found afterwards:
#   pipx ensurepath   # then open a new terminal tab

# Alternatives (pick one):
#   pip3 install snowflake-cli
#   python3.11 -m pip install snowflake-cli

snow --version      # verify, e.g. "Snowflake CLI version: 3.28.0"
```

### Add the connection (this account: `ULMSSTP-VNB57096`)

Non-secret values can be passed as flags; the CLI then prompts for the **password** (leave other
optional prompts blank and press Enter):

```bash
snow connection add \
  --connection-name governed-aml \
  --account ULMSSTP-VNB57096 \
  --user RACHITGUPTA \
  --role AML_APP_ROLE \
  --warehouse COMPUTE_WH \
  --database GOVERNED_AML \
  --schema APP
```

> If a flag name differs on your CLI version, run it bare — `snow connection add --connection-name
> governed-aml` — and answer the prompts with the values above.
>
> `GOVERNED_AML` does not exist yet; `scripts/provision.sh` creates it. A "database not found" warning
> at this point is fine — what matters is that account / user / role / warehouse resolve.

```bash
snow connection test --connection governed-aml   # MUST succeed before continuing
```

---

## Step 3 — Provision all objects + load synthetic data

```bash
cd /Users/rachit.gupta/IdeaProjects/streamcontract-snowflake
scripts/provision.sh --dry-run     # preview every command (runs nothing)
scripts/provision.sh               # create all objects + seed data (idempotent, seed 1337)
```

This single script creates every object the features need, in order (per RUNBOOK §3): RAW/VEC/APP/AUDIT
schemas, the `SEM.ENTITY_RISK_METRICS` semantic view + metric-definition registry, the transaction
stream + ingest task, masking/row-access policies, then generates and loads the synthetic dataset.

**Verify provisioning:**

```bash
snow sql -c governed-aml -D db=GOVERNED_AML \
  -q 'SELECT * FROM SEM.ENTITY_RISK_METRIC_VALUES ORDER BY entity_id, metric_name;'   # v1-stamped figures
snow sql -c governed-aml -D db=GOVERNED_AML -q 'SELECT * FROM RAW.DATA_FRESHNESS;'      # one row
snow sql -c governed-aml -D db=GOVERNED_AML -q 'SHOW GRANTS ON TABLE AUDIT.AUDIT_RECORD;'  # INSERT/SELECT only
```

---

## Step 4 — Point the backend at Snowflake and run it

Create `backend/.env` (git-ignored). The seven keys below are **required** — a missing one aborts
startup and names exactly which (the Req 1.2 guard in `settings.py`).

```bash
# Required (credential guard checks all seven)
SNOWFLAKE_ACCOUNT=ULMSSTP-VNB57096
SNOWFLAKE_USER=RACHITGUPTA
SNOWFLAKE_AUTHENTICATION=your_password            # the only secret — never commit
SNOWFLAKE_ROLE=AML_APP_ROLE
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=GOVERNED_AML
SNOWFLAKE_SCHEMA=APP

# Optional Cortex tuning (safe defaults; override only if Step 1c required a different model/region)
# CORTEX_COMPLETE_MODEL=claude-sonnet-5-5     # default; use claude-opus-5 for a max-quality showcase run
# CORTEX_EMBEDDING_MODEL=snowflake-arctic-embed-m-v1.5   # 768-dim
# CORTEX_TIMEOUT_SECONDS=30.0
# CORTEX_MAX_RETRIES=2
# CORTEX_RETRIEVAL_TOP_K=5

# Optional triage tuning
# TRIAGE_CORRELATION_WINDOW_SECONDS=3600
# TRIAGE_FRESHNESS_THRESHOLD_SECONDS=86400
```

Run the backend:

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload
```

Clean startup = backend connected. A startup abort that names a setting = that env key is missing/empty.

Run the dashboard (optional, for the full UI):

```bash
cd frontend
npm install
npm run dev      # http://localhost:3000  (set NEXT_PUBLIC_API_BASE_URL=http://localhost:8000)
```

---

## Step 5 — Feature → Snowflake service → how to verify

| Feature | Snowflake service used | Verify it works |
|---|---|---|
| **Governed metrics** (deterministic figures) | **Semantic view** `SEM.ENTITY_RISK_METRICS` + registry table | Query `SEM.ENTITY_RISK_METRIC_VALUES` — values stamped `v1` |
| **Ingestion + dedup + freshness** | **Streams + Tasks** on `RAW.TRANSACTION_EVENT` | `scripts/provision.sh --load-stream`; check `RAW.TRANSACTION_SUPPRESSION_LOG` + `RAW.DATA_FRESHNESS` |
| **NL investigation / narrative** | **Cortex `COMPLETE`** | Ask a question in the dashboard; narrative appears, marked distinct from figures |
| **Evidence retrieval (RAG)** | **Cortex `EMBED_TEXT_768` + `VECTOR_COSINE_SIMILARITY`** over `VEC.*` | Investigation returns cited policy passages |
| **Dual-grounding gate** | app logic over governed figures + Cortex | Below-0.7 answers are flagged/refused (DEMO Scenario C) |
| **Masking / RBAC** | **Masking + row-access policies** (`snowflake/policies/`) | Query a masked field as a non-entitled role → masked |
| **Immutable audit** | **Append-only** `AUDIT.AUDIT_RECORD` (INSERT/SELECT grants only) | `SHOW GRANTS ON TABLE AUDIT.AUDIT_RECORD;` → no UPDATE/DELETE |
| **Approval / HITL + idempotency** | `APP.CASE/SAR_DRAFT/APPROVAL` tables | Approve a case twice → acts once |

None of these need a Snowflake connector or marketplace integration — all native SQL objects + Cortex,
driven by the two connections (CLI + backend).

---

## Step 6 — Evaluation harness (no live Snowflake needed)

Proves AI quality against ground truth using the real service layer over deterministic fakes:

```bash
PYTHONPATH="$PWD:$PWD/backend" python3 -m evaluation.harness            # text report
PYTHONPATH="$PWD:$PWD/backend" python3 -m evaluation.harness --strict   # exit 1 if a metric fails
```

---

## Order of operations (do this first)

1. **Step 1c (Cortex check) before anything else** — the whole AI layer depends on it. If the two
   test queries work, the rest is mechanical.
2. `snow connection test` must pass (Step 2).
3. `scripts/provision.sh --dry-run`, then the real run (Step 3).
4. Backend `.env` + `uvicorn` (Step 4).
5. Verify each feature (Step 5), then record the demo (see [DEMO.md](./DEMO.md)).

---

## Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| `COMPLETE` errors *"model ... in legacy state"* | Using a retired model (e.g. `mistral-large2`) | Use `claude-sonnet-5-5` (default); `SHOW CORTEX BASE MODELS;` lists live ones |
| `COMPLETE` errors *"not available for trial accounts"* | Standard $400 trial blocks AI functions | Use the Cortex/CoCo trial (`signup.snowflake.com/cortex-code/`) — see top of this doc |
| `ARRAY_SIZE(EMBED_TEXT_768(...))` returns `NULL` | `ARRAY_SIZE` on a `VECTOR` type is a casting quirk | Not a failure — select the vector itself; a row of floats means it works |
| Backend aborts naming a setting | A `SNOWFLAKE_*` key missing/empty (Req 1.2) | Add it to `backend/.env`; restart |
| `snow: command not found` | Snowflake CLI not installed / not on PATH | `pipx install snowflake-cli` (not `snowflake-cli-labs`); `--dry-run` previews without it |
| `pip: command not found` | Homebrew Python puts no `pip` on PATH | Use `pip3`, `python3 -m pip`, or `pipx` |
| `pipx: No apps associated with package` | Installed the deprecated `snowflake-cli-labs` | Install `snowflake-cli` instead (ships the `snow` app) |
| Role 'AML_APP_ROLE' does not exist (Step 1b) | Snowsight ran only the statement under the cursor | Use **Run All**, or run 1a (CREATE ROLE) before 1b (grants) |
| `snow connection test` fails | Wrong account id / role / network | Re-check the account id (Snowsight: `SELECT CURRENT_ORGANIZATION_NAME()\|\|'-'\|\|CURRENT_ACCOUNT_NAME();`); confirm the role is granted to your user |
| Ingest task does no work | Stream empty / task on wrong warehouse | Land rows, then `EXECUTE TASK RAW.INGEST_TRANSACTION_EVENTS;`; pass `-w <WH>` |
| Embedding dim mismatch | Embedding model ≠ 768-dim | Use a 768-dim `EMBED_TEXT_768` model to match `VEC.*` columns |

---

## Appendix — Local environment pre-flight (verified)

Results of a local pre-flight on this machine (macOS, 2026-10-04). All checks were **offline** —
no live Snowflake account was touched. Use this to get the machine ready before Step 1.

### ✅ Verified working (offline)

| Check | Result |
|---|---|
| `scripts/provision.sh --dry-run` | Exit 0; prints the full ordered provisioning sequence (DDL → semantic → streams/freshness/ingest task → policies → seed generate → seed load) |
| Synthetic seed generator | `python3 snowflake/seed/generate_seed.py --out <dir> --seed 1337` produced 3 entities, 43 transaction events, 2 alerts, 3 policy docs, plus `load.sql` + `manifest.json`; injected defects: `missing_field`, `duplicate_event`, `stale_late_record` |
| Config credential guard (Req 1.2) | `validate_config()` with empty env returns all seven: `['account','user','authentication','role','warehouse','database','schema']` |
| Backend libs | `fastapi` + `pydantic_settings` import OK in the existing `.venv` |
| Frontend tooling | Node present, npm present |

### ✅ Blockers resolved (previously open)

All three local blockers from the first pre-flight are now fixed:

1. **`snow` CLI installed** — `pipx install snowflake-cli` (the current package; the old
   `snowflake-cli-labs` is deprecated). `snow --version` → `3.28.0`.
2. **Python 3.11** — `brew install python@3.11`; `backend/.venv` rebuilt on 3.11.17, and
   `import snowflake.connector` succeeds (connector `3.11.0`). On macOS use `pip3` / `python3 -m pip`
   (plain `pip` is not on PATH).
3. **Cortex account** — standard `$400` trial blocks AI functions; switched to the Cortex/CoCo trial
   (`signup.snowflake.com/cortex-code/`), account **`VNB57096`**. Live models verified
   (see Step 1 banner): `claude-sonnet-5-5` (COMPLETE) + `snowflake-arctic-embed-m-v1.5` (768-dim).

### Progress checklist

- [x] `pip3 install snowflake-cli` → `snow` on PATH (v3.28.0)
- [x] `brew install python@3.11` + rebuild `backend/.venv` → connector imports OK
- [x] Cortex trial account `VNB57096` (`ULMSSTP-VNB57096`) created
- [x] Step 1a/1b — `COMPUTE_WH` + `AML_APP_ROLE` created and granted
- [x] `GRANT CREATE ROLE ON ACCOUNT TO ROLE AML_APP_ROLE` (needed by DDL `CREATE ROLE`)
- [x] Step 1c — Cortex models verified; defaults wired into `settings.py` + docs
- [x] Zscaler corporate TLS fix (see below) — `snow connection test` reaches the account
- [x] `snow connection add/test --connection governed-aml` → connects (user `RACHITGUPTA`)
- [x] `scripts/provision.sh` → all objects created + synthetic seed loaded
- [x] Governed semantic view verified: `ENT-SMURF-001` structuring_score ≈ **0.905** (v1);
      normal entities 0.0 — matches `docs/DEMO.md`
- [x] `backend/.env` + `uvicorn app.main:app` → **`/health` reports `status: healthy`,
      `services_wired: true`** (live Snowpark session + probe query OK)
- [ ] Feature verification (Step 5) → record demo

### Fixes applied during the live run (CLI 3.28 compatibility)

The SQL scripts were authored for SnowSQL-style session variables; CLI 3.28's `snow sql`
does client-side templating and splits on `;`. Five fixes were needed (all committed):

1. **`SET db = COALESCE($db, 'GOVERNED_AML')` → `SET db = 'GOVERNED_AML'`** across all 8 SQL
   files — `$db` was never defined as a session variable, so `COALESCE($db, …)` errored
   (`Session variable '$DB' does not exist`).
2. **`SET ingest_warehouse = COALESCE($ingest_warehouse, …)` → direct default** (same cause).
3. **Ingest task body wrapped in `EXECUTE IMMEDIATE $$ … $$`** — the procedural `BEGIN…END`
   block has internal `;`, which the connector's statement splitter broke (`unexpected '<EOF>'`).
   Dollar-quoting makes the block atomic.
4. **Row-access policy signature → `(attributes VARIANT)` with the `COALESCE` moved into the
   body** — `ADD ROW ACCESS POLICY … ON (…)` accepts only column names, not expressions.
5. **`setuptools<81` installed into `backend/.venv`** — Snowpark 1.19.0 imports `pkg_resources`,
   absent by default on Python 3.11's modern setuptools (`ModuleNotFoundError: pkg_resources`).

### Zscaler corporate TLS fix (certificate verify failed)

This machine is behind **Zscaler**, which re-signs HTTPS with its own root CA. Python/`snow`
don't trust it by default → `250003: certificate verify failed`. Permanent fix applied: the
Zscaler root (exported from the macOS System keychain) was appended to the **certifi** CA bundle
used by both `snow`'s Python 3.11 and `backend/.venv`:

```bash
# export Zscaler root from the keychain
security find-certificate -a -c "Zscaler" -p /Library/Keychains/System.keychain > zscaler-root.pem
# append it to each environment's certifi bundle (dedup one block)
CERTIFI=$(python3 -c "import certifi; print(certifi.where())")
cat zscaler-root.pem >> "$CERTIFI"
```

After this, `snow connection test` and the backend connect with no SSL env vars.
