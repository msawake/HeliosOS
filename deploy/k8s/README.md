# Kubernetes deployment (portable)

Vendor-neutral manifests for EKS, GKE, AKS, k3s, or any conformant cluster. Uses **Kustomize** only (no cloud-specific CRDs).

## Layout

- `base/` — Namespace, ConfigMap, Deployments, Services, Ingress.

## Images

Build two OCI images from the repo root:

```bash
docker build -f infrastructure/docker/Dockerfile -t forgeos-api:latest .
docker build -f dashboard/Dockerfile -t forgeos-web:latest .
```

Push to your registry, then edit `base/kustomization.yaml` `images:` (or use Kustomize `images` / `kustomize edit set image`).

**GitHub Actions** (`.github/workflows/docker-publish.yml`) pushes to:

`ghcr.io/<github-owner>/forgeos-api` and `ghcr.io/<github-owner>/forgeos-web`.

Example:

```bash
kustomize build deploy/k8s/base \
  | sed 's|forgeos-api:latest|ghcr.io/my-org/forgeos-api:main|g; s|forgeos-web:latest|ghcr.io/my-org/forgeos-web:main|g' \
  | kubectl apply -f -
```

## Secrets

Create `forgeos-secrets` in namespace `forgeos` (optional keys — all optional in Deployments):

```bash
kubectl -n forgeos create secret generic forgeos-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=OPENAI_API_KEY=sk-... \
  --from-literal=DATABASE_URL=postgres://... \
  --from-literal=REDIS_URL=redis://...
```

## Ingress

- Replace `forgeos.example.local` in `ingress.yaml` with your hostname.
- Set `spec.ingressClassName` to your controller’s class (`nginx`, `traefik`, `webapprouting`, etc.).
- TLS: uncomment `tls` and create a certificate secret or use cert-manager.

Traffic split: **`/api` → API Service**, **`/` → Web Service**. The browser calls same origin `/api/*`; no Next.js rewrite is required in-cluster.

## GCP Cloud Build

Optional GCP pipeline remains in `infrastructure/docker/cloudbuild.yaml` (Artifact Registry + Cloud Run). It is not required for generic Kubernetes.
