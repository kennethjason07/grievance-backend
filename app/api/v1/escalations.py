"""
Escalation REST API Endpoints (Stage 10E).

Endpoints:
  GET   /escalations
  GET   /escalations/{escalation_id}
  PATCH /escalations/{escalation_id}/acknowledge
  PATCH /escalations/{escalation_id}/resolve
  PATCH /escalations/{escalation_id}/dismiss
  POST  /escalations/process-due
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.escalation import (
    EscalationDismissRequest,
    EscalationResolveRequest,
    EscalationResponse,
    PaginatedEscalationResponse,
    ProcessEscalationsRequest,
    ProcessEscalationsResponse,
)
from app.services import escalation_service

escalations_router = APIRouter(tags=["Escalations"])


@escalations_router.get(
    "/escalations",
    response_model=PaginatedEscalationResponse,
    summary="List escalations with filters and department scoping",
)
def list_escalations(
    grievance_id: Optional[uuid.UUID] = Query(None, description="Filter by grievance ID"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (PENDING, ACKNOWLEDGED, RESOLVED, DISMISSED)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_ESCALATIONS)),
) -> PaginatedEscalationResponse:
    items, total, total_pages = escalation_service.list_escalations(
        db=db,
        current_user=current_user,
        grievance_id=grievance_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    return PaginatedEscalationResponse(
        items=[EscalationResponse.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@escalations_router.get(
    "/escalations/{escalation_id}",
    response_model=EscalationResponse,
    summary="Retrieve single escalation by ID",
)
def get_escalation(
    escalation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_ESCALATIONS)),
) -> EscalationResponse:
    escalation = escalation_service.get_escalation_by_id(
        db=db,
        escalation_id=escalation_id,
        current_user=current_user,
    )
    return EscalationResponse.model_validate(escalation)


@escalations_router.patch(
    "/escalations/{escalation_id}/acknowledge",
    response_model=EscalationResponse,
    summary="Acknowledge an escalation (PENDING -> ACKNOWLEDGED)",
)
def acknowledge_escalation(
    escalation_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_ESCALATIONS)),
) -> EscalationResponse:
    ip = request.client.host if request.client else None
    escalation = escalation_service.acknowledge_escalation(
        db=db,
        escalation_id=escalation_id,
        current_user=current_user,
        ip_address=ip,
    )
    return EscalationResponse.model_validate(escalation)


@escalations_router.patch(
    "/escalations/{escalation_id}/resolve",
    response_model=EscalationResponse,
    summary="Resolve an active escalation (PENDING/ACKNOWLEDGED -> RESOLVED)",
)
def resolve_escalation(
    escalation_id: uuid.UUID,
    resolve_in: EscalationResolveRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.RESOLVE_ESCALATION)),
) -> EscalationResponse:
    ip = request.client.host if request.client else None
    escalation = escalation_service.resolve_escalation(
        db=db,
        escalation_id=escalation_id,
        resolve_in=resolve_in,
        current_user=current_user,
        ip_address=ip,
    )
    return EscalationResponse.model_validate(escalation)


@escalations_router.patch(
    "/escalations/{escalation_id}/dismiss",
    response_model=EscalationResponse,
    summary="Dismiss an active escalation (PENDING/ACKNOWLEDGED -> DISMISSED) with mandatory reason",
)
def dismiss_escalation(
    escalation_id: uuid.UUID,
    dismiss_in: EscalationDismissRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.RESOLVE_ESCALATION)),
) -> EscalationResponse:
    ip = request.client.host if request.client else None
    escalation = escalation_service.dismiss_escalation(
        db=db,
        escalation_id=escalation_id,
        dismiss_in=dismiss_in,
        current_user=current_user,
        ip_address=ip,
    )
    return EscalationResponse.model_validate(escalation)


@escalations_router.post(
    "/escalations/process-due",
    response_model=ProcessEscalationsResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger evaluation of due escalations",
)
def trigger_process_due_escalations(
    payload: Optional[ProcessEscalationsRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.ESCALATE_GRIEVANCE)),
) -> ProcessEscalationsResponse:
    ref_time = payload.reference_time if payload else None
    result = escalation_service.process_due_escalations(db=db, now=ref_time)
    return ProcessEscalationsResponse(
        evaluated_grievances=result["evaluated_grievances"],
        escalated_count=result["escalated_count"],
        skipped_or_existing=result["skipped_or_existing"],
        failures=result["failures"],
        evaluated_at=result["evaluated_at"],
    )

