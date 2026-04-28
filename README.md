# Agentic AI Lab

This lab walks through the transformation of a conventional 3-tier web application into a fully agentic AI system — step by step, keeping the same observable infrastructure throughout. Each phase builds directly on the previous one, so you can see exactly what changes when you add MCP, then LLMs, then security controls, then a proper orchestration framework.

The application is a travel booking service. It's simple enough to understand at a glance but complex enough to surface real architectural decisions: service-to-service communication, tool governance, RAG retrieval, multi-agent coordination, prompt injection, token cost, and Zero Trust identity. The same app runs across all six phases; what changes is how it thinks, how it's protected, and how its components talk to each other.

## What you will build

By the end of the lab you will have a system where a user types "plan me a nature trip to Barcelona in spring under €800" and gets back a personalised itinerary — ranked flights, ranked hotels, curated activities, and a weather summary — produced by cooperating AI agents that call real tools, retrieve context from a vector database, and have their inputs and outputs inspected by a security proxy before they ever reach the LLM.

That final state is not assembled in one step. Each phase introduces one layer of the architecture so you can reason about it in isolation before the next layer arrives.

## Phases

### Phase 1 — Baseline 3-Tier Application
A Flask frontend, a Flights API, a Hotels API, and PostgreSQL. No AI, no protocols, just a working application instrumented with OpenTelemetry from the start. F5 BIG-IP AWAF sits in front handling standard OWASP protections.

**[Lab Guide](./phase1_current_3tier_app/LAB_GUIDE.md)** — deploy, test, and understand the baseline before anything AI-related is added.

Reference docs: [Goals](./phase1_current_3tier_app/01-goals-and-reminders.md) · [Deployment](./phase1_current_3tier_app/02-deployment.md) · [Tests](./phase1_current_3tier_app/03-usage-and-tests.md) · [Security](./phase1_current_3tier_app/04-security-guide.md)

---

### Phase 2 — Introducing MCP
The same application, but the frontend now calls the backend through **Model Context Protocol** servers instead of direct HTTP. A Travel MCP server wraps the Flights and Hotels APIs; a Weather MCP server provides forecast data. A Kubernetes Custom Resource (MCPCard) publishes the available tools as first-class cluster objects.

This phase has no LLM. Its purpose is to establish the tool interface that an LLM will eventually call — and to demonstrate the new attack surface that comes with it.

**[Lab Guide](./phase2_introducing_mcp/LAB_GUIDE.md)** — deploy the MCP servers, call tools with the `fastmcp` CLI, and inspect the MCPCard inventory.

Reference docs: [Goals](./phase2_introducing_mcp/01-goals-and-reminders.md) · [Deployment](./phase2_introducing_mcp/02-deployment-and-troubleshooting.md) · [Tests](./phase2_introducing_mcp/03-usage-and-tests.md) · [Security](./phase2_introducing_mcp/04-security-guide.md)

---

### Phase 3 — Introducing Generative AI
Ollama runs locally on CPU with `llama3.2:3b` for reasoning and `nomic-embed-text` for embeddings. Travel documents in MinIO are chunked, embedded, and stored in pgvector. An orchestrator coordinates five specialist agents — flight, hotel, activity, weather, and travel — each of which receives pre-fetched MCP data and applies LLM reasoning to produce a ranked decision.

Communication between the orchestrator and agents follows the **A2A (Agent-to-Agent) JSON-RPC 2.0** protocol. Agents advertise their capabilities at `/.well-known/agent.json` and never call MCP directly — the orchestrator's governance layer handles all tool calls on their behalf.

**[Lab Guide](./phase3_introducing_generative_ai/LAB_GUIDE.md)** — deploy the full AI stack, run the agentic pipeline end-to-end, trace it through Jaeger, and run the RAG poisoning baseline test before Phase 4 controls are in place.

Reference docs: [Goals](./phase3_introducing_generative_ai/01-goals-and-reminders.md) · [Deployment](./phase3_introducing_generative_ai/02-deployment-and-troubleshooting.md) · [Tests](./phase3_introducing_generative_ai/03-usage-and-tests.md) · [Security](./phase3_introducing_generative_ai/04-security-guide.md)

---

### Phase 4 — Agentic Security & Red Teaming
All external traffic now flows through **F5 BIG-IP AWAF** (iRule-based pattern matching) and then **Calypso AI** (semantic prompt inspection and output DLP) before reaching the orchestrator. Ollama calls from inside the cluster are also routed through the Calypso proxy.

Four red team scenarios demonstrate what happens with and without mitigations: prompt injection, RAG poisoning, PII exfiltration, and the €1-budget token exhaustion loop. Each maps to a specific OWASP LLM Top 10 risk.

**[Lab Guide](./phase4_adding_agents/LAB_GUIDE.md)** — deploy the security controls, run each attack scenario twice (unmitigated and mitigated), and observe the difference in OTel traces and BIG-IP logs.

Reference docs: [Goals](./phase4_adding_agents/01-goals-and-reminders.md) · [Deployment](./phase4_adding_agents/02-deployment-and-troubleshooting.md) · [Tests](./phase4_adding_agents/03-usage-and-tests.md) · [Security](./phase4_adding_agents/04-security-guide.md)

---

### Phase 5 — LangGraph Orchestration & BiFrost Gateway
The Flask orchestrator is replaced with a **LangGraph StateGraph**. A supervisor node calls the BiFrost AI Gateway (OpenAI-compatible) to decide which specialist agent runs next, then dispatches via conditional edges. BiFrost handles routing between Ollama and Calypso AI, abstracting the LLM backend from the graph.

**[Lab Guide](./phase5_langgraph_bifrost/LAB_GUIDE.md)** — migrate to LangGraph, deploy BiFrost, observe graph execution in LangSmith, and understand why conditional edges matter for enterprise agentic systems.

Reference docs: [Goals](./phase5_langgraph_bifrost/01-goals-and-reminders.md) · [Deployment](./phase5_langgraph_bifrost/02-deployment-and-troubleshooting.md) · [Tests](./phase5_langgraph_bifrost/03-usage-and-tests.md) · [Security](./phase5_langgraph_bifrost/04-security-guide.md)

---

### Phase 6 — Zero Trust & Federation
Full Zero Trust across all clusters. Keycloak handles user OIDC authentication; the resulting JWT is carried as a Bearer token through every service hop. LangGraph performs OAuth2 Token Exchange before each east-west agent call, scoping down the token to the target audience. SPIRE provisions short-lived X.509 SVIDs for mTLS between clusters. BIG-IP APM validates the cryptographic signature of every JWT at ingress.

**[Lab Guide](./phase6_identity_and_federation/LAB_GUIDE.md)** — deploy Keycloak and SPIRE, implement RFC 8693 token exchange, configure BIG-IP APM JWT validation, and verify mTLS rejection from unatested workloads.

Reference docs: [Goals](./phase6_identity_and_federation/01-goals-and-reminders.md) · [Deployment](./phase6_identity_and_federation/02-deployment-and-troubleshooting.md) · [Tests](./phase6_identity_and_federation/03-usage-and-tests.md) · [Security](./phase6_identity_and_federation/04-security-guide.md)

---

## Infrastructure

The lab runs in the **F5 UDF (Unified Demonstration Facility)** across multiple K3s clusters, each dedicated to a tier of the architecture:

```bash
kubectl config use-context TOOLS       # Phase 1–2: application services
kubectl config use-context MCP         # Phase 2: MCP servers
kubectl config use-context LLM         # Phase 3–5: LLM inference, agents, orchestrator
```

All services deploy into the `demo-travel` namespace. Images are pushed to a local registry at `localhost:30500`.

LLM inference runs on CPU using quantised models. Expect 30–120 seconds per orchestrator request — this is a deliberate constraint that makes token cost and latency visible in traces, not a bug to work around.

---

> This is a lab environment. Security configurations, credentials, and code patterns here are designed for learning and demonstration. None of it should be deployed to production without a proper independent review.
