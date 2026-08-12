# SLOs, error budget, and local trace evidence

This lab states two service-level objectives for the fixture API and proves the
alert math plus a local OpenTelemetry trace. The numbers are executable in
Prometheus rules and native proofs. They are not a production SLO program.

## Objectives

| Objective | Target | Budget |
|---|---|---|
| Availability | 99% of non-probe requests return non-5xx | 1% of requests may be 5xx |
| Latency | 95% of non-probe requests finish in under 500 ms | 5% of requests may be slower than 500 ms |

Probes are `/healthz` and `/readyz`; the burn expressions exclude them. The
metrics middleware does not instrument `/metrics`, so Prometheus scrapes cannot
spend the request budget. `/metrics` is unauthenticated on purpose
so Prometheus can scrape without an application bearer token; Compose publishes
the API on loopback only.

A 30-day month at 99% availability is 7.2 hours of 5xx (`30 * 24 * 0.01`). The
latency budget is a request share, not a time share: 5% of requests may exceed
500 ms.

## Multi-window burn alerts

The rules in `observability/alerts.yml` use Google SRE-style multi-window burn
rates against those budgets.

| Alert | Windows | Threshold | Meaning |
|---|---|---|---|
| `EnterpriseApiAvailabilityFastBurn` | 1h and 5m | `14.4 * 0.01` | 14.4% 5xx in both windows. At 14.4x, a 30-day 1% budget would empty in about two days. |
| `EnterpriseApiAvailabilitySlowBurn` | 6h and 30m | `6 * 0.01` | 6% 5xx in both windows. Exhausts the same budget in five days. |
| `EnterpriseApiLatencyFastBurn` | 1h and 5m | `14.4 * 0.05` | More than 72% of requests slower than 500 ms in both windows. |
| `EnterpriseApiLatencySlowBurn` | 6h and 30m | `6 * 0.05` | More than 30% of requests slower than 500 ms in both windows. |
| `EnterpriseApiPostCommitFailure` | 10m, `for: 0m` | any `postcommit_error` | The write committed and the caller still saw 5xx. Resume with the same capability and idempotency key. |

Fast burns page. Slow burns ticket. `promtool test rules observability/alerts.test.yml`
has a positive firing fixture for every alert and an idle-series negative control
so unused latency histograms do not consume budget.

## Synthetic traffic limit

Demo, telemetry-proof, trace-proof, and unit tests generate the only traffic.
Burn rates computed from that traffic are not evidence of production load,
multi-tenant mix, or real error rates. The native Prometheus proof scrapes
localhost. The Compose path is a separate container-runtime claim.

## Alert delivery gap

Prometheus loads and evaluates the five rules. Nothing pages a human. There is
no Alertmanager, no chat hook, and no on-call rotation. A firing rule in
`promtool` or a local query is not notification delivery.

## Tracing

Opt-in OpenTelemetry 1.43.0 tracing is off unless `OTEL_TRACES_EXPORTER=otlp`
and a loopback OTLP/HTTP endpoint are set. The workflow runner starts CLIENT
spans and injects W3C `traceparent`. The API extracts that context and starts
SERVER spans. `workflow-runner` wraps `apply_incident_action` in an INTERNAL
span with bounded events: `approval.requested`, `approval.accepted`, `mutation.dispatched`,
`mutation.failed_resumable`, `mutation.applied`, `mutation.replayed`,
`approval.rejected`.

Attributes are allowlisted. Bearer tokens, approval capabilities, idempotency
keys, notes, request bodies, and incident or action identifiers are dropped.
Route labels use templates such as `/v1/incidents/{incident_id}`, not the
concrete path. `SimpleSpanProcessor` is lab-only so proof shutdown is
deterministic.

`./scripts/trace-proof.sh` starts a loopback capture endpoint, runs the
separated approval then post-commit failure then resume path, flushes both
providers, and parses OTLP protobuf. It requires a shared trace ID with
workflow CLIENT and API SERVER spans plus the failure/resume events. It is
sampled local capture, not a collector backend, not retention, and not
production tracing.

## What this does not prove

- No collector backend, trace store, or retention window.
- No external pager or ticket system.
- No production traffic or customer data.
- No cloud deploy, TLS at an edge, or multi-host mesh.
- Native process proof is not Compose-runtime proof.
