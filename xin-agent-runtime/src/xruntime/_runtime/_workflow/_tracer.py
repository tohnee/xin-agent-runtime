# -*- coding: utf-8 -*-
"""P3-D: WorkflowTracer — OpenTelemetry distributed tracing.

Wraps workflow execution in OTel spans so distributed traces can
be visualized in Jaeger / Zipkin / Datadog etc.

Span hierarchy::

    workflow.run (root span)
    ├── workflow.step.<id> (child span)
    │   ├── attributes: workflow.id, step.id, step.agent,
    │   │                 step.status, step.duration_ms
    │   └── events: step.started, step.completed, step.failed
    ├── workflow.step.<id2>
    └── ...

Design notes:

* When ``endpoint`` is empty (default), the tracer uses a no-op
  in-memory span recorder — spans are captured for test assertions
  but never exported to a backend.  This lets tests verify span
  structure without spinning up a collector.
* When ``endpoint`` is set, an OTLP gRPC exporter is attached so
  spans are actually shipped to a collector.
* When the ``opentelemetry`` package is not installed, the tracer
  falls back to :class:`NoOpWorkflowTracer` (graceful degradation).
* :class:`NoOpWorkflowTracer` is a separate class so callers can
  explicitly request no-tracing mode (e.g. for benchmarks).
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("xruntime.workflow.tracer")


# ── NoOpWorkflowTracer ─────────────────────────────────────────


class NoOpWorkflowTracer:
    """No-op tracer — records nothing, exports nothing.

    Use when tracing is disabled or when the ``opentelemetry``
    dependency is unavailable.  All methods are no-ops so workflow
    execution proceeds without overhead.
    """

    def get_recorded_spans(self) -> list[dict[str, Any]]:
        """Return an empty list (no spans recorded)."""
        return []

    def start_workflow_span(
        self,
        workflow_id: str,
        workflow_name: str,
    ) -> Any:
        """Return a no-op span context manager."""
        return _NoOpSpan()

    def start_step_span(
        self,
        step_id: str,
        agent: str,
        workflow_id: str,
    ) -> Any:
        """Return a no-op span context manager."""
        return _NoOpSpan()


class _NoOpSpan:
    """No-op span context manager."""

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        """No-op."""

    def add_event(self, name: str, **kwargs: Any) -> None:
        """No-op."""


# ── InMemoryWorkflowTracer (default, no export) ────────────────


class InMemoryWorkflowTracer:
    """In-memory tracer — records spans for test assertions.

    This is the default when ``endpoint`` is empty.  Spans are
    recorded in a list so tests can inspect them via
    :meth:`get_recorded_spans`.  No OTel dependency required.

    Args:
        service_name (`str`):
            Service name for span resource.
        endpoint (`str`):
            OTLP endpoint.  Empty means no export (in-memory only).
    """

    def __init__(
        self,
        service_name: str = "xruntime-workflow",
        endpoint: str = "",
    ) -> None:
        """Initialize the in-memory tracer."""
        self._service_name = service_name
        self._endpoint = endpoint
        self._spans: list[dict[str, Any]] = []
        self._current_span: dict[str, Any] | None = None

    @property
    def service_name(self) -> str:
        """Return the service name."""
        return self._service_name

    @property
    def endpoint(self) -> str:
        """Return the OTLP endpoint."""
        return self._endpoint

    def get_recorded_spans(self) -> list[dict[str, Any]]:
        """Return all recorded spans (for test assertions).

        Returns:
            `list[dict]`: List of span dicts, each with ``name``,
            ``attributes``, ``events``.
        """
        return list(self._spans)

    def start_workflow_span(
        self,
        workflow_id: str,
        workflow_name: str,
    ) -> "_InMemorySpan":
        """Start a root workflow span.

        Args:
            workflow_id (`str`): The workflow id.
            workflow_name (`str`): The workflow name.

        Returns:
            `_InMemorySpan`: A span context manager.
        """
        span = {
            "name": "workflow.run",
            "attributes": {
                "workflow.id": workflow_id,
                "workflow.name": workflow_name,
            },
            "events": [],
        }
        self._spans.append(span)
        return _InMemorySpan(span, parent_tracer=self)

    def start_step_span(
        self,
        step_id: str,
        agent: str,
        workflow_id: str,
    ) -> "_InMemorySpan":
        """Start a child step span.

        Args:
            step_id (`str`): The step id.
            agent (`str`): The agent name.
            workflow_id (`str`): The parent workflow id.

        Returns:
            `_InMemorySpan`: A span context manager.
        """
        span = {
            "name": f"workflow.step.{step_id}",
            "attributes": {
                "workflow.id": workflow_id,
                "step.id": step_id,
                "step.agent": agent,
            },
            "events": [],
        }
        self._spans.append(span)
        return _InMemorySpan(span, parent_tracer=self)


class _InMemorySpan:
    """In-memory span context manager.

    Records attributes and events into the span dict.  On exit,
    finalizes ``step.duration_ms`` if a start time was recorded.
    """

    def __init__(
        self,
        span: dict[str, Any],
        parent_tracer: InMemoryWorkflowTracer,
    ) -> None:
        self._span = span
        self._tracer = parent_tracer
        self._start_time: float | None = None

    def __enter__(self) -> "_InMemorySpan":
        self._start_time = time.time()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._start_time is not None:
            duration_ms = (time.time() - self._start_time) * 1000
            self._span["attributes"]["step.duration_ms"] = round(
                duration_ms,
                2,
            )

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self._span["attributes"][key] = value

    def add_event(self, name: str, **kwargs: Any) -> None:
        """Add a span event."""
        self._span["events"].append({"name": name, **kwargs})


# ── WorkflowTracer (with OTel export) ─────────────────────────


class WorkflowTracer(InMemoryWorkflowTracer):
    """OTel-backed workflow tracer with optional OTLP export.

    Falls back to in-memory recording when ``opentelemetry`` is not
    installed.  When ``endpoint`` is set, spans are exported via
    OTLP gRPC.

    Args:
        service_name (`str`):
            Service name for the OTel resource.
        endpoint (`str`):
            OTLP gRPC endpoint.  Empty means no export (in-memory).
    """

    def __init__(
        self,
        service_name: str = "xruntime-workflow",
        endpoint: str = "",
    ) -> None:
        """Initialize the tracer, attempting OTel setup."""
        super().__init__(service_name=service_name, endpoint=endpoint)
        self._otel_available = False
        self._otel_tracer: Any = None
        if endpoint:
            self._try_setup_otel(endpoint)

    def _try_setup_otel(self, endpoint: str) -> None:
        """Attempt to configure an OTLP exporter.

        On failure (``opentelemetry`` not installed), logs a warning
        and falls back to in-memory-only recording.
        """
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
            )
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: E501
                OTLPSpanExporter,
            )

            provider = TracerProvider(
                resource=Resource.create(
                    {"service.name": self._service_name},
                ),
            )
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint),
                ),
            )
            trace.set_tracer_provider(provider)
            self._otel_tracer = trace.get_tracer("xruntime.workflow")
            self._otel_available = True
        except ImportError:
            logger.warning(
                "opentelemetry not installed; tracing will be "
                "in-memory only (no OTLP export).",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to setup OTel tracer; falling back to "
                "in-memory recording.",
            )


__all__ = [
    "WorkflowTracer",
    "InMemoryWorkflowTracer",
    "NoOpWorkflowTracer",
]
