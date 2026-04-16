# MiniKube Deployment Guide — Trust Flow

This guide provides step-by-step instructions for deploying the full Trust Flow stack on a local MiniKube cluster. This satisfy the judging criteria for local Kubernetes orchestration.

## Prerequisites
- **MiniKube** (v1.32+)
- **kubectl**
- **Docker Desktop** (or equivalent runtime compatible with MiniKube)

## 1. Start MiniKube
Bootstrap the cluster with sufficient resources for the LangGraph and Monitoring stack.
```bash
minikube start --memory=6144 --cpus=4 --driver=docker
minikube addons enable ingress
eval $(minikube docker-env)
```

## 2. Build Images
Since the manifests use `imagePullPolicy: Never`, you must build the images within the MiniKube Docker daemon.
```bash
docker build -t trustflow/backend:latest ./backend
docker build -t trustflow/frontend:latest ./frontend
```

## 3. Deploy Everything
Use the provided `kustomization.yaml` to deploy all resources into the `trust-flow` namespace.
```bash
kubectl apply -k k8s/
kubectl get pods -n trust-flow --watch
```

## 4. Verify Architecture (Judges' Check)
Confirm that parallelisation is active by checking for 2 active Celery worker pods.
```bash
kubectl get pods -n trust-flow | grep celery
# Expected output: celery-workers-<hash>-<hash>   1/1   Running   (×2)
```

## 5. Access Services
Retrieve the local URLs for the key dashboards and the application frontend.
```bash
# Frontend UI
minikube service frontend-service -n trust-flow --url

# Grafana (User: admin / Password: in k8s/01-secrets.yaml)
minikube service grafana-service -n trust-flow --url

# Prometheus
minikube service prometheus-service -n trust-flow --url
```

### Optional: Ingress Access
To access the stack via `trustflow.local`, add the MiniKube IP to your hosts file:
```bash
echo "$(minikube ip) trustflow.local" | sudo tee -a /etc/hosts
```

## 6. Teardown
Clean up all resources by removing the namespace.
```bash
kubectl delete namespace trust-flow
```
