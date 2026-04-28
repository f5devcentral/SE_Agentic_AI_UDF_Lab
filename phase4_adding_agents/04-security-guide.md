# Phase 4: Security Guide

The Phase 4 security perimeter utilizes application delivery controllers and AI-specific proxies.

## Defensive Architecture

The defense strategy implements mitigation controls corresponding to the OWASP Top 10 for LLMs.

### 1. Gateway Edge: F5 BIG-IP AWAF
The F5 BIG-IP operates as the initial ingress boundary, intended to block established AI attack vectors before allocating LLM compute resources.

**Controls:**
- **Rate-limiting**: Restricts resource exhaustion attacks against backend API tools (LLM10).
- **iRule Pattern Matching**: Identifies structural patterns associated with command insertions targeting MCP JSON parameters.

*Example iRule subset:*
```tcl
when HTTP_REQUEST_DATA {
    set payload [HTTP::payload]
    set injection_patterns { "IGNORE ALL PREVIOUS INSTRUCTIONS" "ignore all" "SYSTEM:" }
    foreach pattern $injection_patterns {
        if { [string match -nocase "*$pattern*" $payload] } {
            HTTP::respond 403 content "Malicious input detected"
            return
        }
    }
}
```

### 2. Internal Proxy: Calypso AI
Calypso AI acts as an inspection proxy positioned immediately before the LLM, leveraging semantic analysis engines.

**Controls:**
- **Prompt Injection Scoring (LLM01)**: Intercepts manipulation attempts and heuristic evasion techniques against agent system prompts.
- **Data Loss Prevention (DLP)**: Scans outbound transmissions from Ollama to mask PII data prior to application handling (LLM02).

### 3. Application Hardening
Agent definitions incorporate validation strategies that reject unstructured outputs.

**Controls:**
- **Strict Parsing**: Implementation of Pydantic JSON validation. Malformed intent generation outside the defined JSON schemas causes the execution sequence to fail closed (LLM05).
- **Tool Access Limits**: The orchestrator enforces strict limitations on operable MCP tools to prevent unauthorized remote command execution (LLM06).
