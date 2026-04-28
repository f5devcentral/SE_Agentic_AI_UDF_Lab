# Observability Service Map & TraceID Correlation

This document explains how all observability components (across K3s clusters and BIG-IP) are connected, and how a single traceID can be used to view the full request path across all services.

## Service Map

- **BIG-IP**: Entry point, injects/propagates trace context headers, logs events via iRules.
- **K3s Clusters**: Each cluster runs:
  - **Promtail**: Collects pod logs, forwards to Loki.
  - **Fluentd**: Collects pod logs and syslog (including BIG-IP), forwards to OTEL Collector.
  - **OTEL Collector**: Receives traces/metrics/logs, exports to Jaeger, Prometheus, Loki.
- **Central Observability (OBS) Cluster**:
  - **Grafana**: Visualization for metrics, logs, traces.
  - **Loki**: Log storage and search.
  - **Jaeger**: Trace storage and visualization.

## TraceID Correlation

- All services (including BIG-IP) propagate the W3C `traceparent` header.
- Logs and traces include the traceID from this header.
- In Grafana/Jaeger, you can search for a traceID to see the full request path, including BIG-IP events, API calls, and downstream services.

## Example Flow

1. **Client → BIG-IP**: BIG-IP injects or propagates `traceparent` header, logs request with traceID.
2. **BIG-IP → K3s Service**: Trace context is forwarded; service logs and traces include the same traceID.
3. **Fluentd/Promtail**: Collect logs (with traceID) and forward to Loki/OTEL Collector.
4. **OTEL Collector**: Receives traces (with traceID) and exports to Jaeger.
5. **Grafana/Jaeger**: Search for traceID to view the full distributed trace and related logs.

---

See manifests and configs in the `manifests/` folder for deployment details.
