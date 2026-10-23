# Sentinel-Q Webhooks

## Current Status

Outbound webhooks were implemented by the end of Week 3.

## Administration Endpoints

- `POST /admin/webhooks`
- `GET /admin/webhooks`
- `DELETE /admin/webhooks/{webhook_id}`

All of them require:

- Bearer JWT
- `admin` role
- `100/minute` rate limit

## Create Body

Supported fields:

- required `url`
- optional `secret`
- optional `events`

Valid `events` values:

- `down`
- `up`
- `down,up`

## When Sentinel-Q Dispatches

The scheduler dispatches only on real state transitions:

- `up -> down`
- `down -> up`

It does not dispatch while the service stays in a stable state without a transition.

## Current Payload

```json
{
  "event": "down",
  "target": {
    "id": 42,
    "name": "My API",
    "url": "https://api.example.com/health"
  },
  "tenant_id": 1,
  "timestamp": "2026-04-23T19:00:00+00:00"
}
```

## Emitted Headers

- `Content-Type: application/json`
- `X-Sentinel-Event: down|up`
- `X-Sentinel-Signature: sha256=<hex>` when a `secret` exists

## Signature

If the webhook has a `secret`, Sentinel-Q signs the raw body with HMAC-SHA256.

Current format:

```text
sha256=<hex>
```

## Current Implementation

- short timeout: `5.0` seconds
- `httpx.AsyncClient`
- fire-and-forget behavior
- exceptions are logged and not propagated back to the scheduler

## V1 Limitations

- no retries
- no queue
- no dead-letter queue
- no delivery history
- no alternate signature format or payload versioning
