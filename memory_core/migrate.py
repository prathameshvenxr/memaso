"""Schema + provisioning, run with the admin DSN (never the app role)."""
from __future__ import annotations

import hashlib
import re
import secrets
from importlib import resources

import asyncpg

SERVER_SETTINGS = {"search_path": "memaso,public"}  # our tables live in schema memaso; vector stays in public


async def migrate(admin_dsn: str, app_role: str = "memaso_app") -> None:
    """Creates schema `memaso` and everything in it. Never touches other schemas, so it can share a database
    with an application that has its own `memories`, `documents` and `usage` tables."""
    conn = await asyncpg.connect(admin_dsn)  # plain connection: the extension and schema are created first
    try:
        await conn.execute("SELECT pg_advisory_lock(727274)")  # replicas starting together migrate one at a time
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
        await conn.execute("CREATE SCHEMA IF NOT EXISTS memaso")
        await conn.execute("SET search_path TO memaso, public")
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY)")
        for f in sorted(resources.files("memory_core.migrations").iterdir(), key=lambda p: p.name):
            if not f.name.endswith(".sql"):
                continue
            if await conn.fetchval("SELECT 1 FROM schema_migrations WHERE version=$1", f.name):
                continue
            async with conn.transaction():
                await conn.execute(f.read_text())
                await conn.execute("INSERT INTO schema_migrations VALUES ($1)", f.name)
        await conn.execute(grants_sql(app_role))  # every run: a role created later, or renamed, still gets access
    finally:
        await conn.close()


def grants_sql(role: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", role):
        raise ValueError("app role must be a plain identifier")
    return f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
  GRANT USAGE ON SCHEMA memaso TO {role};
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA memaso TO {role};
  REVOKE INSERT, UPDATE, DELETE ON memaso.tenants, memaso.api_keys FROM {role};
  REVOKE ALL ON memaso.schema_migrations FROM {role};
  GRANT USAGE ON ALL SEQUENCES IN SCHEMA memaso TO {role};
END IF; END $$;"""


async def reset(admin_dsn: str) -> None:
    """Dev/test only: drop schema memaso (only that schema) and migrate again."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS memaso CASCADE")
    finally:
        await conn.close()
    await migrate(admin_dsn)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def create_tenant(admin_dsn: str, tenant: str, monthly_budget: int | None = None) -> None:
    conn = await asyncpg.connect(admin_dsn, server_settings=SERVER_SETTINGS)
    try:
        await conn.execute(
            "INSERT INTO tenants(id, monthly_budget) VALUES ($1,$2) "
            "ON CONFLICT (id) DO UPDATE SET monthly_budget = COALESCE(EXCLUDED.monthly_budget, tenants.monthly_budget)",
            tenant, monthly_budget)
    finally:
        await conn.close()


async def create_api_key(admin_dsn: str, tenant: str, subject: str | None = None) -> str:
    """Returns the raw key once; only its hash is stored."""
    raw = "mc_" + secrets.token_urlsafe(32)
    conn = await asyncpg.connect(admin_dsn, server_settings=SERVER_SETTINGS)
    try:
        await conn.execute("INSERT INTO api_keys(key_hash, tenant, subject) VALUES ($1,$2,$3)",
                           hash_key(raw), tenant, subject)
    finally:
        await conn.close()
    return raw
