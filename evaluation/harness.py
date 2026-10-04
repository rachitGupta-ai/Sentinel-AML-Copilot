"""Evaluation harness runner (deterministic, fakes-backed).

Spec: aml-regulatory-copilot | Task 20.1 | Requirements: 12.1, 12.2, 12.3, 12.4, 12.5.

Runs the ground-truth dataset against the **real** service layer
(:class:`~app.services.investigation_service.InvestigationServiceImpl`,
:class:`~app.services.grounding_service.GroundingServiceImpl`,
:class:`~app.services.metric_service.MetricServiceImpl`,
:class:`~app.services.guard_service.GuardServiceImpl`) wired to deterministic
fakes (:mod:`evaluation.fixtures`), so no live Snowflake/Cortex is needed and the
run is reproducible.

It computes the declared metrics (:mod:`evaluation.metrics`) — groundedness,
citation precision/coverage, unsupported-claim rate, refusal correctness,
metric-consistency, and injection-resistance (Req 12.2, 12.5) — and emits an
:class:`~evaluation.metrics.EvaluationReport` that separates demonstrated results
from intended targets and reports a result only for a metric whose test actually
executed (Req 12.4).

The harness never computes a regulatory figure itself: governed values are read
through the metric service (Req 3.2); the fake LLM only narrates. A malicious
document in an injection case is rejected by the real guard inside the fake Cortex
adapter — the harness asserts the governed figure is unchanged, not that the
harness special-cased it (Req 12.5).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Optional

from app.models.answer import Answer
from app.services.audit_service import AuditServiceImpl
from app.services.grounding_service import GroundingServiceImpl
from app.services.guard_service import GuardServiceImpl
from app.services.investigation_service import (
    Clarification,
    InvestigationServiceImpl,
    Refusal,
)

from evaluation.fixtures import Dataset, FakeCortexAdapter, build_metric_service, load_dataset
from evaluation.metrics import (
    METRIC_CITATION_COVERAGE,
    METRIC_CITATION_PRECISION,
    METRIC_DESCRIPTORS,
    METRIC_GROUNDEDNESS,
    METRIC_INJECTION_RESISTANCE,
    METRIC_METRIC_CONSISTENCY,
    METRIC_REFUSAL_CORRECTNESS,
    METRIC_UNSUPPORTED_CLAIM_RATE,
    CaseOutcome,
    EvaluationReport,
    IntendedTarget,
    MetricDescriptor,
    MetricResult,
)


def _outcome_label(result: Any) -> str:
    """Map an investigation result to its outcome label (answer/clarification/refusal)."""
    if isinstance(result, Clarification):
        return "clarification"
    if isinstance(result, Refusal):
        return "refusal"
    if isinstance(result, Answer):
        return "answer"
    return "unknown"  # pragma: no cover - defensive


class EvaluationHarness:
    """Runs the dataset and computes the declared evaluation metrics.

    A fresh harness builds one metric service seeded from the dataset's synthetic
    governed values; a new investigation service is built per question/injection
    case so each run is isolated (fresh audit trail), keeping the run deterministic
    and side-effect free.
    """

    def __init__(self, dataset: Optional[Dataset] = None) -> None:
        self._dataset = dataset if dataset is not None else load_dataset()
        self._metric_service = build_metric_service(self._dataset)

    # -- investigation wiring ----------------------------------------------

    def _answer(self, question: str, case, *, extra_documents=None):
        """Answer a question through the real investigation service + fakes."""
        guard = GuardServiceImpl(AuditServiceImpl())
        cortex = FakeCortexAdapter(
            self._dataset, guard=guard, extra_documents=extra_documents
        )
        service = InvestigationServiceImpl(
            cortex,
            metric_service=self._metric_service,
            grounding_service=GroundingServiceImpl(),
            audit_service=AuditServiceImpl(),
        )
        return service.answer(question, case), cortex

    # -- run ---------------------------------------------------------------

    def run(self) -> EvaluationReport:
        """Execute every dataset test and build the demonstrated/intended report."""
        # Produce an answer/clarification/refusal for each question once; reuse for
        # all answer-derived metrics.
        produced: dict[str, Any] = {}
        case_outcomes: list[CaseOutcome] = []
        for question in self._dataset.questions:
            case = self._dataset.case(question["case_id"], question["entity_id"])
            result, _ = self._answer(question["question"], case)
            produced[question["id"]] = result
            expected = question["expected_outcome"]
            actual = _outcome_label(result)
            case_outcomes.append(
                CaseOutcome(
                    question_id=question["id"],
                    expected=expected,
                    actual=actual,
                    match=expected == actual,
                )
            )

        executed: list[str] = []
        skipped: list[str] = []
        demonstrated: list[MetricResult] = []
        intended: list[IntendedTarget] = []

        # Each metric is computed only when its dataset slice is non-empty; an
        # empty slice is reported as skipped, never with a fabricated value (12.4).
        computations = [
            (METRIC_GROUNDEDNESS, lambda: self._groundedness(produced)),
            (METRIC_CITATION_PRECISION, lambda: self._citation_precision(produced)),
            (METRIC_CITATION_COVERAGE, lambda: self._citation_coverage(produced)),
            (METRIC_UNSUPPORTED_CLAIM_RATE, lambda: self._unsupported_claim_rate(produced)),
            (METRIC_REFUSAL_CORRECTNESS, lambda: self._refusal_correctness(case_outcomes)),
            (METRIC_METRIC_CONSISTENCY, self._metric_consistency),
            (METRIC_INJECTION_RESISTANCE, self._injection_resistance),
        ]

        for metric_id, compute in computations:
            descriptor = METRIC_DESCRIPTORS[metric_id]
            computed = compute()
            if computed is None:
                skipped.append(metric_id)
                continue
            value, sample_size, detail = computed
            executed.append(metric_id)
            demonstrated.append(
                MetricResult(
                    metric_id=metric_id,
                    value=value,
                    sample_size=sample_size,
                    verdict=descriptor.verdict(value),
                    detail=detail,
                )
            )
            intended.append(_intended(descriptor))

        return EvaluationReport(
            synthetic_only=self._dataset.synthetic_only,
            executed_metric_ids=executed,
            skipped_metric_ids=skipped,
            demonstrated=demonstrated,
            intended=intended,
            case_outcomes=case_outcomes,
        )

    # -- metric computations (pure over the produced results) --------------

    def _answer_items(self, produced: dict[str, Any]) -> list[tuple[dict[str, Any], Answer]]:
        """(question, Answer) pairs for questions that expected an answer and got one."""
        items: list[tuple[dict[str, Any], Answer]] = []
        for question in self._dataset.questions:
            if question["expected_outcome"] != "answer":
                continue
            result = produced.get(question["id"])
            if isinstance(result, Answer):
                items.append((question, result))
        return items

    def _groundedness(self, produced):
        items = self._answer_items(produced)
        if not items:
            return None
        scores = [answer.groundedness_score for _, answer in items]
        value = sum(scores) / len(scores)
        return value, len(scores), f"mean over {len(scores)} produced answer(s)."

    def _citation_precision(self, produced):
        items = self._answer_items(produced)
        if not items:
            return None
        produced_total = 0
        correct = 0
        for question, answer in items:
            expected = set(question.get("expected_textual_citations", []))
            produced_refs = [c.evidence.ref for c in answer.textual_claims]
            produced_total += len(produced_refs)
            correct += sum(1 for ref in produced_refs if ref in expected)
        if produced_total == 0:
            return 0.0, len(items), "no citations produced."
        return correct / produced_total, len(items), f"{correct}/{produced_total} produced citations expected."

    def _citation_coverage(self, produced):
        items = self._answer_items(produced)
        if not items:
            return None
        expected_total = 0
        covered = 0
        for question, answer in items:
            expected = set(question.get("expected_textual_citations", []))
            produced_refs = {c.evidence.ref for c in answer.textual_claims}
            expected_total += len(expected)
            covered += len(expected & produced_refs)
        if expected_total == 0:
            return 1.0, len(items), "no expected citations."
        return covered / expected_total, len(items), f"{covered}/{expected_total} expected citations covered."

    def _unsupported_claim_rate(self, produced):
        items = self._answer_items(produced)
        if not items:
            return None
        total_claims = 0
        uncited = 0
        for _, answer in items:
            numeric = answer.numeric_claims
            textual = answer.textual_claims
            total_claims += len(numeric) + len(textual)
            uncited += sum(1 for c in numeric if c.lineage is None)
            uncited += sum(1 for c in textual if not (c.evidence.ref or "").strip())
        if total_claims == 0:
            return 0.0, len(items), "no claims produced."
        return uncited / total_claims, len(items), f"{uncited}/{total_claims} claims uncited."

    @staticmethod
    def _refusal_correctness(case_outcomes: list[CaseOutcome]):
        if not case_outcomes:
            return None
        matched = sum(1 for o in case_outcomes if o.match)
        return matched / len(case_outcomes), len(case_outcomes), (
            f"{matched}/{len(case_outcomes)} questions matched expected outcome."
        )

    def _metric_consistency(self):
        checks = self._dataset.consistency_checks
        if not checks:
            return None
        consistent = 0
        for check in checks:
            question = self._dataset.question_by_id(check["question_id"])
            case = self._dataset.case(question["case_id"], question["entity_id"])
            repetitions = int(check.get("repetitions", 2))
            signatures: set[tuple] = set()
            for _ in range(repetitions):
                result, _cortex = self._answer(question["question"], case)
                signatures.add(_numeric_signature(result))
            if len(signatures) == 1:
                consistent += 1
        return consistent / len(checks), len(checks), (
            f"{consistent}/{len(checks)} consistency checks fully stable."
        )

    def _injection_resistance(self):
        cases = self._dataset.injection_cases
        if not cases:
            return None
        resisted = 0
        for inj in cases:
            case = self._dataset.case(inj["case_id"], inj["entity_id"])
            expected = inj["expected_behaviour_unchanged"]
            # Baseline answer with no malicious document.
            baseline, _ = self._answer(inj["question"], case)
            # Answer WITH the malicious document injected into the retrieval set.
            injected, cortex = self._answer(
                inj["question"],
                case,
                extra_documents=[(f"MAL-{inj['id']}", inj["malicious_document"])],
            )
            document_rejected = f"MAL-{inj['id']}" in cortex.retrieve_policy_evidence(
                inj["question"], case_ref=inj["case_id"], entity_ref=inj["entity_id"]
            ).rejected_chunk_ids
            figure_unchanged = _numeric_signature(baseline) == _numeric_signature(injected)
            expected_claim = expected["governed_numeric_claim"]
            matches_ground_truth = _has_numeric_claim(injected, expected_claim)
            if document_rejected and figure_unchanged and matches_ground_truth:
                resisted += 1
        return resisted / len(cases), len(cases), (
            f"{resisted}/{len(cases)} injection cases: document rejected and figure unchanged."
        )


def _numeric_signature(result: Any) -> tuple:
    """A stable signature of a result's governed numeric claims (metric, value, version).

    Used for consistency and injection checks: two results are "the same governed
    answer" iff they carry the identical set of (metric_name, value, version)
    numeric claims. Non-answers signature on their outcome label so a flip from
    answer to refusal is also detected.
    """
    if not isinstance(result, Answer):
        return (_outcome_label(result),)
    return tuple(
        sorted(
            (c.metric_name, str(c.value), c.metric_definition_version)
            for c in result.numeric_claims
        )
    )


def _has_numeric_claim(result: Any, expected: dict[str, Any]) -> bool:
    """Whether ``result`` carries the expected governed numeric claim (value+version)."""
    if not isinstance(result, Answer):
        return False
    for claim in result.numeric_claims:
        if (
            claim.metric_name == expected["metric_name"]
            and claim.value == Decimal(str(expected["value"]))
            and claim.metric_definition_version == expected["metric_definition_version"]
        ):
            return True
    return False


def _intended(descriptor: MetricDescriptor) -> IntendedTarget:
    """Build the intended-target entry for a descriptor (disjoint from demonstrated)."""
    return IntendedTarget(
        metric_id=descriptor.metric_id,
        target=descriptor.target,
        acceptable_threshold=descriptor.acceptable_threshold,
        failure_threshold=descriptor.failure_threshold,
        unit=descriptor.unit,
    )


# ---------------------------------------------------------------------------
# Report rendering + CLI.
# ---------------------------------------------------------------------------


def report_to_dict(report: EvaluationReport) -> dict[str, Any]:
    """Serialise an :class:`EvaluationReport` to a JSON-ready dict (Req 12.4).

    The ``demonstrated`` and ``intended`` channels are kept as separate keys so a
    measured result is never conflated with a target, and ``skipped`` names metrics
    whose test did not execute (reported as skipped, never with a value).
    """
    return {
        "synthetic_only": report.synthetic_only,
        "executed_metric_ids": report.executed_metric_ids,
        "skipped_metric_ids": report.skipped_metric_ids,
        "demonstrated": [asdict(r) | {"verdict": r.verdict.value} for r in report.demonstrated],
        "intended": [asdict(t) for t in report.intended],
        "case_outcomes": [asdict(o) for o in report.case_outcomes],
        "all_passed": report.all_passed,
    }


def render_text(report: EvaluationReport) -> str:
    """Render a human-readable report separating demonstrated from intended."""
    lines: list[str] = []
    lines.append("SentinelAML Copilot — Evaluation Report")
    lines.append("=" * 60)
    lines.append(f"synthetic_only: {report.synthetic_only}")
    lines.append("")
    lines.append("DEMONSTRATED (measured this run):")
    for result in report.demonstrated:
        descriptor = METRIC_DESCRIPTORS[result.metric_id]
        lines.append(
            f"  - {result.metric_id}: {result.value:.4f} {descriptor.unit} "
            f"[{result.verdict.value}] (n={result.sample_size}) — {result.detail}"
        )
    lines.append("")
    lines.append("INTENDED (production targets, NOT measured):")
    for target in report.intended:
        lines.append(
            f"  - {target.metric_id}: target={target.target} "
            f"acceptable={target.acceptable_threshold} failure={target.failure_threshold} "
            f"{target.unit}"
        )
    if report.skipped_metric_ids:
        lines.append("")
        lines.append("SKIPPED (test not executed — no result reported):")
        for metric_id in report.skipped_metric_ids:
            lines.append(f"  - {metric_id}")
    lines.append("")
    lines.append(f"ALL EXECUTED METRICS PASSED: {report.all_passed}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: run the harness and print the report. Exit non-zero if any metric fails."""
    parser = argparse.ArgumentParser(description="Run the SentinelAML evaluation harness.")
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Report format (default: text).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with a non-zero code if any executed metric fails its threshold.",
    )
    args = parser.parse_args(argv)

    report = EvaluationHarness().run()
    if args.format == "json":
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print(render_text(report))

    if args.strict and not report.all_passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
