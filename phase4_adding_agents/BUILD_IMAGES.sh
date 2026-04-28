#!/bin/bash
#

REGISTRY_FOLDER="ai"
VERSION="v0.4"

# RAG + ETL
docker build --no-cache -f services/rag/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/rag-service:$VERSION .
docker push localhost:30500/$REGISTRY_FOLDER/rag-service:$VERSION

docker build --no-cache -f services/etl-job/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/etl-job:$VERSION .
docker push localhost:30500/$REGISTRY_FOLDER/etl-job:$VERSION


# Orchestrator
docker build  -f services/orchestrator/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/orchestrator:$VERSION .
docker push localhost:30500/$REGISTRY_FOLDER/orchestrator:$VERSION

# Agents
docker build -f services/travel-agent/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/travel-agent:$VERSION .
docker build -f services/flights-agent/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/flights-agent:$VERSION .
docker build -f services/hotels-agent/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/hotels-agent:$VERSION .
docker build -f services/activities-agent/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/activities-agent:$VERSION .
docker build -f services/weather-agent/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/weather-agent:$VERSION .

docker push localhost:30500/$REGISTRY_FOLDER/travel-agent:$VERSION
docker push localhost:30500/$REGISTRY_FOLDER/flights-agent:$VERSION
docker push localhost:30500/$REGISTRY_FOLDER/hotels-agent:$VERSION
docker push localhost:30500/$REGISTRY_FOLDER/activities-agent:$VERSION
docker push localhost:30500/$REGISTRY_FOLDER/weather-agent:$VERSION

# Frontend
docker build --no-cache -f services/frontend/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/frontend_ai:$VERSION .
docker push localhost:30500/$REGISTRY_FOLDER/frontend_ai:$VERSION


# LOGVIZ: switch context to OBS, move to the observability stack / logviz folder
kubectl config use-context OBS

docker build --no-cache -f /home/ubuntu/techXchange_AI_lab/observability_stack/logviz/Dockerfile -t localhost:30500/$REGISTRY_FOLDER/logviz:$VERSION .
docker push localhost:30500/$REGISTRY_FOLDER/logviz:$VERSION

# Deploy Core infra
kubectl apply -f k8s/minio.yaml
kubectl exec -it deployment/minio -- mc mb minio/travel-data

kubectl apply -f k8s/postgres.yaml
kubectl rollout status deploy/postgres

kubectl apply -f k8s/ollama.yaml
kubectl rollout status deploy/ollama

kubectl exec -it deploy/ollama -- ollama pull mistral:7b-instruct-q4_K_M
kubectl exec -it deploy/ollama -- ollama pull nomic-embed-text

kubectl apply -f k8s/agents.yaml
kubectl apply -f k8s/orchestrator.yaml

kubectl apply -f k8s/frontend.yaml
