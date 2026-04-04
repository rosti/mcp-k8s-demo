# MCP + Kubernetes Troubleshooting Demo

## The Scenario

A Go-based order service is deployed to Kubernetes. Everything **looks** healthy:
- Pod status: `Running`
- Liveness probe: `passing`
- Readiness probe: `passing`

But every request to `/api/orders` returns **500 Internal Server Error**.

## The Bug

The application expects its database config file at:
```
/app/config/db-credentials.json
```

The Kubernetes manifest mounts the secret at:
```
/etc/secrets/db-credentials.json
```

This path mismatch is invisible to Kubernetes — the pod starts, the health checks
pass (they don't touch the config), and the pod stays in `Running` state.

The failure only surfaces when real traffic hits the business endpoint.

## Why This Is a Good Demo Bug

1. **It's realistic** — path mismatches between app expectations and K8s manifests
   happen constantly, especially when different teams own the app vs. the infra.

2. **It's cross-layer** — you can't find the root cause by looking at *only* the
   logs, *only* the K8s events, or *only* the deployment spec. You need all three.

3. **It's subtle** — `kubectl get pods` shows `Running 1/1 Ready`. The problem is
   invisible to standard monitoring until users start complaining.


## Running Locally (without K8s)

```bash
# The service starts fine — no crash
cd app && go run main.go

# Health check passes
curl http://localhost:8080/healthz

# But orders fail (no config file at /app/config/)
curl http://localhost:8080/api/orders

# Run the traffic simulator
../scripts/simulate-traffic.sh
```

## Running on Kubernetes

```bash
# Create namespace
kubectl create namespace demo

# Build image (if using local cluster like kind/minikube)
docker build -t order-service:latest ./app

# Deploy
kubectl apply -f k8s/deployment.yaml

# Watch — pods will show Running, Ready 1/1
kubectl get pods -n demo -w

# Port-forward and test
kubectl port-forward -n demo svc/order-service 8080:80
./scripts/simulate-traffic.sh
```

## What MCP Reveals

When the MCP context aggregator collects data from this scenario, it provides:

| Signal | What It Shows |
|--------|--------------|
| Pod status | Running, Ready — misleading |
| Logs | `CONFIG ERROR: failed to read config from /app/config/...` |
| Deployment spec | `volumeMounts.mountPath: /etc/secrets` |
| Events | No warnings — K8s sees nothing wrong |

The AI agent cross-references these signals:
- Log says: expected path is `/app/config/`
- Spec says: mounted at `/etc/secrets/`
- **Conclusion: path mismatch → fix the volumeMount**

This is cross-layer reasoning that no single tool provides out of the box.
