


## Deploy

```bash
kubectl apply -f k8s/librechat/
```

You should see NodePort 30080 exposed on your k3s node IP (e.g. 10.1.1.4:30080).


# Test


I created an admin user: admin@f5demo.com with password adminadmin but you can create your own credentials.

In the LibreChat UI:

Choose Preset: Agentic Travel Planner.

Model: Ollama Mistral (mapped to ollama_mistral).


## Verify Ollama connectivity:
kubectl exec -it deploy/librechat -- wget -qO- http://ollama:11434/api/tags |  
{
  "models": [
    {
      "name": "llama3.2:3b",
      "model": "llama3.2:3b",
      "modified_at": "2026-03-12T23:28:12.444415196Z",
      "size": 2019393189,
      "digest": "a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "llama",
        "families": [
          "llama"
        ],
        "parameter_size": "3.2B",
        "quantization_level": "Q4_K_M"
      }
    },
    {
      "name": "nomic-embed-text:latest",
      "model": "nomic-embed-text:latest",
      "modified_at": "2026-03-03T15:33:20.707662635Z",
      "size": 274302450,
      "digest": "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "nomic-bert",
        "families": [
          "nomic-bert"
        ],
        "parameter_size": "137M",
        "quantization_level": "F16"
      }
    },
    {
      "name": "mistral:7b-instruct-q4_K_M",
      "model": "mistral:7b-instruct-q4_K_M",
      "modified_at": "2026-03-03T15:33:04.566707072Z",
      "size": 4369387754,
      "digest": "1a85656b534f84f8ab5b235aa0e24a954769539b0f47a4bd11f5272cba43c892",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "llama",
        "families": [
          "llama"
        ],
        "parameter_size": "7B",
        "quantization_level": "Q4_K_M"
      }
    }
  ]
}


```bash
kubectl logs deploy/librechat | grep -iE "(mcp|travel|activities)" -A 5 -B 5
```:wq


5.3. Send your test prompt
Paste:

```text
I love nature and I would like to book a trip in spring for at least one week with a budget not exceeding $1000
```



