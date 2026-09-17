# VPS deployment

ARIA runs on the VPS as an isolated Docker Compose project. Only the API joins the existing
attachable `memora_public` edge network; PostgreSQL, Redis, and every worker remain on ARIA's
private bridge network. Caddy reaches the API through the `aria-api` network alias. The API's
optional host port remains bound to loopback for recovery and diagnostics.

The deployment uses all three Compose files, in this order:

```text
compose.yaml
compose.production.yaml
compose.vps.yaml
```

Set `ARIA_ALLOWED_HOSTS=aria.memora.com.my,api,aria-api` and
`ARIA_CSRF_TRUSTED_ORIGINS=https://aria.memora.com.my` in the VPS `.env`. Production also requires
unique application, PostgreSQL, Redis, metrics, and backup-encryption secrets; scoped R2
credentials; an immutable `ARIA_RELEASE_REVISION`; and distinct reviewer and publisher accounts.

Start and validate the deployment from the checked-out release directory:

```text
make vps-start
make vps-status
make vps-readiness
```

`make vps-readiness` must pass before adding the Caddy block from
`deploy/caddy/aria.Caddyfile` or creating public DNS. The expected Cloudflare DNS record is a
proxied `A` record for `aria.memora.com.my` pointing to the VPS origin address. Keep PostgreSQL,
Redis, Prometheus, and the loopback API port out of public firewall rules.

Stop ARIA without deleting its volumes:

```text
make vps-stop
```

Persistent deletion is intentionally not wrapped in a Make target.
