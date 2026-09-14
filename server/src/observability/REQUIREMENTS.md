# Observability Requirements

## Overview

OpenTelemetry as the instrumentation layer, exporting to a local Grafana LGTM stack (Tempo, Loki, Prometheus) for traces, logs, and metrics.

## Instrumentation Points

- Agent runs (full task lifecycle)
- Model calls (per-provider latency, tokens, cost)
- Tool calls (per-tool latency, success/failure)
- Subagent execution
- Memory retrieval (per-type latency, relevance)
- Skill retrieval and usage
- Guardrail checks (pass/fail, type)
- Evaluation runs (skill vs baseline)
- Failures and retries

## Agent-Level Metrics

| Metric | Description |
|--------|-------------|
| task_success_rate | % of tasks completed successfully |
| model_latency | Time per model call |
| tool_latency | Time per tool call |
| tool_failure_rate | % of tool calls that fail |
| tokens_per_task | Total tokens consumed per task |
| cost_per_task | Estimated cost per task |
| subagent_count | Number of subagents spawned per task |
| retry_count | Retries per task |
| agent_loop_count | Agent reasoning loops per task |
| memory_retrieval_latency | Time per memory lookup |
| skill_retrieval_frequency | How often skills are retrieved |
| skill_success_rate | % of skill evaluations that pass |
| skill_regression_rate | % of skill evaluations showing regression |
| guardrail_violations | Count of blocked/denied actions |
| user_approvals | Count of human approval requests |
| local_vs_hosted_usage | Split between local and hosted model calls |

## Grafana Stack

```
OpenTelemetry SDK
      ↓
  OTLP Export
      ↓
┌───────────────┬──────────────┬───────────────┐
│ Tempo         │ Loki         │ Prometheus    │
│ traces        │ logs         │ metrics       │
└───────────────┴──────────────┴───────────────┘
      ↓
   Grafana (dashboards)
```

## File Map

```
server/src/observability/
├── REQUIREMENTS.md
├── __init__.py
├── setup.py              # OpenTelemetry SDK initialization
├── tracing.py            # Trace span helpers
├── metrics.py            # Metric instruments and recording
├── logging.py            # Structured logging with OTel correlation
└── exporters.py          # OTLP exporter configuration
```

## Key Interfaces

- `init_tracer(service_name) -> TracerProvider` — set up tracing
- `init_metrics(service_name) -> MeterProvider` — set up metrics
- `trace_span(name, attributes) -> ContextManager` — create a span
- `record_metric(name, value, attributes)` — record a metric point
- `get_trace_id() -> str` — current trace context
