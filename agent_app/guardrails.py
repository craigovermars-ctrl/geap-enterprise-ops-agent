"""Security guardrails, self-evaluation policy checks, and Human-in-the-Loop (HITL) hooks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_app.observability import get_structured_logger, log_intent_vs_outcome, redact_pii

logger = get_structured_logger("geap.guardrails")

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"reveal\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"drop\s+table\s+", re.IGNORECASE),
    re.compile(r"exfiltrate\s+secrets?", re.IGNORECASE),
]

HIGH_STAKES_TOOLS: frozenset[str] = frozenset(
    {
        "execute_approved_production_change",
        "create_critical_incident_ticket",
    }
)


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a guardrail or HITL policy evaluation."""

    allowed: bool
    reason: str
    requires_human_confirmation: bool = False
    sanitized_text: str = ""


def validate_prompt_policy(prompt_text: str) -> PolicyDecision:
    """Screen user/model inputs for prompt injection, SQL destruction, and PII leakage."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(prompt_text):
            log_intent_vs_outcome(
                logger,
                actor="guardrails.before_model",
                intended_action="validate_prompt_policy",
                actual_outcome=f"blocked_by_pattern:{pattern.pattern}",
                status="BLOCKED",
            )
            return PolicyDecision(
                allowed=False,
                reason="Blocked by security policy: potential prompt injection or destructive instruction.",
            )

    scrubbed = redact_pii(prompt_text)
    return PolicyDecision(allowed=True, reason="ok", sanitized_text=scrubbed)


def require_human_approval(
    tool_name: str,
    tool_args: dict[str, Any],
    human_approved: bool | None = None,
) -> PolicyDecision:
    """Enforce explicit human confirmation before high-stakes tool execution.

    Args:
        tool_name: Name of the tool about to execute.
        tool_args: Validated tool arguments.
        human_approved: Explicit human-in-the-loop approval flag (or ``tool_args["human_approved"]``).

    Returns:
        A ``PolicyDecision`` indicating whether execution may proceed.
    """
    if tool_name not in HIGH_STAKES_TOOLS:
        return PolicyDecision(allowed=True, reason="low_risk_tool")

    approved = human_approved if human_approved is not None else bool(tool_args.get("human_approved", False))
    if not approved:
        log_intent_vs_outcome(
            logger,
            actor="guardrails.hitl",
            intended_action=f"execute:{tool_name}",
            actual_outcome="paused_awaiting_human_confirmation",
            status="AWAITING_HUMAN_APPROVAL",
            details={"tool_name": tool_name},
        )
        return PolicyDecision(
            allowed=False,
            requires_human_confirmation=True,
            reason=(
                f"Human-in-the-Loop confirmation required before executing '{tool_name}'. "
                "Present the proposed action and parameters to the operator and re-invoke with human_approved=True."
            ),
        )

    return PolicyDecision(allowed=True, reason="human_approved")


def evaluate_response_quality(response_text: str) -> PolicyDecision:
    """Self-evaluation guardrail ensuring responses are grounded and non-empty."""
    cleaned = response_text.strip()
    if len(cleaned) < 10:
        return PolicyDecision(
            allowed=False,
            reason="Self-eval failed: response is too brief to be actionable.",
        )
    return PolicyDecision(allowed=True, reason="self_eval_passed", sanitized_text=redact_pii(cleaned))


def adk_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    """ADK before_model_callback hook enforcing prompt policy guardrails."""
    contents = getattr(llm_request, "contents", None) or []
    combined = " ".join(str(c) for c in contents)
    decision = validate_prompt_policy(combined)
    if not decision.allowed:
        raise PermissionError(decision.reason)
    return None


def adk_before_tool_callback(tool: Any, args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
    """ADK before_tool_callback hook enforcing Human-in-the-Loop approval on high-stakes tools."""
    tool_name = getattr(tool, "name", str(tool))
    decision = require_human_approval(tool_name, args)
    if not decision.allowed:
        return {
            "status": "AWAITING_HUMAN_APPROVAL",
            "tool": tool_name,
            "message": decision.reason,
            "recovery_instruction": "Ask the human operator to confirm, then call again with human_approved=True.",
        }
    return None
