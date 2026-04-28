# Phase 6: Goals and Reminders

## Objective
Phase 6 establishes a Zero Trust identity architecture across the disconnected Kubernetes environments (`APP_TOOLS`, `MCP`, `LLM`, `OBS`). The architecture implements precise cryptographic workload isolation via SPIFFE/SPIRE and strict user identity token propagation via Keycloak OIDC.

## Architecture Configuration

### Keycloak OIDC Federation
A centralized Keycloak instance runs on the `OBS` Kubernetes cluster.
- **Frontend Authentication**: The application frontend forces user authentication via OIDCv2 prior to fulfilling search requests.
- **Bearer Token Passage**: Authenticated searches utilize the resulting JSON Web Token (JWT) supplied as the Bearer parameter within API headers.

### LangGraph Token Exchange
East-West logic executed by LangGraph is evaluated against access models.
- **OAuth2 Token Exchange**: Before initiating HTTP payloads to underlying external agents (e.g., `flight-agent:8000`), the Python logic extracts the user's JWT and requests a down-scoped JWT restricted explicitly for the target audience via Keycloak's Token Exchange protocol.

### BIG-IP Service Validation
Each microservice is fronted natively by a BIG-IP Virtual Server endpoint rather than relying solely on Kubernetes DNS topology.
- **APM Validation**: The BIG-IP Access Policy Manager (APM) intercepts traffic, validating the cryptographic signature of the JWT against the Keycloak JWKS framework.

### SPIFFE and mTLS
Authentication payloads (JWTs) transmit across a mutually authenticated (mTLS) transport layer orchestrated by SPIFFE.
- **SVID Injection**: SPIRE daemonsets provision Envoy sidecars with short-lived X.509 SVIDs, authenticating both extremities of active TCP connections operating across the disparate cluster spaces.
