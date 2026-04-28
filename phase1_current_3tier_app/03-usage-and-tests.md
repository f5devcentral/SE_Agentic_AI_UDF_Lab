# Phase 1: Usage & Tests

## Testing

### Health checks

Verify each service is active:

```bash
curl http://localhost:8080/health
curl http://localhost:5001/health
curl http://localhost:5002/health
```

Expected response format:
```json
{"status": "ok"}
```

---

### Test the Flights service

Execute a query directly to the flights endpoint:

```bash
curl "http://localhost:5002/flights?origin=Paris&destination=Barcelona&date=2025-06-15"
```

Expected output: A JSON array of flight payloads:

```json
[
  {
    "airline": "SkyJet",
    "origin": "Paris",
    "destination": "Barcelona",
    "date": "2025-06-15",
    "departure_time": "08:00",
    "arrival_time": "09:30",
    "duration_hours": 1.5,
    "stops": 0,
    "price": 134
  }
]
```

Executing the command repeatedly will yield different payload values.

---

### Test the Hotels service

```bash
curl "http://localhost:5001/hotels?city=Barcelona&checkin=2025-06-15&checkout=2025-06-20"
```

Expected output: A JSON array of hotel payloads:

```json
[
  {
    "name": "Grand Palace Barcelona",
    "city": "Barcelona",
    "stars": 4,
    "price_per_night": 120,
    "total_price": 600,
    "nights": 5,
    "amenities": ["WiFi", "Pool", "Gym"],
    "rating": 8.7
  }
]
```

---

### Test PostgreSQL

Connect to the database terminal to evaluate the seeded data:

```bash
docker exec demo_travel-postgres-1 psql -U travel -d travel -c "SELECT * FROM activities;"
```

Expected output:
```
  id |         title          |               description                |    city
----+------------------------+------------------------------------------+-----------
  1 | Sagrada Familia        | Gaudí's unfinished masterpiece           | Barcelona
  2 | Park Güell             | Mosaic terraces with city views          | Barcelona
```

---

### End-to-end test via UI

1. Access `http://localhost:8080`.
2. Input origin, destination, and dates.
3. Submit the search form.
4. Verify results display Flights, Hotels, and Activities derived from the backend microservices.

---

### End-to-end test via curl

```bash
curl -s -X POST http://localhost:8080/ \
  -d "origin=Paris&destination=Barcelona&departure_date=2025-06-15&return_date=2025-06-20" \
  | grep -o "<h3>[^<]*</h3>"
```

Output includes elements dynamically rendered by the backend context arrays.
