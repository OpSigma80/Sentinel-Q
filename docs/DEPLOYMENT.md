# Sentinel-Q Deployment

## Production Database

Sentinel-Q uses PostgreSQL as the production database.

SQLite is limited to fixtures and tests.

## Startup and Migrations

On startup the application runs:

1. `Base.metadata.create_all(bind=engine)`
2. `run_migrations(engine)`

The current migrations are idempotent and can run on every startup.

## Minimum Environment Variables

Minimum variables for a functional deployment:

- `DATABASE_URL`
- `JWT_SECRET_KEY`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Useful JWT variables:

- `JWT_ALGORITHM` default `HS256`
- `JWT_EXPIRE_MINUTES`

Optional Telegram variables:

- `BOT_TOKEN`
- `CHAT_ID`

Useful operational variables:

- `CORS_ORIGINS`
- `LOG_LEVEL`
- `ALERT_FAILURE_THRESHOLD`
- `ALERT_RECOVERY_THRESHOLD`
- `ALERT_COOLDOWN_SECONDS`

## Minimal Local Example

```bash
export DATABASE_URL=postgresql://user:password@localhost:5432/sentinel_db
export JWT_SECRET_KEY=change_me_in_production
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=sentinel_admin_2026
PYTHONPATH=src venv/bin/uvicorn sentinel.main:app --host 0.0.0.0 --port 8000
```

## JWT Secret and Configuration

Operational requirements:

- do not use the default value in production
- keep `JWT_SECRET_KEY` out of the repository
- set a coherent expiration through `JWT_EXPIRE_MINUTES`
- keep `ADMIN_USERNAME` and `ADMIN_PASSWORD` as environment secrets

## Post-Deploy Smoke Checks

### 1. Startup without import errors

```bash
PYTHONPATH=src venv/bin/python -c "from sentinel.main import app; print('OK')"
```

### 2. Admin login

```bash
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=$ADMIN_USERNAME&password=$ADMIN_PASSWORD"
```

### 3. List targets with a token

```bash
TOKEN="<access_token>"

curl http://localhost:8000/targets \
  -H "Authorization: Bearer $TOKEN"
```

### 4. Verify authenticated health endpoint

```bash
curl http://localhost:8000/health \
  -H "Authorization: Bearer $TOKEN"
```

### 5. Verify admin endpoints

```bash
curl http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer $TOKEN"
```

## Deployment Notes

- the application mounts static files during startup
- the scheduler starts during startup
- the Telegram bot is also started during startup when configuration is present
- current lifecycle warnings remain outside the main Week 4 scope
