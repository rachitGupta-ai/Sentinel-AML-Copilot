"""Deterministic fakes and dataset loading for the evaluation harness.

Spec: aml-regulatory-copilot | Task 20.1 | Requirements: 12.1, 12.2, 12.5.

The harness runs the real service layer (``InvestigationService``,
``GroundingService``, ``MetricService``, ``GuardService``) against **fakes** so it
is runnable deterministically without a live Snowflake/Cortex (design §Evaluation
/ Testing Strategy):

* :class:`FakeCortexAdapter` — a drop-in :class:`~app.snowflake.cortex.CortexAdapter`
  that returns a deterministic narrative and retrieves policy "chunks" from the
  loaded dataset. Crucially it routes every retrieved chunk through the **real**
  :class:`~app.services.guard_service.GuardService` injection scan before
  returning it, exactly like the production adapter — so a malicious document is
  dropped by the guard, not by a special-case in the harness (Req 9.1, 12.5).
* The governed figures and metric definitions are seeded into an
  :class:`~app.services.metric_repository.InMemoryMetricRepository` from the
  synthetic dataset, so investigation reads the ground-truth governed values
  through the normal metric path (figures never come from the fake LLM, Req 3.2).

All data originates from ``dataset/ground_truth.json`` which is fully synthetic
(Req 12.1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from app.models.case import Case
from app.models.governed import EntityRiskView, GovernedMetricValue
from app.services.guard_service import GuardService, GuardServiceImpl
from app.services.metric_repository import (
    InMemoryMetricRepository,
    MetricDefinitionRow,
    MetricValueRow,
)
from app.services.metric_service import MetricServiceImpl, STRUCTURING_SCORE_METRIC
from app.snowflake.cortex import RetrievalResult, RetrievedChunk

# Location of the ground-truth dataset (fully synthetic, Req 12.1).
DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "ground_truth.json"

# A deliberately-ambiguous business term seeded into the fake registry so the
# "ambiguous question -> clarification" path (Req 5.2) can be exercised: it is a
# synonym of more than one canonical metric, so resolve_term returns Ambiguous.
AMBIGUOUS_TERM = "risk"
_AMBIGUOUS_FOR = ("exposure_90d", "structuring_score")

# Glossary synonyms per canonical metric, mirroring
# snowflake/semantic/10_metric_definition_registry.sql so resolve_term behaves as
# it does in production. The ambiguous term above is added to two of them.
_SYNONYMS: dict[str, list[str]] = {
    "exposure_90d": [
        "exposure",
        "90 day exposure",
        "90-day exposure",
        "ninety day exposure",
        "total exposure",
        "transacted amount",
    ],
    "txn_velocity": [
        "velocity",
        "transaction velocity",
        "txn rate",
        "transaction rate",
        "transactions per day",
        "activity rate",
    ],
    "structuring_score": [
        "structuring",
        "smurfing",
        "structuring risk",
        "structuring indicator",
        "threshold avoidance",
    ],
}

_DISPLAY_NAMES: dict[str, str] = {
    "exposure_90d": "Exposure (90-day)",
    "txn_velocity": "Transaction Velocity",
    "structuring_score": "Structuring Score",
}

_UNITS: dict[str, str] = {
    "exposure_90d": "amount",
    "txn_velocity": "count_per_day",
    "structuring_score": "score_0_1",
}


@dataclass(frozen=True)
class Dataset:
    """The parsed, fully-synthetic ground-truth dataset (Req 12.1).

    Thin wrapper over the JSON so the runner reads typed, convenient structures.
    ``raw`` keeps the original document for fields the runner reads directly.
    """

    raw: dict[str, Any]

    @property
    def synthetic_only(self) -> bool:
        return bool(self.raw.get("synthetic_only"))

    @property
    def questions(self) -> list[dict[str, Any]]:
        return list(self.raw.get("questions", []))

    @property
    def consistency_checks(self) -> list[dict[str, Any]]:
        return list(self.raw.get("consistency_checks", []))

    @property
    def injection_cases(self) -> list[dict[str, Any]]:
        return list(self.raw.get("injection_cases", []))

    @property
    def governed_metric_values(self) -> list[dict[str, Any]]:
        return list(self.raw.get("governed_metric_values", []))

    @property
    def policy_docs(self) -> list[dict[str, Any]]:
        return list(self.raw.get("policy_docs", []))

    def question_by_id(self, question_id: str) -> dict[str, Any]:
        for question in self.questions:
            if question["id"] == question_id:
                return question
        raise KeyError(f"question '{question_id}' not found in dataset")

    def case(self, case_id: str, entity_id: str) -> Case:
        """Build a :class:`Case` whose risk aggregation is the entity's governed metrics.

        The aggregation is populated solely from the seeded governed values (as
        the triage service would), so investigation reads figures through the
        normal governed path and the data-state is tied to the snapshot.
        """
        metrics = _entity_metric_values(self.governed_metric_values, entity_id)
        aggregate = next(
            (m.value for m in metrics if m.metric_name == STRUCTURING_SCORE_METRIC),
            Decimal("0"),
        )
        return Case(
            case_id=case_id,
            entity_id=entity_id,
            risk_aggregation=EntityRiskView(
                entity_id=entity_id, metrics=metrics, aggregate_score=aggregate
            ),
        )


def load_dataset(path: Optional[Path] = None) -> Dataset:
    """Load and parse the synthetic ground-truth dataset (Req 12.1)."""
    dataset_path = path if path is not None else DATASET_PATH
    with dataset_path.open("r", encoding="utf-8") as handle:
        return Dataset(raw=json.load(handle))


def build_metric_repository(dataset: Dataset) -> InMemoryMetricRepository:
    """Seed an in-memory metric repository from the synthetic governed values.

    Figures and their ``metric_definition_version`` come straight from the dataset,
    so the metric service returns the ground-truth governed values (Req 3.2). The
    registry definitions mirror the production registry's synonyms, with one term
    (:data:`AMBIGUOUS_TERM`) deliberately shared across two metrics so the
    clarification path can be exercised (Req 5.2).
    """
    repo = InMemoryMetricRepository()
    for row in dataset.governed_metric_values:
        repo.put_value(
            MetricValueRow(
                entity_id=row["entity_id"],
                metric_name=row["metric_name"],
                value=Decimal(str(row["value"])),
                metric_definition_version=row["metric_definition_version"],
                display_name=_DISPLAY_NAMES.get(row["metric_name"]),
                unit=_UNITS.get(row["metric_name"]),
            )
        )
    version = dataset.raw.get("metric_definition_version", "v1")
    for metric_name, synonyms in _SYNONYMS.items():
        resolved = list(synonyms)
        if metric_name in _AMBIGUOUS_FOR:
            resolved = [*resolved, AMBIGUOUS_TERM]
        repo.put_definition(
            MetricDefinitionRow(
                metric_name=metric_name,
                metric_definition_version=version,
                glossary_synonyms=resolved,
                display_name=_DISPLAY_NAMES.get(metric_name),
            )
        )
    return repo


def _entity_metric_values(
    rows: list[dict[str, Any]], entity_id: str
) -> list[GovernedMetricValue]:
    """Build governed metric values for a case's risk aggregation.

    The lineage/data-state is deterministic and derived from the dataset, so the
    aggregation is reproducible run-to-run (used only to anchor the case; the
    investigation re-reads figures through the metric service).
    """
    from app.models.common import QueryLineage

    values: list[GovernedMetricValue] = []
    for row in rows:
        if row["entity_id"] != entity_id:
            continue
        data_state = f"eval:{entity_id}"
        values.append(
            GovernedMetricValue(
                metric_name=row["metric_name"],
                entity_id=entity_id,
                value=Decimal(str(row["value"])),
                metric_definition_version=row["metric_definition_version"],
                data_state_hash=data_state,
                query_lineage=QueryLineage(
                    generated_query=(
                        "SELECT metric_value, metric_definition_version "
                        "FROM SEM.ENTITY_RISK_METRICS "
                        f"WHERE entity_id = '{entity_id}' "
                        f"AND metric_name = '{row['metric_name']}'"
                    ),
                    source_row_refs=[f"{entity_id}:{row['metric_name']}"],
                    semantic_view="SEM.ENTITY_RISK_METRICS",
                    data_state_hash=data_state,
                ),
            )
        )
    return sorted(values, key=lambda v: v.metric_name)


class FakeCortexAdapter:
    """Deterministic :class:`~app.snowflake.cortex.CortexAdapter` for the harness.

    * :meth:`complete` returns a fixed, deterministic narrative string (the LLM is
      only ever handed governed figures + scanned evidence; the harness does not
      need a real model to exercise the governed/grounding paths, Req 3.2).
    * :meth:`retrieve_policy_evidence` returns policy chunks drawn from the
      synthetic dataset, each routed through the **real** guard injection scan
      before being returned — identical to the production adapter — so an injected
      malicious document is dropped by the guard (Req 9.1, 12.5).

    An optional ``extra_documents`` map (``source_ref -> text``) injects additional
    retrieved documents for a specific question, used by the injection cases to
    smuggle a malicious instruction into the retrieval set.
    """

    def __init__(
        self,
        dataset: Dataset,
        *,
        guard: Optional[GuardService] = None,
        extra_documents: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        self._dataset = dataset
        self._guard: GuardService = guard if guard is not None else GuardServiceImpl()
        self._extra_documents = list(extra_documents or [])

    # -- CortexAdapter surface ---------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        """Deterministic stub embedding (unused by the grounding path)."""
        return [0.0]

    def complete(self, prompt: str, *, model: str | None = None) -> str:
        """Return a deterministic narrative; never produces a governed figure."""
        return (
            "AI-generated decision support (synthetic demo). The governed figures "
            "and cited policy passages for this entity are summarised below for "
            "analyst review; all numbers are governed values, not generated."
        )

    def retrieve_policy_evidence(
        self, query: str, *, top_k: int | None = None, **ctx: Any
    ) -> RetrievalResult:
        """Retrieve the most relevant policy chunks, guarding every chunk first.

        Policy chunks (from the synthetic dataset, plus any injected
        ``extra_documents``) are ranked deterministically by a simple term-overlap
        similarity against ``query`` and the top-ranked clean chunk(s) are
        returned, so the produced citations are the question-relevant ones (making
        citation precision/coverage meaningful). Any chunk the guard flags as
        prompt-injection is dropped and recorded in ``rejected_chunk_ids`` — the
        same contract as the production adapter (Req 9.1). ``top_k`` defaults to 1
        so the single best policy passage is cited.
        """
        case_ref = ctx.get("case_ref")
        entity_ref = ctx.get("entity_ref")
        k = top_k if top_k is not None else 1

        candidates: list[tuple[str, str, str]] = []  # (chunk_id, source_ref, text)
        for doc in self._dataset.policy_docs:
            candidates.append(
                (doc["policy_doc_id"], doc["policy_doc_id"], doc["excerpt"])
            )
        # Injected documents are considered for retrieval exactly like real chunks;
        # the guard is what stops a malicious one (Req 12.5), not a special-case.
        for source_ref, text in self._extra_documents:
            candidates.append((source_ref, source_ref, text))

        query_terms = _terms(query)
        ranked = sorted(
            enumerate(candidates),
            key=lambda item: (-_overlap(query_terms, item[1][2]), item[0]),
        )

        clean: list[RetrievedChunk] = []
        rejected: list[str] = []
        for order, (index, (chunk_id, source_ref, text)) in enumerate(ranked):
            scan = self._guard.scan_for_injection(
                text,
                source_ref=f"eval:{chunk_id}",
                case_ref=case_ref,
                entity_ref=entity_ref,
            )
            if scan.is_injection:
                rejected.append(chunk_id)
                continue
            if len(clean) >= k:
                continue
            clean.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    chunk_text=text,
                    similarity=1.0 - (order * 0.01),
                    source_kind="policy_passage",
                    source_ref=source_ref,
                    chunk_index=index,
                    embedding_model="fake-eval",
                )
            )
        return RetrievalResult(chunks=clean, rejected_chunk_ids=rejected)

    def retrieve_evidence(
        self, query: str, *, top_k: int | None = None, **ctx: Any
    ) -> RetrievalResult:
        """Alias to policy retrieval for the harness (same guarded contract)."""
        return self.retrieve_policy_evidence(query, top_k=top_k, **ctx)


def _terms(text: str) -> set[str]:
    """Lower-cased alphanumeric word set for deterministic term-overlap ranking."""
    normalized = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.lower())
    return {w for w in normalized.split() if len(w) > 2}


def _overlap(query_terms: set[str], document_text: str) -> int:
    """Count of shared terms between the query and a candidate document."""
    return len(query_terms & _terms(document_text))


def build_metric_service(dataset: Dataset) -> MetricServiceImpl:
    """Build a metric service backed by the seeded synthetic repository."""
    return MetricServiceImpl(build_metric_repository(dataset))


__all__ = [
    "Dataset",
    "FakeCortexAdapter",
    "DATASET_PATH",
    "AMBIGUOUS_TERM",
    "load_dataset",
    "build_metric_repository",
    "build_metric_service",
]
