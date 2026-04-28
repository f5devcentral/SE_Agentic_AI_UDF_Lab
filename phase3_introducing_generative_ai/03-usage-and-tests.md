# Phase 3: Usage & Tests

## Testing Requirements

### Test 1: Verify RAG Ingestion into Postgres
Check that the ETL job properly placed embeddings into `pgvector`.
```bash
kubectl exec deployment/postgres -- psql ragdb -U postgres -c "
SELECT count(*) as docs_ingested FROM documents;

SELECT left(content, 60) as preview, length(embedding::text) as embed_size 
FROM documents ORDER BY id DESC LIMIT 5;"
```

### Test 2: Test Semantic Search
Execute query to validate `pgvector` distance searches.
```bash
kubectl exec deployment/postgres -- psql ragdb -U postgres -c "
SELECT content, 1 - (embedding <=> avg_embedding) as relevance
FROM documents, (SELECT avg(embedding) as avg_embedding FROM documents) avg
ORDER BY embedding <=> avg_embedding LIMIT 1;"
```

### Test 3: LibreChat Execution
1. Open the LibreChat NodePort via standard browser.
2. Login (demo credentials: `admin@f5demo.com` / `adminadmin`).
3. Under **Model**, select **Ollama Mistral**.
4. Test standard interaction flow.

### Test 4: End-to-End Orchestrator (Agentic Flow)
Use the CLI to simulate frontend execution of the Agentic workflow.

```bash
kubectl port-forward svc/orchestrator 9000:9000 &

curl -X POST http://localhost:9000/plan \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "I love nature and I would like to book a trip in spring for at least one week with a budget not exceeding $1000"
  }' | jq .
```

*Flow execution steps:*
1. Orchestrator extracts intent.
2. Orchestrator performs RAG requests (fetching MinIO data representations).
3. Logic routes sequentially through Travel, Flight, Hotel, Activity, and Weather agents.
4. Orchestrator generates the final JSON object.

### Test 5: Verify distributed tracing in Jaeger
Access the OpenTelemetry tracing dashboard (Jaeger).
- Query the `orchestrator` service.
- Follow the sequence cascading from `orchestrator` to `rag-service` to `travel-agent` to `ollama`. Ensure all spans are appropriately tracked.
