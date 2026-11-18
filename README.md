# GEAP Enterprise Incident Triage & Remediation Multi-Agent System

Built with **Google Agent Development Kit (ADK)** for the **Gemini Enterprise Agent Platform (GEAP) — AI in 5 Days** assessment.

## Problem & Solution
Enterprise incident responders waste critical minutes looking up customer SLA tiers, scrubbing PII from logs, drafting remediation runbooks, and coordinating approvals across teams. This multi-agent system automates triage, planning, and Human-in-the-Loop (HITL) gated execution:

`incident_triage_agent (gemini-2.5-flash)` → `remediation_planner_agent (gemini-2.5-pro)` → `gated_execution_agent (gemini-2.5-flash + HITL)`

## AgentOps Architecture Mapping
| Category | Criterion | Implementation |
| --- | --- | --- |
| **1. Tool & Interface** | Comprehensive Docstrings, Descriptive Naming, Explicit JSON Schemas, Guided Error Handling | `agent_app/tools.py` (Pydantic Input/Output models, descriptive names, `recovery_instruction`) |
| **2. Context & Memory** | Robust System Instructions, History Compaction, Persistent Session State, Async Memory Operations | `agent_app/agent.py` (`CONSTITUTION_SYSTEM_PROMPT`), `agent_app/memory.py` (`PersistentSessionStore`, `compact_history`, `schedule_background_memory_consolidation`) |
| **3. Orchestration & Logic** | Multi-Agent Patterns, Strategic Model Routing, Guardrails & Policy Plugins, HITL Hooks | `agent_app/agent.py` (`SequentialAgent`, Flash/Pro routing), `agent_app/guardrails.py` (`adk_before_model_callback`, `adk_before_tool_callback`, `require_human_approval`) |
| **4. Observability & Tracing** | Structured JSON Logging, Intent vs. Outcome Capture, Distributed Tracing, PII Redaction | `agent_app/observability.py` (`JsonFormatter`, `log_intent_vs_outcome`, OpenTelemetry `traced_operation`, Cloud DLP + regex `redact_pii`) |
| **5. Infrastructure & CI/CD** | Automated Evaluation Suites, Infrastructure as Code, Secure Secret Management | `evals/golden_evalset.json`, `tests/test_agent_eval.py`, `terraform/main.tf`, `.github/workflows/ci.yml`, `agent_app/config.py` (`SecretManagerServiceClient`) |

## Quick Start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```
