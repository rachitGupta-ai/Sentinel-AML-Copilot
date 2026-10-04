#!/usr/bin/env python3
"""Synthetic dataset generator for the SentinelAML Copilot demo.

Spec: aml-regulatory-copilot | Task 5.1 | Requirements: 2.1, 2.2, 2.3

This generator emits a **fully synthetic** AML dataset (Req 2.1 — no real
customer, account, or transaction data appears anywhere) that drives the
end-to-end demo:

* ``RAW.ENTITY``            — synthetic entities under investigation.
* ``RAW.TRANSACTION_EVENT`` — synthetic transactions, including a
  structuring/smurfing pattern sufficient to raise ``structuring_score`` and
  support a SAR (Req 2.2).
* ``RAW.ALERT``             — suspicious-activity alerts, incl. the structuring alert.
* ``RAW.POLICY_DOC``        — synthetic AML policy passages for textual grounding.

The output is written as CSV files (one per RAW table) plus a ``load.sql``
loader and a ``manifest.json`` describing what was generated. The column
order/types mirror ``snowflake/ddl/01_raw.sql`` so the files are directly
loadable with ``COPY INTO`` (ingestion wiring is task 5.2; this task focuses on
generation but keeps the output loadable).

Determinism: a single seeded ``random.Random`` and a fixed anchor date make the
run fully reproducible, so the demo and its KPIs are identical on every run.

Structuring typology (Req 2.2): the semantic view
``SEM.ENTITY_RISK_METRICS`` computes ``structuring_score`` as the fraction of a
trailing-90-day window's transactions that fall in the just-below-reporting-
threshold band ``9000 <= amount < 10000`` (anchored on the entity's latest
``occurred_at``). The smurfing entity therefore gets many such just-below-10000
deposits over < 90 days so its ``structuring_score`` is high (~1.0) and clearly
flags, while a normal entity's score stays near 0.

Data-quality defects injected (Req 2.3), each clearly tagged in ``manifest.json``:

* **missing field** — one transaction row with a NULL/empty ``currency``.
* **duplicate event** — a second row re-using an existing ``event_id`` (the
  dedup key, Req 2.5) so ingestion can suppress it.
* **stale/late record** — one transaction whose ``occurred_at`` is far in the
  past relative to the anchor (an old, late-arriving event) *and* whose
  ``ingested_at`` lags well behind ``occurred_at``.

Usage::

    python snowflake/seed/generate_seed.py            # writes to snowflake/seed/data/
    python snowflake/seed/generate_seed.py --out /tmp/seed --seed 42

All names/ids are obviously fake (``ENT-...``, ``Synthetic ...``) to make the
synthetic-only contract (Req 2.1) self-evident and machine-checkable.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

# --- Fixed, synthetic constants ------------------------------------------------

# Deterministic anchor for business time. All "recent" activity is placed in the
# trailing-90-day window ending here so the semantic view's window (anchored on
# MAX(occurred_at) per entity) covers it. A fixed anchor keeps runs reproducible.
ANCHOR = datetime(2026, 1, 15, 12, 0, 0)

# Just-below-reporting-threshold band used by SEM.ENTITY_RISK_METRICS.structuring_score.
STRUCTURING_LOW = Decimal("9000")
STRUCTURING_HIGH = Decimal("10000")  # exclusive upper bound

DEFAULT_CURRENCY = "USD"
INGEST_SOURCE = "synthetic-seed-generator"

TWO_DP = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    """Round to 2 dp (matches RAW.TRANSACTION_EVENT NUMBER(38,2))."""
    return value.quantize(TWO_DP, rounding=ROUND_HALF_UP)


# --- Row dataclasses (column order mirrors snowflake/ddl/01_raw.sql) -----------


@dataclass
class EntityRow:
    entity_id: str
    display_name_masked: str
    attributes: dict  # serialized to a JSON string for the VARIANT column


@dataclass
class TransactionRow:
    event_id: str
    entity_id: str
    amount: str  # Decimal rendered as string, or "" for a missing value
    currency: str
    occurred_at: str  # ISO-8601, or "" for missing
    ingested_at: str
    source: str
    is_duplicate_suppressed: bool


@dataclass
class AlertRow:
    alert_id: str
    entity_id: str
    typology: str
    raised_at: str
    correlation_key: str


@dataclass
class PolicyDocRow:
    policy_doc_id: str
    title: str
    body: str
    source: str
    version: str
    ingested_at: str


@dataclass
class Dataset:
    entities: list[EntityRow]
    transactions: list[TransactionRow]
    alerts: list[AlertRow]
    policy_docs: list[PolicyDocRow]
    defects: dict  # provenance of injected data-quality defects (Req 2.3)


# --- Generator -----------------------------------------------------------------


class SeedGenerator:
    """Builds the synthetic dataset deterministically from a seed."""

    def __init__(self, seed: int = 1337) -> None:
        self._rng = random.Random(seed)
        self._seed = seed

    def _iso(self, dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    def _ingest_time(self, occurred: datetime) -> datetime:
        """Normal ingestion lands a few minutes to a few hours after business time."""
        return occurred + timedelta(minutes=self._rng.randint(1, 240))

    # -- entities --------------------------------------------------------------

    def _entities(self) -> list[EntityRow]:
        return [
            EntityRow(
                entity_id="ENT-SMURF-001",
                display_name_masked="Synthetic Smurf Holdings LLC (FAKE)",
                attributes={
                    "synthetic": True,
                    "segment": "retail",
                    "risk_notes": "demo structuring/smurfing typology",
                    "home_branch": "SYNTH-BRANCH-01",
                },
            ),
            EntityRow(
                entity_id="ENT-NORMAL-002",
                display_name_masked="Synthetic Everyday Trading Co (FAKE)",
                attributes={"synthetic": True, "segment": "sme"},
            ),
            EntityRow(
                entity_id="ENT-NORMAL-003",
                display_name_masked="Synthetic Quiet Savings Ltd (FAKE)",
                attributes={"synthetic": True, "segment": "retail"},
            ),
        ]

    # -- transactions ----------------------------------------------------------

    def _structuring_txns(self, entity_id: str) -> list[TransactionRow]:
        """Many just-below-threshold deposits inside the trailing-90-day window.

        Produces a high ``structuring_score`` (~1.0): nearly every windowed txn
        falls in [9000, 10000). This is the recognisable smurfing typology that
        raises an Alert and supports a SAR (Req 2.2).
        """
        rows: list[TransactionRow] = []
        # 18 structuring deposits spread across ~60 days (well within 90 days of
        # the anchor), plus a couple of small benign txns so the pattern is
        # realistic yet the band fraction stays dominant.
        n_structuring = 18
        for i in range(n_structuring):
            # Spread days back from the anchor: 2..59 days, deterministic.
            days_back = 2 + (i * 57) // max(1, n_structuring - 1)
            occurred = ANCHOR - timedelta(
                days=days_back,
                hours=self._rng.randint(0, 23),
                minutes=self._rng.randint(0, 59),
            )
            # Amount in [9000, 9980], staying strictly below the 10000 threshold.
            amount = _money(STRUCTURING_LOW + Decimal(self._rng.randint(0, 980)))
            rows.append(
                TransactionRow(
                    event_id=f"TXN-SMURF-{i:03d}",
                    entity_id=entity_id,
                    amount=str(amount),
                    currency=DEFAULT_CURRENCY,
                    occurred_at=self._iso(occurred),
                    ingested_at=self._iso(self._ingest_time(occurred)),
                    source=INGEST_SOURCE,
                    is_duplicate_suppressed=False,
                )
            )
        # A few small benign transactions (clearly outside the band) — realistic
        # noise that does not materially dilute the structuring fraction.
        for j in range(2):
            occurred = ANCHOR - timedelta(days=5 + j * 10, hours=self._rng.randint(0, 23))
            amount = _money(Decimal(self._rng.randint(40, 300)))
            rows.append(
                TransactionRow(
                    event_id=f"TXN-SMURF-BENIGN-{j:03d}",
                    entity_id=entity_id,
                    amount=str(amount),
                    currency=DEFAULT_CURRENCY,
                    occurred_at=self._iso(occurred),
                    ingested_at=self._iso(self._ingest_time(occurred)),
                    source=INGEST_SOURCE,
                    is_duplicate_suppressed=False,
                )
            )
        return rows

    def _normal_txns(self, entity_id: str, prefix: str, count: int) -> list[TransactionRow]:
        """Ordinary activity with varied amounts, none clustered below threshold."""
        rows: list[TransactionRow] = []
        for i in range(count):
            days_back = self._rng.randint(1, 85)
            occurred = ANCHOR - timedelta(
                days=days_back, hours=self._rng.randint(0, 23), minutes=self._rng.randint(0, 59)
            )
            # Spread amounts across small and large; deliberately avoid the
            # [9000, 10000) band so structuring_score stays ~0.
            choice = self._rng.random()
            if choice < 0.6:
                amount = Decimal(self._rng.randint(10, 2500))
            elif choice < 0.9:
                amount = Decimal(self._rng.randint(10001, 45000))
            else:
                amount = Decimal(self._rng.randint(2501, 8900))
            rows.append(
                TransactionRow(
                    event_id=f"{prefix}-{i:03d}",
                    entity_id=entity_id,
                    amount=str(_money(amount)),
                    currency=DEFAULT_CURRENCY,
                    occurred_at=self._iso(occurred),
                    ingested_at=self._iso(self._ingest_time(occurred)),
                    source=INGEST_SOURCE,
                    is_duplicate_suppressed=False,
                )
            )
        return rows

    def build(self) -> Dataset:
        entities = self._entities()
        transactions: list[TransactionRow] = []
        transactions += self._structuring_txns("ENT-SMURF-001")
        transactions += self._normal_txns("ENT-NORMAL-002", "TXN-N2", 12)
        transactions += self._normal_txns("ENT-NORMAL-003", "TXN-N3", 8)

        defects = self._inject_defects(transactions)

        alerts = [
            AlertRow(
                alert_id="ALERT-STRUCT-001",
                entity_id="ENT-SMURF-001",
                typology="structuring",
                raised_at=self._iso(ANCHOR - timedelta(days=1)),
                correlation_key="CORR-ENT-SMURF-001-structuring",
            ),
            # A second structuring alert on the same entity within the window —
            # exercises correlation/dedup into one case (Req 4.5) downstream.
            AlertRow(
                alert_id="ALERT-STRUCT-002",
                entity_id="ENT-SMURF-001",
                typology="structuring",
                raised_at=self._iso(ANCHOR - timedelta(hours=12)),
                correlation_key="CORR-ENT-SMURF-001-structuring",
            ),
        ]

        policy_docs = self._policy_docs()

        return Dataset(
            entities=entities,
            transactions=transactions,
            alerts=alerts,
            policy_docs=policy_docs,
            defects=defects,
        )

    # -- data-quality defects (Req 2.3) ---------------------------------------

    def _inject_defects(self, transactions: list[TransactionRow]) -> dict:
        """Inject the three required defects and record their provenance.

        * missing field  — a transaction with an empty ``currency``.
        * duplicate event — a row re-using an existing ``event_id`` (Req 2.5).
        * stale/late      — an old event whose ``ingested_at`` lags far behind.
        """
        # 1) Missing field: a normal-looking deposit with no currency.
        occurred_missing = ANCHOR - timedelta(days=7, hours=3)
        missing = TransactionRow(
            event_id="TXN-DEFECT-MISSING-CURRENCY",
            entity_id="ENT-NORMAL-002",
            amount=str(_money(Decimal("1200.00"))),
            currency="",  # <-- intentionally missing field (Req 2.3)
            occurred_at=self._iso(occurred_missing),
            ingested_at=self._iso(self._ingest_time(occurred_missing)),
            source=INGEST_SOURCE,
            is_duplicate_suppressed=False,
        )

        # 2) Duplicate event: re-use an existing structuring event_id. Stored as a
        #    raw duplicate (is_duplicate_suppressed=False) so the ingestion
        #    stream/task (task 5.2) is what suppresses it and records suppression.
        dup_source = next(t for t in transactions if t.event_id == "TXN-SMURF-000")
        duplicate = TransactionRow(
            event_id=dup_source.event_id,  # <-- same event_id (Req 2.5 dedup key)
            entity_id=dup_source.entity_id,
            amount=dup_source.amount,
            currency=dup_source.currency,
            occurred_at=dup_source.occurred_at,
            ingested_at=self._iso(self._ingest_time(ANCHOR - timedelta(days=1))),
            source=INGEST_SOURCE,
            is_duplicate_suppressed=False,
        )

        # 3) Stale/late record: a very old event that only arrived now (ingested_at
        #    far after occurred_at). Placed well outside the 90-day window so it
        #    does not distort the structuring metric, but still exercises
        #    stale-data handling and freshness indicators downstream.
        occurred_stale = ANCHOR - timedelta(days=400)
        stale = TransactionRow(
            event_id="TXN-DEFECT-STALE-LATE",
            entity_id="ENT-NORMAL-003",
            amount=str(_money(Decimal("530.00"))),
            currency=DEFAULT_CURRENCY,
            occurred_at=self._iso(occurred_stale),  # ~400 days old
            ingested_at=self._iso(ANCHOR + timedelta(hours=1)),  # arrived late, now
            source="synthetic-late-arrival-feed",
            is_duplicate_suppressed=False,
        )

        transactions.extend([missing, duplicate, stale])

        return {
            "missing_field": {
                "event_id": missing.event_id,
                "field": "currency",
                "requirement": "2.3",
            },
            "duplicate_event": {
                "event_id": duplicate.event_id,
                "duplicates_of": dup_source.event_id,
                "requirement": "2.3 / 2.5",
            },
            "stale_late_record": {
                "event_id": stale.event_id,
                "occurred_at": stale.occurred_at,
                "ingested_at": stale.ingested_at,
                "requirement": "2.3",
            },
        }

    # -- policy docs -----------------------------------------------------------

    def _policy_docs(self) -> list[PolicyDocRow]:
        ingested = self._iso(ANCHOR - timedelta(days=30))
        return [
            PolicyDocRow(
                policy_doc_id="POL-STRUCTURING-001",
                title="Synthetic AML Policy — Structuring and Smurfing",
                body=(
                    "SYNTHETIC POLICY (demo only). Structuring, also known as smurfing, is the "
                    "practice of breaking a large amount into multiple smaller transactions, each "
                    "deliberately kept below a mandatory reporting threshold, to evade detection. "
                    "A pattern of repeated deposits just below the reporting threshold over a short "
                    "window is a recognised indicator of suspicious activity and may warrant a "
                    "Suspicious Activity Report (SAR)."
                ),
                source="SYNTHETIC-AML-HANDBOOK",
                version="v1",
                ingested_at=ingested,
            ),
            PolicyDocRow(
                policy_doc_id="POL-REPORTING-THRESHOLD-002",
                title="Synthetic AML Policy — Reporting Threshold",
                body=(
                    "SYNTHETIC POLICY (demo only). Transactions at or above the reporting threshold "
                    "of 10,000 require a currency transaction report. Transactions clustered in the "
                    "band just below this threshold (for example 9,000 to 9,999) warrant enhanced "
                    "scrutiny as potential threshold-avoidance."
                ),
                source="SYNTHETIC-AML-HANDBOOK",
                version="v1",
                ingested_at=ingested,
            ),
            PolicyDocRow(
                policy_doc_id="POL-SAR-BASIS-003",
                title="Synthetic AML Policy — SAR Filing Basis",
                body=(
                    "SYNTHETIC POLICY (demo only). A SAR should document the entity, the suspicious "
                    "pattern observed, the governed risk figures supporting the suspicion, and the "
                    "policy basis cited. A SAR draft produced with AI assistance is decision support "
                    "and requires human review before any filing."
                ),
                source="SYNTHETIC-AML-HANDBOOK",
                version="v1",
                ingested_at=ingested,
            ),
        ]


# --- Writers -------------------------------------------------------------------


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)


def write_dataset(dataset: Dataset, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(
        out_dir / "entity.csv",
        ["entity_id", "display_name_masked", "attributes"],
        [[e.entity_id, e.display_name_masked, json.dumps(e.attributes)] for e in dataset.entities],
    )

    _write_csv(
        out_dir / "transaction_event.csv",
        [
            "event_id",
            "entity_id",
            "amount",
            "currency",
            "occurred_at",
            "ingested_at",
            "source",
            "is_duplicate_suppressed",
        ],
        [
            [
                t.event_id,
                t.entity_id,
                t.amount,
                t.currency,
                t.occurred_at,
                t.ingested_at,
                t.source,
                "TRUE" if t.is_duplicate_suppressed else "FALSE",
            ]
            for t in dataset.transactions
        ],
    )

    _write_csv(
        out_dir / "alert.csv",
        ["alert_id", "entity_id", "typology", "raised_at", "correlation_key"],
        [[a.alert_id, a.entity_id, a.typology, a.raised_at, a.correlation_key] for a in dataset.alerts],
    )

    _write_csv(
        out_dir / "policy_doc.csv",
        ["policy_doc_id", "title", "body", "source", "version", "ingested_at"],
        [
            [p.policy_doc_id, p.title, p.body, p.source, p.version, p.ingested_at]
            for p in dataset.policy_docs
        ],
    )

    manifest = {
        "generator": "snowflake/seed/generate_seed.py",
        "spec": "aml-regulatory-copilot",
        "task": "5.1",
        "requirements": ["2.1", "2.2", "2.3"],
        "synthetic_only": True,
        "anchor": ANCHOR.strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": {
            "entities": len(dataset.entities),
            "transaction_events": len(dataset.transactions),
            "alerts": len(dataset.alerts),
            "policy_docs": len(dataset.policy_docs),
        },
        "structuring_entity": {
            "entity_id": "ENT-SMURF-001",
            "band": "9000 <= amount < 10000",
            "window_days": 90,
            "note": "Dominant fraction of windowed txns in band → high structuring_score (Req 2.2).",
        },
        "injected_defects": dataset.defects,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    _write_loader(out_dir)
    return manifest


def _write_loader(out_dir: Path) -> None:
    """Emit a COPY INTO loader so the CSVs are directly loadable into RAW.*.

    Loading into Snowflake streams/tasks is task 5.2; this loader covers the
    plain bulk load of the generated files into the RAW tables created by
    snowflake/ddl/01_raw.sql, keeping the generated output loadable (task 5.1).
    """
    loader = """\
-- =============================================================================
-- SentinelAML Copilot — load synthetic seed data into RAW.*
-- Spec: aml-regulatory-copilot | Task 5.1 | Requirements: 2.1, 2.2, 2.3
-- Generated by snowflake/seed/generate_seed.py — DO NOT EDIT BY HAND.
-- =============================================================================
-- All data here is fully synthetic (Req 2.1). Run AFTER the RAW DDL
-- (snowflake/ddl/01_raw.sql). Files are loaded from an internal stage; adjust
-- the PUT paths to the directory containing the generated CSVs.
--
--   snow sql -c governed-aml -D db=GOVERNED_AML -f snowflake/seed/data/load.sql
-- =============================================================================

SET db = 'GOVERNED_AML';
USE DATABASE IDENTIFIER($db);
USE SCHEMA RAW;

CREATE FILE FORMAT IF NOT EXISTS RAW.SEED_CSV_FORMAT
    TYPE = CSV
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    SKIP_HEADER = 1
    NULL_IF = ('', 'NULL')
    EMPTY_FIELD_AS_NULL = TRUE
    TIMESTAMP_FORMAT = 'YYYY-MM-DD"T"HH24:MI:SS';

CREATE STAGE IF NOT EXISTS RAW.SEED_STAGE
    FILE_FORMAT = RAW.SEED_CSV_FORMAT
    COMMENT = 'Internal stage for synthetic seed CSVs (Req 2.1).';

-- Upload the generated CSVs (run from the directory holding this load.sql).
PUT 'file://entity.csv'            @RAW.SEED_STAGE OVERWRITE = TRUE AUTO_COMPRESS = FALSE;
PUT 'file://transaction_event.csv' @RAW.SEED_STAGE OVERWRITE = TRUE AUTO_COMPRESS = FALSE;
PUT 'file://alert.csv'             @RAW.SEED_STAGE OVERWRITE = TRUE AUTO_COMPRESS = FALSE;
PUT 'file://policy_doc.csv'        @RAW.SEED_STAGE OVERWRITE = TRUE AUTO_COMPRESS = FALSE;

COPY INTO RAW.ENTITY (entity_id, display_name_masked, attributes)
    FROM (
        SELECT $1, $2, TRY_PARSE_JSON($3)
        FROM @RAW.SEED_STAGE/entity.csv
    )
    FILE_FORMAT = (FORMAT_NAME = RAW.SEED_CSV_FORMAT)
    ON_ERROR = 'ABORT_STATEMENT';

COPY INTO RAW.TRANSACTION_EVENT
    (event_id, entity_id, amount, currency, occurred_at, ingested_at, source, is_duplicate_suppressed)
    FROM @RAW.SEED_STAGE/transaction_event.csv
    FILE_FORMAT = (FORMAT_NAME = RAW.SEED_CSV_FORMAT)
    ON_ERROR = 'ABORT_STATEMENT';

COPY INTO RAW.ALERT (alert_id, entity_id, typology, raised_at, correlation_key)
    FROM @RAW.SEED_STAGE/alert.csv
    FILE_FORMAT = (FORMAT_NAME = RAW.SEED_CSV_FORMAT)
    ON_ERROR = 'ABORT_STATEMENT';

COPY INTO RAW.POLICY_DOC (policy_doc_id, title, body, source, version, ingested_at)
    FROM @RAW.SEED_STAGE/policy_doc.csv
    FILE_FORMAT = (FORMAT_NAME = RAW.SEED_CSV_FORMAT)
    ON_ERROR = 'ABORT_STATEMENT';
"""
    (out_dir / "load.sql").write_text(loader, encoding="utf-8")


# --- CLI -----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic AML seed dataset.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
        help="Output directory for the generated CSVs/loader/manifest.",
    )
    parser.add_argument("--seed", type=int, default=1337, help="RNG seed (deterministic output).")
    args = parser.parse_args()

    dataset = SeedGenerator(seed=args.seed).build()
    manifest = write_dataset(dataset, args.out)

    print(f"Wrote synthetic seed to {args.out}")
    print(json.dumps(manifest["counts"], indent=2))
    print("Injected defects:", ", ".join(manifest["injected_defects"].keys()))


if __name__ == "__main__":
    main()
