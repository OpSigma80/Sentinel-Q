"""
Startup database migration for Sentinel-Q — multi-tenant support.

Design:
  - Idempotent: safe to run on every startup (uses IF NOT EXISTS / IF EXISTS)
  - No Alembic: raw PostgreSQL via SQLAlchemy engine.connect()
  - Advisory lock: prevents concurrent DDL if two instances start simultaneously
  - Validates data integrity post-migration (0 rows with tenant_id IS NULL)

Phases:
  1. Create tenants and users tables
  2. Add tenant_id column to services (nullable, safe for existing rows)
  3. Create default tenant (id=1)
  4. Migrate existing services to default tenant
  5. Replace UNIQUE(name) with UNIQUE(tenant_id, name)  [advisory-locked]
  6. Post-migration assertions
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine
from loguru import logger


# Arbitrary advisory lock key — must be consistent across all instances
_ADVISORY_LOCK_KEY = 987654321


def run_migrations(engine: Engine) -> None:
    """
    Execute all migration phases in order.
    Rolls back and re-raises on any failure so the caller can retry.
    """
    with engine.connect() as conn:
        _phase1_create_tables(conn)
        _phase2_add_tenant_column(conn)
        _phase3_ensure_default_tenant(conn)
        _phase4_migrate_orphan_services(conn)
        _phase5_replace_unique_constraint(conn)
        _phase6_validate(conn)
        _phase7_create_webhook_subscriptions(conn)
    logger.success("✅ Migraciones multi-tenant completadas.")


# ---------------------------------------------------------------------------
# Phase 1 — Create tenants and users tables
# ---------------------------------------------------------------------------

def _phase1_create_tables(conn) -> None:
    logger.info("MIGRATION FASE 1: Creating tenants/users tables")
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS tenants (
            id         SERIAL PRIMARY KEY,
            name       VARCHAR NOT NULL UNIQUE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id              SERIAL PRIMARY KEY,
            tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            username        VARCHAR NOT NULL,
            hashed_password VARCHAR NOT NULL,
            role            VARCHAR NOT NULL DEFAULT 'viewer',
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_users_tenant_username UNIQUE (tenant_id, username)
        )
    """))
    conn.commit()
    logger.info("MIGRATION FASE 1: ✅ Completed")


# ---------------------------------------------------------------------------
# Phase 2 — Add nullable tenant_id column to services
# ---------------------------------------------------------------------------

def _phase2_add_tenant_column(conn) -> None:
    logger.info("MIGRATION FASE 2: Adding tenant_id column to services")
    conn.execute(text("""
        ALTER TABLE services
        ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL
    """))
    conn.commit()
    logger.info("MIGRATION FASE 2: ✅ Completed")


# ---------------------------------------------------------------------------
# Phase 3 — Ensure default tenant exists with id=1
# ---------------------------------------------------------------------------

def _phase3_ensure_default_tenant(conn) -> None:
    logger.info("MIGRATION FASE 3: Ensuring default tenant exists")
    conn.execute(text("""
        INSERT INTO tenants (name)
        SELECT 'default'
        WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE name = 'default')
    """))
    conn.commit()
    logger.info("MIGRATION FASE 3: ✅ Completed")


# ---------------------------------------------------------------------------
# Phase 4 — Assign orphan services to default tenant
# ---------------------------------------------------------------------------

def _phase4_migrate_orphan_services(conn) -> None:
    logger.info("MIGRATION FASE 4: Assigning orphan services to default tenant")
    result = conn.execute(text("""
        UPDATE services
        SET tenant_id = (SELECT id FROM tenants WHERE name = 'default')
        WHERE tenant_id IS NULL
    """))
    conn.commit()
    rows_updated = result.rowcount
    logger.info(f"MIGRATION FASE 4: Updated {rows_updated} rows — ✅ Completed")


# ---------------------------------------------------------------------------
# Phase 5 — Replace UNIQUE(name) with UNIQUE(tenant_id, name)
#           Uses advisory lock to prevent concurrent DDL
# ---------------------------------------------------------------------------

def _phase5_replace_unique_constraint(conn) -> None:
    logger.info("MIGRATION FASE 5: Replacing UNIQUE(name) with UNIQUE(tenant_id, name)")
    # Try to acquire advisory lock (non-blocking)
    row = conn.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
    ).scalar()

    if not row:
        logger.info(
            "MIGRATION FASE 5: advisory lock unavailable — skipping DDL this startup. "
            "Constraint will be updated on next startup."
        )
        return

    try:
        # Drop legacy global unique constraint on name (name varies by PG version / creation method)
        conn.execute(text("""
            DO $$
            DECLARE
                _cname TEXT;
            BEGIN
                SELECT conname INTO _cname
                FROM pg_constraint
                WHERE conrelid = 'services'::regclass
                  AND contype = 'u'
                  AND conname != 'uq_services_tenant_name'
                  AND array_length(conkey, 1) = 1
                  AND conkey[1] = (
                      SELECT attnum FROM pg_attribute
                      WHERE attrelid = 'services'::regclass AND attname = 'name'
                  );
                IF _cname IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE services DROP CONSTRAINT %I', _cname);
                    RAISE NOTICE 'Dropped legacy constraint: %', _cname;
                END IF;
            END $$
        """))

        # Add composite unique constraint (idempotent — error if already exists is caught)
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'services'::regclass
                      AND conname = 'uq_services_tenant_name'
                ) THEN
                    ALTER TABLE services
                    ADD CONSTRAINT uq_services_tenant_name UNIQUE (tenant_id, name);
                END IF;
            END $$
        """))
        conn.commit()
        logger.info("MIGRATION FASE 5: ✅ UNIQUE(tenant_id, name) constraint ensured.")
    finally:
        conn.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
        )


# ---------------------------------------------------------------------------
# Phase 6 — Post-migration assertions
# ---------------------------------------------------------------------------

def _phase6_validate(conn) -> None:
    # Assert no orphan services remain
    orphan_count: int = conn.execute(
        text("SELECT COUNT(*) FROM services WHERE tenant_id IS NULL")
    ).scalar() or 0

    if orphan_count > 0:
        raise RuntimeError(
            f"Migration validation failed: {orphan_count} service row(s) still have tenant_id IS NULL. "
            "Manual intervention required."
        )

    # Verify composite constraint exists
    constraint_exists: bool = conn.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'services'::regclass
              AND conname = 'uq_services_tenant_name'
        )
    """)).scalar()

    if not constraint_exists:
        logger.warning(
            "Migration validation: uq_services_tenant_name not yet present "
            "(advisory lock was skipped). Will retry on next startup."
        )

    logger.info(f"MIGRATION FASE 6: null_services={orphan_count} ✅ COMPLETED")


# ---------------------------------------------------------------------------
# Phase 7 — Create webhook_subscriptions table (idempotent)
# ---------------------------------------------------------------------------

def _phase7_create_webhook_subscriptions(conn) -> None:
    logger.info("MIGRATION FASE 7: Creating webhook_subscriptions table")
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS webhook_subscriptions (
            id         SERIAL PRIMARY KEY,
            tenant_id  INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            url        VARCHAR NOT NULL,
            secret     VARCHAR(128),
            events     VARCHAR NOT NULL DEFAULT 'down,up',
            is_active  BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    conn.commit()
    logger.info("MIGRATION FASE 7: ✅ Completed")
