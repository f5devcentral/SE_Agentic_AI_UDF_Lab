
# Nuke the PVC
```bash
kubectl exec -it deployment/minio -- env | grep MINIO_ROOT
#MINIO_ROOT_USER_FILE=access_key
#MINIO_ROOT_PASSWORD_FILE=secret_key
#MINIO_ROOT_USER=minioadmin
#MINIO_ROOT_PASSWORD=minioadmin123

kubectl exec -it deployment/minio -- ls /data/.minio.sys/
#buckets  config  format.json  multipart  pool.bin  tmp

kubectl scale deployment minio --replicas=0

# Wipe the PVC by spinning a temporary pod
kubectl run minio-wipe --image=busybox --restart=Never \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"minio-pvc"}}],"containers":[{"name":"wipe","image":"busybox","command":["sh","-c","rm -rf /data/.minio.sys && echo done"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' 

kubectl logs minio-wipe   
# should print "done"
kubectl delete pod minio-wipe
```

# Bring MinIO back up with the corrected secret

```bash
kubectl scale deployment minio --replicas=1
kubectl rollout status deployment/minio
deployment.apps/minio scaled
pod/minio-wipe created
Error from server (BadRequest): container "wipe" in pod "minio-wipe" is waiting to start: ContainerCreating
pod "minio-wipe" deleted from demo-travel namespace
deployment.apps/minio scaled
Waiting for deployment "minio" rollout to finish: 0 of 1 updated replicas are available...
deployment "minio" successfully rolled out
```


# Quick S3 connectivity test from inside the cluster
```bash
kubectl run s3test --image=amazon/aws-cli --restart=Never --rm -it \
  --env="AWS_ACCESS_KEY_ID=minioadmin" \
  --env="AWS_SECRET_ACCESS_KEY=minioadmin123" \
  -- s3 ls --endpoint-url http://minio:9000
All commands and output from this session will be recorded in container logs, including credentials and sensitive information passed through the command prompt.
If you don't see a command prompt, try pressing enter.
1-01-01 00:00:00    travel-data
pod "s3test" deleted from demo-travel namespace
```
