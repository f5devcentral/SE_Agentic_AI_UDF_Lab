# Phase 5: Deployment & Troubleshooting

## Deployment Guide

### Step 1: Deploy BiFrost AI Gateway
Deploy the gateway and attach Calypso AI tenant details. Ensure the Kubernetes context is set correctly.

```bash
kubectl apply -f k8s/bifrost-gateway.yaml
kubectl rollout status deploy/bifrost-gateway -n demo-travel
```

### Step 2: Build the LangGraph Orchestrator Image
Execute the build command from the `phase5_langgraph_bifrost` directory:

```bash
docker build -t localhost:30500/demo-travel/langgraph-orchestrator:latest .
docker push localhost:30500/demo-travel/langgraph-orchestrator:latest
```

### Step 3: Deploy LangGraph Orchestrator
Supply the LangSmith API key before applying the manifest to enable traces.

```bash
export LANGSMITH_API_KEY="ls_your_api_key"

kubectl apply -f k8s/langgraph-orchestrator.yaml
kubectl rollout status deploy/langgraph-orchestrator -n demo-travel
```

---

## Troubleshooting

### LangGraph State Truncation
If graph execution stops before generating itinerary nodes, check the `recursion_limit` parameter in the LangGraph compilation (`graph.compile()`). The default is set to 25.

### BiFrost Disconnection (502 Bad Gateway)
If LLM API calls fail:
1. Verify network connectivity from BiFrost to the `Ollama` engine on port `11434`.
2. Check the Calypso AI connector configuration. BiFrost will fail-closed if Calypso AI is unresponsive.

### Missing OpenTelemetry Traces
Ensure `opentelemetry-instrumentation-langchain` is installed via `requirements.txt`. Verify that the OTLP environment variables in the orchestrator YAML resolve to the correct `otel-collector` service endpoint.
