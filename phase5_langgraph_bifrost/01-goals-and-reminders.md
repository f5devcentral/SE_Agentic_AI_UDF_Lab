# Phase 5: Goals and Reminders

## Objective
Phase 5 replaces the Python orchestrator used in Phase 3/4 with LangGraph. It also implements the BiFrost AI Gateway to handle API routing, LLM connectivity, and integration with Calypso AI.

## Architecture Configuration

### LangGraph Topology: Distributed External Microservices
The external Flask agent microservices (`flight-agent:8000`, `hotel-agent:8000`, etc.) created in earlier phases are retained. This architecture allows individual agents to operate on separate namespaces or remote clusters.

**Characteristics:**
- **Microservice Scaling:** Agent instances can be scaled horizontally and independently based on load.
- **StateGraph Implementation:** The LangGraph orchestrator operates centrally. It serializes the `StateGraph` over HTTP dynamically while standardizing retry and timeout mechanisms at the node level.

### BiFrost AI Gateway
BiFrost serves as the primary gateway. The orchestrators send LLM requests to BiFrost instead of connecting directly to Ollama or Calypso AI.

**Characteristics:**
- **Centralized Routing:** BiFrost integrates with Calypso AI. It proxies LLM requests, RAG retrievals, and MCP payloads, applying structural evaluations before returning generative outputs.
- **Protocol Abstraction:** Models are abstracted behind standard APIs managed by the gateway.

### Observability Setup
This phase utilizes two observability tools simultaneously:
- **LangSmith:** Records graph permutations, token execution counts, and node traversals automatically within the orchestrator pod.
- **OpenTelemetry (OTLP):** Propagates architectural traces to Jaeger across the HTTP architecture.
