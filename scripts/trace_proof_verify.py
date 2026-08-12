"""Executable parent-child checks for the native OTLP trace proof.

Shared-trace-ID alone is not enough: a workflow-runner CLIENT span must be the
direct parent of an enterprise-api SERVER span on the same trace.
"""

from __future__ import annotations

from typing import Any

# opentelemetry.proto.trace.v1.trace_pb2.Span kind enum values
SPAN_KIND_SERVER = 2
SPAN_KIND_CLIENT = 3

WORKFLOW_SERVICE = "workflow-runner"
API_SERVICE = "enterprise-api"


def find_parent_child_links(spans: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return CLIENT→SERVER links where SERVER.parent_span_id == CLIENT.span_id."""
    clients = [
        span
        for span in spans
        if span.get("kind") == SPAN_KIND_CLIENT and span.get("service") == WORKFLOW_SERVICE
    ]
    servers = [
        span
        for span in spans
        if span.get("kind") == SPAN_KIND_SERVER and span.get("service") == API_SERVICE
    ]
    by_trace_and_span = {(span["trace_id"], span["span_id"]): span for span in clients}
    links: list[dict[str, str]] = []
    for server in servers:
        parent = by_trace_and_span.get((server["trace_id"], server.get("parent_span_id", "")))
        if parent is None:
            continue
        links.append(
            {
                "trace_id": server["trace_id"],
                "client_span_id": parent["span_id"],
                "server_span_id": server["span_id"],
                "client_span_name": str(parent.get("name", "")),
                "server_span_name": str(server.get("name", "")),
            }
        )
    return links


def assert_parent_child_propagation(spans: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Fail unless at least one direct CLIENT→SERVER parent-child link exists."""
    links = find_parent_child_links(spans)
    if not links:
        client_ids = {
            (span.get("trace_id"), span.get("span_id"))
            for span in spans
            if span.get("kind") == SPAN_KIND_CLIENT and span.get("service") == WORKFLOW_SERVICE
        }
        server_parents = {
            (span.get("trace_id"), span.get("parent_span_id"), span.get("span_id"))
            for span in spans
            if span.get("kind") == SPAN_KIND_SERVER and span.get("service") == API_SERVICE
        }
        raise AssertionError(
            "no workflow-runner CLIENT span is the direct parent of an "
            f"enterprise-api SERVER span; clients={sorted(client_ids)!r} "
            f"server_parents={sorted(server_parents)!r}"
        )
    return links
