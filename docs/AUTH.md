# Sentinel-Q Auth

## Login Endpoint

### POST /auth/token

- Flow: OAuth2 password form
- Expected body:
  - `username`
  - `password`
- Successful response:
  - `access_token`
  - `token_type=bearer`

## Bearer JWT

The protected API uses this header:

```text
Authorization: Bearer <access_token>
```

Current validation logic lives in `src/sentinel/infrastructure/auth.py`:

- `401` if the token is missing
- `403` if the token is invalid or expired

## Current JWT Claims

Claims issued by `src/sentinel/infrastructure/jwt_service.py`:

- `sub`
- `tenant_id`
- `role`
- `exp`

Conceptual example:

```json
{
  "sub": "admin",
  "tenant_id": 1,
  "role": "admin",
  "exp": 1745000000
}
```

## Roles

- `admin`
- `viewer`

Current usage:

- `admin`: access to `/admin/*` endpoints
- `viewer`: access to authenticated non-admin endpoints

## Current Authentication Sources

The login flow checks the environment-configured admin account first. If that does not match, it then checks database-backed users.

## Minimum Configuration

- `JWT_SECRET_KEY`
- `JWT_ALGORITHM` default `HS256`
- `JWT_EXPIRE_MINUTES`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

## Expected Errors

- `401 Invalid credentials` on login
- `401 Missing authentication token`
- `403 Invalid or expired token`
- `403 Admin role required`

## Operational Note

`GET /health` currently requires JWT. It should not be documented or tested as an anonymous endpoint.
