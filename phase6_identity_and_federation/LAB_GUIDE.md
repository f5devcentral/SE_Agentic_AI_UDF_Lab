# Phase 6 Lab Guide — Zero Trust & Federation

## Goals

Every phase up to this point has been securing the perimeter: WAF rules at ingress, prompt inspection before the LLM, DLP on the way out. What has been missing is identity. The services inside the cluster trust each other because they can reach each other. If an attacker compromises any pod, they can call any service. If a user's session token is stolen, it works against every backend.

Phase 6 closes that gap by establishing **identity at every layer**. Users authenticate through Keycloak and receive a JWT. That token is carried as a Bearer header through every request. When the LangGraph orchestrator calls a specialist agent, it does not forward the user's token — it exchanges it at Keycloak for a scoped token that only works against that specific agent's audience. Each exchange is logged. BIG-IP APM validates the cryptographic signature of every inbound JWT using Keycloak's public key set. Between clusters, Envoy sidecars enforce mTLS using X.509 SVIDs issued by SPIRE, so even a valid JWT cannot reach a service from a workload that SPIRE has not attested.

The result is a system where every request carries a proof of who made it, what it is allowed to do, and which workload is making it — independently verifiable at every hop.

The objectives for this phase are:
- Deploy Keycloak on the OBS cluster with the `travel-realm` and the necessary clients.
- Implement OAuth2 Token Exchange (RFC 8693) in the LangGraph orchestrator so each agent call uses a scoped token.
- Configure BIG-IP APM virtual servers to validate JWTs against Keycloak's JWKS endpoint.
- Deploy SPIRE and provision X.509 SVIDs for workload-level mTLS across clusters.
- Demonstrate what happens when a request with a valid JWT arrives from an unatested workload.

---

## Architecture

```
Browser
  │
  └── HTTPS (BIG-IP APM) ──► Frontend (OIDC redirect → Keycloak on OBS)
                                │
                                │  Authorization: Bearer <user_jwt>
                                │
                                └── POST /api/plan
                                      │
                                      └── LangGraph Orchestrator
                                            │
                                            ├── exchange_jwt(user_jwt, "flight-agent-service")
                                            │     └── Keycloak Token Exchange (RFC 8693)
                                            │           └── scoped_jwt (aud: flight-agent-service)
                                            │
                                            ├── POST flight-agent /reason
                                            │     Authorization: Bearer <scoped_jwt>
                                            │     [BIG-IP APM validates aud + sig via JWKS]
                                            │     [Envoy sidecar enforces mTLS via SPIFFE SVID]
                                            │
                                            ├── exchange_jwt(user_jwt, "hotel-agent-service")
                                            │     └── scoped_jwt (aud: hotel-agent-service)
                                            │
                                            └── ... (same pattern for activity-agent, weather-agent)
```

**What changed from Phase 5:**

- The frontend redirects unauthenticated users to Keycloak. After login, it stores the issued JWT and attaches it to every API request.
- The LangGraph orchestrator calls `exchange_jwt()` before each `_call_external_agent()` invocation. Each call gets a fresh token scoped to exactly one agent audience.
- BIG-IP APM validates token signatures against Keycloak's `/.well-known/openid-configuration` JWKS. A token issued for `flight-agent-service` is rejected at the hotel-agent virtual server.
- Envoy sidecars on every pod hold X.509 SVIDs issued by SPIRE. All inter-service TCP connections require mutual TLS. A pod without a valid SVID cannot open a connection, regardless of what JWT it carries.

---

## Deployment

### Step 1 — Deploy Keycloak on the OBS cluster

```bash
kubectl config use-context OBS
kubectl create namespace iam
kubectl apply -f k8s/keycloak.yaml
kubectl rollout status deploy/keycloak -n iam
```

Keycloak initialises the `travel-realm` on startup. The manifest includes an init container that creates:
- `frontend-client`: the public OIDC client used by the browser
- `orchestrator-client`: the confidential client used by LangGraph for token exchange (client secret: `super-secret-key` — change this in any non-lab environment)

Verify Keycloak is up:

```bash
kubectl port-forward svc/keycloak 8180:8080 -n iam &
curl http://localhost:8180/realms/travel-realm/.well-known/openid-configuration | jq .issuer
```

The issuer should be `http://keycloak.iam.svc.cluster.local:8080/realms/travel-realm`. Note this is the cluster-internal address — services inside the cluster use it. BIG-IP will use the external NodePort address when fetching JWKS.

### Step 2 — Deploy SPIRE on the OBS cluster

SPIRE runs its server on OBS, which holds the federation trust bundle for all clusters.

```bash
kubectl config use-context OBS
kubectl apply -f k8s/spire-server.yaml
kubectl rollout status statefulset/spire-server -n spire
```

Check that the SPIRE server started and is ready to issue SVIDs:

```bash
kubectl exec -n spire statefulset/spire-server -- /opt/spire/bin/spire-server healthcheck
```

### Step 3 — Deploy SPIRE agents on the subordinate clusters

Each cluster that runs application workloads needs a SPIRE agent that joins the server on OBS.

```bash
kubectl config use-context APP_TOOLS
kubectl apply -f k8s/spire-agent.yaml

kubectl config use-context MCP
kubectl apply -f k8s/spire-agent.yaml

kubectl config use-context LLM
kubectl apply -f k8s/spire-agent.yaml
```

After the agents start, they synchronise the trust bundle from OBS. Verify synchronisation:

```bash
kubectl config use-context LLM
kubectl exec -n spire daemonset/spire-agent -- /opt/spire/bin/spire-agent healthcheck
```

Once the agents are running, Envoy sidecars on workload pods will begin requesting SVIDs. Give them 30–60 seconds to propagate. The SVID lifetime is 1 hour — SPIRE agents rotate them automatically in the background.

### Step 4 — Configure BIG-IP APM

The AS3 declaration in `k8s/bigip-apm.json` creates virtual servers with JWT validation policies for each agent service. Apply it to the BIG-IP management endpoint:

```bash
export BIGIP_IP=<your-bigip-management-ip>
export BIGIP_TOKEN=<your-auth-token>

curl -sk -X POST \
  https://$BIGIP_IP/mgmt/shared/appsvcs/declare \
  -H "Content-Type: application/json" \
  -H "X-F5-Auth-Token: $BIGIP_TOKEN" \
  -d @k8s/bigip-apm.json
```

The declaration creates one virtual server per agent service. Each virtual server has an APM policy that:
1. Extracts the Bearer token from the `Authorization` header.
2. Fetches the public keys from Keycloak's JWKS endpoint (cached for 5 minutes).
3. Validates the token's signature, expiry (`exp`), and audience (`aud`).
4. Rejects with `401 Unauthorized` if any check fails.

Verify the declaration was accepted:

```bash
curl -sk https://$BIGIP_IP/mgmt/shared/appsvcs/declare \
  -H "X-F5-Auth-Token: $BIGIP_TOKEN" | jq .declaration.class
```

### Step 5 — Deploy the secured frontend and orchestrator

```bash
kubectl config use-context LLM

kubectl apply -f k8s/frontend-oidc.yaml
kubectl rollout status deploy/frontend -n demo-travel

kubectl apply -f k8s/langgraph-orchestrator-secured.yaml
kubectl rollout status deploy/langgraph-orchestrator -n demo-travel
```

The `langgraph-orchestrator-secured.yaml` differs from the Phase 5 manifest in two ways: it adds `KEYCLOAK_TOKEN_URL`, `OAUTH_CLIENT_ID`, and `OAUTH_CLIENT_SECRET` environment variables for the token exchange, and it enables the SPIRE Envoy sidecar annotation so SPIRE provisions an SVID for the orchestrator pod.

Confirm the orchestrator pod has the sidecar:

```bash
kubectl get pod -n demo-travel -l app=langgraph-orchestrator -o jsonpath='{.items[0].spec.containers[*].name}'
```

You should see both `langgraph-orchestrator` and `envoy-proxy` in the container list.

---

## Testing

### Test 1 — OIDC login flow

Open the frontend NodePort in a browser. Because the BIG-IP APM policy requires a valid session, you will be redirected to the Keycloak login page at the OBS cluster.

Log in with the default `travel-realm` credentials (set in the Keycloak init container — check `k8s/keycloak.yaml` for the test user). After successful authentication, Keycloak redirects back to the frontend with an authorization code. The frontend exchanges the code for a JWT and stores it in localStorage.

Open the browser developer tools, go to Application → Local Storage, and confirm the JWT is present. Decode it at jwt.io — the `iss` field should be your Keycloak realm URL and the `aud` field should be `frontend-client`.

### Test 2 — Authenticated query

With the JWT in localStorage, submit a travel query through the frontend. Watch the Network tab in developer tools — the request to `/api/plan` should carry an `Authorization: Bearer <token>` header. The BIG-IP APM virtual server validates this before the request reaches the orchestrator.

You can confirm BIG-IP is validating by temporarily using an expired or malformed token:

```bash
curl -X POST http://<bigip-vip>:8000/api/plan \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer invalid.token.here" \
  -d '{"message": "test"}'
```

BIG-IP should return `401 Unauthorized` without the request reaching the orchestrator pod. Check the BIG-IP APM access logs to confirm it was rejected there.

### Test 3 — Verify token exchange in orchestrator logs

Submit a full travel query and watch the orchestrator logs:

```bash
kubectl logs -n demo-travel deploy/langgraph-orchestrator -f | grep exchange
```

For each agent the graph calls, you should see a structured log line:

```json
{"event": "token_exchange", "target": "flight-agent", "status": "success", "issued_aud": "flight-agent-service"}
```

There should be one exchange per agent call. If you count four agents in the query, expect four exchange log lines. Each exchange hits Keycloak with the user's original token and gets back a new JWT scoped to the specific agent's audience.

### Test 4 — Verify audience enforcement

This test confirms that a token issued for one agent cannot be used against a different agent's virtual server. Get a scoped token for `flight-agent-service` by triggering the token exchange manually, then try to use it against the hotel-agent virtual server:

```bash
# Get a user token from Keycloak (password grant for lab testing only)
USER_TOKEN=$(curl -s -X POST \
  http://<keycloak-nodeport>/realms/travel-realm/protocol/openid-connect/token \
  -d "client_id=frontend-client&grant_type=password&username=testuser&password=testpass" \
  | jq -r .access_token)

# Exchange it for a flight-agent-scoped token
FLIGHT_TOKEN=$(curl -s -X POST \
  http://<keycloak-nodeport>/realms/travel-realm/protocol/openid-connect/token \
  -d "client_id=orchestrator-client&client_secret=super-secret-key" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "subject_token=$USER_TOKEN" \
  -d "audience=flight-agent-service" \
  | jq -r .access_token)

# Try to use the flight-scoped token against the hotel-agent virtual server
curl -X POST http://<hotel-agent-vip>:8000/reason \
  -H "Authorization: Bearer $FLIGHT_TOKEN" \
  -d '{"messages": [], "itinerary_fragments": {}}'
```

BIG-IP should return `401 Unauthorized` because the token's `aud` claim is `flight-agent-service`, not `hotel-agent-service`. The hotel-agent virtual server rejects it. This is the core of the token exchange pattern — tokens are not reusable across agents.

### Test 5 — Verify mTLS enforcement

Identify a pod in the `demo-travel` namespace that does not have the SPIRE Envoy sidecar (for example, a debug pod you create without the SPIRE annotation):

```bash
kubectl run test-pod --image=curlimages/curl:latest -n demo-travel -- sleep 3600
```

Wait for it to start, then try to reach a service that requires mTLS:

```bash
kubectl exec -n demo-travel test-pod -- \
  curl -H "Authorization: Bearer $FLIGHT_TOKEN" \
  http://flight-agent:8001/reason \
  -d '{"messages": [], "itinerary_fragments": {}}'
```

The connection should be reset at the transport layer before any HTTP is exchanged. The Envoy sidecar on the flight-agent pod requires mutual TLS — the test pod has no SVID to present, so the TLS handshake fails. This happens regardless of whether the JWT is valid. JWT and mTLS are independent security controls that must both pass.

Clean up the test pod:

```bash
kubectl delete pod test-pod -n demo-travel
```

---

## Troubleshooting

### `401 Unauthorized` from BIG-IP APM on every request

First check that BIG-IP can reach Keycloak's JWKS endpoint:

```bash
curl https://$BIGIP_IP/mgmt/tm/ltm/virtual \
  -H "X-F5-Auth-Token: $BIGIP_TOKEN" | jq '.items[] | .name'
```

Then confirm the JWKS URL in the APM policy matches your Keycloak external address. The internal `keycloak.iam.svc.cluster.local` address is not reachable from BIG-IP — the APM policy must use the NodePort or external IP.

If the JWKS is reachable but tokens are still rejected, check the token's `iss` claim against what Keycloak actually issues. The issuer in the token must exactly match the issuer in the APM policy configuration.

### Token exchange returns `400 Bad Request` from Keycloak

```bash
kubectl logs -n demo-travel deploy/langgraph-orchestrator | grep "token_exchange"
```

The most common cause is the `audience` parameter not matching a client that exists in the `travel-realm`. The `flight-agent-service`, `hotel-agent-service`, `activity-agent-service`, and `weather-agent-service` audience identifiers must each be registered as clients in Keycloak with `service_accounts_enabled: true`.

Verify the clients exist:

```bash
kubectl port-forward svc/keycloak 8180:8080 -n iam &
curl http://localhost:8180/admin/realms/travel-realm/clients \
  -H "Authorization: Bearer <admin_token>" | jq '.[].clientId'
```

### SPIRE agent fails to join server

```bash
kubectl logs -n spire daemonset/spire-agent | tail -30
```

If you see `failed to fetch bundle`, the agent cannot reach the SPIRE server on the OBS cluster. Check that the server's NodePort is reachable from the worker cluster node and that the `trust_domain` in the agent configuration matches the server's configured trust domain.

### Envoy sidecar not receiving SVID

```bash
kubectl exec -n demo-travel deploy/langgraph-orchestrator -c envoy-proxy -- \
  curl -s http://localhost:15000/certs | jq .
```

If the response shows no certificates, the SPIRE agent on the LLM cluster has not attested the pod. This usually happens when the pod's service account is not registered with SPIRE as a valid workload selector. Check the SPIRE registration entries:

```bash
kubectl exec -n spire statefulset/spire-server -- \
  /opt/spire/bin/spire-server entry show
```

If there is no entry for the orchestrator's service account, create one:

```bash
kubectl exec -n spire statefulset/spire-server -- \
  /opt/spire/bin/spire-server entry create \
  -spiffeID spiffe://travel.demo/demo-travel/langgraph-orchestrator \
  -parentID spiffe://travel.demo/spire/agent/k8s_sat/llm-cluster/<node-uid> \
  -selector k8s:sa:langgraph-orchestrator \
  -selector k8s:ns:demo-travel
```

---

## Phase Highlights

### Why Token Exchange Instead of Forwarding

The naive approach to JWT propagation in a multi-service architecture is to forward the user's token. Service A receives a token, passes it to Service B, which passes it to Service C. This is simpler to implement but fails a basic security requirement: the token proves the user's identity but says nothing about whether Service B is authorised to act as Service A.

If Service A is compromised, it can call Service C directly with the user's token and impersonate a legitimate request path. If the user's token is captured in transit, it works against every service in the chain.

RFC 8693 Token Exchange breaks this by requiring each service to explicitly request a token scoped to its immediate downstream. The orchestrator cannot get a `hotel-agent-service` token without presenting valid `orchestrator-client` credentials to Keycloak. If an attacker captures the scoped token in flight, it only works against that one agent. If the orchestrator is compromised, the attacker can impersonate the orchestrator but cannot directly call agents — each call still requires a new exchange at Keycloak.

In the LangGraph implementation, `invoke_agent_securely()` in `token_exchange.py` wraps every `_call_external_agent()` call. The pattern is: exchange the user's subject token for an audience-scoped token, then use that token for the downstream HTTP call. The orchestrator never reuses tokens across agents.

### BIG-IP APM and Cryptographic Validation

The BIG-IP APM validation in this phase is different in kind from the WAF rules in earlier phases. WAF rules match patterns — they look for known attack signatures in the request content. JWT validation is a mathematical operation: BIG-IP fetches Keycloak's public key set, verifies the token's signature against those keys, and checks the standard claims (`exp`, `aud`, `iss`). A token that passes is cryptographically proven to have been issued by that specific Keycloak realm.

This matters because it closes a class of attack that WAF rules cannot address. An attacker who constructs a forged token — one with a valid-looking structure but not signed by Keycloak — is rejected at the APM layer before the request enters the cluster. The check happens at the perimeter, not inside the application.

The JWKS endpoint caching in APM (5-minute TTL) is a practical consideration. Keycloak rotates signing keys periodically. If the cache is stale when a rotation happens, tokens signed with the new key will be rejected until the cache refreshes. In production you would tune this TTL based on your key rotation schedule.

### SPIFFE/SPIRE: Workload Identity

The Keycloak JWT answers who the user is. SPIRE answers what workload is making the request. These are separate identity questions and you need both answers to enforce Zero Trust properly.

SPIRE attests workloads at startup. A SPIRE agent on each node watches which pods start, checks their Kubernetes service account and namespace against registered workload selectors, and issues short-lived X.509 SVIDs to pods that match. The SVID is a standard TLS certificate with a SPIFFE URI in the Subject Alternative Name field — `spiffe://travel.demo/demo-travel/langgraph-orchestrator` for the orchestrator pod, for example.

Envoy sidecars use these SVIDs to establish mutual TLS on every connection. When the orchestrator connects to flight-agent, both sides present their SVIDs. The Envoy on flight-agent verifies that the orchestrator's SVID was issued for an identity it is willing to accept traffic from. A test pod without a SVID cannot complete the mTLS handshake regardless of what JWT it carries — the connection fails at the TCP layer.

The short SVID lifetime (1 hour) is a deliberate security property. Compromised SVIDs expire quickly. SPIRE agents rotate them before expiry, so legitimate workloads experience no interruption. The only way to maintain a valid SVID is to be a legitimate, attested workload — which means the SPIRE server trusts the workload's Kubernetes identity, which means it has not been tampered with.

### The Combined Control Stack

It is worth being explicit about what each control layer defends against and where it sits, because they are independent and the distinction matters when something fails.

**BIG-IP AWAF (Phase 1/4)** — Pattern-based attack signatures at the HTTP/application layer. Blocks known injection strings before they reach any processing.

**Calypso AI via BiFrost (Phase 4/5)** — Semantic inspection of prompts and LLM outputs. Detects adversarial content that looks legitimate to pattern matchers.

**BIG-IP APM JWT validation (Phase 6)** — Cryptographic verification that every inbound request carries a token signed by a trusted issuer, with the right audience and unexpired claims.

**OAuth2 Token Exchange (Phase 6)** — Ensures east-west calls use scoped tokens. Captures every agent invocation as an auditable Keycloak token exchange event.

**SPIFFE/SPIRE mTLS (Phase 6)** — Transport-layer workload authentication. Prevents any unatested process from opening a connection to protected services.

None of these layers makes the others redundant. They defend different threat vectors independently. An attacker who bypasses WAF pattern matching still hits semantic inspection. An attacker who steals a JWT still needs a valid SVID to reach the target service. This is defence in depth applied to an agentic AI system.

### Token Economy and Audit

Phase 6 adds Keycloak token exchange calls to the cost baseline. Each exchange is a synchronous HTTP request to Keycloak — typically 20–50ms in a well-provisioned environment, but that is 4–5 extra round trips per orchestrator request. On a lab deployment where Keycloak is on a separate cluster node, this can add 200–400ms.

More importantly, every token exchange creates an entry in Keycloak's event log. This means every agent invocation — not just every user request — is an auditable event with a timestamp, the user identity, the target audience, and the result. For regulated industries where you need to prove that a specific user's request triggered a specific agent action at a specific time, this log is the evidence trail.

```bash
kubectl port-forward svc/keycloak 8180:8080 -n iam &
curl http://localhost:8180/admin/realms/travel-realm/events \
  -H "Authorization: Bearer <admin_token>" \
  | jq '.[] | select(.type == "TOKEN_EXCHANGE") | {time, userId, details}'
```

### Business Value for Enterprises

Enterprises adopt Zero Trust for agentic systems for the same reason they adopt it everywhere else: the perimeter cannot be assumed to hold. When an AI agent can call external APIs, write to databases, and trigger automated actions, the consequence of a compromised agent is much larger than the consequence of a compromised static web page.

The specific combination in this phase — OIDC for user identity, Token Exchange for scoped propagation, APM for cryptographic validation, SPIRE for workload attestation — maps directly to the controls that enterprise security teams require before they will approve a system for production use.

Concretely:
- **Audit trails** exist at every layer: BIG-IP access logs, Keycloak event logs, OTel spans, SPIRE attestation records.
- **Blast radius is bounded** by scoped tokens. A compromised agent can call what it is allowed to call, not everything in the cluster.
- **Identity is verifiable** without trusting the network. The JWT proves the user. The SVID proves the workload. Neither relies on IP-based trust.
- **Key rotation is automated**. SPIRE rotates SVIDs hourly. Keycloak signing key rotation updates the JWKS endpoint that BIG-IP polls — no manual certificate management.

What was demonstrated as a travel booking lab throughout these six phases is the same architecture an enterprise would use for an AI system that handles financial approvals, healthcare decisions, or any domain where the actions the AI takes need to be provably authorized at each step.
