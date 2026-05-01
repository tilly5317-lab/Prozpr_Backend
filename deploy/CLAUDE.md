# deploy/ — DEPLOY-ONLY

Deployment artifacts (Dockerfile context, CI/CD config, infrastructure definitions). Not imported by runtime code.

## Imported by active code?

NO — deploy assets are consumed by CI/CD or container build tooling, not Python runtime.

## When to touch this

When changing deploy infra (Docker image, CI pipeline, k8s manifests). Don't put runtime code here.

## Don't read unless

- You're making a deploy-time or infrastructure change.
