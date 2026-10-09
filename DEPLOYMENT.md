# Deployment

The service runs in a Docker container on a VPS, behind Nginx.

```
Internet -> Nginx (port 80, later 443) -> 127.0.0.1:8000 -> container "okolomota" (FastAPI)
```

## Current setup

| Item | Value |
|---|---|
| VPS | Ubuntu 24.04, 2 CPU, 1.9 GB RAM, IP `45.80.70.196` |
| App directory | `/opt/okolomota` (git clone of this repo) |
| Container | `okolomota`, restarts automatically (`restart: unless-stopped`) |
| Secrets | `/opt/okolomota/.env` on the server only. Never commit it. |
| Nginx site | `/etc/nginx/sites-available/okolomota` (proxies to `127.0.0.1:8000`) |
| Firewall | `ufw` allows only ports 22, 80, 443 |
| Webhook URL | `http://45.80.70.196/okolomota-ctosrm/webhook/add-offer` |
| Health check | `http://45.80.70.196/health` |

The container port is bound to `127.0.0.1` only, so the app is reachable from the internet only through Nginx.

The VPS pulls code through a read-only GitHub deploy key (`/root/.ssh/okolomota_deploy`, SSH host alias `github-okolomota`), registered on `fares1978/okolomota_stocrm`.

## OpenRouter through a VPN

OpenRouter returns `403 Access denied by security policy` for this VPS's IP (it is hosted in Russia). So the app's OpenRouter calls go through an OpenVPN tunnel; STOCRM is called directly.

- `docker-compose.yml` runs a second container, `vpn` (gluetun), which is an OpenVPN client plus an HTTP proxy on port 8888 inside the Docker network. The VPN lives inside that container only, so the VPS's own routing and SSH are not affected.
- The app container sets `HTTPS_PROXY=http://vpn:8888` and `NO_PROXY=okolomota.stocrm.ru,...`. `httpx` picks these up automatically, so there is no code change.
- The VPN profile is `/opt/okolomota/vpn/custom.ovpn` on the server. It contains keys, so it is gitignored and must be copied to the server by hand (`scp`, then `chmod 600`).
- The profile downloaded from the OpenVPN Access Server points at `89.110.86.50`, which is not reachable. We changed the `remote` line to `remote 109.107.187.176 443`. If the profile is regenerated, apply the same fix, or correct the "Hostname or IP address" in the Access Server admin UI (Network Settings) so new profiles are right.
- Test the tunnel: `docker compose logs vpn` should end with `Initialization Sequence Completed` and a public IP in the Netherlands.
- If offers fail with proxy or connection errors, check `docker compose logs vpn` first. The free Access Server license allows 2 simultaneous connections.

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
   docker compose ps                       # status should be "healthy"
   curl -s http://127.0.0.1:8000/health
   docker compose logs --tail 30
   ```

What needs extra steps:

| You changed | Also do |
|---|---|
| Python code only | Nothing, steps above are enough |
| Dependencies (`pyproject.toml`) | Run `uv lock` locally and commit `uv.lock`. The image installs from the lockfile. |
| New or changed setting in `config.py` / `env.example` | Edit `/opt/okolomota/.env` on the server, then `docker compose up -d` |
| Nginx config | Edit the site file, run `nginx -t`, then `systemctl reload nginx`. Keep a copy of the change in this repo. |

Rollback: `git checkout <previous-commit>` in `/opt/okolomota`, then `docker compose up -d --build`.

## Useful commands (run in `/opt/okolomota`)

```bash
docker compose logs -f          # live logs
docker compose restart          # restart without rebuilding
docker compose down             # stop and remove the container
docker image prune -f           # reclaim disk from old images
```

## Open items

- [ ] **Domain + HTTPS.** Buy a domain, add an A record to `45.80.70.196`, set `server_name` in the Nginx site to the domain, then run certbot to get a Let's Encrypt certificate. Until then the webhook is plain HTTP.
- [ ] **Webhook signature.** `SHARED_SECRET` is empty, so anyone who knows the URL can post to the webhook. To enable it, set `SHARED_SECRET` in `.env` and have the caller send `x-yapogovoru-signature: sha256=<HMAC of the raw body>`. To be agreed with the developer partner.
- [ ] **Rotate the STOCRM SID.** The README notes the SID in the original n8n workflow was exposed.
- [ ] **Swap.** The VPS has no swap. Add a 2 GB swap file (`fallocate`, `mkswap`, `swapon`, plus an `/etc/fstab` entry) to avoid out-of-memory kills during builds.
- [ ] **Non-root user.** Everything runs as `root` over SSH. Create a sudo user, then disable root login and password login in `sshd_config`. Keep a second session open while testing.
- [ ] **Remove old test data** created in STOCRM during testing.
- [ ] **Log growth.** Docker logs are unlimited by default. Consider adding `logging: options: max-size` in `docker-compose.yml`.

## Adding another project on the same VPS

Give it its own directory under `/opt`, its own `docker-compose.yml` bound to a different loopback port (for example `127.0.0.1:8001:8000`), and its own Nginx site file with its own `server_name` (a domain or subdomain). Get a certificate for each domain with certbot.
