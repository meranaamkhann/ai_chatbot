"""
Lightweight observability for Sibbu.

No APM service, no Prometheus, no paid tier — just structured log lines
with enough fields to actually answer "how slow are we, and why" from
whatever log viewer the free hosting tier gives you (Render's log tab
included). That's a deliberate scope cut, called out in AUDIT.md: real
metrics/alerting is the natural next step once this needs to run at a
scale where grepping logs stops being enough.

Every request gets a `request_id` (also returned as a response header, so
a frontend error report or a support conversation can reference it) and
one JSON-ish log line on completion with method, path, status, and
latency. Chat routes additionally log which topic the guard assigned and
how long the Gemini call itself took, separate from total request time —
that split is what actually tells you whether a slow response is the
model or your own code.
"""

from __future__ import annotations

import logging
import time
import uuid

from flask import g, request

logger = logging.getLogger("sibbu.request")


def _now() -> float:
    return time.perf_counter()


def start_request_timer() -> None:
    g.request_id = str(uuid.uuid4())[:8]
    g.request_start = _now()
    g.model_call_ms = None
    g.chat_topic = None


def record_model_latency(started_at: float) -> None:
    """Call after a Gemini API call completes to record just that portion of the request."""
    g.model_call_ms = round((_now() - started_at) * 1000, 1)


def record_topic(topic: str) -> None:
    g.chat_topic = topic


def log_request_completed(response):
    total_ms = round((_now() - g.get("request_start", _now())) * 1000, 1)
    request_id = g.get("request_id", "-")

    parts = [
        f"{request.method} {request.path} -> {response.status_code} in {total_ms}ms [{request_id}]"
    ]
    if g.get("chat_topic"):
        parts.append(f"topic={g.chat_topic}")
    if g.get("model_call_ms") is not None:
        parts.append(f"model_ms={g.model_call_ms}")

    logger.info(" ".join(parts))
    response.headers["X-Request-Id"] = request_id
    return response
