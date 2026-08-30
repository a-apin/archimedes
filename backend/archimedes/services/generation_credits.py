"""Session-owning wrapper around the generation credit ledger (#1441).

``models/generation_credit.py`` is pure persistence and takes a ``Session``.
This module owns the sessions and decides, for each step, whether a failure is
allowed to reach the caller. That split is the whole point of the file, because
the answer changes at exactly one moment: **when the money moves.**

- **Before the settle, failures are loud.** Claiming the idempotency key is
  what makes a double-charge impossible. If the claim cannot be written, the
  request must not proceed to take money — running the paywall anyway would
  buy a charge with no protection behind it.

- **After the settle, failures are quiet.** The payer's funds are already gone.
  Raising here would hand them a 500 *and* keep their money, which is strictly
  worse than the ledger inconsistency it would be reporting. These paths log at
  ``error`` — they are real defects, not noise — but never surface.

The asymmetry is deliberate and is the fail-soft principle applied honestly:
fail-soft is wrong for anything a claim depends on, and right when the
alternative harms the person the claim was meant to protect.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from archimedes.models.generation_credit import (
    claim_credit,
    consume_credit,
    list_credits as _list_credit_rows,
    mark_credit_settled,
    restore_credit_for_job,
    take_available_credit,
    void_credit,
)

logger = logging.getLogger(__name__)

#: USDC base units per dollar. Mirrors ``generate_routes._RECEIPT_USDC_DECIMALS``
#: so a credit and the receipt written for the SAME payment render the same
#: price string. ``test_credit_and_receipt_agree_on_the_price`` pins that.
_USDC_DECIMALS = 6


def _format_usd(amount_base_units: int) -> str:
    return f"${Decimal(amount_base_units) / Decimal(10**_USDC_DECIMALS):.2f}"


def _session():
    from archimedes.db import get_session

    return get_session()


def take_credit(user_id: str) -> int | None:
    """The id of an unspent credit for *user_id*, or None.

    Quiet on failure: a ledger read that errors must not block a generation the
    caller is about to pay for anyway. The cost of missing a credit here is
    that the payer is charged again and ends up holding two — recoverable, and
    strictly better than refusing them service over a read error.
    """
    try:
        with _session() as session:
            credit = take_available_credit(session, user_id)
            if credit is None:
                return None
            credit_id = credit.id
            session.commit()
            return credit_id
    except Exception:
        logger.exception("generation credit lookup failed for user %s — treating as no credit", user_id)
        return None


def claim(user_id: str, idempotency_key: str | None) -> tuple[str, int | None]:
    """Claim the right to charge. Raises on failure — see the module docstring.

    Returns ``(outcome, credit_id)``; outcomes are those of
    ``models.generation_credit.claim_credit``.
    """
    with _session() as session:
        outcome, credit = claim_credit(session, user_id=user_id, idempotency_key=idempotency_key)
        credit_id, job_id = credit.id, credit.job_id
        session.commit()
    if outcome != "claimed":
        logger.info(
            "generation credit claim for user %s returned %s (credit=%s job=%s)",
            user_id,
            outcome,
            credit_id,
            job_id,
        )
    return outcome, credit_id


def settle(credit_id: int, payment) -> None:
    """Record that the money moved. Quiet on failure — the funds are gone."""
    try:
        with _session() as session:
            amount_base_units = int(payment.amount)
            mark_credit_settled(
                session,
                credit_id,
                payer_wallet=str(payment.payer),
                amount_base_units=amount_base_units,
                price_usd=_format_usd(amount_base_units),
                network=str(payment.network),
                settlement_ref=payment.transaction,
            )
            session.commit()
    except Exception:
        logger.error(
            "generation credit %s could not be marked settled — the payment CLEARED but is "
            "not recorded as owed. Manual reconciliation needed.",
            credit_id,
            exc_info=True,
        )


def void(credit_id: int) -> None:
    """Release a claim that never became a charge. Quiet: no value moved.

    Loud enough to matter, though — a claim left ``pending`` poisons that
    idempotency key for every later retry.
    """
    try:
        with _session() as session:
            void_credit(session, credit_id)
            session.commit()
    except Exception:
        logger.error(
            "generation credit claim %s could not be voided — that idempotency key will read "
            "as in-flight on retry until it is cleared.",
            credit_id,
            exc_info=True,
        )


def consume(credit_id: int, *, job_id: str) -> None:
    """Spend the credit on *job_id*. Quiet on failure.

    A credit that fails to be marked consumed stays ``available``, so the payer
    could spend it a second time. That errs toward the payer, which is the
    correct direction for an accounting bug on a paywall.
    """
    try:
        with _session() as session:
            consume_credit(session, credit_id, job_id=job_id)
            session.commit()
    except Exception:
        logger.error(
            "generation credit %s could not be marked consumed for job %s — it stays spendable.",
            credit_id,
            job_id,
            exc_info=True,
        )


def list_credits(user_id: str) -> list[dict]:
    """Newest-first ledger rows for *user_id* — the calling user's own
    credits, surfaced by ``GET /api/generate/credits`` (v8 Lane 1.3a).

    Quiet on failure like ``take_credit``: a broken read here must not break
    the page that displays it, it just renders no credits — the ledger
    itself (and what a real submit does with it) is unaffected either way.
    """
    try:
        with _session() as session:
            return _list_credit_rows(session, user_id)
    except Exception:
        logger.exception("generation credit list failed for user %s — returning empty", user_id)
        return []


def restore_for_job(job_id: str) -> bool:
    """Give a credit back because *job_id* did not deliver. Quiet on failure."""
    try:
        with _session() as session:
            restored = restore_credit_for_job(session, job_id)
            session.commit()
            return restored is not None
    except Exception:
        logger.error(
            "generation credit for job %s could not be restored — a payer may be owed a "
            "generation with nothing recording it.",
            job_id,
            exc_info=True,
        )
        return False
