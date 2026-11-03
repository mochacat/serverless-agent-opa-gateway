"""Injected metric sink — decision/deny-rate/eval-latency counters (O5).

The gateway holds **zero coupling** to OTel or AWS (REQUIREMENTS §5.4): it
writes counters through this dependency-injected :class:`MetricsSink`. A thin
OTLP exporter is a drop-in implementation of the same protocol, attached per
environment *after* the audit write and never on the critical path.

Core schema (docs/observability.md): counters ``decisions``, ``denials`` and a
gauge ``policy_eval_ms``. Attribute names follow the OTel ``policy_decision``
span so the numbers reconcile.
"""

from __future__ import annotations

import time
from typing import Any, Protocol


class MetricsSink(Protocol):
    """Counters + gauges for policy decisions (swappable, injected)."""

    def incr(self, metric: str, amount: int = 1, **attrs: Any) -> None:
        """Add ``amount`` to a monotonic counter, e.g. ``decisions``/``denials``."""
        ...

    def gauge(self, metric: str, value: float, **attrs: Any) -> None:
        """Record a gauge sample, e.g. ``policy_eval_ms``."""
        ...

    def timed(self, metric: str, **attrs: Any) -> MetricsTimer:
        """Return a context manager that records elapsed ms on exit."""
        ...


class InMemoryMetricsSink:
    """Test/local sink: accumulates counters and gauge samples in-process."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.gauges: list[tuple[str, float, dict[str, Any]]] = []

    def incr(self, metric: str, amount: int = 1, **attrs: Any) -> None:
        self.counters[metric] = self.counters.get(metric, 0) + amount

    def gauge(self, metric: str, value: float, **attrs: Any) -> None:
        self.gauges.append((metric, value, attrs))

    def timed(self, metric: str, **attrs: Any) -> MetricsTimer:
        return MetricsTimer(self, metric, attrs)

    def counter(self, metric: str) -> int:
        return self.counters.get(metric, 0)

    def gauge_by_name(self, metric: str) -> list[float]:
        return [value for name, value, _ in self.gauges if name == metric]


class MetricsTimer:
    """Records ``policy_eval_ms``-style duration gauges on ``__exit__``."""

    def __init__(self, sink: MetricsSink, metric: str, attrs: dict[str, Any]) -> None:
        self._sink = sink
        self._metric = metric
        self._attrs = attrs
        self._start = time.perf_counter()

    def __enter__(self) -> MetricsTimer:
        return self

    def __exit__(self, *exc) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self._sink.gauge(self._metric, elapsed_ms, **self._attrs)


__all__ = ["InMemoryMetricsSink", "MetricsSink", "MetricsTimer"]
