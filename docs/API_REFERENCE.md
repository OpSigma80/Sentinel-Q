# Sentinel-Q API Reference

Functional reference for the API currently exposed by Sentinel-Q in `src/sentinel/main.py`.

## Auth

### POST /auth/token

- Auth required: no
- Rate limit: `5/minute` per IP
- Content-Type: `application/x-www-form-urlencoded`
- Body:
  - `username`
  - `password`
- 200 response:
  - `access_token`
  - `token_type`
- Common errors:
  - `401 Invalid credentials`
  - `429 Rate limit exceeded`

## Targets

### GET /targets

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`
- Scope: token tenant

### POST /targets

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `30/minute`
- JSON body:
  - `name`
  - `url`
  - `check_interval`
  - `is_active`
- Response: 201

### DELETE /targets/{target_id}

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `20/minute`
- Response: 204
- Common error: `404 Target not found`

### DELETE /stop/{target_id}

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `20/minute`
- Response: 204
- Note: legacy/UI endpoint with behavior equivalent to `DELETE /targets/{target_id}`

## Monitoring

### GET /status

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`
- Scope: token tenant

### GET /metrics/{target_id}

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`
- Returns the latest 100 metrics for the target

### GET /stats/{target_id}

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`
- Returns `health_score` and aggregated target statistics

### GET /health

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`
- Note: currently not public

## Telegram Snapshots

### GET /telegram/status

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`

### GET /telegram/metrics

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`

### GET /telegram/alerts

- Auth required: Bearer JWT
- Role required: any authenticated user
- Rate limit: `60/minute`
- Query params:
  - `hours` default `24`

## Admin Tenants

### POST /admin/tenants

- Auth required: Bearer JWT
- Role required: `admin`
- Rate limit: `100/minute`
- JSON body:
  - `name`
- Response: 201
- Common errors:
  - `403 Admin role required`
  - `409` for duplicate tenant
  - `422 'name' is required`

### GET /admin/tenants

- Auth required: Bearer JWT
- Role required: `admin`
- Rate limit: `100/minute`

## Admin Users

### POST /admin/users

- Auth required: Bearer JWT
- Role required: `admin`
- Rate limit: `100/minute`
- JSON body:
  - `username`
  - `password`
  - optional `role`: `admin` or `viewer`
  - optional `tenant_id`
- Response: 201
- Common errors:
  - `403 Admin role required`
  - `409` for duplicate user in the tenant
  - `422` for invalid body content

### GET /admin/users

- Auth required: Bearer JWT
- Role required: `admin`
- Rate limit: `100/minute`
- Scope: users for the authenticated admin tenant

## Admin Webhooks

### POST /admin/webhooks

- Auth required: Bearer JWT
- Role required: `admin`
- Rate limit: `100/minute`
- JSON body:
  - `url`
  - optional `secret`
  - optional `events`: `down`, `up`, `down,up`
- Response: 201
- Common errors:
  - `422 'url' is required`
  - `422 'events' must be one of ...`

### GET /admin/webhooks

- Auth required: Bearer JWT
- Role required: `admin`
- Rate limit: `100/minute`
- Scope: webhooks for the authenticated admin tenant

### DELETE /admin/webhooks/{webhook_id}

- Auth required: Bearer JWT
- Role required: `admin`
- Rate limit: `100/minute`
- Response: 204
- Common error: `404 Webhook not found`

## Common Errors

- `401 Missing authentication token`
- `403 Invalid or expired token`
- `403 Admin role required`
- `404` resource not found
- `409` business conflict
- `422` validation/body error
- `429 Rate limit exceeded. Retry after N seconds.`
