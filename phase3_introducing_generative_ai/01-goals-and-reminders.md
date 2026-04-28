# Phase 3: Goals and Reminders

## Objective
Phase 3 integrates Generative AI into the deployment environment. The standard MCP execution framework from Phase 2 is upgraded to a distributed AI system. The environment utilizes localized inference engines (Ollama), vector search and RAG capabilities (MinIO and PostgreSQL with `pgvector`), and user-facing orchestrators (LibreChat and an Agentic Orchestrator). 

## Architecture Overview

- **Frontend to Orchestrator**: The frontend UI communicates with the orchestrator to initiate agent-based workflows.
- **Orchestrator to RAG**: The orchestrator interacts with PostgreSQL and pgvector for context retrieval.
- **Orchestrator to Agents**: Traffic routes to five distinct LLM-powered agents (Travel, Flight, Hotel, Activity, Weather).
- **Orchestrator to MCP servers**: Utilizes Phase 2 MCP servers.
- **Agent-to-Agent (A2A)**: Communication is routed sequentially by the orchestrator.
- **ETL**: MinIO S3 data is processed and synchronized into pgvector.

Ollama is utilized as the LLM API backend, configuring `mistral:7b-instruct-q4_K_M` for text generation and `nomic-embed-text` for embedding generation.

## Components

1. **Ollama**: CPU-based LLM backend available at `http://ollama:11434`.
2. **PostgreSQL + pgvector**: Stores vector embeddings (`VECTOR(768)`) inside the `ragdb` database.
3. **MinIO (S3)**: Stores standard travel documents (text/JSON) within the `travel-data` bucket.
4. **ETL Job**: Synchronizes S3 data, generates embeddings via Ollama, and populates pgvector.
5. **RAG Service**: Executes similarity searches based on embedding distances.
6. **Orchestrator**: Core entry point that manages intent extraction, RAG requests, agent sequencing, and final output generation.
7. **Agents**: Independent pods dedicated to reasoning tasks utilizing JSON-mode LLM execution.
8. **LibreChat**: User Interface for Generative AI interaction.

## Kubernetes Context
```bash
kubectl config get-contexts
kubectl config use-context LLM
kubectl create ns demo-travel
```
