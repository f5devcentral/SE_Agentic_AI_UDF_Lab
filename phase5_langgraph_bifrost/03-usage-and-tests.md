# Phase 5: Usage & Tests

## Testing LangGraph

Phase 5 maintains the decoupled external microservices architecture. Individual agent endpoints can be tested directly (e.g., POST `/reason` to the flight-agent). However, validating the full system native architecture requires testing the primary orchestrator entrypoint, which dynamically routes the state graph between services.

### Test 1: Full Architecture Validation

Initiate a travel query through the LangGraph Orchestrator:

```bash
kubectl port-forward svc/langgraph-orchestrator 8000:8000 &

curl -X POST http://localhost:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "I want to visit Barcelona next spring on a tight budget. Get me flights, hotels, and nature activities."
      }
    ]
  }' | jq .
```

*Expected Flow:*
1. LangSmith logs a new trace.
2. The orchestrator invokes RAG through BiFrost.
3. Graph state progresses sequentially: `Supervisor` -> `FlightNode` -> `HotelNode` -> `ActivityNode`.
4. A structured JSON itinerary is returned.

### Test 2: Verify Observability

1. **LangSmith Dashboard:** Review the trace to identify token counts associated with individual Node executions.
2. **Jaeger UI:** Verify that the `span.name` corresponds to the LangChain internal execution tools and that span inheritance links to the gateway context.
