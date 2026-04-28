



```bash
kubectl apply -f k8s/crds/mcpcard-crd.yaml
kubectl apply -f k8s/travel-mcp/
kubectl apply -f k8s/weather-mcp/
# Then rebuild and redeploy the frontend with the new env vars:
# TRAVEL_MCP_URL=http://<NODE_IP>:30100/mcp
# WEATHER_MCP_URL=http://<NODE_IP>:30101/mcp
```


# MCP Servers Deployment Guide — K3s + Local Registry

Complete step-by-step guide to build, push and deploy the Travel and Weather MCP servers to your K3s cluster with a local registry at `localhost:30500`.

---

## Prerequisites

- K3s cluster running
- Local registry deployed at NodePort 30500
- kubectl configured to access the cluster
- Docker installed on your build machine

---
## Step 0: Verify on which Kubernetes Context you are in:

``bash
kubectl config get-contexts
kubectl config use-context MCP
``


## Step 1: Verify Registry is Running

```bash
# Check registry pod is up
kubectl get pods -n registry

# Test registry connectivity
curl http://mcpregistry:30500/v2/_catalog
# Expected: {"repositories":[]}
```

---

## Step 2: Prepare the Build Context

Organize your files like this:

```
mcp-deployment/
├── mcp-servers/
│   ├── shared/
│   │   └── otel.py
│   ├── travel-mcp/
│   │   ├── server.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── weather-mcp/
│       ├── server.py
│       ├── requirements.txt
│       └── Dockerfile
├── frontend/
│   ├── app.py
│   ├── mcp_client.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── templates/
│       └── index.html
└── k8s/
    ├── crds/
    │   └── mcpcard-crd.yaml
    ├── travel-mcp/
    │   ├── deployment.yaml
    │   ├── service.yaml
    │   └── mcpcard.yaml
    └── weather-mcp/
        ├── deployment.yaml
        ├── service.yaml
        └── mcpcard.yaml
```

---

## Step 3: Build and Push Travel MCP Server

```bash
cd mcp-deployment/mcp-servers

# Build the image
docker build \
  -f travel-mcp/Dockerfile \
  -t localhost:30500/demo-travel/travel-mcp:latest \
  .

# Verify image exists locally
docker images | grep travel-mcp

# Push to local registry
docker push localhost:30500/demo-travel/travel-mcp:latest

# Verify push succeeded
curl http://localhost:30500/v2/demo-travel/travel-mcp/tags/list
# Expected: {"name":"demo-travel/travel-mcp","tags":["latest"]}
```

---

## Step 4: Build and Push Weather MCP Server

```bash
# Still in mcp-servers/

# Build the image
docker build \
  -f weather-mcp/Dockerfile \
  -t localhost:30500/demo-travel/weather-mcp:latest \
  .

# Push to local registry
docker push localhost:30500/demo-travel/weather-mcp:latest

# Verify
curl http://localhost:30500/v2/demo-travel/weather-mcp/tags/list
```

---

## Step 5: Build and Push Frontend

```bash
cd ../frontend

# Build (note: this Dockerfile expects shared/ to be copied in already)
# You may need to adjust the build context - either:
# OPTION A: Copy shared into frontend/ first
cp -r ../mcp-servers/shared .

docker build \
  -t localhost:30500/demo-travel/frontend:latest \
  .

# OPTION B: Build from parent directory with -f flag
# cd ..
# docker build -f frontend/Dockerfile -t localhost:30500/demo-travel/frontend:latest .

# Push to registry
docker push localhost:30500/demo-travel/frontend:latest

# Verify
curl http://localhost:30500/v2/demo-travel/frontend/tags/list
```

---

## Step 6: Update K8s Manifests for Local Registry

Edit the deployment files to reference your local registry:

**k8s/travel-mcp/deployment.yaml:**
```yaml
spec:
  template:
    spec:
      containers:
        - name: travel-mcp
          image: localhost:30500/demo-travel/travel-mcp:latest
          imagePullPolicy: Always  # Force pull on every deployment
```

**k8s/weather-mcp/deployment.yaml:**
```yaml
spec:
  template:
    spec:
      containers:
        - name: weather-mcp
          image: localhost:30500/demo-travel/weather-mcp:latest
          imagePullPolicy: Always
```

---

## Step 7: Create Namespace and Secrets

```bash
# Create demo-travel namespace
kubectl create namespace demo-travel

# Create PostgreSQL credentials secret (for travel-mcp)
kubectl create secret generic travel-db-secret \
  --namespace=demo-travel \
  --from-literal=username=travel \
  --from-literal=password=travelpass

# Verify secret exists
kubectl get secrets -n demo-travel
```

---

## Step 8: Deploy the CRD

```bash
cd ../k8s

# Apply the MCPCard Custom Resource Definition
kubectl apply -f crds/mcpcard-crd.yaml

# Verify CRD is registered
kubectl get crd mcpcards.travel.demo
```

---

## Step 9: Deploy Travel MCP Server

```bash
# Apply all travel-mcp manifests
kubectl apply -f travel-mcp/

# Watch deployment rollout
kubectl rollout status deployment/travel-mcp -n demo-travel

# Check pod is running
kubectl get pods -n demo-travel -l app=travel-mcp

# Check logs
kubectl logs -n demo-travel -l app=travel-mcp --tail=50

# Verify NodePort service
kubectl get svc -n demo-travel travel-mcp
# Should show NodePort 30100

# Test from outside the cluster
curl http://localhost:30100/health
# Expected: HTTP 200 (may need to add /health endpoint to server.py)
```

---

## Step 10: Deploy Weather MCP Server

```bash
# Apply all weather-mcp manifests
kubectl apply -f weather-mcp/

# Watch deployment
kubectl rollout status deployment/weather-mcp -n demo-travel

# Check pod
kubectl get pods -n demo-travel -l app=weather-mcp

# Check logs
kubectl logs -n demo-travel -l app=weather-mcp --tail=50

# Verify service
kubectl get svc -n demo-travel weather-mcp
# Should show NodePort 30101

# Test
curl http://localhost:30101/health
```

---

## Step 11: Test MCP Servers with fastmcp CLI

```bash
# Install fastmcp CLI (if not already installed)
pip install fastmcp

# List tools on travel-mcp
fastmcp inspect http://localhost:30100/mcp

# Call search_flights
fastmcp call http://localhost:30100/mcp search_flights \
  --input '{"origin":"Paris","destination":"Barcelona","date":"2025-06-15"}'

# List tools on weather-mcp
fastmcp inspect http://localhost:30101/mcp

# Call get_weather_forecast
fastmcp call http://localhost:30101/mcp get_weather_forecast \
  --input '{"city":"Barcelona","date":"2025-06-15"}'
```

---

## Step 12: Deploy Frontend (Optional — if running in K8s)

If you want to run the frontend in K8s as well (instead of Docker Compose):

**Create `k8s/frontend/deployment.yaml`:**
```yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
  namespace: demo-travel
spec:
  replicas: 1
  selector:
    matchLabels:
      app: frontend
  template:
    metadata:
      labels:
        app: frontend
    spec:
      containers:
        - name: frontend
          image: localhost:30500/demo-travel/frontend:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 5000
          env:
            - name: TRAVEL_MCP_URL
              value: "http://travel-mcp:8000/mcp"
            - name: WEATHER_MCP_URL
              value: "http://weather-mcp:8001/mcp"
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: "http://otel-collector:4317"
---
apiVersion: v1
kind: Service
metadata:
  name: frontend
  namespace: demo-travel
spec:
  type: NodePort
  selector:
    app: frontend
  ports:
    - port: 5000
      targetPort: 5000
      nodePort: 30080
```

Deploy:
```bash
kubectl apply -f k8s/frontend/deployment.yaml
kubectl rollout status deployment/frontend -n demo-travel

# Access UI
open http://localhost:30080
```

---

## Step 13: View MCPCards (Custom Resources)

```bash
# List all MCP cards
kubectl get mcpcards -n demo-travel

# Describe travel-mcp card
kubectl describe mcpcard travel-mcp-card -n demo-travel

# Get as YAML
kubectl get mcpcard travel-mcp-card -n demo-travel -o yaml
```

---

## Troubleshooting

### Image Pull Errors

If you see `ErrImagePull` or `ImagePullBackOff`:

```bash
# Check if image exists in registry
curl http://localhost:30500/v2/demo-travel/travel-mcp/tags/list

# Verify K3s can reach the registry
kubectl run test --image=localhost:30500/demo-travel/travel-mcp:latest --rm -it -- /bin/sh

# If K3s can't pull, you may need to configure it to trust the insecure registry
# Edit /etc/rancher/k3s/registries.yaml on the K3s node:
mirrors:
  "localhost:30500":
    endpoint:
      - "http://localhost:30500"

# Then restart K3s
sudo systemctl restart k3s
```

### Pod CrashLoopBackOff

```bash
# Check logs
kubectl logs -n demo-travel <pod-name>

# Common issues:
# - Missing OTEL_EXPORTER_OTLP_ENDPOINT if otel-collector isn't deployed
# - Missing PG_HOST/PG_USER/PG_PASSWORD for travel-mcp
# - Port conflicts

# Describe pod for events
kubectl describe pod -n demo-travel <pod-name>
```

### Database Connection Errors (travel-mcp only)

Travel-mcp needs PostgreSQL. If you don't have it in K8s yet:

```bash
# Quick PostgreSQL deployment (same as docker-compose setup)
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: demo-travel
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:15
          env:
            - name: POSTGRES_USER
              value: travel
            - name: POSTGRES_PASSWORD
              value: travelpass
            - name: POSTGRES_DB
              value: travel
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: init-sql
              mountPath: /docker-entrypoint-initdb.d
      volumes:
        - name: init-sql
          configMap:
            name: postgres-init
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: demo-travel
spec:
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: postgres-init
  namespace: demo-travel
data:
  init.sql: |
    CREATE TABLE IF NOT EXISTS activities (
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        city VARCHAR(100)
    );
    INSERT INTO activities (title, description, city) VALUES
        ('Sagrada Familia', 'Gaudí''s unfinished masterpiece', 'Barcelona'),
        ('Park Güell', 'Mosaic terraces with city views', 'Barcelona'),
        ('La Boqueria Market', 'Famous food market on Las Ramblas', 'Barcelona'),
        ('Eiffel Tower', 'Visit the symbol of Paris', 'Paris'),
        ('Louvre Museum', 'Home of the Mona Lisa', 'Paris'),
        ('Seine River Cruise', 'See Paris from the water', 'Paris'),
        ('Colosseum Tour', 'Explore the iconic ancient amphitheatre', 'Rome'),
        ('Vatican Museums', 'World-renowned art and history', 'Rome'),
        ('City Walking Tour', 'Explore the historic city center on foot', NULL),
        ('Cooking Class', 'Learn to cook traditional local dishes', NULL);
EOF
```

---

## check if Frontend has its MCP Clients functional

kubectl exec -it -n demo-travel deploy/frontend -- sh
# python3
Python 3.11.14 (main, Feb 24 2026, 19:44:43) [GCC 14.2.0] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> from mcp_client import search_flights
>>> 
>>> search_flights(
...     "http://travel-mcp:8000/mcp",
...     "Paris",
...     "Barcelona",
...     "2025-06-15"
... )



---

## Quick Rebuild & Redeploy

When you change code:

```bash
# Rebuild
cd mcp-servers
docker build -f travel-mcp/Dockerfile -t localhost:30500/demo-travel/travel-mcp:latest .
docker push localhost:30500/demo-travel/travel-mcp:latest

# Force pod restart (K8s will pull the new image)
kubectl rollout restart deployment/travel-mcp -n demo-travel

# Watch it come up
kubectl rollout status deployment/travel-mcp -n demo-travel
```

---

## Observability Stack (Prometheus, Loki, Jaeger, OTel Collector)

If you mentioned these are already running, verify the MCP pods can reach them:

```bash
# Check if otel-collector service exists
kubectl get svc -n demo-travel otel-collector
# If not, you need to deploy it or update OTEL_EXPORTER_OTLP_ENDPOINT

# Test connectivity from inside a pod
kubectl exec -n demo-travel -it <travel-mcp-pod> -- curl http://otel-collector:4317
```

If you need to deploy the stack, let me know and I can provide those manifests too.

---

## Summary Commands

```bash
# Full deployment from scratch
kubectl create namespace demo-travel
kubectl create secret generic travel-db-secret -n demo-travel --from-literal=username=travel --from-literal=password=travelpass
kubectl apply -f k8s/crds/mcpcard-crd.yaml
kubectl apply -f k8s/travel-mcp/
kubectl apply -f k8s/weather-mcp/

# Check everything
kubectl get all -n demo-travel
kubectl get mcpcards -n demo-travel

# Test MCP endpoints
curl http://localhost:30100/mcp
curl http://localhost:30101/mcp

# View logs
kubectl logs -n demo-travel -l app=travel-mcp --tail=100 -f
kubectl logs -n demo-travel -l app=weather-mcp --tail=100 -f
```
