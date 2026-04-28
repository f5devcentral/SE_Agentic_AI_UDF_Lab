# Phase 6: Usage & Tests

## Testing Identity Propagation

The implementation of OAuth2 Token Exchange and SPIFFE validation alters baseline testing behaviors. Bypassing the authentication pathways directly via generic `curl` commands without Bearer tokens is disabled by the BIG-IP APM configuration.

### Test 1: Frontend Authentication Flow

1. Access the Frontend component exposed via NodePort on the `LLM` cluster.
2. The UI automatically generates a redirect response referencing the `OBS` Keycloak instance.
3. Authenticate using credentials initialized within `travel-realm`.
4. Validate the browser `localStorage` successfully mounted the issued JWT payload.

### Test 2: Natural Language Query Initiation

With the UI authenticated:
1. Input travel parameters (e.g., "Flights to Barcelona").
2. The UI constructs an `Authorization: Bearer <token>` sequence applied against the `/api/plan` target.

### Test 3: Validate East-West OIDC Token Exchange

Monitor the LangGraph Orchestrator stdout output or OTLP spans to verify execution of the Token Exchange operation.

```bash
kubectl logs deploy/langgraph-orchestrator -n demo-travel | grep "exchange"
```

Expected output syntax:
```json
{"event": "token_exchange", "target": "flight-agent", "status": "success", "issued_aud": "flight-agent-service"}
```

### Test 4: Validate mTLS Transport Security

Attempt network execution against a microservice from a pod lacking the required SPIFFE SVID Envoy sidecar.

```bash
kubectl exec -it <unauthorized-pod> -- curl -H "Authorization: Bearer <valid-jwt>" http://bigip-vip:8000/flights
```
*Expected Result:*
Execution fails at the transport layer before L7 HTTP parsing, returning a connection reset due to mTLS verification failure.
