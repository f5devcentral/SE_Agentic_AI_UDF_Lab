


## testing to the orchestrator

curl -X POST http://10.1.1.4:30990/plan \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "I love nature and I would like to book a trip in spring for at least one week with a budget not exceeding $1000"
  }'
<!doctype html>
<html lang=en>
<title>500 Internal Server Error</title>
<h1>Internal Server Error</h1>
<p>The server encountered an internal error and was unable to complete your request. Either the server is overloaded or there is an error in the application.</p>



## testing from the orchestrator to ollama LLM

```bash
kubectl exec -it orchestrator-84769dfdcc-2ln6g -- /bin/bash
root@orchestrator-84769dfdcc-2ln6g:/app# python3 -c '
import requests
resp = requests.post("http://ollama:11434/api/chat", json={
    "model": "mistral:7b-instruct-q4_K_M",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, world!"}
    ],
    "stream": False
})
print(resp.status_code, resp.json())
'
500 {'error': 'model requires more system memory (4.5 GiB) than is available (3.7 GiB)'}
```

kubectl exec -it deploy/ollama -- /bin/bash
root@ollama-7cf654d98b-4rgck:/# ollama show mistral:7b-instruct-q4_K_M
  Model
    architecture        llama     
    parameters          7B        
    context length      32768     
    embedding length    4096      
    quantization        Q4_K_M    

  Capabilities
    completion    

  Parameters
    stop    "[INST]"     
    stop    "[/INST]"    

  License
    Apache License               
    Version 2.0, January 2004    
    ...                          

root@ollama-7cf654d98b-4rgck:/# ollama run mistral:7b-instruct-q4_K_M
>>> hello how are you?
I am an AI language model, so I do not have feelings or emotions. I am here to assist you with any questions or tasks 
you have to the best of my ability. How can I help you today?
