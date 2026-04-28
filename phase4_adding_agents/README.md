



# Switch to the LLM Context so you will use the AI dedicated K3s cluster

```bash
kubectl config get-contexts
kubectl config use-context LLM
kubectl create ns demo-travel
```


# Pull the AI models

````bash
kubectl apply -f k8s/ollama.yaml -n deo-travel

kubectl exec -it deploy/ollama -- ollama pull mistral:7b-instruct-q4_K_M
kubectl exec -it deploy/ollama -- ollama pull nomic-embed-text




```

MINIO_USER=$(kubectl get secret minio-secret -o jsonpath='{.data.MINIO_ROOT_USER}' | base64 -d)
MINIO_PASS=$(kubectl get secret minio-secret -o jsonpath='{.data.MINIO_ROOT_PASSWORD}' | base64 -d)

NODE_IP=10.1.1.4


ll misc/minio/travel-data/
mc cp misc/catalog_travel_options.json minio/travel-data/
...avel_options.json: 1.06 KiB / 1.06 KiB ┃▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓┃ 71.58 KiB/s 0subuntu@tools:~/agentic_lab/agentic-stack$ 

mc cp misc/minio/travel-data/*.json  minio/travel-data/
mc cp misc/minio/travel-data/alps_budget_hotels.txt  minio/travel-data/
mc cp misc/minio/travel-data/alps_nature_activities.txt  minio/travel-data/
mc cp misc/minio/travel-data/barcelona_budget_hotels.txt  minio/travel-data/
mc cp misc/minio/travel-data/barcelona_nature_activities.txt  minio/travel-data/


mc ls minio/travel-data/
[2026-03-05 20:43:46 UTC] 2.3KiB STANDARD alps_budget_hotels.txt
[2026-03-05 20:44:04 UTC] 2.3KiB STANDARD alps_nature_activities.txt
[2026-03-05 20:44:17 UTC] 2.7KiB STANDARD barcelona_budget_hotels.txt
[2026-03-05 20:44:32 UTC] 3.2KiB STANDARD barcelona_nature_activities.txt
[2026-03-05 20:43:06 UTC] 1.1KiB STANDARD catalog_travel_options.json


## Verify RAG Ingestion
kubectl exec deployment/postgres -- psql ragdb -U postgres -c "
SELECT count(*) as docs_ingested FROM documents;

SELECT left(content, 60) as preview, 
       length(embedding::text) as embed_size 
FROM documents 
ORDER BY id DESC LIMIT 5;"
 docs_ingested 
---------------
            10
(1 row)

                    preview                     | embed_size 
------------------------------------------------+------------
 {                                             +|       7220
   "destinations": [                           +| 
     {                                         +| 
       "city": "Barcelona",                    +| 
                                                | 
 City: Barcelona                               +|       7229
 Theme: Nature and outdoor activities          +| 
 Season:                                        | 
 City: Barcelona                               +|       7226
 Theme: Budget-friendly hotels for nature-ori   | 
 Region: Alps (example: Chamonix / Alpine town)+|       7204
 Theme: Mounta                                  | 
 Region: Alps (generic Alpine town)            +|       7199
 Theme: Budget hotels and                       | 
(5 rows)

### Test semantic search

kubectl exec deployment/postgres -- psql ragdb -U postgres -c "
SELECT content, 1 - (embedding <=> avg_embedding) as relevance
FROM documents, (SELECT avg(embedding) as avg_embedding FROM documents) avg
ORDER BY embedding <=> avg_embedding LIMIT 1;"


