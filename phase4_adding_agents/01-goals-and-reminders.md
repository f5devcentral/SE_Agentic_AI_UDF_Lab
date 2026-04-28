# Phase 4: Goals and Reminders

## Objective
Phase 4 covers agentic tooling and security. It demonstrates red-teaming operations and establishes security controls against LLM-specific vulnerabilities, such as prompt injection, data exfiltration, and tool poisoning.

## Security Architecture
The environment utilizes the following security layers:
- **F5 BIG-IP Advanced WAF (AWAF)**: Deployed at the ingress layer to proxy external traffic and filter prompt injections via intent-based routing and iRules.
- **Calypso AI**: Validates input prompts directed to the LLMs and filters LLM output for PII and data leakage.
- **OpenTelemetry Aggregation**: Centralizes logging across security boundaries to track interactions between agents.

## Prerequisites
- The Generative AI Stack from Phase 3 must be operational.
- The Kubernetes context must be set to the LLM Red-Teaming cluster.
- Access to the F5 BIG-IP Management Console is required for WAF policy updates.
- Calypso AI tenant credentials must be configured in the environment.
