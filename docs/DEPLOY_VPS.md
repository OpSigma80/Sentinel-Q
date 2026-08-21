# Sentinel-Q — VPS Deployment Guide

This guide covers everything needed to deploy Sentinel-Q on a single Linux VPS
with automatic HTTPS using Docker Compose and Caddy.

---

## Requirements

| Requirement | Minimum |
|---|---|
| OS | Ubuntu 22.04 LTS or Debian 12 |
| CPU | 1 vCPU |
| RAM | 1 GB (2 GB recommended) |
| Disk | 10 GB |
| Docker | 24+ with Compose plugin (`docker compose`) |
| DNS | Two A records pointing to the VPS IP |

### Install Docker (if not already installed)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in for group membership to take effect
```

---

## Open firewall ports

Allow inbound traffic on ports 22, 80, and 443. Example using `ufw`:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw reload
```

> **SSH hardening:** Prefer SSH key authentication and disable password auth
> (`PasswordAuthentication no` in `/etc/ssh/sshd_config`). This is the single
> highest-impact security step for a VPS.

> ⚠️ **Do NOT expose Postgres to the public internet.**
> Port 5432 must never be opened in the firewall. The database is accessible
> only within the internal Docker network.

---

## DNS setup

Create two A records pointing to your VPS public IP before deploying.
Caddy requires DNS to resolve correctly to obtain TLS certificates.

```
api.example.com       A   <YOUR_VPS_IP>
dashboard.example.com A   <YOUR_VPS_IP>   # optional — skip if not using dashboard
```

---

## First-time deployment

### 1. Clone the repository

```bash
git clone https://github.com/your-org/sentinel-q.git
cd sentinel-q
```

### 2. Configure environment variables

```bash
cp .env.example .env
nano .env
```

Replace every `CHANGE_ME` placeholder with real values.
At minimum, set:

- `POSTGRES_PASSWORD` — strong random password
- `DATABASE_URL` — must match `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- `JWT_SECRET_KEY` — at least 32 random characters (`openssl rand -hex 32`)
- `ADMIN_PASSWORD` — strong admin password
- `BOT_TOKEN` / `CHAT_ID` — only if using Telegram alerts

### 3. Configure domains

Edit `deploy/Caddyfile` and replace the two placeholders:

```
YOUR_API_DOMAIN       → api.example.com
YOUR_DASHBOARD_DOMAIN → dashboard.example.com
```

If you are not using the dashboard, delete the second block and comment out
the `dashboard` service in `docker-compose.prod.yml`.

### 4. Build and start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Caddy will obtain TLS certificates automatically on first start.
This requires ports 80 and 443 to be reachable from the internet.

### 5. Verify services

```bash
docker compose -f docker-compose.prod.yml ps
```

Expected output:

```
NAME                   STATUS
sentinel_db_container  Up (healthy)
sentinel_app_container Up (healthy)
sentinel_dashboard     Up
sentinel_caddy         Up
```

Test the API:

```bash
curl https://api.example.com/health
```

---

## Viewing logs

All services at once:

```bash
docker compose -f docker-compose.prod.yml logs -f
```

Single service:

```bash
docker compose -f docker-compose.prod.yml logs -f app
docker compose -f docker-compose.prod.yml logs -f caddy
```

---

## PostgreSQL backup and restore

### Backup

```bash
docker exec sentinel_db_container \
  pg_dump -U $POSTGRES_USER $POSTGRES_DB \
  > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Restore

```bash
docker exec -i sentinel_db_container \
  psql -U $POSTGRES_USER $POSTGRES_DB \
  < backup_YYYYMMDD_HHMMSS.sql
```

> Store backups outside the VPS (S3, rsync to another host, etc.).
> The named volume `postgres_data` persists across container restarts but NOT
> across `docker compose down -v` (which removes volumes).

---

## Updating to a new version

```bash
git pull origin main
docker compose -f docker-compose.prod.yml up -d --build
```

Docker Compose will rebuild only the changed images and recreate the affected
containers. The database volume is preserved.

---

## Dashboard (optional — disabled by default)

The Streamlit dashboard requires ~300MB RAM and is **not required** for the API
to operate. It is **disabled by default** in `docker-compose.prod.yml` to ensure
safe deployment on a 1GB VPS.

**Memory budget (1GB VPS, dashboard off):**
- `db`: 256MB
- `app`: 384MB
- `caddy`: 128MB
- Total: ~768MB — leaves headroom for the OS

**To enable the dashboard (requires 2GB+ VPS):**

1. In `docker-compose.prod.yml`, uncomment the entire `dashboard:` service block.
2. In `deploy/Caddyfile`, uncomment the `YOUR_DASHBOARD_DOMAIN` block and set your domain.
3. Rebuild: `docker compose -f docker-compose.prod.yml up -d --build`

---

## Security checklist

- [ ] All `CHANGE_ME` values replaced in `.env`
- [ ] `.env` not committed to version control (check `.gitignore`)
- [ ] Firewall: only ports 22, 80, 443 open — **5432 closed**
- [ ] DNS A records set before starting Caddy
- [ ] `JWT_SECRET_KEY` is at least 32 random characters
- [ ] `ADMIN_PASSWORD` is strong and unique

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Caddy shows certificate error | DNS not propagated yet | Wait and retry; check `docker logs sentinel_caddy` |
| App container unhealthy | DB not ready or wrong `DATABASE_URL` | Check `.env`; `docker logs sentinel_app_container` |
| 502 Bad Gateway | App crashed | `docker logs sentinel_app_container` |
| Dashboard crash loop | pyarrow version mismatch | Rebuild: `docker compose -f docker-compose.prod.yml build dashboard` |
