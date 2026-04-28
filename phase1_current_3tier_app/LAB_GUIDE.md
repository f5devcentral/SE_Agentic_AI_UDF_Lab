# Phase 1 Lab Guide — Baseline 3-Tier Application

## Goals

Phase 1 establishes the **pre-AI baseline**. Before introducing models, agents, or protocols, you need a working application that represents what enterprises actually run today: a traditional web frontend backed by independent REST microservices and a relational database.

The objectives for this phase are:
- Deploy a functional 3-tier travel booking app (Frontend, Flights API, Hotels API, PostgreSQL).
- Instrument it end-to-end with OpenTelemetry to produce traces, metrics, and logs from day one.
- Introduce F5 BIG-IP AWAF as the application security layer protecting exposed endpoints.
- Establish the baseline you will progressively upgrade through the remaining phases.

---

## Architecture

```
Browser
  │
  └── HTTP :8080 ──► Frontend (Flask)
                        │
                        ├── GET /flights  ──► Flights API (Flask :5002)  [JSON response]
                        ├── GET /hotels   ──► Hotels API  (Flask :5001)  [JSON response]
                        └── SQL SELECT    ──► PostgreSQL  (:5432)        [activities rows]
```

| Service   | Port | Role |
|-----------|------|------|
| frontend  | 8080 | Renders the search form, aggregates API results, returns HTML |
| flights   | 5002 | Generates randomised flight options from query parameters |
| hotels    | 5001 | Generates randomised hotel options from query parameters |
| postgres  | 5432 | Stores seeded activities data per city |

Every service imports the `shared/` library which wires up OTel tracing, structured logging, and Prometheus metrics with three lines of code. If no collector is reachable, telemetry is silently dropped — the service still starts.

---

## Deployment

### Option A — Docker Compose (local development)

```bash
cd phase1_current_3tier_app

# Start tools (OTel collector, etc.) then the application
docker compose -f docker-compose-tools.yaml up -d --build
docker compose up --build -d

# Confirm all four containers report "Up"
docker ps
```

Access the UI at `http://localhost:8080`.

### Option B — Kubernetes (K3s, APP_TOOLS cluster)

**Step 1 — Switch context**
```bash
kubectl config use-context APP_TOOLS
```

**Step 2 — Build and push images**
```bash
cd phase1_current_3tier_app

docker build -f flights/Dockerfile  -t localhost:30500/demo-travel/flights:latest  .
docker push localhost:30500/demo-travel/flights:latest

docker build -f hotels/Dockerfile   -t localhost:30500/demo-travel/hotels:latest   .
docker push localhost:30500/demo-travel/hotels:latest

docker build -f frontend/Dockerfile -t localhost:30500/demo-travel/frontend:latest .
docker push localhost:30500/demo-travel/frontend:latest
```

**Step 3 — Deploy**
```bash
kubectl create namespace demo-travel

kubectl create secret generic travel-db-secret -n demo-travel \
  --from-literal=username=travel \
  --from-literal=password=travelpass

cd k8s
kubectl apply -f postgres-init-configmap.yaml
kubectl apply -f postgres.yaml
kubectl rollout status deploy/postgres -n demo-travel

kubectl apply -f flights.yaml
kubectl apply -f hotels.yaml
kubectl apply -f frontend.yaml
kubectl rollout status deploy/frontend -n demo-travel
```

**Step 4 — Verify**
```bash
kubectl get pods -n demo-travel
kubectl get svc  -n demo-travel
```

---

## Testing

### Health checks
```bash
curl http://localhost:8080/health
curl http://localhost:5001/health
curl http://localhost:5002/health
```
All three should return `{"status": "ok"}`.

### Flights API (direct)
```bash
curl "http://localhost:5002/flights?origin=Paris&destination=Barcelona&date=2026-06-15"
```
Returns a JSON array of 3–6 flight objects. Run it twice — results differ each call (randomised). Fields: `airline`, `origin`, `destination`, `date`, `departure_time`, `arrival_time`, `duration_hours`, `stops`, `price`.

### Hotels API (direct)
```bash
curl "http://localhost:5001/hotels?city=Barcelona&checkin=2026-06-15&checkout=2026-06-20"
```
Returns 3–5 hotel objects. Verify `total_price` equals `price_per_night × nights`.

### PostgreSQL activities
```bash
# Docker Compose
docker exec demo_travel-postgres-1 psql -U travel -d travel -c "SELECT * FROM activities;"

# Kubernetes
kubectl exec -n demo-travel deploy/postgres -- psql -U travel -d travel -c "SELECT * FROM activities;"
```
Expect 10 rows seeded from `postgres/init.sql`, with cities Barcelona, Paris, and Rome.

### End-to-end search via the UI
1. Open `http://localhost:8080`
2. Fill in: **From** = Paris, **To** = Barcelona, **Departure** = any future date, **Return** = a few days later
3. Click **Search trips**
4. The results page must show three sections: **Flights**, **Hotels**, and **Activities**

### End-to-end via curl (POST)
```bash
curl -s -X POST http://localhost:8080/ \
  -d "origin=Paris&destination=Barcelona&departure_date=2026-06-15&return_date=2026-06-20" \
  | grep -o "<h3>[^<]*</h3>"
```
Output contains hotel names, airline names, and activity titles rendered server-side.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Container exits with code 1 | Python import error | `docker logs <id>` to see traceback |
| `500 Internal Server Error` on frontend | DB not ready or volume stale | `docker compose down -v && docker compose up -d` |
| `No activities found for <city>` | City not in DB | Insert rows (see below) |
| `ModuleNotFoundError: No module named 'shared'` | Dockerfile missing `COPY shared/ shared/` | Fix Dockerfile, `docker compose up --build -d` |
| Flights or hotels return empty | Service unreachable | `docker ps` — confirm all four containers are `Up` |
| Schema error after code change | Old volume still present | `docker compose down -v` before rebuilding |

**Inserting activities for a new city:**
```bash
docker exec demo_travel-postgres-1 psql -U travel -d travel << 'EOF'
INSERT INTO activities (title, description, city) VALUES
    ('Eiffel Tower',    'Visit the symbol of Paris',     'Paris'),
    ('Louvre Museum',   'Home of the Mona Lisa',         'Paris'),
    ('Seine River Cruise', 'See Paris from the water',   'Paris');
EOF
```

---

## Phase Highlights

### Traffic Routing
At this stage all traffic is plain HTTP. The frontend is the single aggregation point: it issues two outbound HTTP GET calls (flights, hotels) and one SQL query, then stitches the results into a single HTML page. There is **no service mesh, no API gateway, no load balancer** between services — connections are direct container-to-container DNS names or Kubernetes ClusterIP addresses.

Observe: every search request that lands on the frontend generates exactly three downstream calls. You can confirm this in the logs or in the Jaeger trace once the OTel collector is wired up.

### Protocol Understanding
Three different protocols are in play simultaneously:
- **HTTP/JSON** — between frontend and the Flights/Hotels APIs. Stateless, query-parameter driven.
- **SQL over TCP** — between the frontend and PostgreSQL. A synchronous blocking query on the `activities` table.
- **OTLP/gRPC** — background telemetry export to the OTel collector.

Note that the Flights and Hotels APIs return **procedurally generated** data on every call. There is no persistent state in those services. This models enterprise microservices that aggregate data from downstream systems on demand.

### Security
The relevant attack surface at this tier is the classic OWASP Web Top 10:
- The `?city=` and `?origin=` query parameters are passed directly into SQL `WHERE city = %s` clauses — these are parameterised and safe, but the shape of input going to backend APIs is entirely trust-based.
- The `?destination=` parameter is reflected into the HTML response — a candidate for XSS without output encoding.
- None of the three services have authentication. Any actor who can reach port 5001 or 5002 can call the APIs.

**F5 BIG-IP AWAF** provides the perimeter layer:
- A WAF policy in **fundamental blocking mode** covers SQL injection and XSS signatures out of the box.
- Rate-limiting on the Virtual Server prevents volumetric abuse against the flights and hotels endpoints.
- Parameter enforcement can constrain the allowed values for `origin`, `destination`, and `city`.

This is the security baseline. In Phase 4, the same BIG-IP will be extended with LLM-specific iRules to handle AI attack vectors.

### Token Economy
There are no LLM tokens at this phase — all responses are deterministic code paths. This is deliberately the **cost-free baseline** against which you will measure the AI lift in Phases 3 and 4. When Ollama inference is introduced, you will see latency jump from tens of milliseconds to several seconds per request on CPU-only hardware.

### Visibility
Every service initialises three OTel instruments on startup:
- A **tracer** attached to Flask via `FlaskInstrumentor` (auto-instruments every route).
- A **meter** exporting a `requests_total` Prometheus counter.
- A **structured logger** that emits JSON to stdout and, if a collector is reachable, ships logs via OTLP.

With the OTel collector running, open Jaeger and search for the `frontend` service. Every search request produces a root span with two child HTTP spans (flights, hotels) and one implicit DB query. This is the distributed trace you will use to compare latency before and after AI components are added.

### Business Value for Enterprises
Phase 1 represents the state most enterprise applications are in today: functional but fragile. There is no intelligence, no context awareness, and no ability to personalise results. Bookings are a static lookup. The user must do all the reasoning.

Key enterprise pain points visible at this phase:
- **No personalisation**: every user gets the same sorted list regardless of preferences or budget.
- **No context**: the system cannot answer "what is the weather like?" or "is this within my budget?".
- **No audit trail**: without the observability stack, there is no way to correlate a user complaint to a specific backend call.
- **Static security perimeter**: WAF rules protect against known signatures but cannot detect semantic attacks (which become relevant in Phase 4).

The remaining phases progressively address each of these gaps while keeping the same observable architecture.
