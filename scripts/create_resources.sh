#!/usr/bin/env bash
# =============================================================================
# SentinelAML Copilot — create resources (bring up all Snowflake stages)
# =============================================================================
# Provisions EVERY Snowflake object for the copilot on a clean (or existing)
# account, in the correct dependency order for all pipeline stages, then
# generates + loads the fully synthetic demo dataset. By default it SUSPENDS the
# warehouse and ingest task again at the end so bringing resources up does NOT
# leave Snowflake burning credits (opt out with --leave-running).
#
# Stage order (each stage depends on the previous one):
#   1. DDL        00 schemas -> 01 raw -> 02 vec -> 03 app -> 04 audit
#   2. Semantic   10 metric_definition_registry -> 20 entity_risk_metrics (views)
#   3. Streams    30 transaction_stream (+ landing) -> 31 data_freshness view
#   4. Tasks      30 ingest_transaction_task (suppression log + ingest task)
#   5. Policies   05 masking + row-access policies
#   6. Seed       generate synthetic CSVs -> load into RAW.*
#
# This is a thin, safe wrapper around scripts/provision.sh (the single source of
# truth for the exact file order). It adds:
#   - a preflight connection test,
#   - credit-safe post-provision suspend (default),
#   - a verification query over the governed metric values.
#
# Config-only credentials (Req 1.1): no secrets here; the Snowflake connection
# comes from your configured CoCo CLI connection selected with `-c <conn>`.
#
# Idempotent: underlying scripts use CREATE ... IF NOT EXISTS / CREATE OR REPLACE
# and idempotent MERGE/REVOKE, so re-running is safe (audit records retained).
#
# Usage:
#   scripts/create_resources.sh [-c CONN] [-d DB] [-w WAREHOUSE] [--seed N]
#                               [--skip-seed] [--load-stream]
#                               [--leave-running] [--dry-run]
#
#   -c, --connection CONN   CoCo CLI connection name   (default: governed-aml)
#   -d, --database   DB      target database name        (default: GOVERNED_AML)
#   -w, --warehouse  WH      warehouse for the ingest task (default: COMPUTE_WH)
#       --seed       N       RNG seed for the generator   (default: 1337)
#       --skip-seed          provision objects only; do not generate/load data
#       --load-stream        land + run ingest task once (exercises dedup/
#                            freshness) instead of the default direct COPY INTO
#       --leave-running      do NOT suspend warehouse/task at the end
#                            (default is to suspend them to save credits)
#       --dry-run            print the plan without executing anything
#   -h, --help               show this help and exit
#
# Prerequisites: Snowflake CoCo CLI (`snow`) + Python 3.11 (`python3`) on PATH,
# and a tested connection (`snow connection test -c <conn>`). See docs/RUNBOOK.md.
# =============================================================================
set -euo pipefail

CONN="governed-aml"
DB="GOVERNED_AML"
WAREHOUSE="COMPUTE_WH"
SEED="1337"
SKIP_SEED=false
LOAD_STREAM=false
LEAVE_RUNNING=false
DRY_RUN=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() { sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--connection)  CONN="$2"; shift 2 ;;
        -d|--database)    DB="$2"; shift 2 ;;
        -w|--warehouse)   WAREHOUSE="$2"; shift 2 ;;
        --seed)           SEED="$2"; shift 2 ;;
        --skip-seed)      SKIP_SEED=true; shift ;;
        --load-stream)    LOAD_STREAM=true; shift ;;
        --leave-running)  LEAVE_RUNNING=true; shift ;;
        --dry-run)        DRY_RUN=true; shift ;;
        -h|--help)        usage; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

log() { printf '\n==> %s\n' "$*"; }
run() {
    if [[ "${DRY_RUN}" == true ]]; then
        printf '  [dry-run]'; printf ' %q' "$@"; printf '\n'
    else
        "$@"
    fi
}

require_tool() {
    if ! command -v "$1" >/dev/null 2>&1; then
        if [[ "${DRY_RUN}" == true ]]; then
            echo "WARNING: tool '$1' not found on PATH (ignored in --dry-run)." >&2; return 0
        fi
        echo "ERROR: required tool '$1' not found on PATH. See docs/RUNBOOK.md." >&2
        exit 1
    fi
}

log "SentinelAML Copilot — create resources (all stages)"
echo "    connection    : ${CONN}"
echo "    database      : ${DB}"
echo "    warehouse     : ${WAREHOUSE}"
echo "    seed          : ${SEED}"
echo "    skip seed     : ${SKIP_SEED}"
echo "    load mode     : $([[ "${LOAD_STREAM}" == true ]] && echo stream || echo direct)"
echo "    leave running : ${LEAVE_RUNNING}"
echo "    dry run       : ${DRY_RUN}"

require_tool snow
[[ "${SKIP_SEED}" == false ]] && require_tool python3

# --- preflight: test the connection -----------------------------------------
log "Testing Snowflake connection"
if [[ "${DRY_RUN}" == true ]]; then
    echo "  [dry-run] snow connection test -c ${CONN}"
else
    if ! snow connection test -c "${CONN}" >/dev/null 2>&1; then
        echo "ERROR: connection '${CONN}' failed. Configure/test it first:" >&2
        echo "       snow connection test -c ${CONN}   (see docs/SNOWFLAKE_SETUP.md)" >&2
        exit 1
    fi
    echo "  connection OK"
fi

# --- provision all stages via the canonical provision.sh ---------------------
PROVISION_ARGS=(-c "${CONN}" -d "${DB}" -w "${WAREHOUSE}" --seed "${SEED}")
[[ "${SKIP_SEED}" == true ]]   && PROVISION_ARGS+=(--skip-seed)
[[ "${LOAD_STREAM}" == true ]] && PROVISION_ARGS+=(--load-stream) || PROVISION_ARGS+=(--load-direct)
[[ "${DRY_RUN}" == true ]]     && PROVISION_ARGS+=(--dry-run)

log "Provisioning all stages (DDL -> semantic -> streams -> tasks -> policies -> seed)"
run bash "${SCRIPT_DIR}/provision.sh" "${PROVISION_ARGS[@]}"

# --- credit-safe wind-down (default) -----------------------------------------
# Provisioning / COPY INTO / the one-shot ingest task auto-resume the warehouse.
# Unless the caller asked to leave it running, suspend compute again so the
# account returns to 0-credit idle immediately after bring-up.
if [[ "${LEAVE_RUNNING}" == false ]]; then
    log "Suspending warehouse + ingest task (credit-safe idle). Use --leave-running to keep them on."
    if [[ "${DRY_RUN}" == true ]]; then
        echo "  [dry-run] snow sql -c ${CONN} -q 'ALTER TASK IF EXISTS ${DB}.RAW.INGEST_TRANSACTION_EVENTS SUSPEND;'"
        echo "  [dry-run] snow sql -c ${CONN} -q 'ALTER WAREHOUSE IF EXISTS ${WAREHOUSE} SUSPEND;'"
    else
        snow sql -c "${CONN}" -q "ALTER TASK IF EXISTS ${DB}.RAW.INGEST_TRANSACTION_EVENTS SUSPEND;" >/dev/null 2>&1 \
            && echo "  task suspended" || echo "  note: ingest task already suspended / absent"
        snow sql -c "${CONN}" -q "ALTER WAREHOUSE IF EXISTS ${WAREHOUSE} SUSPEND;" >/dev/null 2>&1 \
            && echo "  warehouse suspended" || echo "  note: warehouse already suspended"
    fi
else
    log "Leaving warehouse + ingest task running (--leave-running). Remember: this uses credits."
fi

# --- verification ------------------------------------------------------------
if [[ "${SKIP_SEED}" == false && "${DRY_RUN}" == false ]]; then
    log "Verifying governed metric values (will briefly resume the warehouse)"
    snow sql -c "${CONN}" -D "db=${DB}" \
        -q "SELECT entity_id, metric_name, metric_value, metric_definition_version
            FROM SEM.ENTITY_RISK_METRIC_VALUES
            ORDER BY entity_id, metric_name;" 2>/dev/null || \
        echo "  note: verification query skipped (view name/path may differ — see snowflake/semantic/README.md)"
    if [[ "${LEAVE_RUNNING}" == false ]]; then
        snow sql -c "${CONN}" -q "ALTER WAREHOUSE IF EXISTS ${WAREHOUSE} SUSPEND;" >/dev/null 2>&1 || true
        echo "  warehouse re-suspended after verification"
    fi
fi

log "Create-resources complete."
echo "    All stages provisioned$([[ "${SKIP_SEED}" == false ]] && echo " and seeded")."
echo "    Snowflake is $([[ "${LEAVE_RUNNING}" == true ]] && echo "RUNNING (using credits)" || echo "idle (0 credits) until the next query")."
echo "    Start backend/UI: see docs/RUNBOOK.md · Scripted demo: docs/DEMO.md"
echo "    Shut everything down again: scripts/shutdown.sh"
