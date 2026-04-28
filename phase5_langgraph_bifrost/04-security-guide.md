# Phase 5: Security Guide

In Phase 5, the bulk of our AI security posture is solidified by integrating the **BiFrost AI Gateway** alongside **Calypso AI**.

## BiFrost capabilities

Previously, Calypso AI acted as a transparent proxy for Ollama HTTP calls. Moving to BiFrost introduces an intelligent AI Gateway paradigm.

### 1. Calypso AI Connector
BiFrost does not replace Calypso AI; rather, it *enhances* it. BiFrost acts as the centralized router that explicitly integrates with Calypso AI endpoints to perform:
- **Prompt Inspection**: Evaluating user structures for known injection attempts.
- **RAG Inspection**: Ensuring documents returned from the PostgreSQL Vector searches aren't poisoned before injecting them into the prompt window.
- **MCP Payload Validation**: Intercepting and validating outbound `fastmcp` Tool Calls the LLM decides to trigger.
- **Output Validation**: Applying DLP (Data Loss Prevention) and hallucination indexing. 

### 2. Distributed Microservices Security Consideration
By utilizing the **Distributed External Microservices** model in LangGraph, we continue passing Agent State configurations over standard internal HTTP infrastructure to distinct pods (e.g., `flight-agent:8000`, `activity-agent:8000`).

To maintain a strict zero-trust boundary, it remains highly recommended to leverage the **F5 BIG-IP / Calico** network policies introduced in earlier lab phases to prevent lateral movement. The orchestrator pod should be the *exclusive* entity permitted network ingress access into the respective remote Agent pods.
