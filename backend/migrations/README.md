# backend/migrations — Alembic

Adopted in issue #1028 (chunk 1/5, `alembic-baseline`). On Postgres, Alembic
is now the sole owner of schema: new tables, columns, and constraints go
through an Alembic revision from here forward, not a new hand-rolled `ALTER
TABLE` in `init_db()`. `init_db()` / `Base.metadata.create_all()` is gated to
SQLite only (all tests + local dev) — it no longer runs against Postgres at
all, because running it unconditionally raced Alembic's own DDL under
multiple concurrently-booting Fargate tasks.

That said, this does **not** mean the pre-Alembic ad-hoc `ALTER TABLE ...
ADD COLUMN IF NOT EXISTS` patches (and `_ensure_ownership_columns()`) were
removed from `init_db()` — they still run, unchanged, on every Postgres
boot. They are transitional: kept only until every column they cover has
landed as a proper Alembic revision (a follow-up cleanup, not done here),
at which point they can be deleted. See the docstrings in `archimedes/db.py`
and `migrations/env.py` for the full two-path rationale.

## Layout

- `../alembic.ini` — config; run all `alembic` commands from `backend/`
  (this file's parent) so path + `DATABASE_URL` resolution both anchor
  correctly.
- `env.py` — wires Alembic to `archimedes.db.DATABASE_URL` (same env var +
  SQLite fallback the app itself uses) and `Base.metadata` (imports every
  ORM model so autogenerate/upgrade see the full schema).
- `versions/` — actual migrations. `versions/af9c6a9376e4_baseline_schema.py`
  is baseline. Better Auth identity lands additively in `9ad1c4e2b7f0`; nullable
  canonical ownership columns land in `b7e3f1a2c9d4`. See
  [`docs/account-authentication.md`](../../docs/account-authentication.md) for
  rollout and rollback order.
- The `*.sql` files alongside this README (dated `20260518`–`20260703`) are
  the **historical hand-rolled migrations that predate Alembic** — kept for
  the record, superseded by the baseline revision. New schema changes are
  Alembic revisions from here forward, not new `.sql` files.

## Common commands

```bash
cd backend

# New dev clone / CI / a fresh test DB — build the schema from scratch:
alembic upgrade head

# Check what revision a DB is on:
alembic current

# An existing prod DB whose schema already matches the models (built via the
# retiring create_all + idempotent-ALTER pattern) — mark the baseline as
# already applied WITHOUT running its DDL, then upgrade normally after that:
alembic stamp af9c6a9376e4

# After changing an ORM model, generate the next revision:
alembic revision --autogenerate -m "add some_column to some_table"
# then read the generated file before committing — autogenerate is a first
# draft, not a guarantee (it misses some constraint/default changes and can't
# see server-side data backfills).
```

## DATABASE_URL

Same variable the app reads (`archimedes.db.DATABASE_URL`): set `DATABASE_URL`
in the environment to point Alembic at Postgres; unset, it falls back to the
same `backend/archimedes_chat.db` SQLite file the app defaults to. There is no
separate Alembic-specific DB config to keep in sync.
