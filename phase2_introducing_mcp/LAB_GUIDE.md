# Phase 2 Lab Guide — Introducing MCP

## Goals

Phase 2 introduces the **Model Context Protocol (MCP)** — the standard that enables AI models to call external tools in a structured, discoverable way. Before hooking up an LLM, you need a tool layer the LLM can reliably invoke. That is what MCP provides.

The objectives for this phase are:
- Deploy two MCP servers (Travel, Weather) that expose the Phase 1 application APIs as callable tools.
- Introduce the **MCPCard Custom Resource Definition** — a Kubernetes-native way to advertise and inventory available AI tools.
- Replace the Phase 1 frontend's direct HTTP calls with MCP client calls, so the same data flows through a standardised protocol.
- Build intuition for the attack surface introduced by exposing executable tools over HTTP before any LLM is involved.

This phase runs on its own K3s cluster using the `MCP` kubectl context, isolated from the application tier (`APP_TOOLS`) and the LLM tier (`LLM`).

---

## Architecture

```
Browser
  │
  └── HTTP :30080 ──► Frontend (Flask, MCP-aware)
                        │
                        ├── MCP StreamableHTTP ──► travel-mcp  (:30100/mcp)
                        │                             ├── search_flights  → Flights API (APP_TOOLS cluster)
                        │                             ├── search_hotels   → Hotels API  (APP_TOOLS cluster)
                        │                             └── search_activities → PostgreSQL (APP_TOOLS cluster)
                        │
                        └── MCP StreamableHTTP ──► weather-mcp (:30101/mcp)
                                                      └── get_weather_forecast → seeded forecast engine
```

**What changed from Phase 1:**
- The frontend no longer calls the Flights/Hotels APIs directly. It calls `travel-mcp` via `mcp_client.py`, which calls the APIs on the frontend's behalf.
- Both MCP servers export OTel spans for every tool invocation, so you can trace a search from the browser all the way through the MCP call into the backend API.
- A Kubernetes Custom Resource (`MCPCard`) is deployed alongside each server, publishing the server's name, transport type, NodePort, and supported tools as first-class Kubernetes objects.

### Services

| Service     | NodePort | Tools Exposed |
|-------------|----------|---------------|
| travel-mcp  | 30100    | `search_flights`, `search_hotels`, `search_activities` |
| weather-mcp | 30101    | `get_weather_forecast` |
| frontend    | 30080    | N/A (MCP client only) |

---

## Deployment

### Step 1 — Set Kubernetes context

```bash
kubectl config use-context MCP
kubectl create namespace demo-travel
```

### Step 2 — Create the database secret
```bash
kubectl create secret generic travel-db-secret \
  --namespace=demo-travel \
  --from-literal=username=travel \
  --from-literal=password=travelpass
```

### Step 3 — Build and push the MCP server images

From the `phase2_introducing_mcp/` directory:

```bash
cd mcp-servers

# Travel MCP Server
docker build -f travel-mcp/Dockerfile \
  -t localhost:30500/demo-travel/travel-mcp:latest .
docker push localhost:30500/demo-travel/travel-mcp:latest

# Weather MCP Server
docker build -f weather-mcp/Dockerfile \
  -t localhost:30500/demo-travel/weather-mcp:latest .
docker push localhost:30500/demo-travel/weather-mcp:latest
```

**Build the MCP-aware frontend:**
```bash
cd ../frontend
cp -r ../mcp-servers/shared .   # shared OTel library needed by Dockerfile
docker build -t localhost:30500/demo-travel/frontend_mcp:latest .
docker push localhost:30500/demo-travel/frontend_mcp:latest
```

### Step 4 — Deploy the CRD and MCP servers

```bash
cd ../k8s

# Register the MCPCard custom resource type
kubectl apply -f crds/mcpcard-crd.yaml
kubectl get crd mcpcards.travel.demo    # verify registration

# Deploy Travel MCP
kubectl apply -f travel-mcp/
kubectl rollout status deployment/travel-mcp -n demo-travel

# Deploy Weather MCP
kubectl apply -f weather-mcp/
kubectl rollout status deployment/weather-mcp -n demo-travel
```

### Step 5 — Deploy the frontend (optional — run Phase 2 frontend in K3s)

```bash
kubectl apply -f frontend/deployment.yaml
kubectl rollout status deployment/frontend -n demo-travel
```

### Step 6 — Verify everything is running

```bash
kubectl get pods     -n demo-travel
kubectl get svc      -n demo-travel
kubectl get mcpcards -n demo-travel
```

Expected `kubectl get mcpcards` output:
```
NAME               SERVER               TRANSPORT         NODEPORT
travel-mcp-card    travel-mcp-server    streamable-http   30100
weather-mcp-card   weather-mcp-server   streamable-http   30101
```

---

## Testing

### Test 1 — TCP connectivity

```bash
curl http://localhost:30100/health
curl http://localhost:30101/health
```

Both must return `{"status": "ok"}`.

### Test 2 — Inspect MCP servers with `fastmcp`

```bash
pip install fastmcp   # one-time

# List all tools advertised by each server
fastmcp inspect http://localhost:30100/mcp
fastmcp inspect http://localhost:30101/mcp
```

Expected for `travel-mcp`: three tools — `search_flights`, `search_hotels`, `search_activities`.
Expected for `weather-mcp`: one tool — `get_weather_forecast`.

### Test 3 — Call individual MCP tools

```bash
# Flights
fastmcp call http://localhost:30100/mcp search_flights \
  --input '{"origin":"Paris","destination":"Barcelona","date":"2026-06-15"}'

# Hotels
fastmcp call http://localhost:30100/mcp search_hotels \
  --input '{"city":"Barcelona","checkin":"2026-06-15","checkout":"2026-06-20"}'

# Activities
fastmcp call http://localhost:30100/mcp search_activities \
  --input '{"city":"Barcelona"}'

# Weather
fastmcp call http://localhost:30101/mcp get_weather_forecast \
  --input '{"city":"Barcelona","date":"2026-06-15"}'
```

Each call must return a populated JSON payload. The travel tools proxy to the Phase 1 APIs on the `APP_TOOLS` cluster; if those are not running, they return HTTP 502 errors — which is a useful test in itself.

### Test 4 — Verify the Python MCP client inside the frontend pod

```bash
kubectl exec -it -n demo-travel deploy/frontend -- python3
```

Inside the interactive Python shell:
```python
>>> from mcp_client import search_flights
>>> search_flights(
...     "http://travel-mcp:8000/mcp",
...     "Paris",
...     "Barcelona",
...     "2026-06-15"
... )
```

If this returns a list of flight dicts, the MCP client inside the frontend is correctly communicating with the travel MCP server over the cluster-internal DNS name.

### Test 5 — MCPCard inspection

```bash
kubectl describe mcpcard travel-mcp-card -n demo-travel
kubectl get mcpcard travel-mcp-card -n demo-travel -o yaml
```

This confirms that the tool inventory is persisted as a Kubernetes resource, not just running processes.

---

## Troubleshooting

### `ErrImagePull` or `ImagePullBackOff`
```bash
# Verify image is in the registry
curl http://localhost:30500/v2/demo-travel/travel-mcp/tags/list

# If K3s cannot pull, configure the insecure registry
# Edit /etc/rancher/k3s/registries.yaml on the K3s node:
#   mirrors:
#     "localhost:30500":
#       endpoint:
#         - "http://localhost:30500"
# Then: sudo systemctl restart k3s
```

### `CrashLoopBackOff` on travel-mcp
The travel-mcp server connects to PostgreSQL on the `APP_TOOLS` cluster and to the Flights/Hotels APIs. If those addresses are not reachable:
```bash
kubectl logs -n demo-travel deploy/travel-mcp --tail=50
kubectl describe pod -n demo-travel -l app=travel-mcp
```
Check that `DB_HOST`, `FLIGHTS_API_URL`, and `HOTELS_API_URL` env vars in `k8s/travel-mcp/deployment.yaml` resolve correctly from within the MCP cluster.

### `fastmcp call` returns empty or times out
```bash
# Check the MCP server is receiving the request
kubectl logs -n demo-travel deploy/travel-mcp --tail=50 -f
```
Run the `fastmcp call` command in another terminal and watch the log. You will see the tool invocation arrive and its upstream HTTP call logged.

### OTel collector not reachable
If you see `OTLP export failed` in the logs, the MCP pods cannot reach the collector. Either deploy one in the `demo-travel` namespace or update `OTEL_EXPORTER_OTLP_ENDPOINT` in the deployment manifests:
```bash
kubectl set env deployment/travel-mcp \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://<NODE_IP>:<COLLECTOR_PORT> \
  -n demo-travel
```

---

## Phase Highlights

### Traffic Routing

In Phase 1, traffic was:  
`Browser → Frontend → {Flights API, Hotels API, PostgreSQL}`

In Phase 2, traffic becomes:  
`Browser → Frontend → {travel-mcp → {Flights API, Hotels API, PostgreSQL}, weather-mcp}`

The **MCP layer is now the only entry point** to the backend services. The frontend has lost direct knowledge of the upstream API addresses — it only knows the MCP server URLs and the tool names. This is the first architectural step toward the AI agent pattern, where the LLM will replace the frontend as the entity deciding which tool to call.

Traffic flows through a **StreamableHTTP transport**, a bidirectional HTTP/1.1 stream defined by the MCP specification. This is not a standard REST call: the client sends a tool invocation over a stream and waits for a structured result. Observe this in Jaeger — the MCP call span is longer than a plain HTTP span because the stream must be opened, the tool executed, and the stream closed.

### Protocol Understanding

MCP defines a three-layer protocol:
1. **Transport** — StreamableHTTP in this deployment. The client opens a persistent HTTP stream to `/mcp`.
2. **Session** — MCP initialises a session between client and server, exchanging capability manifests.
3. **Message** — Typed messages: `tools/list` to discover capabilities, `tools/call` with named arguments to invoke a tool, and a structured result back.

The **MCPCard CRD** extends Kubernetes to understand MCP natively. Instead of checking if a tool server is alive by calling it, operators can `kubectl get mcpcards` and see the inventory. This is the first step toward platform-level governance of AI tools — the same way Kubernetes knows about Deployments and Services, it now knows about AI capabilities.

The `mcp_client.py` in the frontend handles three response formats:
- Modern FastMCP `structured_content` (preferred)
- Legacy `TextContent` with embedded JSON string
- Raw list fallback

This compatibility handling is a practical illustration of why protocol versioning matters in AI systems — the wire format has changed as the MCP specification has evolved.

### Security

Phase 2 significantly expands the attack surface:

**Authentication bypass** — The MCP NodePorts (30100, 30101) are accessible to anyone who can reach the K3s node IP. A malicious actor can call `search_flights` with arbitrary parameters, bypassing the frontend entirely. In a production deployment, MCP servers would sit behind the BIG-IP with mTLS and token validation.

**Tool poisoning** — The `search_activities` tool queries PostgreSQL directly. If an attacker can insert malicious content into the `activities` table, that content will be returned by the MCP tool and eventually consumed by an LLM in Phase 3. Sanitise database outputs before they leave the MCP server boundary.

**Resource exhaustion** — The `search_flights` tool spawns an HTTP call to the Flights API on the `APP_TOOLS` cluster. Without rate limiting, an attacker can flood the MCP server and cascade load into the App cluster. The `APP_TOOLS` BIG-IP rate-limiting policy from Phase 1 is the only defence at this stage.

**Tool discovery** — `tools/list` is unauthenticated by default. Any caller can enumerate every tool, its input schema, and its description. In Phase 4, BIG-IP iRules will add signature-based filtering on MCP payloads at the ingress.

### Token Economy

There are still no LLM tokens at this phase. However, Phase 2 establishes the **cost model for tool calls** that LLMs will generate in Phase 3:

- Every `tools/call` involves a round-trip HTTP request from the MCP server to a backend API.
- The `get_weather_forecast` tool generates a 5-day forecast — 5 JSON objects per call.
- The `search_flights` tool can return up to 6 flights per call.

When an LLM orchestrates these tools in Phase 3, it will call them multiple times as it reasons. The cost is not just the LLM tokens — it is the sum of all MCP tool invocations those tokens trigger. Understanding the tool call overhead now helps reason about the cost structure in later phases.

### Visibility

Every MCP tool invocation creates an OTel span:
- The frontend creates a root span when it handles the search request.
- `mcp_client.py` propagates the W3C `traceparent` header through the MCP `meta` field.
- The MCP server receives the trace context and creates a child span for the tool execution.
- The child span captures `weather.city`, `weather.date`, and the number of forecast days as span attributes.

In Jaeger, a single frontend search now produces a trace tree that spans **two clusters**: the MCP cluster and the App cluster. This cross-cluster trace propagation — through standard W3C headers over HTTP — is the same mechanism that will connect the LLM cluster in Phase 3.

The MCPCard resources in Kubernetes serve as a **static inventory** alongside the dynamic traces. Operators can audit which tools are deployed without querying live traffic.

### Business Value for Enterprises

MCP solves a real enterprise problem: **tool integration fragmentation**. Without a standard, every AI system must write bespoke connectors to every backend API, each with its own auth, serialisation format, and error handling. MCP replaces that N×M matrix with a single standard.

Concrete enterprise benefits visible in this phase:
- **Tool discoverability** — A new AI agent can call `tools/list` on any MCP server and immediately understand what actions are available, without reading documentation or reverse-engineering an API.
- **Governance through MCPCards** — The Kubernetes CRD means IT operations can enforce policy through standard RBAC: who can deploy an MCPCard, which namespaces are allowed to host MCP servers, which tools are available in which environment.
- **Separation of concerns** — The Flights and Hotels API teams don't need to know anything about AI. The MCP team wraps their APIs in a tool interface. The AI team consumes tools. Three teams, zero coupling.
- **Audit trail** — Because every tool call is a distinct OTel span, enterprises get automatic audit logs of every external action the AI system took, correlated to the user session that triggered it.
