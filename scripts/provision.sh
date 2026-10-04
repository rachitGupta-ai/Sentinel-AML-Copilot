#!/usr/bin/env bash
# =============================================================================
# SentinelAML Copilot — one-shot provisioning + seed routine
# Spec: aml-regulatory-copilot | Task 21.1 | Requirements: 1.3, 14.1
# =============================================================================
# Creates every Snowflake object for the copilot ON A CLEAN ACCOUNT, in the
# order the per-directory READMEs document, then generates and loads the fully
# synthetic demo dataset (Req 2.1 — no real customer/account/transaction data).
#
# Ordering (matches snowflake/*/README.md):
#   ddl 00 -> 01 -> 02 -> 03 -> 04        (schemas, raw, vec, app, audit)
#   semantic 10 -> 20                     (metric registry, governed views)
#   streams 30 -> 31                      (landing+stream, freshness view)
#   tasks 30                              (suppression log + ingest task)
#   policies 05                           (masking + row-access policies)
#   generate seed                         (synthetic CSVs + load.sql)
#   load seed                             (COPY INTO RAW.*)
#
# Config-only credentials (Req 1.1): this script NEVER embeds secrets. The
# Snowflake connection (account/user/auth/role/warehouse) comes from your
# configured CoCo CLI connection selected with `-c <conn>`. Only non-secret
# object names (database, warehouse) are parameterised here.
#
# Idempotent: every underlying script uses CREATE ... IF NOT EXISTS / CREATE OR
# REPLACE and idempotent MERGE/REVOKE, so re-running on an existing account is
# safe (audit records are retained unaltered — Req 8.5).
#
# Usage:
#   scripts/provision.sh [-c CONN] [-d DB] [-w WAREHOUSE] [--seed N]
#                        [--skip-seed] [--load-direct|--load-stream] [--dry-run]
#
#   -c, --connection CONN   CoCo CLI connection name     (default: governed-aml)
#   -d, --database   DB      target database name          (default: GOVERNED_AML)
#   -w, --warehouse  WH      warehouse for the ingest task  (default: COMPUTE_WH)
#       --seed       N       RNG seed for the generator     (default: 1337)
#       --skip-seed          provision objects only; do not generate/load data
#       --load-direct        load seed with COPY INTO RAW.* (default; task 5.1 path)
#       --load-stream        land into RAW.TRANSACTION_LANDING + run ingest task
#                            (exercises dedup + freshness — task 5.2 path)
#       --dry-run            print the commands without executing them
#   -h, --help               show this help and exit
#
# Prerequisites: Snowflake CoCo CLI (`snow`) on PATH, Python 3.11 (`python3`),
# and a configured, tested connection (`snow connection test -c <conn>`).
# See docs/RUNBOOK.md for the full prerequisite + config-key reference.
# =============================================================================

set -euo pipefail

# --- defaults ----------------------------------------------------------------
CONN="governed-aml"
DB="GOVERNED_AML"
WAREHOUSE="COMPUTE_WH"
SEED="1337"
SKIP_SEED=false
LOAD_MODE="direct"   # direct | stream
DRY_RUN=false

# Repo root = parent of this script's directory, so the script works from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() { sed -n '2,55p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# --- arg parsing -------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--connection) CONN="$2"; shift 2 ;;
        -d|--database)   DB="$2"; shift 2 ;;
        -w|--warehouse)  WAREHOUSE="$2"; shift 2 ;;
        --seed)          SEED="$2"; shift 2 ;;
        --skip-seed)     SKIP_SEED=true; shift ;;
        --load-direct)   LOAD_MODE="direct"; shift ;;
        --load-stream)   LOAD_MODE="stream"; shift ;;
        --dry-run)       DRY_RUN=true; shift ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# --- helpers -----------------------------------------------------------------
log() { printf '\n==> %s\n' "$*"; }

# Run (or, in --dry-run, print) a command. Quoting-safe: args are passed through
# verbatim so values containing spaces are not re-split (avoids injection).
run() {
    if [[ "${DRY_RUN}" == true ]]; then
        printf '  [dry-run]'; printf ' %q' "$@"; printf '\n'
    else
        "$@"
    fi
}

# Execute a single Snowflake SQL script file through the CoCo CLI, pinning the
# database (and, where relevant, the ingest warehouse) via -D variables so the
# scripts stay database-name agnostic (consistent with the READMEs).
snow_sql() {
    local file="$1"; shift
    run snow sql -c "${CONN}" -D "db=${DB}" "$@" -f "${REPO_ROOT}/${file}"
}

require_tool() {
    if ! command -v "$1" >/dev/null 2>&1; then
        # In --dry-run we only print commands, so a missing tool is a warning,
        # not a hard failure — this lets you preview the plan on any machine.
        if [[ "${DRY_RUN}" == true ]]; then
            echo "WARNING: tool '$1' not found on PATH (ignored in --dry-run)." >&2
            return 0
        fi
        echo "ERROR: required tool '$1' not found on PATH." >&2
        echo "       See docs/RUNBOOK.md → Prerequisites." >&2
        exit 1
    fi
}

# --- preflight ---------------------------------------------------------------
log "SentinelAML Copilot provisioning"
echo "    connection : ${CONN}"
echo "    database   : ${DB}"
echo "    warehouse  : ${WAREHOUSE} (ingest task)"
echo "    seed       : ${SEED}"
echo "    load mode  : ${LOAD_MODE}"
echo "    skip seed  : ${SKIP_SEED}"
echo "    dry run    : ${DRY_RUN}"

require_tool snow
if [[ "${SKIP_SEED}" == false ]]; then
    require_tool python3
fi

# =============================================================================
# 1) DDL — schemas, raw, vec, app, audit (snowflake/ddl/README.md)
# =============================================================================
log "Provisioning base DDL (schemas -> raw -> vec -> app -> audit)"
for f in 00_databases_schemas 01_raw 02_vec 03_app 04_audit; do
    snow_sql "snowflake/ddl/${f}.sql"
done

# =============================================================================
# 2) Semantic layer — metric registry then governed views (semantic/README.md)
# =============================================================================
log "Provisioning governed semantic layer (registry -> views)"
for f in 10_metric_definition_registry 20_entity_risk_metrics; do
    snow_sql "snowflake/semantic/${f}.sql"
done

# =============================================================================
# 3) Streams + tasks — landing/stream, freshness, ingest task (streams/README.md)
# =============================================================================
log "Provisioning ingestion streams, freshness view, and ingest task"
snow_sql "snowflake/streams/30_transaction_stream.sql"
snow_sql "snowflake/streams/31_data_freshness.sql"
# The ingest task binds its warehouse via $ingest_warehouse (default COMPUTE_WH).
snow_sql "snowflake/tasks/30_ingest_transaction_task.sql" -D "ingest_warehouse=${WAREHOUSE}"

# =============================================================================
# 4) Policies — masking + row-access (policies/README.md)
# =============================================================================
log "Provisioning masking + row-access policies"
snow_sql "snowflake/policies/05_policies.sql"

# =============================================================================
# 5) Seed — generate synthetic dataset, then load (seed/README.md)
# =============================================================================
if [[ "${SKIP_SEED}" == true ]]; then
    log "Skipping seed generation/load (--skip-seed)"
    log "Provisioning complete (objects only)."
    exit 0
fi

SEED_DATA_DIR="${REPO_ROOT}/snowflake/seed/data"

log "Generating synthetic seed dataset (deterministic, seed=${SEED})"
run python3 "${REPO_ROOT}/snowflake/seed/generate_seed.py" --out "${SEED_DATA_DIR}" --seed "${SEED}"

if [[ "${LOAD_MODE}" == "direct" ]]; then
    # load.sql uses relative PUT 'file://entity.csv' paths, so it must run from
    # the directory holding the generated CSVs (per snowflake/seed/README.md).
    log "Loading seed into RAW.* via COPY INTO (direct load)"
    if [[ "${DRY_RUN}" == true ]]; then
        printf '  [dry-run] (cd %q &&' "${SEED_DATA_DIR}"
        printf ' %q' snow sql -c "${CONN}" -D "db=${DB}" -f "${SEED_DATA_DIR}/load.sql"
        printf ')\n'
    else
        ( cd "${SEED_DATA_DIR}" && snow sql -c "${CONN}" -D "db=${DB}" -f "${SEED_DATA_DIR}/load.sql" )
    fi
else
    # Stream path: exercises dedup + freshness exactly as a live feed would.
    # The direct loader is still used to stage data, then the ingest task runs
    # once to consume the stream and MERGE deduplicated keepers.
    log "Loading seed, then running the ingest task once (stream load)"
    if [[ "${DRY_RUN}" == true ]]; then
        printf '  [dry-run] (cd %q &&' "${SEED_DATA_DIR}"
        printf ' %q' snow sql -c "${CONN}" -D "db=${DB}" -f "${SEED_DATA_DIR}/load.sql"
        printf ')\n'
        printf '  [dry-run]'; printf ' %q' snow sql -c "${CONN}" -D "db=${DB}" -q "EXECUTE TASK ${DB}.RAW.INGEST_TRANSACTION_EVENTS;"; printf '\n'
    else
        ( cd "${SEED_DATA_DIR}" && snow sql -c "${CONN}" -D "db=${DB}" -f "${SEED_DATA_DIR}/load.sql" )
        run snow sql -c "${CONN}" -D "db=${DB}" -q "EXECUTE TASK ${DB}.RAW.INGEST_TRANSACTION_EVENTS;"
    fi
fi

log "Provisioning + seed complete."
echo "    Verify: snow sql -c ${CONN} -D db=${DB} -q 'SELECT * FROM SEM.ENTITY_RISK_METRIC_VALUES ORDER BY entity_id, metric_name;'"
echo "    Next:   see docs/RUNBOOK.md to start the backend/UI and docs/DEMO.md for the scripted demo."
