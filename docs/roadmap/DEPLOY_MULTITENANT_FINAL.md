# DEPLOY MULTI-TENANT - Sentinel-Q Week 2

**Date:** April 22, 2026  
**Status:** Production-Ready  
**Tests:** 193/193 passing

---

## Pre-deploy Checks

```bash
# 1. Full suite
venv/bin/pytest tests/ -q
# Expected: 193 passed

# 2. Import without errors
PYTHONPATH=src venv/bin/python -c "from sentinel.infrastructure.migrations import run_migrations; print('OK')"

# 3. Critical migration test
venv/bin/pytest tests/test_multitenant.py::TestMigrationValidation -v
# Expected: 4/4 passed
```

---

## New / Modified Files

| File | Change |
|---|---|
| `src/sentinel/infrastructure/jwt_service.py` | `UserTokenData(sub, tenant_id, role)` + new `create_access_token` signature |
| `src/sentinel/infrastructure/auth.py` | `verify_jwt_token` returns `UserTokenData` |
| `src/sentinel/infrastructure/orm_models.py` | `TenantTable`, `UserTable`, `tenant_id` FK on `ServiceTargetTable` |
| `src/sentinel/infrastructure/migrations.py` | 6-phase idempotent migration + INFO logging |
| `src/sentinel/infrastructure/repository.py` | `tenant_id`-scoped methods, tenant/user CRUD |
| `src/sentinel/services/tenant_service.py` | tenant CRUD |
| `src/sentinel/services/user_service.py` | user CRUD + passlib bcrypt |
| `src/sentinel/main.py` | `run_migrations()` on startup, admin endpoints, `TokenData` in routes |
| `requirements.txt` | `passlib[bcrypt]==1.7.4`, `bcrypt==4.3.0` |
| `tests/test_multitenant.py` | 32 new tests |

---

## Deploy Process

```bash
# 1. Rebuild image
docker-compose build sentinel_app

# 2. Start the stack
docker-compose up -d

# 3. Verify migration logs (first 30 seconds)
docker-compose logs sentinel_app | grep "MIGRATION PHASE"
```

**Expected startup logs:**
```
MIGRATION PHASE 1: Creating tenants/users tables
MIGRATION PHASE 1: ✅ Completed
MIGRATION PHASE 2: Adding tenant_id column to services
MIGRATION PHASE 2: ✅ Completed
MIGRATION PHASE 3: Ensuring default tenant exists
MIGRATION PHASE 3: ✅ Completed
MIGRATION PHASE 4: Assigning orphan services to default tenant
MIGRATION PHASE 4: Updated 2 rows - ✅ Completed
MIGRATION PHASE 5: Replacing UNIQUE(name) with UNIQUE(tenant_id, name)
MIGRATION PHASE 5: ✅ UNIQUE(tenant_id, name) constraint ensured.
MIGRATION PHASE 6: null_services=0 ✅ COMPLETED
✅ Multi-tenant migrations completed.
```

> If phase 4 shows `Updated 0 rows` on subsequent deploys, that is correct and expected because the migration is idempotent.

---

## Post-deploy Verification

```bash
# Get token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -d "username=admin&password=sentinel_admin_2026" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Verify admin endpoints
curl -s http://localhost:8000/admin/tenants -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Create a new tenant
curl -s -X POST http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "acme_corp"}' | python3 -m json.tool

# Create a tenant user
curl -s -X POST http://localhost:8000/admin/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "secure123", "role": "viewer", "tenant_id": 2}' \
  | python3 -m json.tool

# Verify scoped targets (should be the 2 targets from the default tenant)
curl -s http://localhost:8000/targets -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## Admin Endpoints

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `POST` | `/admin/tenants` | admin | Create tenant. Body: `{"name": str}` |
| `GET` | `/admin/tenants` | admin | List all tenants |
| `POST` | `/admin/users` | admin | Create user. Body: `{"username", "password", "role", "tenant_id?"}` |
| `GET` | `/admin/users` | admin | List users for the admin tenant |

**Valid roles:** `admin`, `viewer`

---

## JWT Structure

```json
{
  "sub": "admin",
  "tenant_id": 1,
  "role": "admin",
  "exp": 1745000000
}
```

Tokens without `tenant_id` or without `role` are **rejected with 403** without exception.

---

## Idempotent Migration Behavior

| Condition | Behavior |
|---|---|
| First run (clean DB) | Creates tables, default tenant, and assigns services |
| Normal restart | All phases become no-op (`IF NOT EXISTS` / `WHERE NULL`) |
| `pg_try_advisory_lock` unavailable | Phase 5 is skipped and retried on the next startup |
| `tenant_id IS NULL` after phase 4 | `RuntimeError` - startup continues but logs a critical error |

---

## Emergency Rollback

If the migration breaks production:

```sql
-- Revert tenant_id column (last resort)
ALTER TABLE services DROP COLUMN IF EXISTS tenant_id;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS tenants;
-- Restore original constraint
ALTER TABLE services ADD CONSTRAINT services_name_key UNIQUE (name);
```

> After the SQL rollback, roll back the code to the pre-Week 2 version and rebuild Docker.

---

## Next Step - Week 3

Rate limiting and outbound webhooks.
