# Observability Stack Documentation

This documentation covers the deployment and integration of the full observability stack across all K3s clusters and BIG-IP, including:

- OpenTelemetry Collector (all clusters)
- Fluentd (all clusters)
- Promtail (all clusters)
- iRules for BIG-IP (logging and tracing)
- Grafana
- Loki
- Jaeger
- Service/trace map and traceID correlation

## Structure
- `manifests/`: All Kubernetes manifests and Helm values for the stack
- `irule_bigip_tracing.tcl`: Example iRule for trace context propagation and logging
- `trace_map.md`: Service map and traceID correlation guide

## TraceID End-to-End
- All services and BIG-IP propagate the W3C `traceparent` header
- Logs and traces include the traceID
- Grafana and Jaeger allow searching for a traceID to view the full request path

See each file for deployment and configuration details.
