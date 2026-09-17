# VPS deployment

ARIA runs on the VPS as an isolated Docker Compose project. Application code and the reader bundle
run directly from the immutable, read-only container image; production does not bind-mount the
host source tree. Only the API joins the existing
attachable `memora_public` edge network; PostgreSQL, Redis, and every worker remain on ARIA's
private bridge network. Caddy reaches the API through the `aria-api` network alias. The API's
optional host port remains bound to loopback for recovery and diagnostics.

The deployment uses all three Compose files, in this order:

```text
compose.yaml
compose.production.yaml
compose.vps.yaml
```

Set `ARIA_ALLOWED_HOSTS=aria.axelyn.com,api,aria-api` and
`ARIA_CSRF_TRUSTED_ORIGINS=https://aria.axelyn.com` in the VPS `.env`. Set the Grafana public URL
to `https://metrics.aria.axelyn.com`; the production Compose override enforces that domain and
redirect target. Production also requires
unique application, PostgreSQL, Redis, metrics, and backup-encryption secrets; scoped R2
credentials; an immutable `ARIA_RELEASE_REVISION`; and distinct reviewer and publisher accounts.

Start and validate the deployment from the checked-out release directory:

```text
make vps-start
make vps-status
make vps-readiness
```

`make vps-readiness` must pass before adding the Caddy block from
`deploy/caddy/aria.Caddyfile` or creating public DNS. Use a proxied `A` record for
`aria.axelyn.com`. Use a DNS-only `A` record for `metrics.aria.axelyn.com`, because Cloudflare's
standard `*.axelyn.com` edge certificate does not cover that nested hostname; Caddy terminates its
public TLS connection at the VPS. Proxy the metrics record only after provisioning an edge
certificate that explicitly covers it. The shared edge proxy may reach only `aria-api` and
`aria-grafana`; Grafana still requires its own login. Cloudflare Access can be placed in front of
the metrics hostname after proxying and configuring the account identity policy. Keep PostgreSQL,
Redis, Prometheus, and the loopback API and Grafana ports out of public firewall rules.

Stop ARIA without deleting its volumes:

```text
make vps-stop
```

Persistent deletion is intentionally not wrapped in a Make target.
