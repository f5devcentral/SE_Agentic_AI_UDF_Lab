# Phase 5 Lab Guide — LangGraph Orchestration & BiFrost Gateway

## Goals

The Flask orchestrator you built in Phase 3 works, but it has a ceiling. It dispatches agents sequentially, hardcodes the routing logic, and has no way to revisit a decision once it has been made. That ceiling matters in production: enterprise agentic systems need to branch, retry, and adapt their execution path based on intermediate results, not just march down a fixed list of steps.

Phase 5 replaces the Flask orchestrator with a **LangGraph StateGraph**. The graph has one supervisor node that calls an LLM to decide which specialist agent to run next, and it makes that decision again after every agent completes. This is the conditional edge pattern — execution is not a pipeline, it is a loop with an exit condition.

At the same time, the Calypso AI proxy is replaced by the **BiFrost AI Gateway**. BiFrost exposes an OpenAI-compatible `/v1` endpoint that handles routing between Ollama and Calypso, so the LangGraph code talks to one address and never needs to know which model backend or inspection layer is behind it.

The objectives for this phase are:
- Replace the Flask orchestrator with a LangGraph StateGraph that uses a supervisor node for dynamic routing.
- Deploy BiFrost as the OpenAI-compatible gateway abstraction over Ollama + Calypso AI.
- Understand the `AgentState` structure and how itinerary fragments accumulate across agent calls.
- Use LangSmith alongside OTel to observe graph execution at the token level.
- Trace why conditional edges make agentic systems more reliable for enterprise deployment than fixed pipelines.

This phase runs on the `LLM` cluster. The external Flask agent microservices (flight-agent, hotel-agent, activity-agent, weather-agent) deployed in earlier phases remain in place — LangGraph calls them over HTTP, the same way the Flask orchestrator did.

---

## Architecture

```
Browser / curl
  │
  └── HTTP :30200 ──► LangGraph Orchestrator (FastAPI :8000)
                         │
                         └── StateGraph
                               │
                               ├── supervisor_node ──► BiFrost Gateway (:8080)
                               │                          ├── Ollama (mistral:7b)
                               │                          └── Calypso AI (semantic inspect)
                               │
                               ├── flight_node ──────► flight-agent  :8001 /reason
                               ├── hotel_node  ──────► hotel-agent   :8002 /reason
                               ├── activity_node ───► activity-agent :8003 /reason
                               └── weather_node ────► weather-agent  :8004 /reason
```

**What changed from Phase 3:**

- The orchestrator is a LangGraph `StateGraph` compiled with `recursion_limit=25`. Every edge transition — supervisor to agent, agent back to supervisor — increments the counter.
- The supervisor calls BiFrost via a `ChatOpenAI` client pointed at `http://bifrost-gateway:8080/v1`. BiFrost handles the Ollama connection and Calypso inspection behind that address.
- Agent nodes call the same external Flask microservices via `_call_external_agent()`, which POSTs the current serialised `AgentState` to each service's `/reason` endpoint.
- The `AgentState` TypedDict carries `messages`, `next_agent`, and `itinerary_fragments`. Fragments accumulate in the state across all agent calls rather than being assembled by the orchestrator at the end.
- LangSmith records every node execution and token count. OTel spans from `LangchainInstrumentor` show up in Jaeger alongside the HTTP spans from the agent microservice calls.

---

## Deployment

### Step 1 — Set context and verify the Phase 3 agents are running

```bash
kubectl config use-context LLM
kubectl get pods -n demo-travel
```

The flight-agent, hotel-agent, activity-agent, and weather-agent pods from Phase 3 must be running. LangGraph calls them at their cluster-internal service addresses. If they are not running, deploy them before continuing.

### Step 2 — Create the BiFrost secrets

```bash
kubectl create secret generic bifrost-secrets \
  --namespace=demo-travel \
  --from-literal=calypso_ai_tenant_key=<your-calypso-key>
```

The Calypso key is the same one used in Phase 4. BiFrost uses it to connect to the Calypso inspection endpoint. If you do not have a Calypso key, BiFrost will still start but will skip semantic inspection.

### Step 3 — Deploy BiFrost

```bash
kubectl apply -f k8s/bifrost-gateway.yaml
kubectl rollout status deploy/bifrost-gateway -n demo-travel
```

Verify BiFrost is reachable:

```bash
kubectl port-forward svc/bifrost-gateway 8080:8080 -n demo-travel &
curl http://localhost:8080/health
```

BiFrost is an OpenAI-compatible gateway. You can probe its model list:

```bash
curl http://localhost:8080/v1/models
```

It should return a list that includes the Ollama model BiFrost is routing to.

### Step 4 — Build and push the LangGraph orchestrator image

From the `phase5_langgraph_bifrost/` directory:

```bash
docker build -t localhost:30500/demo-travel/langgraph-orchestrator:latest .
docker push localhost:30500/demo-travel/langgraph-orchestrator:latest
```

The Dockerfile copies `src/` and installs from `requirements.txt`. The entry point is `python -m src.app`, which starts a uvicorn server on port 8000.

### Step 5 — Set up LangSmith (optional but recommended)

LangSmith provides the graph-level view of execution that Jaeger does not — you can see which node ran, how many tokens it used, and whether the supervisor decided to exit the loop or call another agent. Without it you are working blind on routing decisions.

Create a project at smith.langchain.com and grab an API key, then:

```bash
export LANGSMITH_API_KEY="ls_your_api_key"
```

Edit `k8s/langgraph-orchestrator.yaml` and update the `LANGCHAIN_API_KEY` secret reference, or patch it directly:

```bash
kubectl create secret generic langsmith-secret \
  --namespace=demo-travel \
  --from-literal=api_key=$LANGSMITH_API_KEY
```

The `LANGCHAIN_PROJECT` env var in the manifest is already set to `agentic-travel-lab`.

### Step 6 — Deploy the LangGraph orchestrator

```bash
kubectl apply -f k8s/langgraph-orchestrator.yaml
kubectl rollout status deploy/langgraph-orchestrator -n demo-travel
```

Verify the orchestrator started cleanly:

```bash
kubectl logs -n demo-travel deploy/langgraph-orchestrator --tail=30
```

You should see uvicorn reporting it is running on `0.0.0.0:8000` and a log line confirming BiFrost is reachable.

---

## Testing

### Test 1 — Full graph execution

```bash
kubectl port-forward svc/langgraph-orchestrator 8000:8000 -n demo-travel &

curl -X POST http://localhost:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{"message": "Plan me a nature trip to Barcelona in spring under €800"}' \
  | jq .
```

This is the same query used throughout the lab. The response should be a structured itinerary with flights, hotels, activities, and a weather summary.

Watch the orchestrator logs in another terminal while the request is processing:

```bash
kubectl logs -n demo-travel deploy/langgraph-orchestrator -f
```

You will see the supervisor node fire once per agent, logging which `next_agent` it selected and the token count for each decision. At the end you will see the graph exit condition log line.

### Test 2 — Call an agent directly

You can bypass the graph and test a single agent to confirm the microservice communication layer is working:

```bash
kubectl port-forward svc/flight-agent 8001:8001 -n demo-travel &

curl -X POST http://localhost:8001/reason \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Find me flights from Paris to Barcelona in June"}],
    "mcp_results": {
      "flights": [
        {"airline": "Vueling", "price": 89, "departure_time": "08:30", "arrival_time": "10:15"}
      ]
    },
    "itinerary_fragments": {}
  }'
```

The agent receives the state, calls Ollama via BiFrost to rank the options, and returns a ranked recommendation as its fragment.

### Test 3 — Verify LangSmith traces

Open smith.langchain.com, select the `agentic-travel-lab` project, and find the trace created by Test 1. Click through it to see:

- Each node execution as a separate step with its own token count
- The supervisor's routing decision (which `next_agent` value it returned)
- How many times the supervisor ran before it returned `FINISH`
- The accumulated `itinerary_fragments` in the final state

This is the view that distinguishes LangGraph from the Phase 3 Flask orchestrator: you can see the graph structure, not just a flat list of log lines.

### Test 4 — Verify OTel traces in Jaeger

```bash
kubectl config use-context OBS
kubectl port-forward svc/jaeger-query 16686:16686 -n observability &
```

Open `http://localhost:16686` and search for the `langgraph-orchestrator` service. The trace should show:

- A root span from the FastAPI `/api/plan` handler
- Child spans from `LangchainInstrumentor` for each LLM call to BiFrost
- Child spans for each HTTP POST to the agent microservices
- The total end-to-end duration, which on CPU will be 60–180 seconds

Compare this to the Phase 3 trace for the same query. The structure is similar but the LangGraph trace shows supervisor calls as distinct spans, making the routing decisions visible in the timing.

### Test 5 — Trigger a recursion limit

Send a request that will confuse the supervisor:

```bash
curl -X POST http://localhost:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{"message": "?"}'
```

With no meaningful query, the supervisor may loop without reaching a `FINISH` decision. Watch the logs — you should see the recursion counter increment until it hits the `recursion_limit=25` configured in `graph.py`. LangGraph raises a `GraphRecursionError` which the FastAPI handler catches and returns as a 500 with an explanation. This demonstrates that the limit is a safety rail, not a bug.

---

## Troubleshooting

### `GraphRecursionError` on normal requests

This happens when the supervisor keeps routing to agents without returning `FINISH`. The most common cause is the LLM returning a routing decision in an unexpected format. Check the supervisor node logs for the raw LLM output — if it is returning something other than one of the agent names or `FINISH`, the parsing logic in `travel_planner_node` is not matching it.

```bash
kubectl logs -n demo-travel deploy/langgraph-orchestrator | grep "next_agent"
```

If the raw output looks correct but parsing fails, check that the `next_agent` extraction logic in `graph.py` matches what the current Ollama model actually returns.

### BiFrost returns 502 to the orchestrator

```bash
kubectl logs -n demo-travel deploy/bifrost-gateway --tail=50
```

First check that BiFrost can reach Ollama: `OLLAMA_ENDPOINT` in `bifrost-gateway.yaml` must resolve from inside the pod. On the LLM cluster, Ollama runs at `http://ollama:11434`.

```bash
kubectl exec -n demo-travel deploy/bifrost-gateway -- curl http://ollama:11434/api/tags
```

If that returns model list, the problem is on the Calypso AI side. BiFrost fails closed when Calypso is unreachable — remove the `CALYPSO_AI_TENANT_KEY` from the secret temporarily to confirm whether Calypso is the blocker.

### Agent microservice returns an empty fragment

```bash
kubectl logs -n demo-travel deploy/flight-agent --tail=30
```

Check that the `mcp_results` key in the state being POSTed to `/reason` contains actual data. If MCP calls failed upstream, the agent receives empty tool results and has nothing to rank — it will return a fragment that says no data was available rather than a ranked list.

### LangSmith traces not appearing

Verify the `LANGCHAIN_TRACING_V2=true` environment variable is set in the orchestrator pod:

```bash
kubectl exec -n demo-travel deploy/langgraph-orchestrator -- env | grep LANGCHAIN
```

Also confirm the `LANGCHAIN_API_KEY` secret was created correctly and that the key is valid. LangSmith silently drops traces if the key is wrong — you will not see errors in the logs.

---

## Phase Highlights

### Why LangGraph Instead of Flask

The Flask orchestrator in Phase 3 calls agents in a fixed order: flight → hotel → activity → weather. That works for a travel query that always needs all four outputs. It fails for anything more nuanced — a query that only needs weather, a query where the hotel search should influence activity selection, or a query that should retry if the first flight result is out of budget.

LangGraph solves this with a state machine. The supervisor node looks at the current `AgentState` — what messages have been sent, what fragments have been collected — and decides what to do next. It can call agents in any order, call the same agent twice, or exit immediately if the question does not require all agents. The recursion limit is the only bound on how long this can go.

For enterprises, the practical consequence is that you can express business rules in the graph topology rather than in imperative code. A rule like "if the flight cost exceeds budget, find a cheaper alternative before proceeding to hotels" becomes a conditional edge. A rule like "always verify weather before confirming outdoor activities" becomes a required node transition. The routing logic is in the graph definition, not buried in if/else chains inside a function.

### BiFrost as a Gateway Pattern

The move from calling Ollama directly to calling BiFrost is an instance of a pattern enterprises adopt consistently as AI systems mature: the gateway abstraction.

In Phase 3, the orchestrator had Ollama's address hardcoded. Switching to a different model meant changing code. Adding inspection meant inserting a proxy into every request path. Tracking costs required parsing logs.

BiFrost centralises all of that. The orchestrator sends an OpenAI-compatible chat completion request. BiFrost decides which backend handles it, runs it through Calypso inspection, enforces budget limits, and returns the response. If you want to switch from Ollama to GPT-4 or to a private API gateway in your enterprise environment, you change one BiFrost configuration, not the application code.

The OpenAI compatibility is not incidental. Most enterprise AI tooling targets OpenAI's API format because it became the de facto standard. BiFrost's compatibility means any LangGraph application built for OpenAI works with BiFrost without modification — which matters when you are migrating production workloads to a self-hosted or on-premises model.

### AgentState and Accumulated Context

The `AgentState` TypedDict carries three fields: `messages` (the conversation history), `next_agent` (the supervisor's routing decision), and `itinerary_fragments` (a dict that grows as each agent contributes its output).

This accumulation pattern is different from Phase 3, where the orchestrator assembled the final response from agent return values. In LangGraph, the state is the response. Each agent adds its fragment and returns the updated state. The final state, after the supervisor decides `FINISH`, contains everything.

The consequence is that every agent can see what previous agents decided. The hotel agent can read the flight fragment and factor in total trip cost. The activity agent can read the weather fragment and skip outdoor activities if rain is forecast. This is not wired up in the current implementation, but the architecture supports it — all agents receive the full state, so the cross-agent reasoning capability is there when you need it.

### Token Economy

Phase 5 adds token costs beyond the Phase 3 baseline because the supervisor runs once per agent transition. For a typical four-agent query:

| Call | Tokens (approx.) |
|------|-----------------|
| Supervisor → flight decision | 350 |
| flight-agent LLM ranking | 800 |
| Supervisor → hotel decision | 380 |
| hotel-agent LLM ranking | 750 |
| Supervisor → activity decision | 400 |
| activity-agent LLM ranking | 600 |
| Supervisor → weather decision | 360 |
| weather-agent LLM ranking | 450 |
| Supervisor → FINISH decision | 320 |
| **Total** | **~4,410** |

Compared to Phase 3's ~3,700 token baseline, the supervisor adds roughly 1,800 tokens per query (five supervisor calls). On CPU at quantised precision, that adds 15–30 seconds of inference time. BiFrost's token tracking makes this cost visible per request — check the `x-token-usage` header in BiFrost's response or look at the LangSmith trace.

### Observability: Two Dashboards

Phase 5 is the first phase with two complementary observability surfaces. They answer different questions.

**Jaeger** answers: what happened and how long did it take? You see HTTP spans, LLM call spans, and the full wall-clock duration. It tells you which component was slow.

**LangSmith** answers: why did the graph take this path? You see the supervisor's input and output at each decision point, the token count per node, and the full state at every transition. It tells you what the system decided and what it was reasoning about.

Neither replaces the other. When a query produces an incomplete itinerary, Jaeger tells you if an agent call timed out. LangSmith tells you if the supervisor routed away from an agent before it completed, or if the agent produced a fragment that the supervisor considered insufficient. You need both to diagnose production failures in an agentic system.

### Business Value for Enterprises

The shift from a Flask orchestrator to LangGraph is not primarily a technology swap — it is a change in who can reason about the system's behavior.

A Flask orchestrator is Python code. Understanding its routing logic requires reading the code. Changing the routing logic requires a developer and a deployment.

A LangGraph graph is a declared topology. The nodes and edges are explicit. A platform team can read the graph definition and understand the system's decision tree without following function call chains. Routing changes — adding a new agent, changing the exit condition, adding a retry path — are graph edits, not deep code changes.

This matters for enterprise governance. When an AI system makes a decision that affects a customer — books the wrong hotel, misses a budget constraint, skips a required check — you need to be able to answer: what path did the graph take, and why? LangSmith gives that answer. Without graph-level observability, debugging a conditional routing failure in a production agentic system is guesswork.

The BiFrost pattern matters for a different reason: it separates the application team's concerns from the infrastructure team's concerns. Application developers write LangGraph code that calls an OpenAI-compatible endpoint. The infrastructure team configures BiFrost to route to whatever model is approved, at whatever cost tier is budgeted, through whatever inspection layer compliance requires. Neither team needs to understand the other's domain.
