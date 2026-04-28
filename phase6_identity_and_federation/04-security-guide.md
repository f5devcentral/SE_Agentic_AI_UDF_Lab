# Phase 6: Security Guide

Phase 6 implements the core Zero Trust specifications for cross-boundary network interactions.

## Identity Protocols

Access boundaries correlate identity definitions across user authorization (L7) and workload specification (L4).

### 1. Keycloak Token Exchange
Standard JWT bearer tokens passed linearly are considered insecure within distributed networks due to lateral movement vulnerabilities. To resolve this, Phase 6 implements **OAuth2 Token Exchange (RFC 8693)**.
- The `LangGraph` internal execution processes user intent. Instead of forwarding the user's primary token to the backing agents, it exchanges the token at the Keycloak endpoint.
- Keycloak evaluates if the user identity holds authorization for the requested sub-operation, producing a narrowly scoped JWT mapped explicitly to the designated agent.

### 2. BIG-IP APM Validation
Kubernetes internal clustering services do not natively analyze cryptography payloads. F5 BIG-IP serves as the L7 validation engine bridging the east-west boundaries.
- Virtual Servers utilize Advanced Access Policy Manager configurations to intercept incoming payloads.
- The payloads are evaluated mathematically utilizing Keycloak's dynamic JSON Web Key Sets (JWKS) parameter file, ensuring tokens were uniquely signed utilizing active registry keys and the payload timeframe (`exp`) remains valid.

### 3. SPIFFE mTLS Definitions
JWT structures defend application intent but remain susceptible to network interception during transport execution segments.
- SPIRE agents validate pod execution logic by enforcing workload definition evaluations (verifying the active SHA values of executed binaries against expected runtime values).
- Valid workloads receive verifiable identity structures (x509 SVIDs).
- Envoy sidecars utilize the SVIDs to construct strict mTLS boundaries for all operations, negating standard TCP spoofing protocols across the clusters.
