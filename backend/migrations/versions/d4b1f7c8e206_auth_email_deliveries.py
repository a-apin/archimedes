"""record what actually happened to every outbound auth email

Issue #1748 item 2. ``POST /api/auth/send-verification-email`` returned
``200 {status: true}`` forever — for an address SES had already dropped onto
the account suppression list, for an address whose last send threw, for every
address. The only trace a send left anywhere was a ``console.error`` on the
failure path, so nothing in the product could tell "in your spam folder" from
"SES is silently binning this", and nothing could tell the account owner.

This table is the receipt. ``auth/mailer.js`` writes one row per send (the SES
MessageId when SES accepted it, the error NAME when it did not);
``GET /api/auth/verification-status`` reads them back for the signed-in
caller's OWN address and combines them with a live SESv2
``GetSuppressedDestination`` lookup.

WHY A NEW TABLE RATHER THAN COLUMNS ON ``auth_users``. Better Auth owns every
write to ``auth_users`` (``auth/auth.js`` ``user.modelName``); adding columns
it does not know about to a table its adapter writes with generated statements
invites the library to clobber them, and a per-USER column cannot hold the
per-SEND history the rate-limit and spam-hint states are computed from. There
is nowhere else in ``auth/`` that already holds state of this shape — the four
existing tables are all Better Auth's own models — so the minimal honest answer
is one new table on the same alembic chain, following the same pattern
``9ad1c4e2b7f0`` established for the ``auth_*`` family: Node writes the rows,
alembic owns the DDL.

NO BACKFILL, and nothing to backfill: before this revision no send left a
durable trace. An empty table reads as "no send on record for this address",
which the status endpoint renders as ``unknown`` — never as ``sent``. That is
true rather than manufactured, and it is why ``sends: null`` and ``sends: 0``
are distinct in the response body.

PII. The rows carry an email address, so ``user_id`` is ON DELETE CASCADE and
account deletion takes them with it — the same erasure side of the policy
migration ``85ca5310b7a1`` records for the other account-owned tables. Nothing
else about the message is stored: no subject, no body, no verification URL (a
one-time bearer sign-in credential), and no error MESSAGE — only the AWS SDK
error's NAME, a fixed vocabulary that cannot carry an address or a token.

Revision ID: d4b1f7c8e206
Revises: c5e81a4f7b32
Create Date: 2026-09-01 23:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b1f7c8e206"
down_revision: str | Sequence[str] | None = "c5e81a4f7b32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "auth_email_deliveries",
        sa.Column("id", sa.String(length=32), primary_key=True),
        # Nullable: a send is still recorded when the owning row cannot be
        # resolved. The FK is declared INSIDE create_table, never as a
        # follow-up op.create_foreign_key — SQLite has no ALTER for
        # constraints and every `alembic upgrade head` test in this repo runs
        # against a tmp SQLite file (same reason c5e81a4f7b32 gives).
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("auth_users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # The address the message was actually sent to, normalized lowercase by
        # auth/delivery-log.js. Matched on by the read path rather than user_id
        # because changeEmail can move an account's address while old rows keep
        # the one they were sent to.
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        # "sent" | "failed". NEVER "delivered" — SES returns a MessageId for a
        # suppressed address and then drops the mail.
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        # The AWS SDK error's NAME only. See the module docstring on PII.
        sa.Column("error", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_email_deliveries_user_id", "auth_email_deliveries", ["user_id"])
    # The status endpoint's only query: newest-first rows for one address and
    # kind inside a 24h window.
    op.create_index(
        "ix_auth_email_deliveries_email_kind_created",
        "auth_email_deliveries",
        ["email", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_email_deliveries_email_kind_created", table_name="auth_email_deliveries")
    op.drop_index("ix_auth_email_deliveries_user_id", table_name="auth_email_deliveries")
    op.drop_table("auth_email_deliveries")
