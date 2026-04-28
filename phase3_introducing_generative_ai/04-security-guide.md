# Phase 3: Security Guide

Phase 3 introduces an active Agentic workflow interacting with LLMs and Vector Databases. The security scope expands beyond MCP-specific attacks to cover vulnerabilities related to LLM operations.

## OWASP for LLM Security Risks Mapping

### 1. Prompt Injection & Jailbreaking (LLM01)
* **Location**: User prompt inputs to `orchestrator /plan` or malicious content inside parsed RAG documents.
* **Mechanism**: Directives designed to alter predefined instructions (e.g., "Ignore previous instructions"). Reconstructed RAG documents can also trigger unintended actions during parsing.
* **Mitigation**: Enforce systemic disclaimers stating that all parsed documents should be treated as untrusted. Parameterize JSON output strictness to prevent the intent schema from accepting varied narrative outputs.

### 2. Sensitive Information Disclosure (LLM02)
* **Location**: LLM components processing raw database outputs or internal logs through RAG interactions.
* **Mechanism**: Extraction of metadata or routing architecture configurations via prompt queries.
* **Mitigation**: Standardize sanitization of MCP and Postgres responses before forwarding payloads to the Ollama API.

### 3. Tool Abuse / Excessive Agency (LLM06)
* **Location**: Orchestrator executing MCP tools automatically based on LLM outputs.
* **Mechanism**: Injections directing the logic to force infinite loop tool calls resulting in Denial of Service (DoS).
* **Mitigation**: Implement strict iteration caps for multi-agent loops (`MAX_ITERATIONS` equivalent). Maintain rigid access control lists for permissible tools.

### 4. Vector / Embedding Weaknesses (LLM08)
* **Location**: Synchronization from MinIO to the internal pgvector ETL pipeline.
* **Mechanism**: Modification of raw text files within MinIO. The poisoned data is ingested by the ETL, biasing subsequent RAG query recommendations.
* **Mitigation**: Enforce Role-Based Access Control (RBAC) over all targeted MinIO storage buckets.

### 5. Input / Output Validation (LLM05)
* **Location**: Parsing configurations for agentic outputs (e.g., `new_intents`).
* **Mechanism**: Structural injection attacks causing runtime parsing errors or system crashes.
* **Mitigation**: Implement `json.loads` schema conformity checks inside execution scripts to ensure validation matches expected data types.
