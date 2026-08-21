# Sentinel-Q Rate Limiting

## Current Implementation

Sentinel-Q uses SlowAPI with an in-memory backend.

Base file:

- `src/sentinel/infrastructure/rate_limiter.py`

## Keying Strategy

Public endpoints:

- key by IP

Authenticated endpoints:

- key by `tenant_id:sub`
- identity is read from `request.state.token_data`
- avoids decoding the JWT again inside the key function

## Current Limits

- `POST /auth/token`: `5/minute`
- `GET /`: `120/minute`
- `GET /targets`: `60/minute`
- `POST /targets`: `30/minute`
- `DELETE /targets/{id}`: `20/minute`
- `DELETE /stop/{id}`: `20/minute`
- `GET /status`: `60/minute`
- `GET /metrics/{id}`: `60/minute`
- `GET /health`: `60/minute`
- `GET /stats/{id}`: `60/minute`
- `GET /telegram/status`: `60/minute`
- `GET /telegram/metrics`: `60/minute`
- `GET /telegram/alerts`: `60/minute`
- `POST /admin/tenants`: `100/minute`
- `GET /admin/tenants`: `100/minute`
- `POST /admin/users`: `100/minute`
- `GET /admin/users`: `100/minute`
- `POST /admin/webhooks`: `100/minute`
- `GET /admin/webhooks`: `100/minute`
- `DELETE /admin/webhooks/{id}`: `100/minute`

## 429 Response

Current format:

```json
{
  "detail": "Rate limit exceeded. Retry after N seconds."
}
```

Relevant header:

- `Retry-After`

## Known Limitations

- in-memory storage
- not distributed across multiple instances
- no Redis backend

This is acceptable for the current scope and the project's target hardware.
