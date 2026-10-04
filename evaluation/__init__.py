"""SentinelAML Copilot evaluation harness.

Spec: aml-regulatory-copilot | Task 20.1 | Requirements: 12.1, 12.2, 12.3, 12.4, 12.5.

This package contains the ground-truth dataset (``dataset/ground_truth.json``,
fully synthetic per Req 12.1) and a deterministic evaluation harness that runs the
dataset against the service layer with in-memory/fake collaborators (no live
Snowflake/Cortex required) and reports quality metrics.

The harness measures groundedness, citation precision/coverage, unsupported-claim
rate, refusal correctness, and metric-consistency (Req 12.2). Each metric is a
:class:`~evaluation.metrics.MetricDescriptor` declaring its definition, formula,
dataset, target, and acceptable/failure thresholds (Req 12.3). The report
separates ``demonstrated`` results from ``intended`` targets and never reports a
result for a test that was not executed (Req 12.4), mirroring the
``demonstrated``/``intended`` separation of the KPI model
(``app/models/telemetry.py``). Prompt-injection-resistance cases assert that
malicious instructions embedded in retrieved documents do not alter governed
behaviour (Req 12.5).
"""
