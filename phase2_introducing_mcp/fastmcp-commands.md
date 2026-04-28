


```bash
# List tools on travel-mcp
fastmcp inspect http://<NODE_IP>:30100/mcp

# Call search_flights
fastmcp call http://<NODE_IP>:30100/mcp search_flights \
  --input '{"origin":"Paris","destination":"Barcelona","date":"2025-06-15"}'

# Call search_hotels
fastmcp call http://<NODE_IP>:30100/mcp search_hotels \
  --input '{"city":"Barcelona","checkin":"2025-06-15","checkout":"2025-06-20"}'

# Call search_activities
fastmcp call http://<NODE_IP>:30100/mcp search_activities \
  --input '{"city":"Barcelona"}'

# List tools on weather-mcp
fastmcp inspect http://<NODE_IP>:30101/mcp

# Call get_weather_forecast
fastmcp call http://<NODE_IP>:30101/mcp get_weather_forecast \
  --input '{"city":"Barcelona","date":"2025-06-15"}'
```
