# deploy/ — DEPLOY-ONLY

Deployment artifacts. Today just `nginx-api-location.conf.sample` (reverse-proxy location-block sample); the Dockerfile lives at the repo root, not here.

## Imported by active code?

NO — deploy assets are consumed by ops/build tooling, not Python runtime.

## When to touch this

When changing deploy infra. Don't put runtime code here.

## Don't read unless

- You're making a deploy-time or infrastructure change.
