"""Structured JSON logging, PII redaction, OpenTelemetry tracing, and intent/outcome capture."""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

try:
    from google.cloud import dlp_v2
except ImportError:  # pragma: no cover - optional in lightweight test environments
    dlp_v2 = None

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE_NUMBER", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("API_KEY", re.compile(r"\b(?:AIza[0-9A-Za-z\-_]{20,}|sk-[A-Za-z0-9]{20,})\b")),
]


def configure_tracing(service_name: str = "geap-enterprise-ops-agent") -> trace.Tracer:
    """Initialize OpenTelemetry TracerProvider and return a service tracer."""
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


def redact_pii(text: str, project_id: str | None = None, use_cloud_dlp: bool = False) -> str:
    """Scrub sensitive PII (emails, SSNs, phone numbers, API keys) before logging or storage.

    Uses Google Cloud DLP ``deidentify_content`` when enabled, with deterministic regex
    redaction always applied as a defense-in-depth layer.
    """
    sanitized = text
    if use_cloud_dlp and dlp_v2 is not None and project_id:
        try:
            dlp = dlp_v2.DlpServiceClient()
            parent = f"projects/{project_id}"
            inspect_config = {
                "info_types": [
                    {"name": "EMAIL_ADDRESS"},
                    {"name": "PHONE_NUMBER"},
                    {"name": "US_SOCIAL_SECURITY_NUMBER"},
                ]
            }
            deidentify_config = {
                "info_type_transformations": {
                    "transformations": [
                        {"primitive_transformation": {"replace_with_info_type_config": {}}}
                    ]
                }
            }
            response = dlp.deidentify_content(
                request={
                    "parent": parent,
                    "inspect_config": inspect_config,
                    "deidentify_config": deidentify_config,
                    "item": {"value": sanitized},
                }
            )
            sanitized = response.item.value
        except Exception:
            # Fall through to deterministic regex scrubbing.
            pass

    for label, pattern in _PII_PATTERNS:
        sanitized = pattern.sub(f"[REDACTED_{label}]", sanitized)
    return sanitized


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line structured JSON with PII scrubbing."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": redact_pii(record.getMessage()),
        }
        extra_fields = getattr(record, "structured_fields", None)
        if isinstance(extra_fields, dict):
            payload["metadata"] = json.loads(redact_pii(json.dumps(extra_fields, default=str)))

        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        return json.dumps(payload)


def get_structured_logger(name: str = "geap.agent") -> logging.Logger:
    """Return a logger configured with ``JsonFormatter``."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_intent_vs_outcome(
    logger: logging.Logger,
    actor: str,
    intended_action: str,
    actual_outcome: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record both the agent's intended action and actual outcome in structured JSON logs."""
    record = {
        "actor": actor,
        "intended_action": intended_action,
        "actual_outcome": actual_outcome,
        "status": status,
        "details": details or {},
    }
    logger.info(
        "intent_vs_outcome actor=%s status=%s",
        actor,
        status,
        extra={"structured_fields": record},
    )
    return record


@contextmanager
def traced_operation(tracer: trace.Tracer, span_name: str, attributes: dict[str, Any] | None = None) -> Iterator[trace.Span]:
    """Context manager wrapping an agent or tool step in an OpenTelemetry span."""
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, str(value))
        yield span
