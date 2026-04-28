# Phase 4: Deployment & Troubleshooting

## Deployment Guide

### Step 1: Deploy BIG-IP AI Guardrails (iRules)
Authenticate to the F5 BIG-IP Console. Apply the custom iRules designated for MCP and Prompt traffic inspection (`04-security-guide.md` contains rule details).

Bind the iRules to the VIP representing the Orchestrator ingress:
```bash
tmsh modify ltm virtual orchestrator_vip rules { AWAF_LLM_Guard }
```

### Step 2: Configure Calypso AI
Deploy the Calypso AI proxy sidecar. Update the `k8s/orchestrator.yaml` manifest to route LLM calls (`http://ollama:11434`) through the Calypso AI proxy (`http://calypso:8080`).

```yaml
env:
  - name: OLLAMA_ENDPOINT
    value: "http://calypso:8080" # Previously http://ollama:11434
```

Apply the updated manifests:
```bash
kubectl apply -f k8s/calypso-proxy.yaml
kubectl apply -f k8s/orchestrator.yaml
kubectl rollout status deploy/orchestrator
```

### Step 3: Deploy Red Teaming Tools
Deploy the Red Teaming interactive job to script attack vectors against the stack.
```bash
kubectl apply -f k8s/red-teamer.yaml
```

---

## Troubleshooting

### Calypso API Connection Refused
If the orchestrator fails to reach `http://calypso:8080`, verify the Calypso proxy pod is authenticating using the correct tenant variables. Review the pod logs:
```bash
kubectl logs deploy/calypso-proxy -n demo-travel
```

### F5 WAF False Positives
If legitimate agent interactions return `HTTP 403 Forbidden` from the BIG-IP:
1. Review the Traffic Learning logs in the F5 configuration utility.
2. Ensure the `Awaf_LLM_Guard` iRule regular expressions are not matching valid JSON structures.
