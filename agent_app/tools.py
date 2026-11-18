"""Domain-specific ADK tools with strict Pydantic schemas and guided error handling."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agent_app.guardrails import require_human_approval
from agent_app.observability import configure_tracing, get_structured_logger, log_intent_vs_outcome, traced_operation

logger = get_structured_logger("geap.tools")
tracer = configure_tracing()


class CustomerSlaQueryInput(BaseModel):
    """Input schema for querying a customer's SLA tier and active incident budget."""

    customer_id: str = Field(..., min_length=3, max_length=64, description="Canonical customer identifier (e.g. 'cust-verizon').")
    region: Literal["us-central1", "us-east4", "europe-west1"] = Field(
        default="us-central1",
        description="Primary GCP deployment region for SLA lookup.",
    )


class CustomerSlaQueryOutput(BaseModel):
    """Output schema returned by ``query_customer_sla_ledger``."""

    status: Literal["OK", "ERROR"]
    customer_id: str
    sla_tier: str | None = None
    error_budget_remaining_pct: float | None = None
    recovery_instruction: str | None = None


class IncidentTicketInput(BaseModel):
    """Input schema for creating a critical production incident ticket."""

    customer_id: str = Field(..., min_length=3, description="Affected customer ID.")
    severity: Literal["P0", "P1", "P2"] = Field(..., description="Incident severity level.")
    summary: str = Field(..., min_length=10, max_length=300, description="Concise incident summary.")
    human_approved: bool = Field(default=False, description="Explicit operator confirmation for P0/P1 ticket creation.")


class IncidentTicketOutput(BaseModel):
    """Output schema returned by ``create_critical_incident_ticket``."""

    status: Literal["CREATED", "AWAITING_HUMAN_APPROVAL", "ERROR"]
    ticket_id: str | None = None
    message: str
    recovery_instruction: str | None = None


class RemediationRunbookInput(BaseModel):
    """Input schema for generating a structured remediation runbook."""

    service_name: str = Field(..., min_length=2, description="Target microservice or Agent Engine deployment.")
    symptom: str = Field(..., min_length=5, description="Observed failure symptom or error code.")


class RemediationRunbookOutput(BaseModel):
    """Output schema returned by ``draft_remediation_runbook``."""

    status: Literal["OK", "ERROR"]
    service_name: str
    steps: list[str] = Field(default_factory=list)
    recovery_instruction: str | None = None


class ProductionChangeInput(BaseModel):
    """Input schema for executing a gated production configuration or traffic change."""

    service_name: str = Field(..., min_length=2, description="Target production service.")
    change_summary: str = Field(..., min_length=10, description="Exact change to apply.")
    rollback_command: str = Field(..., min_length=5, description="Verified rollback command.")
    human_approved: bool = Field(default=False, description="Must be True after explicit operator sign-off.")


class ProductionChangeOutput(BaseModel):
    """Output schema returned by ``execute_approved_production_change``."""

    status: Literal["APPLIED", "AWAITING_HUMAN_APPROVAL", "ERROR"]
    change_id: str | None = None
    message: str
    recovery_instruction: str | None = None


_SLA_LEDGER: dict[str, dict[str, object]] = {
    "cust-verizon": {"sla_tier": "Platinum-99.99", "error_budget_remaining_pct": 84.5},
    "cust-bell": {"sla_tier": "Enterprise-99.95", "error_budget_remaining_pct": 62.0},
    "cust-lumen": {"sla_tier": "Enterprise-99.9", "error_budget_remaining_pct": 41.2},
}


def query_customer_sla_ledger(customer_id: str, region: str = "us-central1") -> dict[str, object]:
    """Query the persistent customer SLA ledger for tier and remaining error budget.

    Args:
        customer_id: Canonical customer identifier (e.g. ``cust-verizon``).
        region: Primary GCP deployment region (``us-central1``, ``us-east4``, or ``europe-west1``).

    Returns:
        Structured dictionary matching ``CustomerSlaQueryOutput`` with guided recovery
        instructions if the customer ID or region is invalid.
    """
    with traced_operation(tracer, "tool.query_customer_sla_ledger", {"customer_id": customer_id, "region": region}):
        try:
            validated = CustomerSlaQueryInput(customer_id=customer_id, region=region)  # type: ignore[arg-type]
        except ValidationError as exc:
            out = CustomerSlaQueryOutput(
                status="ERROR",
                customer_id=customer_id,
                recovery_instruction=f"Fix input schema and retry: {exc.errors()[0]['msg']}",
            )
            log_intent_vs_outcome(logger, "tool.query_customer_sla_ledger", "lookup_sla", "validation_error", "ERROR")
            return out.model_dump()

        record = _SLA_LEDGER.get(validated.customer_id)
        if not record:
            out = CustomerSlaQueryOutput(
                status="ERROR",
                customer_id=validated.customer_id,
                recovery_instruction=(
                    f"Customer '{validated.customer_id}' not found. "
                    f"Valid customer IDs include: {', '.join(sorted(_SLA_LEDGER.keys()))}."
                ),
            )
            log_intent_vs_outcome(logger, "tool.query_customer_sla_ledger", "lookup_sla", "not_found", "ERROR")
            return out.model_dump()

        out = CustomerSlaQueryOutput(
            status="OK",
            customer_id=validated.customer_id,
            sla_tier=str(record["sla_tier"]),
            error_budget_remaining_pct=float(record["error_budget_remaining_pct"]),  # type: ignore[arg-type]
        )
        log_intent_vs_outcome(logger, "tool.query_customer_sla_ledger", "lookup_sla", "returned_sla_record", "OK")
        return out.model_dump()


def create_critical_incident_ticket(
    customer_id: str,
    severity: str,
    summary: str,
    human_approved: bool = False,
) -> dict[str, object]:
    """Create a P0/P1/P2 critical incident ticket after Human-in-the-Loop confirmation.

    Args:
        customer_id: Canonical customer identifier.
        severity: Incident severity (``P0``, ``P1``, or ``P2``).
        summary: Clear, 10-300 character summary of the incident.
        human_approved: Must be ``True`` after explicit operator confirmation.

    Returns:
        Structured dictionary matching ``IncidentTicketOutput``.
    """
    with traced_operation(tracer, "tool.create_critical_incident_ticket", {"customer_id": customer_id, "severity": severity}):
        try:
            validated = IncidentTicketInput(
                customer_id=customer_id,
                severity=severity,  # type: ignore[arg-type]
                summary=summary,
                human_approved=human_approved,
            )
        except ValidationError as exc:
            return IncidentTicketOutput(
                status="ERROR",
                message="Invalid ticket parameters.",
                recovery_instruction=f"Correct parameters and retry: {exc.errors()[0]['msg']}",
            ).model_dump()

        decision = require_human_approval(
            "create_critical_incident_ticket",
            validated.model_dump(),
            human_approved=validated.human_approved,
        )
        if not decision.allowed:
            return IncidentTicketOutput(
                status="AWAITING_HUMAN_APPROVAL",
                message=decision.reason,
                recovery_instruction="Ask the operator to confirm ticket creation, then retry with human_approved=True.",
            ).model_dump()

        ticket_id = f"INC-{validated.severity}-{abs(hash(validated.customer_id + validated.summary)) % 10000:04d}"
        log_intent_vs_outcome(
            logger,
            "tool.create_critical_incident_ticket",
            f"create_{validated.severity}_ticket",
            f"created:{ticket_id}",
            "CREATED",
        )
        return IncidentTicketOutput(
            status="CREATED",
            ticket_id=ticket_id,
            message=f"Created {validated.severity} incident ticket {ticket_id} for {validated.customer_id}.",
        ).model_dump()


def draft_remediation_runbook(service_name: str, symptom: str) -> dict[str, object]:
    """Draft a deterministic step-by-step remediation runbook for a failing service.

    Args:
        service_name: Name of the affected service or Agent Engine endpoint.
        symptom: Observed error code or behavioral symptom.

    Returns:
        Structured dictionary matching ``RemediationRunbookOutput``.
    """
    with traced_operation(tracer, "tool.draft_remediation_runbook", {"service_name": service_name}):
        try:
            validated = RemediationRunbookInput(service_name=service_name, symptom=symptom)
        except ValidationError as exc:
            return RemediationRunbookOutput(
                status="ERROR",
                service_name=service_name,
                recovery_instruction=f"Provide a valid service_name and symptom: {exc.errors()[0]['msg']}",
            ).model_dump()

        steps = [
            f"1. Inspect OpenTelemetry traces and structured JSON logs for '{validated.service_name}' matching '{validated.symptom}'.",
            "2. Verify Secret Manager bindings and IAM roles without exposing raw credentials.",
            "3. Validate recent config/traffic rollouts and prepare rollback command before mutating production.",
            "4. Request explicit Human-in-the-Loop approval before executing any production change.",
        ]
        log_intent_vs_outcome(logger, "tool.draft_remediation_runbook", "draft_runbook", "generated_4_steps", "OK")
        return RemediationRunbookOutput(status="OK", service_name=validated.service_name, steps=steps).model_dump()


def execute_approved_production_change(
    service_name: str,
    change_summary: str,
    rollback_command: str,
    human_approved: bool = False,
) -> dict[str, object]:
    """Apply a production configuration or traffic change only after HITL approval.

    Args:
        service_name: Target production service.
        change_summary: Concise description of the change to apply.
        rollback_command: Explicit rollback command to revert the change if needed.
        human_approved: Must be ``True`` after operator confirmation.

    Returns:
        Structured dictionary matching ``ProductionChangeOutput``.
    """
    with traced_operation(tracer, "tool.execute_approved_production_change", {"service_name": service_name}):
        try:
            validated = ProductionChangeInput(
                service_name=service_name,
                change_summary=change_summary,
                rollback_command=rollback_command,
                human_approved=human_approved,
            )
        except ValidationError as exc:
            return ProductionChangeOutput(
                status="ERROR",
                message="Invalid production change parameters.",
                recovery_instruction=f"Fix validation error and retry: {exc.errors()[0]['msg']}",
            ).model_dump()

        decision = require_human_approval(
            "execute_approved_production_change",
            validated.model_dump(),
            human_approved=validated.human_approved,
        )
        if not decision.allowed:
            return ProductionChangeOutput(
                status="AWAITING_HUMAN_APPROVAL",
                message=decision.reason,
                recovery_instruction="Obtain operator sign-off and re-run with human_approved=True.",
            ).model_dump()

        change_id = f"CHG-{abs(hash(validated.service_name + validated.change_summary)) % 100000:05d}"
        log_intent_vs_outcome(
            logger,
            "tool.execute_approved_production_change",
            f"apply_change:{validated.service_name}",
            f"applied:{change_id}",
            "APPLIED",
        )
        return ProductionChangeOutput(
            status="APPLIED",
            change_id=change_id,
            message=f"Applied change {change_id} to {validated.service_name}. Rollback ready: {validated.rollback_command}",
        ).model_dump()
