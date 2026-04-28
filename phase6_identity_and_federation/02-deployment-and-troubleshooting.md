# Phase 6: Deployment & Troubleshooting

## Deployment Guide

### Step 1: Deploy Keycloak (OBS Cluster)
Switch the terminal context to the OBS cluster and initialize the Keycloak IAM instance.

```bash
kubectl config use-context OBS
kubectl apply -f k8s/keycloak.yaml
kubectl rollout status deploy/keycloak -n iam
```

*Note: The YAML manifest automates the initialization of the `travel-realm` and standard `frontend-client` parameters via startup scripts.*

### Step 2: Establish SPIRE Federation
Deploy the SPIRE architecture across the distributed clusters. The `OBS` cluster serves as the federation trust anchor holding the global bundle.

Deploy Server on OBS:
```bash
kubectl apply -f k8s/spire-server.yaml
```

Deploy Agents on subordinate clusters (`MCP`, `LLM`, `APP_TOOLS`):
```bash
kubectl config use-context APP_TOOLS
kubectl apply -f k8s/spire-agent.yaml
```

### Step 3: Configure BIG-IP Virtual Servers
Execute AS3 definitions referencing APM JWT policies against the application deployments.
```bash
# Example execution targeting the BIG-IP management endpoint
curl -X POST https://$BIGIP_IP/mgmt/shared/appsvcs/declare -H 'Auth-Token: <token>' -d @k8s/bigip-apm.json
```

### Step 4: Deploy Frontend and Modified Orchestrator
Target the `LLM` context. The LangGraph Python image now requires the URI configurations pointing to Keycloak and the BIG-IP Virtual Servers.

```bash
kubectl config use-context LLM
kubectl apply -f k8s/frontend-oidc.yaml
kubectl apply -f k8s/langgraph-orchestrator-secured.yaml
```

---

## Troubleshooting

### JWT Audience Rejection (HTTP 401)
If east-west requests fail at the BIG-IP with `401 Unauthorized`, verify the logic inside `token_exchange.py` correctly requests an `--audience` parameter correlating to the targeted virtual server during the OAuth2 exchange.

### SPIFFE Connection Resets (HTTP 503)
Traffic failing to transit between clusters is indicative of mTLS failures.
1. Check the Envoy sidecar logs for `certificate revoked` or mismatch trust domains.
2. Ensure the SPIRE agent on the satellite cluster correctly synchronized the federated trust bundle from the `OBS` cluster.
