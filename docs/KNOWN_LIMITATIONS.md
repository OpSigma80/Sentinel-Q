# Sentinel-Q Known Limitations

## Outside the Immediate Scope

### Lifecycle / startup / shutdown

- the application still uses `@app.on_event("startup")`
- background tasks are created with `asyncio.create_task(...)`
- the lifespan refactor remains outside the main Week 4 scope

### Residual lifecycle warnings

- warnings related to startup/shutdown and transport cleanup still exist
- they do not block the current functional test suite
- they require a deeper lifecycle refactor

## Current Functional Limitations

### Database-backed login by username

- the current login resolves a DB user by `username` and uses the first active match
- this does not represent explicit tenant selection in the login endpoint

### Webhooks V1

- no retries
- no queue
- no delivery history
- no DLQ

### Rate limiting

- in-memory backend
- not distributed across replicas

## Explicit Current Decisions

- PostgreSQL remains the real production database
- SQLite is used only in fixtures/tests
- the lifecycle/lifespan refactor remains a separate technical backlog item
