"""Service layer (interface + ``Impl`` pattern).

triage, metric, investigation, grounding, sar, approval, audit, guard, kpi,
glossary. See design.md "Components and Interfaces".
"""

from __future__ import annotations

from app.services.audit_repository import (
    AUDIT_RECORD_TABLE,
    AuditRecordRepository,
    DuplicateAuditRecordError,
    InMemoryAuditRecordRepository,
    SnowflakeAuditRecordRepository,
)
from app.services.audit_service import (
    ACTION_MUTATION_REJECTED,
    SYSTEM_ACTOR,
    AuditService,
    AuditServiceImpl,
    build_audit_record,
)
from app.services.auth_service import (
    ACTION_ACCESS_DENIED,
    AuthorizationDecision,
    AuthorizationError,
    AuthorizationResult,
    AuthService,
    AuthServiceImpl,
    ProtectedAction,
    Role,
    permitted_roles,
)
from app.services.grounding_service import (
    DEFAULT_GROUNDEDNESS_THRESHOLD,
    GateDecision,
    GateOutcome,
    GroundingResult,
    GroundingService,
    GroundingServiceImpl,
    apply_gate_to_answer,
    should_route_to_review,
)
from app.services.resilience import (
    ACTION_DEGRADATION_RECORDED,
    ACTION_STEP_COMPENSATED,
    HEALTHY_STATE,
    DegradationState,
    PersistFn,
    StepCompensationError,
    StepResult,
    UiState,
    commit_step,
    record_degradation,
)
from app.services.guard_service import (
    ACTION_INJECTION_REJECTED,
    ACTION_OPERATION_REJECTED,
    GuardService,
    GuardServiceImpl,
    OperationDecision,
    OperationValidation,
    ScanResult,
)
from app.services.approval_service import (
    ACTION_APPROVED,
    ACTION_COMPLETED,
    ACTION_DUPLICATE_SUPPRESSED,
    ACTION_FAILED,
    ACTION_REJECTED,
    ACTION_SUBMITTED_FOR_APPROVAL,
    MATERIAL_ACTION_FILE_SAR,
    ActionExecutor,
    ActionResult,
    ApprovalError,
    ApprovalItem,
    ApprovalRepository,
    ApprovalService,
    ApprovalServiceImpl,
    InMemoryApprovalRepository,
)

__all__ = [
    # audit service
    "AuditService",
    "AuditServiceImpl",
    "build_audit_record",
    "SYSTEM_ACTOR",
    "ACTION_MUTATION_REJECTED",
    # audit repository port + adapters
    "AuditRecordRepository",
    "InMemoryAuditRecordRepository",
    "SnowflakeAuditRecordRepository",
    "DuplicateAuditRecordError",
    "AUDIT_RECORD_TABLE",
    # guard service
    "GuardService",
    "GuardServiceImpl",
    "ScanResult",
    "OperationValidation",
    "OperationDecision",
    "ACTION_INJECTION_REJECTED",
    "ACTION_OPERATION_REJECTED",
    # grounding service
    "GroundingService",
    "GroundingServiceImpl",
    "GroundingResult",
    "GateDecision",
    "GateOutcome",
    "DEFAULT_GROUNDEDNESS_THRESHOLD",
    "apply_gate_to_answer",
    "should_route_to_review",
    # authorization service
    "AuthService",
    "AuthServiceImpl",
    "Role",
    "ProtectedAction",
    "AuthorizationDecision",
    "AuthorizationResult",
    "AuthorizationError",
    "permitted_roles",
    "ACTION_ACCESS_DENIED",
    # approval / HITL service
    "ApprovalService",
    "ApprovalServiceImpl",
    "ApprovalItem",
    "ActionResult",
    "ApprovalError",
    "ApprovalRepository",
    "InMemoryApprovalRepository",
    "ActionExecutor",
    "MATERIAL_ACTION_FILE_SAR",
    "ACTION_SUBMITTED_FOR_APPROVAL",
    "ACTION_APPROVED",
    "ACTION_REJECTED",
    "ACTION_COMPLETED",
    "ACTION_FAILED",
    "ACTION_DUPLICATE_SUPPRESSED",
    # workflow resilience (degradation + step/compensation)
    "UiState",
    "DegradationState",
    "HEALTHY_STATE",
    "record_degradation",
    "commit_step",
    "StepResult",
    "StepCompensationError",
    "PersistFn",
    "ACTION_DEGRADATION_RECORDED",
    "ACTION_STEP_COMPENSATED",
]
