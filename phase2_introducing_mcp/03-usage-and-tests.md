# Phase 2: Usage & Tests

## Testing MCP Infrastructure

### Step 1: Verify MCPCards (Custom Resources)

List and describe the deployed MCP cards:
```bash
kubectl get mcpcards -n demo-travel
kubectl get mcpcards.travel.demo -n demo-travel
kubectl describe mcpcard travel-mcp-card -n demo-travel
```

Expected output:
```
NAME               SERVER               TRANSPORT         NODEPORT   TOOLS   AGE
travel-mcp-card    travel-mcp-server    streamable-http   30100              24h
weather-mcp-card   weather-mcp-server   streamable-http   30101              24h
```

### Step 2: Test MCP Endpoints Manually

Verify the TCP NodePorts are responsive:
```bash
curl http://localhost:30100/health
curl http://localhost:30101/health
```

### Step 3: Test MCP Servers with `fastmcp` CLI

Execute the Python `fastmcp` CLI tool to inspect and call the exposed servers.

```bash
# List tools on travel-mcp
fastmcp list --server-spec http://10.1.1.5:30100/mcp
# Alternatively:
fastmcp inspect http://localhost:30100/mcp

# Call tools on travel-mcp
fastmcp call http://localhost:30100/mcp search_flights \
  --input '{"origin":"Paris","destination":"Barcelona","date":"2025-06-15"}'

fastmcp call --server-spec http://10.1.1.5:30100/mcp --target search_hotels \
  --input-json '{"city":"Barcelona","checkin":"2025-06-15","checkout":"2025-06-20"}'

fastmcp call http://localhost:30100/mcp search_activities \
  --input '{"city":"Barcelona"}'

# List and call tools on weather-mcp
fastmcp inspect http://localhost:30101/mcp
fastmcp call http://localhost:30101/mcp get_weather_forecast \
  --input '{"city":"Barcelona","date":"2025-06-15"}'
```

### Step 4: Verify Frontend Python MCP Client Execution

Check that the frontend initiates Python MCP client calls correctly:

```bash
kubectl exec -it -n demo-travel deploy/frontend -- python3
```

Inside the interactive terminal:
```python
>>> from mcp_client import search_flights
>>> search_flights(
...     "http://travel-mcp:8000/mcp",
...     "Paris",
...     "Barcelona",
...     "2025-06-15"
... )
```
