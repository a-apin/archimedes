"""account deletion cascade-vs-anonymize policy (issue #1367, D3)

`b7e3f1a2c9d4` gave five tables a nullable `owner_user_id` FK'd to
`auth_users` with `ondelete="SET NULL"`, uniformly, without asking whether
"detach and keep the row" is the right answer for each one. A sixth
ownership column (`paper_deployments.owner_user_id`) shipped with no FK at
all — a column that *looked* like ownership but enforced nothing. This
revision makes an explicit, per-table call for all six and enforces it at
the database level, so "delete my account" has one real effect regardless
of which of the two services (the Node Better Auth process or the Python
backend) issues the `DELETE FROM auth_users` — Postgres' own FK actions do
the work, not application code that only one of the two services would run.

The decision, table by table:

  * ``user_profiles``            -> CASCADE (changed from SET NULL)
  * ``paper_deployments``        -> CASCADE (new FK; had none before)
  * ``strategy_store``           -> SET NULL (unchanged, decision recorded)
  * ``strategy_passports``       -> SET NULL (unchanged, decision recorded)
  * ``strategy_proposals``       -> SET NULL (unchanged, decision recorded)
  * ``vault_metadata``           -> SET NULL (unchanged, decision recorded)

Why the split, not "cascade everything" or "anonymize everything":

  - ``user_profiles`` is 1:1 with the account and holds the
    Fernet-encrypted email (see `archimedes/models/user_profile.py`'s
    module docstring, Issue #181). SET NULL detaches ownership but leaves
    that encrypted address sitting in the database forever — the opposite
    of what "delete my account" is supposed to mean for the one row that
    exists purely to carry PII. Nothing else joins against it. CASCADE.

  - ``paper_deployments`` is a private per-user paper-trading ledger
    (`archimedes/api/paper_routes.py`'s `_owned_deployment` and the
    `owner_user_id == user.id` filter are the only reads in the tree — no
    leaderboard, no marketplace, no other account's view depends on it).
    Its child table, `paper_daily_returns`, already cascades off
    `paper_deployments.id` (see `archimedes/models/paper_store.py`), so a
    CASCADE here removes the whole ledger cleanly in one direction with no
    separate orphan-child concern. CASCADE.

  - ``strategy_store`` / ``strategy_passports`` / ``strategy_proposals``
    are the opposite shape: a strategy can be published to the
    marketplace, appear on the public leaderboard, and be deployed into
    OTHER users' vaults or paper-trading runs by `strategy_id` (a string
    reference, not itself FK-enforced) — deleting the row out from under
    those references would silently break other accounts' data, not just
    the deleting user's. A strategy passport is additionally a rigor-gate
    provenance/audit artifact; audit trails should outlive the account
    that triggered them. SET NULL: the content survives, ownership is
    detached (same "deleted user" shape used for library posts elsewhere).

  - ``vault_metadata`` rows describe a vault that exists on-chain
    independent of the Better Auth account — `owner_wallet` (non-nullable)
    already anchors it, and the vault can have participants/history beyond
    the one Better Auth account. Destroying the row because one linked
    account was deleted would destroy a record that isn't solely that
    account's to remove. SET NULL.

The second-wallet gap the FK alone cannot close
-----------------------------------------------

``user_profiles.owner_user_id`` is UNIQUE, and the claim path
(`api/wallet_routes.py`'s ``claim_legacy_wallet_data``) only stamps a
profile onto an account that has none yet — ``upsert_profile`` returns 409
"Account already has a canonical profile" for the second. That invariant is
deliberate and stays. Its consequence is not: an account that links a
SECOND wallet whose ``user_profiles`` row predates the link keeps that row
at ``owner_user_id IS NULL`` forever, so the CASCADE above never reaches
it. The Fernet-encrypted email on the account's own second wallet would
survive "delete my account" — the exact outcome the CASCADE decision exists
to prevent, one wallet over. ``paper_deployments`` has the same shape:
``claim_legacy_wallet_data`` does not list it at all, so a pre-link
deployment carrying only ``owner_wallet`` is likewise never reached.

Both CASCADE tables therefore get a second reach — a ``BEFORE DELETE`` row
trigger on ``auth_users`` that removes rows still unclaimed
(``owner_user_id IS NULL``) whose wallet is one the deleting account has
PROVEN control of, i.e. present in its ``linked_wallets``. A trigger, not
application code, for the same reason the FK actions are the mechanism at
all: either service (Node Better Auth or the Python backend) can issue the
``DELETE FROM auth_users``, and only the database sees both. It fires
BEFORE the row goes, so ``linked_wallets`` (itself ``ON DELETE CASCADE``
off ``auth_users``) is still readable when the trigger body runs.

Scope note, deliberately not widened: the SET NULL tables need no
equivalent, because an unclaimed row is ALREADY in the SET NULL end state
(``owner_user_id IS NULL``) — there is nothing left for a delete to do.

Path note: this trigger is DDL that Alembic owns. ``Base.metadata.create_all()``
— the fresh-DB path for local dev and hermetic tests, see
``migrations/env.py``'s two-path docstring — does not carry triggers, so it
does not reproduce this behaviour. That is precisely why
``tests/test_account_deletion_cascade.py`` runs a real ``alembic upgrade
head`` into its throwaway database rather than ``create_all()``: against the
ORM path this policy is invisible, and a guard that cannot see the thing it
guards proves nothing.

Revision ID: 85ca5310b7a1
Revises: a3f19c7d2e84
Create Date: 2026-08-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "85ca5310b7a1"
down_revision: str | Sequence[str] | None = "a3f19c7d2e84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic reads these module globals reflectively.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

TRIGGER_NAME = "trg_auth_users_purge_unclaimed_owned_rows"
_PG_FUNCTION_NAME = "archimedes_purge_unclaimed_owned_rows"

# The trigger body, identical on both dialects (`OLD.` is spelled the same in
# a SQLite trigger and in plpgsql). See the module docstring for WHY these two
# tables and not the SET NULL four.
_PURGE_BODY = """
    DELETE FROM user_profiles
     WHERE owner_user_id IS NULL
       AND wallet_address IN (SELECT address FROM linked_wallets WHERE user_id = OLD.id);
    DELETE FROM paper_deployments
     WHERE owner_user_id IS NULL
       AND owner_wallet IN (SELECT address FROM linked_wallets WHERE user_id = OLD.id);
"""


def _create_purge_trigger(dialect: str) -> None:
    if dialect == "postgresql":
        # plpgsql needs its own BEGIN/END block and an explicit RETURN OLD —
        # a BEFORE DELETE trigger returning NULL would CANCEL the delete.
        op.execute(
            f"CREATE OR REPLACE FUNCTION {_PG_FUNCTION_NAME}() RETURNS trigger AS $$\n"
            f"BEGIN{_PURGE_BODY}    RETURN OLD;\n"
            "END;\n"
            "$$ LANGUAGE plpgsql"
        )
        op.execute(
            f"CREATE TRIGGER {TRIGGER_NAME} BEFORE DELETE ON auth_users "
            f"FOR EACH ROW EXECUTE FUNCTION {_PG_FUNCTION_NAME}()"
        )
    else:
        # SQLite: no function objects — the body lives inline in the trigger,
        # and a trigger body is one statement, so no RETURN is involved.
        op.execute(f"CREATE TRIGGER {TRIGGER_NAME} BEFORE DELETE ON auth_users FOR EACH ROW BEGIN{_PURGE_BODY}END")


def _drop_purge_trigger(dialect: str) -> None:
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON auth_users")
        op.execute(f"DROP FUNCTION IF EXISTS {_PG_FUNCTION_NAME}()")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")


def upgrade() -> None:
    # user_profiles: SET NULL -> CASCADE. Drop and recreate the FK under the
    # same constraint name so no application code or other migration needs
    # to know the name changed.
    with op.batch_alter_table("user_profiles") as batch_op:
        batch_op.drop_constraint("fk_user_profiles_owner_user_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_user_profiles_owner_user_id",
            "auth_users",
            ["owner_user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # paper_deployments.owner_user_id existed as a plain, unenforced column
    # (see the issue evidence: "a sixth ownership column has no foreign key
    # at all"). Give it one, CASCADE, plus the index every other
    # owner_user_id column already has.
    with op.batch_alter_table("paper_deployments") as batch_op:
        batch_op.create_foreign_key(
            "fk_paper_deployments_owner_user_id",
            "auth_users",
            ["owner_user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_paper_deployments_owner_user_id", ["owner_user_id"])

    # strategy_store, strategy_passports, strategy_proposals, vault_metadata:
    # decision is SET NULL, i.e. no schema change — recorded above so the
    # choice is auditable in one place rather than implicit by omission.

    # Created LAST, after both batch_alter_table blocks. On SQLite a batch
    # ALTER is a rename-copy-drop table rebuild, and a trigger whose body
    # names a table being rebuilt is exactly the thing that rename rewrites;
    # creating the trigger only once the rebuilds are done keeps it pointing
    # at the real tables. downgrade() drops it FIRST, symmetrically.
    _create_purge_trigger(op.get_bind().dialect.name)


def downgrade() -> None:
    _drop_purge_trigger(op.get_bind().dialect.name)

    with op.batch_alter_table("paper_deployments") as batch_op:
        batch_op.drop_index("ix_paper_deployments_owner_user_id")
        batch_op.drop_constraint("fk_paper_deployments_owner_user_id", type_="foreignkey")

    with op.batch_alter_table("user_profiles") as batch_op:
        batch_op.drop_constraint("fk_user_profiles_owner_user_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_user_profiles_owner_user_id",
            "auth_users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
