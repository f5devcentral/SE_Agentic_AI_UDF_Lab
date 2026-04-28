#/bin/bash
#


kubectl delete -f k8s/frontend/ -n demo-travel
docker build -f frontend/Dockerfile -t mcpregistry:30500/demo-travel/frontend:latest .
docker push mcpregistry:30500/demo-travel/frontend:latest
kubectl apply -f k8s/frontend/ -n demo-travel

kubectl get pods -n demo-travel
