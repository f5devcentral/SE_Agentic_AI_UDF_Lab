#!/bin/bash

kubectl delete -f ../k8s/travel-mcp/ -n demo-travel
kubectl delete -f ../k8s/weather-mcp/ -n demo-travel

sleep 5

docker build -f  weather-mcp/Dockerfile -t mcpregistry:30500/demo-travel/weather-mcp:latest .
docker build -f travel-mcp/Dockerfile -t mcpregistry:30500/demo-travel/travel-mcp:latest .

sleep 3

docker push mcpregistry:30500/demo-travel/weather-mcp:latest
docker push mcpregistry:30500/demo-travel/travel-mcp:latest

sleep 5

kubectl apply -f ../k8s/travel-mcp/ -n demo-travel
kubectl apply -f ../k8s/weather-mcp/ -n demo-travel

sleep 3

kubectl get pods -n demo-travel -w -o wide
