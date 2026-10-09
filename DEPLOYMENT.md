# Deployment

The service runs in Docker on a VPS, behind Nginx. Its OpenRouter calls go out through an OpenVPN tunnel.

```
caller (n8n / partner) --HTTPS--> Nginx (443, 80) --> 127.0.0.1:8000 --> container "okolomota" (FastAPI)
                                                                              |-- STOCRM: direct
                                                                              '-- OpenRouter: via container "vpn" (OpenVPN) --> VPN server
```

## Current setup

| Item | Value |
|---|---|
| VPS | Ubuntu 24.04, 2 CPU, 1.9 GB RAM, no swap, IP `45.80.70.196` |
| App directory | `/opt/okolomota` (git clone of this repo) |
| Containers | `okolomota` (app) and `okolomota-vpn` (gluetun), both `restart: unless-stopped` |
| Secrets | `/opt/okolomota/.env` and `/opt/okolomota/vpn/custom.ovpn`, on the server only. Never commit them. |
| Nginx site | `/etc/nginx/sites-available/okolomota`, versioned as `nginx/okolomota.conf` |
| Firewall | `ufw` allows only ports 22, 80, 443 |
| Webhook URL | `https://45-80-70-196.sslip.io/okolomota-ctosrm/webhook/add-offer` (POST) |
| Health check | `https://45-80-70-196.sslip.io/health` |
| STOCRM | host, board and source IDs come from `.env` (`STOCRM_HOST`, `STOCRM_BOARD_ID`, `STOCRM_SOURCE_ID`) |

The app port is bound to `127.0.0.1` only, so the app is reachable from the internet only through Nginx.

The VPS pulls code through a read-only GitHub deploy key (`/root/.ssh/okolomota_deploy`, SSH host alias `github-okolomota`), registered on `fares1978/okolomota_stocrm`.

## Security

### What Nginx exposes
`nginx/okolomota.conf` allows only:
- `POST /okolomota-ctosrm/webhook/add-offer` (rate limited to 10 req/s per IP, burst 20)
- `GET /health`
- `/.well-known/acme-challenge/` for certificate validation

Everything else returns 404. This hides `POST /process`, which runs the whole pipeline **without any signature check** (see `main.py`), and FastAPI's `/docs`, `/redoc`, `/openapi.json`. This protection exists only in Nginx. If the app is ever exposed another way, those routes are open.

Nginx also sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and `Content-Security-Policy`, and hides its version.

### Webhook signature (`SHARED_SECRET`)
The code verifies `x-yapogovoru-signature` on the webhook route only, and only when `SHARED_SECRET` is set. The value is `sha256=<lowercase hex HMAC-SHA256 of the raw request body, keyed with SHARED_SECRET>`. `x-yapogovoru-timestamp` is read but only logged. It is not part of the signature and is not checked, so a captured request could be replayed.

Enable it:
1. Generate a secret (`openssl rand -hex 32`). Put it in `/opt/okolomota/.env` as `SHARED_SECRET=...` on the server, then run `docker compose up -d`. Share it with the caller privately, never in git or chat.
2. Every caller must then sign its requests, or it gets `401`.

Signing in n8n:
- A **Crypto** node before the HTTP Request node: Action `Hmac`, Type `SHA256`, Value `{{ JSON.stringify($json.body) }}`, Secret = the secret, Encoding `hex`, Property Name `signature`.
- In the **HTTP Request** node: Send Headers `x-yapogovoru-signature` = `sha256={{ $json.signature }}`. Body Content Type **Raw**, Content Type `application/json`, Body `{{ JSON.stringify($('When Executed by Another Workflow').item.json.body) }}`.
- The hashed string and the sent body must be byte-identical. Read the body from the trigger node, not from `$json`, which after the Crypto node also contains `signature`. A mismatch gives `401 bad signature`.
- Test with `DRY_RUN=true` first: a correct secret returns 200 and logs "DRY_RUN would POST"; a wrong one returns 401.

### Known weaknesses in the app (to discuss with the developer)
- **Any payload is accepted.** An empty `{}` body ran the full pipeline and created a junk offer in STOCRM. `has_phone` only checks the field is non-empty, so a placeholder like "не определён" counts as a phone.
- **The webhook returns 200 even when the LLM step fails**, so the caller does not know and does not retry.
- **`stocrm.py` hard-codes a `FARES TEST` prefix** in the offer title and comment. Remove it before real traffic.
- **The dry-run log prints the full STOCRM request, including the SID.** Mask it, and be careful when pasting logs.
- The timestamp header is not validated (replay).

## HTTPS (Let's Encrypt, sslip.io hostname)

- Hostname: `45-80-70-196.sslip.io` (also `45.80.70.196.sslip.io`). sslip.io is a free third-party DNS service that resolves any name containing an IP to that IP. We do not own the domain. It is a stop-gap until a real domain is bought. Risks: it depends on sslip.io's DNS, and it is not on the Public Suffix List, so Let's Encrypt's weekly new-certificate limit is shared with every sslip.io user (renewals are exempt).
- The certificate is named `sslip` (`/etc/letsencrypt/live/sslip/`), issued with `certbot certonly --webroot --webroot-path /var/www/certbot --cert-name sslip -d 45-80-70-196.sslip.io -d 45.80.70.196.sslip.io`. It is valid for **90 days**, and Certbot renews it about 30 days before expiry.
- The `nginx` Certbot plugin is not used. The certificate paths are written by hand in `nginx/okolomota.conf`.
- Requests to the bare IP over HTTPS get a hostname mismatch error, because the certificate has no IP in it. Callers must use the hostname. (We briefly used a 6-day IP certificate, `--preferred-profile shortlived --ip-address`, and removed it.)
- Certbot 5.x is installed in a virtualenv at `/opt/certbot` (symlinked to `/usr/local/bin/certbot`), because the Ubuntu package is old. Upgrade with `/opt/certbot/bin/pip install -U certbot`.
- Renewal: `certbot-renew.timer` (systemd) runs `certbot renew` twice a day. `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` reloads Nginx after each renewal.
- Check it: `systemctl list-timers certbot-renew.timer`, `certbot certificates`, `certbot renew --dry-run`.
- **If renewal fails, the certificate expires after 90 days** and HTTPS callers start failing. Certbot starts renewing about 30 days before that, so check `journalctl -u certbot-renew` if `certbot certificates` shows less than ~25 days left.
- Challenge files are served from `/var/www/certbot` over port 80, so port 80 must stay open.
- No HSTS yet. Add it once a permanent domain is used.
- Port 80 still proxies to the app, so callers using `http://` keep working. After they switch to `https://`, redirect or close it (keep the `/.well-known/acme-challenge/` path open).

## OpenRouter through a VPN

OpenRouter returns `403 Access denied by security policy` for this VPS's IP (it is hosted in Russia). So the app's OpenRouter calls go through an OpenVPN tunnel, and STOCRM is called directly.

- `docker-compose.yml` runs a second container, `vpn` (gluetun), an OpenVPN client plus an HTTP proxy on port 8888 inside the Docker network. The VPN lives inside that container only, so the VPS's own routing and SSH are not affected.
- The app container sets `HTTPS_PROXY=http://vpn:8888` and `NO_PROXY=okolomota.stocrm.ru,...`. `httpx` picks these up automatically, so there is no code change.
- The VPN profile is `/opt/okolomota/vpn/custom.ovpn` on the server (gitignored, copy it by hand with `scp`, then `chmod 600`). It is an autologin profile (certificate embedded), so no username or password is needed. The original download is kept as `custom.ovpn.orig`.
- The profile from the OpenVPN Access Server points at `89.110.86.50`, which is unreachable. We changed the `remote` line to `remote 109.107.187.176 443`. The Access Server's "Hostname or IP address" (Network Settings) should be fixed, or every new profile will have the wrong address.
- Test the tunnel: `docker compose logs vpn` should show `Initialization Sequence Completed` and a Netherlands public IP. Test OpenRouter from the app container:
  ```bash
  docker compose exec okolomota python -c "import httpx; print(httpx.get('https://openrouter.ai/api/v1/models', timeout=20).status_code)"   # expect 200
  ```
  A plain `curl` on the VPS host still returns 403, because only the containers use the VPN.
- If offers fail with proxy or connection errors, check `docker compose logs vpn` first. The VPN server belongs to someone else and is a dependency. The Access Server's free license allows 2 simultaneous connections.

## Updating after you push changes

1. Push to GitHub (both remotes: `origin` and `brigade`).
2. On the VPS:
   ```bash
   ssh root@45.80.70.196
   cd /opt/okolomota
   git pull
   docker compose up -d --build
   ```
3. Check it came up:
   ```bash
   docker compose ps                       # both containers "healthy"
   curl -s https://45-80-70-196.sslip.io/health
   docker compose logs --tail 30
   ```

What needs extra steps:

| You changed | Also do |
|---|---|
| Python code only | Nothing, steps above are enough |
| Dependencies (`pyproject.toml`) | Run `uv lock` locally and commit `uv.lock`. The image installs from the lockfile. |
| New or changed setting in `config.py` / `env.example` | Edit `/opt/okolomota/.env` on the server, then `docker compose up -d` |
| Nginx config (`nginx/okolomota.conf`) | `cp nginx/okolomota.conf /etc/nginx/sites-available/okolomota`, then `nginx -t && systemctl reload nginx` |

Rollback: `git checkout <previous-commit>` in `/opt/okolomota`, then `docker compose up -d --build`.

`DRY_RUN=true` in `.env` makes the app log the STOCRM request instead of sending it. Remember to set it back to `false`.

## Useful commands (run in `/opt/okolomota`)

```bash
docker compose logs -f okolomota   # live app logs
docker compose logs vpn            # VPN logs
docker compose restart             # restart without rebuilding
docker compose down                # stop and remove the containers
docker image prune -f              # reclaim disk from old images
```

## Open items

- [ ] **Domain.** Buy one, add an A record to `45.80.70.196`, set `server_name`, get a normal certificate with certbot (`--webroot`, same webroot), add HSTS, update the URL in n8n.
- [ ] **Switch all callers to `https://`, then redirect or close plain HTTP.**
- [ ] **Agree the signature with the developer partner**, then set `SHARED_SECRET` and have the real caller sign requests. Until then anyone who knows the URL can create offers.
- [ ] **Fix the app weaknesses** listed under Security (payload validation, error status on LLM failure, the `FARES TEST` prefix, masking the SID in logs, timestamp check, protecting or removing `/process`).
- [ ] **Rotate the STOCRM SID.** The SID in the original n8n workflow was exposed, and it was also printed in dry-run logs that were shared.
- [ ] **Ask the PM to fix the OpenVPN Access Server's "Hostname or IP address".**
- [ ] **Check certificate renewal** after a few days (`certbot certificates`).
- [ ] **Swap.** The VPS has no swap. Add a 2 GB swap file (`fallocate`, `mkswap`, `swapon`, plus an `/etc/fstab` entry) to avoid out-of-memory kills during builds.
- [ ] **Non-root user.** Everything runs as `root` over SSH. Create a sudo user, then disable root login and password login in `sshd_config`. Keep a second session open while testing.
- [ ] **Remove test offers** created in STOCRM during testing.
- [ ] **Log growth.** Docker logs are unlimited by default. Consider `logging: options: max-size` in `docker-compose.yml`.

## Adding another project on the same VPS

Give it its own directory under `/opt`, its own `docker-compose.yml` bound to a different loopback port (for example `127.0.0.1:8001:8000`), and its own Nginx site file with its own `server_name` (a domain or subdomain). Get a certificate for each domain with certbot. Note that a single `default_server` already exists for the IP, so new sites need distinct `server_name` values.
