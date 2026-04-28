



kubectl get crds -n demo-travel
NAME                              CREATED AT
addons.k3s.cattle.io              2026-02-12T09:42:48Z
etcdsnapshotfiles.k3s.cattle.io   2026-02-12T09:42:48Z
helmchartconfigs.helm.cattle.io   2026-02-12T09:42:48Z
helmcharts.helm.cattle.io         2026-02-12T09:42:48Z
mcpcards.mcp.travel.io            2026-02-12T21:28:28Z
mcpcards.travel.demo              2026-02-24T14:20:58Z
ubuntu@tools:~/agentic_lab/phase2_introducing_mcp$ kubectl get cr -n demo-travel
error: the server doesn't have a resource type "cr"
ubuntu@tools:~/agentic_lab/phase2_introducing_mcp$ kubectl get mcpcards.travel.demo -n demo-travel
NAME               SERVER               TRANSPORT         NODEPORT   TOOLS   AGE
travel-mcp-card    travel-mcp-server    streamable-http   30100              24h
weather-mcp-card   weather-mcp-server   streamable-http   30101              24h
ubuntu@tools:~/agentic_lab/phase2_introducing_mcp$ kubectl describe mcpcards.travel.demo travel-mcp-card  -n demo-travel
Name:         travel-mcp-card
Namespace:    demo-travel
Labels:       app=travel-mcp
Annotations:  <none>
API Version:  travel.demo/v1alpha1
Kind:         MCPCard



