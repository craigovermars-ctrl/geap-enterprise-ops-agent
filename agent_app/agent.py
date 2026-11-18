"""Multi-agent ADK orchestration with strategic model routing and constitutional prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_app.config import AgentConfig
from agent_app.guardrails import adk_before_model_callback, adk_before_tool_callback
from agent_app.tools import (
    create_critical_incident_ticket,
    draft_remediation_runbook,
    execute_approved_production_change,
    query_customer_sla_ledger,
)

try:
    from google.adk.agents import LlmAgent, SequentialAgent
except ImportError:  # pragma: no cover - lightweight fallback when google-adk isn't installed locally
    @dataclass
    class LlmAgent:  # type: ignore[no-redef]
        """Fallback representation of ADK LlmAgent for offline unit testing."""

        name: str
        model: str
        description: str
        instruction: str
        tools: list[Any] = field(default_factory=list)
        before_model_callback: Any = None
        before_tool_callback: Any = None

    @dataclass
    class SequentialAgent:  # type: ignore[no-redef]
        """Fallback representation of ADK SequentialAgent for offline unit testing."""

        name: str
        description: str
        sub_agents: list[Any] = field(default_factory=list)


CONSTITUTION_SYSTEM_PROMPT = """
You are the GEAP Enterprise Incident Triage & Remediation Coordinator.
CONSTITUTION & CONSTRAINTS:
1. Never fabricate customer SLA tiers, error budgets, or ticket IDs—always call `query_customer_sla_ledger` first.
2. Never expose PII (emails, phone numbers, SSNs, API keys) in logs, memory, or final responses.
3. Never execute high-stakes mutations (`create_critical_incident_ticket`, `execute_approved_production_change`)
   without explicit Human-in-the-Loop (HITL) operator confirmation (`human_approved=True`).
4. If any tool returns `status="ERROR"`, follow its `recovery_instruction` rather than guessing.
5. Always include a verified rollback command when proposing a production change.
""".strip()


def build_agent_system(config: AgentConfig | None = None) -> SequentialAgent:
    """Construct the multi-agent ADK pipeline with strategic model routing."""
    cfg = config or AgentConfig()

    # Fast Flash model for low-latency intake and deterministic SLA lookups.
    triage_agent = LlmAgent(
        name="incident_triage_agent",
        model=cfg.fast_model,
        description="Rapidly classifies incoming incidents and retrieves customer SLA & error budget.",
        instruction=(
            f"{CONSTITUTION_SYSTEM_PROMPT}\n\n"
            "ROLE: Intake & Triage. Query `query_customer_sla_ledger` and summarize severity and SLA impact."
        ),
        tools=[query_customer_sla_ledger],
        before_model_callback=adk_before_model_callback,
        before_tool_callback=adk_before_tool_callback,
    )

    # Pro model for complex root-cause reasoning and remediation runbook synthesis.
    planner_agent = LlmAgent(
        name="remediation_planner_agent",
        model=cfg.planning_model,
        description="Synthesizes deep root-cause analysis and a step-by-step remediation runbook.",
        instruction=(
            f"{CONSTITUTION_SYSTEM_PROMPT}\n\n"
            "ROLE: Remediation Architect. Use `draft_remediation_runbook` to produce a safe, rollback-ready plan."
        ),
        tools=[draft_remediation_runbook],
        before_model_callback=adk_before_model_callback,
        before_tool_callback=adk_before_tool_callback,
    )

    # Fast Flash model with HITL enforcement for gated ticket creation & production changes.
    execution_agent = LlmAgent(
        name="gated_execution_agent",
        model=cfg.fast_model,
        description="Executes incident ticketing and production changes strictly under HITL confirmation.",
        instruction=(
            f"{CONSTITUTION_SYSTEM_PROMPT}\n\n"
            "ROLE: Gated Executor. Propose actions clearly and require `human_approved=True` before mutating state."
        ),
        tools=[create_critical_incident_ticket, execute_approved_production_change],
        before_model_callback=adk_before_model_callback,
        before_tool_callback=adk_before_tool_callback,
    )

    return SequentialAgent(
        name="geap_enterprise_ops_coordinator",
        description="Sequential multi-agent workflow: Triage (Flash) -> Plan (Pro) -> Gated Execution (Flash + HITL).",
        sub_agents=[triage_agent, planner_agent, execution_agent],
    )


root_agent = build_agent_system()
