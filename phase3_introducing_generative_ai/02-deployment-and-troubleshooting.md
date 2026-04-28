# Phase 3: Deployment & Troubleshooting

## Deployment Guide

### Step 1: Deploy Core Infra (DB, MinIO, Ollama)

**PostgreSQL + pgvector**:
```bash
kubectl apply -f k8s/postgres.yaml
kubectl rollout status deploy/postgres -n demo-travel
```
*Note: This command creates the `documents` table configured with `VECTOR(768)`.*

**MinIO S3**:
```bash
kubectl apply -f k8s/minio.yaml
kubectl rollout status deploy/minio -n demo-travel
```

**Ollama**:
```bash
kubectl apply -f k8s/ollama.yaml
kubectl rollout status deploy/ollama -n demo-travel
```

**Pull Models**:
Execute commands inside the Ollama pod to retrieve required models:
```bash
kubectl exec -it deploy/ollama -n demo-travel -- ollama pull mistral:7b-instruct-q4_K_M
kubectl exec -it deploy/ollama -n demo-travel -- ollama pull nomic-embed-text
```

### Step 2: Deploy RAG, Agents, Orchestrator
Confirm images are built and loaded into the local k3s registry.
```bash
kubectl apply -f k8s/rag.yaml
kubectl apply -f k8s/agents.yaml
kubectl apply -f k8s/orchestrator.yaml
```

### Step 3: Load Travel Documents & Run ETL
Upload target documents into the MinIO bucket.
```bash
mc cp misc/catalog_travel_options.json minio/travel-data/
mc cp misc/minio/travel-data/*.json minio/travel-data/
mc cp misc/minio/travel-data/*.txt minio/travel-data/
```
Verify ingestion:
```bash
mc ls minio/travel-data/
```

Run the ETL Job (S3 to pgvector):
```bash
kubectl apply -f k8s/etl-job.yaml
kubectl wait --for=condition=complete job/etl-job -n demo-travel
```

### Step 4: Deploy LibreChat
```bash
kubectl apply -f k8s/librechat/
```
*Accessible via NodePort exposed on the K3s node IP.*

---

## Troubleshooting

### PostgreSQL Dimensions Error
If dimension mismatch errors occur with `pgvector`, ensure the table is explicitly dimensioned to `768` for compatibility with `nomic-embed-text`.
```bash
kubectl exec -it deployment/postgres -- psql -U postgres -d ragdb -c \
"ALTER TABLE documents ALTER COLUMN embedding TYPE vector(768);"
```

### MinIO Credentials Rejected (HTTP 403)
If `mc admin info alias` returns `HTTP 403 Forbidden`:
Verify the credentials in the `minio-secret` Kubernetes secret match the values in the internal configuration.

### Ollama Out Of Memory Error
If direct API testing via `curl` triggers a `500 {'error': 'model requires more system memory...'}` response:
- Ensure the Pod has adequate CPU/RAM limits configured. Memory bounds must accommodate model quantization requirements.
- Restrict concurrency thresholds within the Orchestrator to prevent simultaneous memory allocation failures.

### Re-running ETL Job
If embeddings fail to load completely:
```bash
kubectl delete job etl-job -n demo-travel
kubectl apply -f k8s/etl-job.yaml
kubectl logs job/etl-job
```
