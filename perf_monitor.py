"""Lightweight structured performance monitoring for MistSiteDashboard."""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import json
import logging
import os
import time
import tracemalloc
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

PERF_LOGGER_NAME = "msd.perf"
PERF_LOGGER = logging.getLogger(PERF_LOGGER_NAME)

SCHEMA_FIELDS = (
    "event_name",
    "module",
    "function",
    "route",
    "method",
    "site_id",
    "duration_ms",
    "network_ms",
    "processing_ms",
    "call_count",
    "items_returned",
    "payload_bytes",
    "status",
    "cache_hit",
    "retry_count",
    "error_class",
    "peak_bytes",
    "timestamp_utc",
)

_api_call_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "msd_perf_api_call_context", default=None
)


def perf_enabled() -> bool:
    """Return whether performance monitoring is enabled."""
    return os.getenv("PERF_MONITORING", "1").strip().lower() not in {"0", "false", "no", "off"}


def tracemalloc_enabled() -> bool:
    """Return whether peak allocation capture is enabled."""
    return os.getenv("PERF_TRACEMALLOC", "0").strip().lower() in {"1", "true", "yes", "on"}


def start_peak_trace() -> bool:
    """Start tracemalloc for this timing scope when requested."""
    if not perf_enabled() or not tracemalloc_enabled():
        return False
    if not tracemalloc.is_tracing():
        tracemalloc.start()
        return True
    tracemalloc.reset_peak()
    return False


def finish_peak_trace(started: bool) -> int | None:
    """Return peak bytes for the active timing scope."""
    if not tracemalloc_enabled() or not tracemalloc.is_tracing():
        return None
    _, peak = tracemalloc.get_traced_memory()
    if started:
        tracemalloc.stop()
    return peak


def now_utc_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def duration_ms_since(start: float) -> float:
    """Return elapsed wall time in milliseconds."""
    return round((time.perf_counter() - start) * 1000, 3)


def response_payload_size(response: Any) -> int | None:
    """Best-effort response payload byte count without reading secrets."""
    content = getattr(response, "content", None)
    if content is not None:
        try:
            return len(content)
        except TypeError:
            pass
    text = getattr(response, "text", None)
    if text is not None:
        return len(str(text).encode("utf-8"))
    return None


def endpoint_path_from_response(response: Any) -> str | None:
    """Extract a path-only endpoint from a requests response."""
    url = getattr(response, "url", None)
    if not url:
        request_obj = getattr(response, "request", None)
        url = getattr(request_obj, "url", None)
    if not url:
        return None
    try:
        return urlsplit(str(url)).path
    except ValueError:
        return None


def emit_perf_event(**fields: Any) -> dict[str, Any] | None:
    """Emit one JSON log line that follows the shared performance schema."""
    if not perf_enabled():
        return None
    event = {field: fields.get(field) for field in SCHEMA_FIELDS}
    event["timestamp_utc"] = event["timestamp_utc"] or now_utc_iso()
    for key, value in fields.items():
        if key not in event:
            event[key] = value
    PERF_LOGGER.info(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str))
    return event


def begin_api_context() -> contextvars.Token:
    """Start an API method context for network-vs-processing attribution."""
    return _api_call_context.set({"network_ms": 0.0, "call_count": 0, "retry_count": 0, "payload_bytes": 0})


def current_api_context() -> dict[str, Any] | None:
    """Return the active API method context, if any."""
    return _api_call_context.get()


def end_api_context(token: contextvars.Token) -> dict[str, Any]:
    """End an API method context and return its accumulated counters."""
    context = _api_call_context.get() or {"network_ms": 0.0, "call_count": 0, "retry_count": 0, "payload_bytes": 0}
    _api_call_context.reset(token)
    parent_context = _api_call_context.get()
    if parent_context is not None:
        parent_context["network_ms"] = round(
            float(parent_context.get("network_ms", 0.0)) + float(context.get("network_ms", 0.0)), 3
        )
        parent_context["call_count"] = int(parent_context.get("call_count", 0)) + int(context.get("call_count", 0))
        parent_context["retry_count"] = int(parent_context.get("retry_count", 0)) + int(context.get("retry_count", 0))
        parent_context["payload_bytes"] = int(parent_context.get("payload_bytes", 0)) + int(
            context.get("payload_bytes", 0)
        )
    return context


def record_http_response(response: Any, *, module: str = "mist_connection", **_: Any) -> None:
    """Requests response hook for Mist API HTTP calls."""
    if not perf_enabled():
        return
    elapsed = getattr(response, "elapsed", None)
    network_ms = 0.0
    if elapsed is not None:
        try:
            network_ms = round(elapsed.total_seconds() * 1000, 3)
        except (AttributeError, TypeError, ValueError):
            network_ms = 0.0
    context = current_api_context()
    payload_bytes = response_payload_size(response)
    if context is not None:
        context["network_ms"] = round(float(context.get("network_ms", 0.0)) + network_ms, 3)
        context["call_count"] = int(context.get("call_count", 0)) + 1
        history = getattr(response, "history", None) or []
        context["retry_count"] = int(context.get("retry_count", 0)) + len(history)
        context["payload_bytes"] = int(context.get("payload_bytes", 0)) + int(payload_bytes or 0)
    emit_perf_event(
        event_name="mist_api_http_call",
        module=module,
        function="http_request",
        route=endpoint_path_from_response(response),
        duration_ms=network_ms,
        network_ms=network_ms,
        processing_ms=0.0,
        call_count=1,
        payload_bytes=payload_bytes,
        status=getattr(response, "status_code", None),
        retry_count=len(getattr(response, "history", None) or []),
    )


def install_requests_response_hook(api_session: Any) -> bool:
    """Install the Mist API response hook once on mistapi's requests.Session."""
    requests_session = getattr(api_session, "_session", None)
    if requests_session is None:
        return False
    hooks = getattr(requests_session, "hooks", None)
    if hooks is None:
        return False
    response_hooks = hooks.setdefault("response", [])
    if record_http_response not in response_hooks:
        response_hooks.append(record_http_response)
    return True


def result_item_count(result: Any) -> int | None:
    """Infer a result item count from common list-bearing payloads."""
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        for key in ("sites", "devices", "clients", "results", "items", "aps", "switches", "gateways"):
            value = result.get(key)
            if isinstance(value, list):
                return len(value)
        for value in result.values():
            if isinstance(value, list):
                return len(value)
    return None


def instrument_mist_method(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate a MistConnection method with total/network/processing timing."""
    try:
        parameters = list(inspect.signature(func).parameters)
        site_id_position = parameters.index("site_id") - 1 if "site_id" in parameters else None
    except (TypeError, ValueError):
        site_id_position = None

    @functools.wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not perf_enabled():
            return func(self, *args, **kwargs)
        token = begin_api_context()
        trace_started = start_peak_trace()
        start = time.perf_counter()
        error_class = None
        result: Any = None
        site_id = kwargs.get("site_id")
        if site_id is None and site_id_position is not None and 0 <= site_id_position < len(args):
            site_id = args[site_id_position]
        try:
            result = func(self, *args, **kwargs)
            return result
        except Exception as exc:
            error_class = exc.__class__.__name__
            raise
        finally:
            duration_ms = duration_ms_since(start)
            context = end_api_context(token)
            network_ms = round(float(context.get("network_ms", 0.0)), 3)
            emit_perf_event(
                event_name="mist_api_method",
                module=func.__module__,
                function=func.__name__,
                site_id=site_id,
                duration_ms=duration_ms,
                network_ms=network_ms,
                processing_ms=round(max(duration_ms - network_ms, 0.0), 3),
                call_count=int(context.get("call_count", 0)),
                items_returned=result_item_count(result),
                payload_bytes=int(context.get("payload_bytes", 0)) or None,
                retry_count=int(context.get("retry_count", 0)),
                error_class=error_class,
                peak_bytes=finish_peak_trace(trace_started),
            )
    return wrapper


@contextlib.contextmanager
def stage(name: str, *, module: str, function: str, site_id: str | None = None) -> Iterator[None]:
    """Emit a stage timing event for a block of work."""
    if not perf_enabled():
        yield
        return
    start = time.perf_counter()
    error_class = None
    try:
        yield
    except Exception as exc:
        error_class = exc.__class__.__name__
        raise
    finally:
        emit_perf_event(
            event_name="mist_api_stage",
            module=module,
            function=f"{function}.{name}",
            site_id=site_id,
            duration_ms=duration_ms_since(start),
            error_class=error_class,
        )
