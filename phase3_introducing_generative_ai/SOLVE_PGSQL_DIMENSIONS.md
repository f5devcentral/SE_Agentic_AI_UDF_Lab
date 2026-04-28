
```bash
kubectl exec -it deployment/postgres -- psql -U postgres -d ragdb -c   "SELECT id, LEFT(content,60) AS snippet FROM documents;"
 id | snippet 
----+---------
(0 rows)

ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ 
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ 
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ 
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ kubectl exec -it postgres-578c4f5894-wwmzw -n demo-travel -- psql -U postgres -d ragdb
psql (17.9 (Debian 17.9-1.pgdg12+1))
Type "help" for help.

ragdb=# 
```


```bash
ALTER TABLE documents
ALTER COLUMN embedding TYPE vector(768);
```


NOTICE:  ivfflat index created with little data
DETAIL:  This will cause low recall.
HINT:  Drop the index until the table has more data.
ALTER TABLE
ragdb=# quit
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ kubectl delete job etl-job -n demo-travel
kubectl apply -f k8s/etl-job.yaml
job.batch "etl-job" deleted from demo-travel namespace
job.batch/etl-job created
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ 
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ 
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ 
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ kubectl get pods
NAME                              READY   STATUS      RESTARTS       AGE
activity-agent-55f9788dcb-5rs78   1/1     Running     1 (17h ago)    40h
etl-job-6q7p2                     0/1     Completed   0              12s
flight-agent-6dd94c9fc7-gf4dj     1/1     Running     1 (17h ago)    40h
frontend-5f4b9d7bbf-zswlm         1/1     Running     1 (4h2m ago)   25h
hotel-agent-6b7ff95c87-jvl9x      1/1     Running     1 (17h ago)    40h
minio-7f9dc5dc44-vhq5b            1/1     Running     0              57m
ollama-7cf654d98b-vkjhw           1/1     Running     1 (17h ago)    2d17h
orchestrator-7ffb794484-65dfb     1/1     Running     1 (4h2m ago)   22h
postgres-578c4f5894-wwmzw         1/1     Running     1 (17h ago)    2d17h
rag-service-79649897f6-8w4gq      1/1     Running     1 (4h2m ago)   22h
travel-agent-85f6b7ff57-hq666     1/1     Running     1 (17h ago)    40h
weather-agent-7c47999bfd-fb5tn    1/1     Running     1 (4h2m ago)   40h
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ kubectl logs etl-job-6q7p2
INFO:etl-job:Processing object alps_budget_hotels.txt
INFO:etl-job:Processing object alps_nature_activities.txt
INFO:etl-job:Processing object barcelona_budget_hotels.txt
INFO:etl-job:Processing object barcelona_nature_activities.txt
INFO:etl-job:Processing object catalog_travel_options.json
INFO:etl-job:ETL completed
ubuntu@tools:~/techXchange_AI_lab/phase3_adding_agents$ kubectl exec -it deployment/postgres -- psql -U postgres -d ragdb -c   "SELECT id, LEFT(content,80) AS snippet, LEFT(embedding::text,40) AS emb_preview FROM documents LIMIT 5;"
 id |                           snippet                           |               emb_preview                
----+-------------------------------------------------------------+------------------------------------------
  1 | Region: Alps (generic Alpine town)                         +| [-0.999166,0.79928,-4.283294,0.448009,0.
    | Theme: Budget hotels and guesthouses for natu               | 
  2 | Region: Alps (example: Chamonix / Alpine town)             +| [0.057693,0.323197,-4.094685,0.184842,0.
    | Theme: Mountain and nature activi                           | 
  3 | City: Barcelona                                            +| [-0.66248,0.972064,-4.2207,0.250844,0.27
    | Theme: Budget-friendly hotels for nature-oriented travelers+| 
    | Seas                                                        | 
  4 | City: Barcelona                                            +| [-0.215508,0.81026,-4.035232,0.279673,0.
    | Theme: Nature and outdoor activities                       +| 
    | Season: Spring                                             +| 
    |                                                            +| 
    | 1) Montjuïc                                                 | 
  5 | {                                                          +| [-1.092538,0.555849,-3.962356,0.345979,0
    |   "destinations": [                                        +| 
    |     {                                                      +| 
    |       "city": "Barcelona",                                 +| 
    |       "country": "Spain",                                   | 
(5 rows)

