# Phase 2: Deployment & Troubleshooting

## Deployment Guide

### Step 1: Verify Registry Status
```bash
kubectl get pods -n registry
curl http://mcpregistry:30500/v2/_catalog
# Expected: {"repositories":[]}
```

### Step 2: Build and Push MCP Servers

**Travel MCP Server:**
```bash
cd mcp-servers
docker build -f travel-mcp/Dockerfile -t localhost:30500/demo-travel/travel-mcp:latest .
docker push localhost:30500/demo-travel/travel-mcp:latest
```

**Weather MCP Server:**
```bash
docker build -f weather-mcp/Dockerfile -t localhost:30500/demo-travel/weather-mcp:latest .
docker push localhost:30500/demo-travel/weather-mcp:latest
```

**Frontend:**
```bash
cd ../frontend
cp -r ../mcp-servers/shared .
docker build -t localhost:30500/demo-travel/frontend:latest .
docker push localhost:30500/demo-travel/frontend:latest
```

### Step 3: Create Namespace and Secrets
```bash
kubectl create namespace demo-travel
kubectl create secret generic travel-db-secret \
  --namespace=demo-travel \
  --from-literal=username=travel \
  --from-literal=password=travelpass
```

### Step 4: Deploy the Components

**Deploy CRDs:**
```bash
cd ../k8s
kubectl apply -f crds/mcpcard-crd.yaml
```

**Deploy MCP Servers:**
```bash
kubectl apply -f travel-mcp/
kubectl rollout status deployment/travel-mcp -n demo-travel

kubectl apply -f weather-mcp/
kubectl rollout status deployment/weather-mcp -n demo-travel
```

**Deploy Frontend (Optional):**
```bash
kubectl apply -f frontend/deployment.yaml
kubectl rollout status deployment/frontend -n demo-travel
```

---

## Troubleshooting

### Image Pull Errors (`ErrImagePull` or `ImagePullBackOff`)
```bash
curl http://localhost:30500/v2/demo-travel/travel-mcp/tags/list
# Ensure K3s is configured to trust the insecure registry (edit /etc/rancher/k3s/registries.yaml)
```

### Pod CrashLoopBackOff
```bash
kubectl logs -n demo-travel <pod-name>
kubectl describe pod -n demo-travel <pod-name>
```

### Observability Stack Connectivity
If using OTel, verify connectivity from the MCP pods to the collector:
```bash
kubectl get svc -n demo-travel otel-collector
kubectl exec -n demo-travel -it <travel-mcp-pod> -- curl http://otel-collector:4317
```
