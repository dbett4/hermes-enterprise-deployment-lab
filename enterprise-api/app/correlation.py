"""Normalize untrusted correlation IDs at API trust boundaries.

Caller-provided values are accepted only as canonical UUID strings
(`str(uuid.UUID(...))` form). Anything else is replaced with a newly generated
UUID and must not be reflected into headers, logs, traces, or durable stores.
W3C `traceparent` propagation is intentionally separate.
"""

from __future__ import annotations

import uuid


def normalize_correlation_id(value: str | None) -> str:
    """Return a canonical UUID correlation ID, never reflecting invalid input."""
    if value is None:
        return str(uuid.uuid4())
    if not isinstance(value, str):
        return str(uuid.uuid4())
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid4())
    canonical = str(parsed)
    if value != canonical:
        return str(uuid.uuid4())
    return canonical
