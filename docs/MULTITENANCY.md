# Sentinel-Q Multi-Tenancy

## Current Status

Multi-tenant isolation is already implemented and runs on PostgreSQL in production.

## Current Model

Relevant tables:

- `tenants`
- `users`
- `services`
- `webhook_subscriptions`

Key relationships:

- one tenant has many users
- one tenant has many services
- one tenant has many outbound webhooks

## Actual Scoping

`tenant_id` travels inside the JWT and is used to:

- list targets for the tenant
- list status for the tenant
- list users for the authenticated admin tenant
- create webhooks for the authenticated tenant
- derive rate-limit keys as `tenant_id:sub`

## Relevant Admin Endpoints

- `POST /admin/tenants`
- `GET /admin/tenants`
- `POST /admin/users`
- `GET /admin/users`

## Migrations

The application runs idempotent migrations during startup.

Current phases:

1. create `tenants` and `users`
2. add `tenant_id` to `services`
3. ensure the `default` tenant exists
4. migrate orphan services into the `default` tenant
5. ensure the `uq_services_tenant_name` composite uniqueness constraint
6. validate the post-migration state
7. create `webhook_subscriptions`

## Default Tenant

The system guarantees a `default` tenant for backward compatibility with previous data.

## Tenant Roles

- `admin`
- `viewer`

Admin authorization is enforced at runtime through the `_require_admin` dependency.

## Known Limitation

The current database-backed login looks up users by `username` and uses the first active match it finds. That keeps the flow simple, but it is not an explicit tenant-aware login resolution model.
