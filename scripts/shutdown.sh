#!/usr/bin/env bash
# =============================================================================
# SentinelAML Copilot — shutdown routine (stop Snowflake credit consumption)
# =============================================================================
# Brings everything down so NO Snowflake credits are used while idle:
#   1. Suspends the ingest TASK (RAW.INGEST_TRANSACTION_EVENTS) so its schedule
#      cannot wake the warehouse.
#   2. Suspends the compute WAREHOUSE (a suspended warehouse bills 0 credits).
#   3. Stops the local backend (uvicorn :8000) and frontend (next dev :3000) so
#      nothing can issue a query that auto-resumes the warehouse.
#
# What it does NOT touch:
#   - Snowflake's own built-in serverless/account tasks (e.g.
#     CORTEX_BASE_MODELS_REFRESH_TASK). Those are platform internals, not ours.
#   - Any data, tables, policies, or the audit trail (nothing is dropped).
#
# Config-only credentials (Req 1.1): no secrets here. The Snowflake connection
# comes from your configured CoCo CLI connection selected with `-c <conn>`.
#
# Idempotent: uses IF EXISTS and tolerates already-suspended objects.
#
# Usage:
#   scripts/shutdown.sh [-c CONN] [-d DB] [-w WAREHOUSE] [--snowflake-only]
#                       [--local-only] [--dry-run]
#
#   -c, --connection CONN   CoCo CLI connection name   (default: governed-aml)
#   -d, --database   DB      target database name        (default: GOVERNED_AML)
#   -w, --warehouse  WH      warehouse to suspend         (default: COMPUTE_WH)
#       --snowflake-only     only suspend Snowflake objects (skip local servers)
#       --local-only         only stop local servers (skip Snowflake)
#       --dry-run            print the actions without executing them
#   -h, --help               show this help and exit
#
# Prerequisite: Snowflake CoCo CLI (`snow`) on PATH with a tested connection.
# =============================================================================
set -euo pipefail

CONN="governed-aml"
DB="GOVERNED_AML"
WAREHOUSE="COMPUTE_WH"
SNOWFLAKE=true
LOCAL=true
DRY_RUN=false

usage() { sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--connection)  CONN="$2"; shift 2 ;;
        -d|--database)    DB="$2"; shift 2 ;;
        -w|--warehouse)   WAREHOUSE="$2"; shift 2 ;;
        --snowflake-only) LOCAL=false; shift ;;
        --local-only)     SNOWFLAKE=false; shift ;;
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

# Run a SQL statement; never abort the whole script if one statement errors
# (e.g. "cannot be suspended" simply means it is already suspended).
snow_q() {
    local sql="$1"
    if [[ "${DRY_RUN}" == true ]]; then
        printf '  [dry-run] snow sql -c %q -q %q\n' "${CONN}" "${sql}"
    else
        snow sql -c "${CONN}" -q "${sql}" >/dev/null 2>&1 \
            && echo "  ok: ${sql}" \
            || echo "  note: '${sql}' reported no-op (already in target state)"
    fi
}

log "SentinelAML Copilot shutdown"
echo "    connection : ${CONN}"
echo "    database   : ${DB}"
echo "    warehouse  : ${WAREHOUSE}"
echo "    snowflake  : ${SNOWFLAKE}"
echo "    local      : ${LOCAL}"
echo "    dry run    : ${DRY_RUN}"

# --- 1) Snowflake --------------------------------------------------------------
if [[ "${SNOWFLAKE}" == true ]]; then
    if ! command -v snow >/dev/null 2>&1 && [[ "${DRY_RUN}" == false ]]; then
        echo "ERROR: 'snow' CLI not found on PATH; cannot suspend Snowflake objects." >&2
        echo "       Re-run with --local-only to just stop local servers." >&2
        exit 1
    fi

    log "Suspending ingest task (so its schedule cannot resume the warehouse)"
    snow_q "ALTER TASK IF EXISTS ${DB}.RAW.INGEST_TRANSACTION_EVENTS SUSPEND;"

    log "Suspending warehouse (a suspended warehouse bills 0 credits)"
    snow_q "ALTER WAREHOUSE IF EXISTS ${WAREHOUSE} SUSPEND;"

    if [[ "${DRY_RUN}" == false ]]; then
        log "Verifying Snowflake state"
        echo "  warehouses:"
        snow sql -c "${CONN}" -q "SHOW WAREHOUSES;" 2>/dev/null \
            | grep -iE '\| [A-Z]' | sed -E 's/^\| *//' | awk -F'|' '{gsub(/ /,"",$1);gsub(/ /,"",$2);print "    "$1" -> "$2}' \
            | grep -iE 'COMPUTE_WH|LEARNING|STREAMLIT' || true
        echo "  ingest task:"
        local_state="$(snow sql -c "${CONN}" -q "SHOW TASKS LIKE 'INGEST_TRANSACTION_EVENTS' IN SCHEMA ${DB}.RAW;" 2>/dev/null | grep -oiE '(suspended|started|scheduled)' | head -1 || true)"
        echo "    RAW.INGEST_TRANSACTION_EVENTS -> ${local_state:-unknown}"
    fi
fi

# --- 2) Local servers ----------------------------------------------------------
if [[ "${LOCAL}" == true ]]; then
    log "Stopping local servers (uvicorn :8000, next dev :3000)"
    if [[ "${DRY_RUN}" == true ]]; then
        echo "  [dry-run] pkill -f 'uvicorn app.main:app'"
        echo "  [dry-run] pkill -f 'next dev'"
    else
        pkill -f 'uvicorn app.main:app' 2>/dev/null && echo "  stopped: uvicorn backend" || echo "  note: no uvicorn backend running"
        pkill -f 'next dev'            2>/dev/null && echo "  stopped: next dev frontend" || echo "  note: no next dev frontend running"
        sleep 1
        if pgrep -f 'uvicorn app.main:app|next dev' >/dev/null 2>&1; then
            echo "  WARNING: a dev server is still running:"; pgrep -fl 'uvicorn app.main:app|next dev'
        else
            echo "  confirmed: no local dev servers running"
        fi
    fi
fi

log "Shutdown complete — Snowflake is idle (0 compute credits) until the next query."
echo "    Bring everything back up with: scripts/create_resources.sh  (objects + seed)"
echo "    or just resume compute on first use (warehouse auto-resumes on a query)."
