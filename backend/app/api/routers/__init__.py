"""FastAPI routers for the SentinelAML Copilot application layer (task 18).

Each module exposes an ``APIRouter`` wired to the service layer through the
dependency-injection seam in :mod:`app.api.dependencies`; they are registered in
:func:`app.main.create_app`. RBAC + the purpose check are enforced by the
``require_action`` dependency (Req 9.2); live Command_Centre / approval-queue
updates are broadcast through :mod:`app.api.live` (Req 10.5, 11.1).
"""
