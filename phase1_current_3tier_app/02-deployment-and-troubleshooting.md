# Phase 1: Deployment & Troubleshooting

## Deployment

### 1. Clone the repository

```bash
git clone <your-repo-url> demo_travel
cd demo_travel
```

### 2. Build and start all services

```bash
docker compose -f docker-compose-tools.yaml up -d --build
docker compose up --build -d
```

> **Note:** The `--build` flag overrides cache and forces Docker to rebuild images. 

### 3. Verify all containers are running

```bash
docker ps
```

Expected output — all 4 services should report `Up`:
```
CONTAINER ID   IMAGE                  STATUS          PORTS
xxxxxxxxxxxx   demo_travel-frontend   Up X seconds    0.0.0.0:8080->5000/tcp
xxxxxxxxxxxx   demo_travel-hotels     Up X seconds    0.0.0.0:5001->5001/tcp
xxxxxxxxxxxx   demo_travel-flights    Up X seconds    0.0.0.0:5002->5002/tcp
xxxxxxxxxxxx   postgres:15            Up X seconds    0.0.0.0:5432->5432/tcp
```

### 4. Open the app

Access the frontend via browser at `http://localhost:8080`.

---

## Operations

### View logs

```bash
# All services at once
docker compose logs -f

# Single service
docker logs demo_travel-frontend-1 --tail 50 -f
docker logs demo_travel-flights-1 --tail 50 -f
docker logs demo_travel-hotels-1 --tail 50 -f
```

### Restart a single service

```bash
docker compose restart frontend
```

### Rebuild after a code change

```bash
docker compose up --build -d
```

### Full reset

```bash
docker compose down -v
docker compose up --build -d
```

> **Warning**: The `-v` flag deletes the `postgres_data` volume. The database will process `postgres/init.sql` upon the next initialization.

### Add activities for a new city

```bash
docker exec demo_travel-postgres-1 psql -U travel -d travel << 'EOF'
INSERT INTO activities (title, description, city) VALUES
    ('Eiffel Tower', 'Visit the symbol of Paris', 'Paris'),
    ('Louvre Museum', 'Home of the Mona Lisa', 'Paris'),
    ('Seine River Cruise', 'See Paris from the water', 'Paris');
EOF
```

---

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------------|-----|
| Container exits with code 1 | Import error in Python | `docker logs <container_id>` |
| `500 Internal Server Error` on frontend | Database uninitialized | `docker compose down -v && docker compose up -d` |
| `No activities found for <city>` | City not seeded in DB | Insert rows into the activities table |
| `ModuleNotFoundError: No module named 'shared'` | Dockerfile missing `COPY shared/ shared/` | Rebuild images after updating Dockerfile |
| Flights/hotels return empty | Services unreachable | Validate container status via `docker ps` |
| Old DB schema after code change | Volume was not deleted | Run `docker compose down -v` |
