# Phase 1: Kubernetes Deployment Guide

This guide provides instructions to deploy the Phase 1 baseline 3-tier microservices application (Frontend, Flights, Hotels, PostgreSQL) to a K3s cluster.
This setup serves as the foundation for the Agentic AI Lab, leveraging Kubernetes NodePorts and **F5 AWAF (ASM)** for application protection before we introduce AI and Calypso AI guardrails in later phases.

## Prerequisites
- K3s cluster provisioned and running.
- `kubectl` configured and authenticated to the target cluster (e.g., `APP_TOOLS` context).
- A local Docker registry (e.g., exposed on NodePort `30500`).
- **F5 AWAF (ASM)** configured to front the K3s node ports.

## Step 1: Cluster Context
Ensure you are targeting the appropriate K3s cluster:
```bash
kubectl config get-contexts
kubectl config use-context APP_TOOLS
```

## Step 2: Build and Push Images
Build the Phase 1 images from the source and push them to the local registry so the K3s cluster can pull them:
```bash
cd phase1_current_3tier_app

# Build & Push Flights API
docker build -f flights/Dockerfile -t localhost:30500/demo-travel/flights:latest .
docker push localhost:30500/demo-travel/flights:latest

# Build & Push Hotels API
docker build -f hotels/Dockerfile -t localhost:30500/demo-travel/hotels:latest .
docker push localhost:30500/demo-travel/hotels:latest

# Build & Push Frontend
docker build -f frontend/Dockerfile -t localhost:30500/demo-travel/frontend:latest .
docker push localhost:30500/demo-travel/frontend:latest
```

## Step 3: Kubernetes Deployment
Apply the Kubernetes manifests for the application stack.

```bash
cd k8s

# Create namespace
kubectl create namespace demo-travel

# Create the Secret for PostgreSQL Credentials
kubectl create secret generic travel-db-secret -n demo-travel \
  --from-literal=username=travel \
  --from-literal=password=travelpass

# Deploy PostgreSQL + Init data ConfigMap
kubectl apply -f postgres-init-configmap.yaml
kubectl apply -f postgres.yaml

# Deploy Flights and Hotels APIs
kubectl apply -f flights.yaml
kubectl apply -f hotels.yaml

# Deploy Frontend
kubectl apply -f frontend.yaml
```

## Step 4: Verify Deployment
Monitor the status of your pods:
```bash
kubectl get pods -n demo-travel
```

Check the NodePorts assigned to your services:
```bash
kubectl get svc -n demo-travel
```

## Step 5: F5 AWAF (ASM) Integration
Ensure the NodePorts are configured in your BIG-IP Virtual Server backend pool members. 
The **F5 AWAF (ASM)** policy should be deployed in fundamental blocking mode, ensuring standard OWASP Top 10 protections (like SQL Injections and Cross-Site Scripting) are actively protecting the flights and hotels APIs.

> [!NOTE]
> *In the later phases (Phases 3 and 4) which introduce the generative components, Calypso AI will be integrated to form unified guardrails against prompt injection and provide dedicated red team testing capabilities.*

## Step 6: OTel Integration
The applications are instrumented for OpenTelemetry and will attempt to reach `http://otel-collector:4317`.
Depending on your architecture, you should either:
- Deploy an `otel-collector` service within the `demo-travel` namespace on this K3s cluster.
- Or, update the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variables in `flights.yaml`, `hotels.yaml`, and `frontend.yaml` to point to your external BIG-IP or centralized collector!
