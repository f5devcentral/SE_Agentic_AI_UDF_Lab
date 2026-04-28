

```bash
fastmcp list --server-spec http://10.1.1.5:30100/mcp
```


fastmcp call --server-spec http://10.1.1.5:30100/mcp --target search_activities --input-json '{"city":"Barcelona"}'


fastmcp call --server-spec http://10.1.1.5:30100/mcp --target search_flights --input-json '{"origin":"Paris","destination":"Barcelona","date":"2025-06-15"}'


fastmcp call --server-spec http://10.1.1.5:30100/mcp --target search_hotels --input-json '{"city":"Barcelona","checkin":"2025-06-15","checkout":"2025-06-20"}'
