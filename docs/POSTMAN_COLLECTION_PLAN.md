# Sentinel-Q Postman Collection Plan

## Environment Variables

- `base_url`
- `admin_username`
- `admin_password`
- `viewer_username`
- `viewer_password`
- `admin_token`
- `viewer_token`
- `tenant_id`
- `target_id`
- `webhook_id`
- `hours`
- `webhook_url`
- `webhook_secret`

## Recommended Structure

### Auth

- Login Admin
- Login Viewer
- Login Invalid Credentials

Minimal scripts:

- `Login Admin`: store `admin_token` from `access_token`
- `Login Viewer`: store `viewer_token` from `access_token`

### Targets

- List Targets
- Create Target
- Delete Target
- Delete Target via Stop Endpoint

### Monitoring

- Get Status
- Get Metrics by Target
- Get Stats by Target
- Get Health

### Telegram Snapshots

- Get Telegram Status
- Get Telegram Metrics
- Get Telegram Alerts

### Admin Tenants

- Create Tenant
- List Tenants

### Admin Users

- Create User
- List Users

### Admin Webhooks

- Create Webhook
- List Webhooks
- Delete Webhook

### Negative Cases

- 401 Missing Token
- 403 Invalid Token
- 403 Viewer Access to Admin Endpoint
- 404 Delete Missing Target
- 404 Delete Missing Webhook
- 409 Create Duplicate Tenant
- 409 Create Duplicate User
- 422 Invalid Webhook Events
- 429 Auth Token Rate Limited

## Recommended Flow

1. Login Admin
2. List Tenants
3. Create Tenant
4. Create User
5. Create Target
6. List Targets
7. Get Status
8. Get Metrics by Target
9. Create Webhook
10. List Webhooks
11. Run negative cases
12. Login Viewer and validate non-admin access

## Tokens by User Type

- `admin_token`: used for `/admin/*` and common authenticated endpoints
- `viewer_token`: used to validate access restricted to non-admin authenticated endpoints

## Errors Worth Modeling

- `401`
- `403`
- `404`
- `409`
- `422`
- `429`
