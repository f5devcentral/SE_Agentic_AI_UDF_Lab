curl -kv -w '\n* Response time: %{time_total}s\n' http://ollama:11434/api/chat   -H "Content-Type: application/json"   -d '{
    "model": "mistral:7b-instruct-q4_K_M",
    "messages": [
      {"role": "user", "content": "Barcelona budget hotel?"}
    ],
    "stream": false
  }'
* Host ollama:11434 was resolved.
* IPv6: (none)
* IPv4: 10.43.220.247
*   Trying 10.43.220.247:11434...
* Connected to ollama (10.43.220.247) port 11434
* using HTTP/1.x
> POST /api/chat HTTP/1.1
> Host: ollama:11434
> User-Agent: curl/8.14.1
> Accept: */*
> Content-Type: application/json
> Content-Length: 154
> 
* upload completely sent off: 154 bytes
< HTTP/1.1 200 OK
< Content-Type: application/json; charset=utf-8
< Date: Fri, 27 Mar 2026 22:10:41 GMT
< Content-Length: 1503
< 
{"model":"mistral:7b-instruct-q4_K_M","created_at":"2026-03-27T22:10:41.902220871Z","message":{"role":"assistant","content":"\nThere are many budget hotels in Barcelona that offer comfortable accommodation at an affordable price. Here are some options to consider:\n\n1. Hostels: Hostels are a great option for budget travelers as they often have dormitory-style rooms with shared bathrooms and kitchens. Some popular hostels in Barcelona include St. Christopher's Inn, Poble Sec Hostel, and The Lost Chambers Hostel.\n2. Budget Hotels: There are many budget hotels in Barcelona that offer private rooms at an affordable price. Some popular options include Hotel Sant Jaume, Gran Hotel Central, and Hotel Catarina.\n3. Airbnb: If you prefer to stay in a private apartment rather than a hotel room, Airbnb can be a good option for budget travelers. You can find many affordable options in Barcelona on the platform, such as apartments located in neighborhoods like Gracia or Poble Sec.\n4. Capsule Hotels: If you're looking f* Connection #0 to host ollama left intact
or something unique and affordable, consider staying at a capsule hotel like Dreams Palace. These hotels offer small, compact rooms that can be a great option for budget travelers.\n\nIt's always a good idea to compare prices across different options to find the best deal for your needs."},"done":true,"done_reason":"stop","total_duration":29272637809,"load_duration":59916303,"prompt_eval_count":14,"prompt_eval_duration":114000621,"eval_count":268,"eval_duration":28860256654}
* Response time: 29.272565s
root@frontend-5f4b9d7bbf-zswlm:/app# 
root@frontend-5f4b9d7bbf-zswlm:/app# 
root@frontend-5f4b9d7bbf-zswlm:/app# curl -kv -w '\n* Response time: %{time_total}s\n' http://ollama:11434/api/chat   -H "Content-Type: application/json"   -d '{
    "model": "llama3.2:1b",
    "messages": [
      {"role": "user", "content": "Barcelona budget hotel?"}
    ],
    "stream": false
  }'
* Host ollama:11434 was resolved.
* IPv6: (none)
* IPv4: 10.43.220.247
*   Trying 10.43.220.247:11434...
* Connected to ollama (10.43.220.247) port 11434
* using HTTP/1.x
> POST /api/chat HTTP/1.1
> Host: ollama:11434
> User-Agent: curl/8.14.1
> Accept: */*
> Content-Type: application/json
> Content-Length: 139
> 
* upload completely sent off: 139 bytes
< HTTP/1.1 200 OK
< Content-Type: application/json; charset=utf-8
< Date: Fri, 27 Mar 2026 22:11:44 GMT
< Content-Length: 1753
< 
{"model":"llama3.2:1b","created_at":"2026-03-27T22:11:44.352041582Z","message":{"role":"assistant","content":"There are many budget-friendly options in Barcelona. Here are a few suggestions:\n\n1. Ibis Barcelona Sagrada Familia - This 2-star hotel is located near the famous Sagrada Familia and offers comfortable rooms at an affordable price.\n2. Hotel NH Collection Barcelona Poble-sec - This 3-star hotel is situated in the trendy El Born neighborhood, just a short walk from La Rambla and Plaza Catalunya.\n3. Hotel Posada San Agustin - This 4-star boutique hotel is located in the heart of the Gothic Quarter, within walking distance to many historical landmarks.\n4. Hotel Cambrils Park - This 2-star hotel is a more affordable option, still offering comfortable rooms and a convenient location near Barceloneta Beach.\n5. Airbnb options: Consider renting an apartment or room through Airbnb, which can be a great way to save money on accommodation. Some popular areas include Eixample, Passeig de Gracia, and Port Ol?* Connection #0 to host ollama left intact
?mpic.\n\nWhen budgeting for a hotel in Barcelona, consider the following costs:\n\n* Single occupancy: around €30-€50 per night\n* Double occupancy: around €50-€80 per night\n* Prices may vary depending on the time of year, location, and availability\n\nKeep in mind that prices may also be higher during peak travel seasons (June to August) and holidays.\n\nTo find the best deals, I recommend checking hotel websites, online travel agencies like Expedia or Booking.com, and comparing prices across different booking platforms."},"done":true,"done_reason":"stop","total_duration":16269155174,"load_duration":5160771876,"prompt_eval_count":30,"prompt_eval_duration":272167048,"eval_count":309,"eval_duration":9859095629}
* Response time: 16.270192s
