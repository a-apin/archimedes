"""Reasoning-disclosure gates: publishing shares the RESULT, not the DERIVATION (#1557).

``is_strategy_visible`` returns True on ``is_example OR is_published``, i.e.
"readable by ANYONE, including anonymous". That is the right rule for a
strategy's CARD and the wrong rule for its REASONING — and five consumers used
it for reasoning. The moment anything flips ``is_published=True`` on a user's
row, an anonymous ``GET /api/strategies/{id}/debate`` returned that user's full
bull/bear generation transcript. The route's own docstring claimed "404 unless
the caller owns the row"; it was false.

This file is the guard for the fix. Three things it is built to do:

**1. Fail against the unfixed predicate.** Every route assertion here is made
on a row that is ``is_published=True`` and owned by someone else — the exact
input ``is_strategy_visible`` returns True for. Swap
``is_strategy_reasoning_visible`` back to ``is_strategy_visible`` at any of the
gated call sites and these tests go red. (Demonstrated by doing it, not
assumed: see the PR body.)

**2. Not pass for the wrong reason.** A 404 is a weak assertion — the row could
be missing, the transcript never persisted, the fixture broken. So every leak
test carries a POSITIVE CONTROL in the same test body: the OWNER gets 200 and
the sentinel payload back, proving the data really is there and reachable, so
the anonymous/stranger 404 is the gate rather than absent data. Leak
assertions are made on the raw response TEXT against a distinctive sentinel,
not only on a status code, so a refactor that moves the transcript into some
other key still trips them.

**3. Prove the public surface survives.** The same published row's CARD must
still 200 for an anonymous caller, and ``is_example`` house content must still
serve its reasoning publicly — otherwise the fix is a regression on the
leaderboard/library/detail pages rather than a privacy gate.

Hermetic: tmp-sqlite rebind (the ``_use_tmp_db`` pattern from
test_strategy_ownership.py / test_selection_bias_generated_gate.py), signed
SIWE fixture cookies mapped onto canonical Better Auth users by conftest's
autouse ``_legacy_siwe_test_adapter``. No network, no Redis, no .env.
"""

from __future__ import annotations

import json
import time

import archimedes.db as db
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from httpx import ASGITransport, AsyncClient

_W_OWNER = "0xAbC0000000000000000000000000000000000F01"  # mixed case on purpose
_W_STRANGER = "0x0000000000000000000000000000000000000F02"

# Distinctive strings/values that must never appear in a non-owner's response.
# Chosen to survive `sanitize_transcript` (no T<n>.<n> / "cutover" / "Phase-"
# jargon patterns), so a missing sentinel means the GATE fired, never that the
# sanitizer ate it.
_BULL_SENTINEL = "SENTINEL-BULL-cross-sectional-momentum-persists-in-small-caps"
_BEAR_SENTINEL = "SENTINEL-BEAR-the-factor-is-crowded-and-decays-post-publication"
_SPEC_SENTINEL = "SENTINEL-SPEC-name-that-must-not-escape"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite (rebinds db.engine + db.SessionLocal).

    db.engine/SessionLocal are created once at import, so setenv alone doesn't
    re-point them; rebind both to a per-test engine, then init_db() registers
    every table (incl. the passport/backtest/debate side-effect imports).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'reasoning_gates.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _legacy_user_id(wallet: str) -> str:
    """The canonical user id conftest's ``_legacy_siwe_test_adapter`` mints for
    a SIWE fixture cookie. Derived, not hard-coded, so it cannot drift from the
    adapter (which builds ``legacy-test:{wallet}`` from a payload lower-cased
    at signing time)."""
    return f"legacy-test:{wallet.lower()}"


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """A real signed SIWE session cookie, not header spoofing."""
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _mk_strategy(
    sid: str,
    *,
    owner_user: str | None = None,
    owner_wallet: str | None = None,
    published: bool = False,
    example: bool = False,
    spec: dict | None = None,
):
    from archimedes.models.strategy_store import StrategyRecord

    with db.get_session() as session:
        session.add(
            StrategyRecord(
                id=sid,
                content_hash=("0x" + sid).ljust(66, "0"),
                generation_method="debate",
                source_papers="[]",
                strategy_name="Reasoning Gate Probe",
                thesis="test thesis",
                asset_universe='["SPY"]',
                risk_profile="moderate",
                status="candidate",
                is_example=example,
                is_published=published,
                owner_user_id=owner_user,
                owner_wallet=owner_wallet.lower() if owner_wallet else None,
                strategy_spec=json.dumps(spec) if spec else None,
            )
        )
        session.commit()


def _mk_passport(sid: str):
    """The strategy_passports mirror the CARD route reads (``get_passport``)."""
    from archimedes.models.strategy_passport_record import PassportPaperRef, StrategyPassportRecord

    with db.get_session() as session:
        record = StrategyPassportRecord(
            id=sid,
            content_hash=("0y" + sid).ljust(66, "0"),
            generation_method="debate",
            methodology_summary="Public methodology writeup",
            asset_universe='["SPY"]',
        )
        record.paper_refs = [PassportPaperRef(passport_id=sid, arxiv_id="2401.00001", title="Paper")]
        session.add(record)
        session.commit()


def _mk_debate_transcript(sid: str):
    from archimedes.models.debate_transcript import record_debate_transcript

    with db.get_session() as session:
        record_debate_transcript(
            session,
            strategy_id=sid,
            generation_id="job-reasoning-gate",
            candidate_id="cand_probe",
            transcript=[
                {"role": "bull", "round": 1, "verdict": "act", "claims": [_BULL_SENTINEL]},
                {"role": "bear", "round": 1, "verdict": "decline", "claims": [_BEAR_SENTINEL]},
            ],
        )
        session.commit()


# A short, deliberately odd series: each value is distinctive enough that its
# string form appearing in a response body is proof the series leaked.
_RETURNS = [0.0137911, -0.0091733, 0.0245177, -0.0033099, 0.0188421]


def _mk_backtest(sid: str, returns: list[float] | None = None):
    from archimedes.models.backtest_store import BacktestResultRecord

    series = _RETURNS if returns is None else returns
    with db.get_session() as session:
        session.add(
            BacktestResultRecord(
                strategy_id=sid,
                content_hash=f"bt_{sid}",
                artifact_json=json.dumps({"results": [{"metrics": {"daily_returns": series}}]}),
                source_pipeline="test",
            )
        )
        session.commit()


# ══════════════════════════════════════════════════════════════════════════
# 1. The predicate itself — the divergence #1557 introduces
# ══════════════════════════════════════════════════════════════════════════


def test_the_two_predicates_diverge_on_a_published_row():
    """The whole bug in one assertion.

    A published row owned by someone else is CARD-visible to an anonymous
    caller and must NOT be REASONING-visible. If this ever reports the same
    answer for both, the split has collapsed and every route gate below is
    decorative.
    """
    from archimedes.services.strategy_visibility import is_strategy_reasoning_visible, is_strategy_visible

    published = {
        "is_example": False,
        "is_published": True,
        "owner_user_id": "user_someone_else",
        "owner_wallet": None,
    }

    assert is_strategy_visible(published, None, caller_user_id=None) is True
    assert is_strategy_reasoning_visible(published, None, caller_user_id=None) is False
    # …and for a DIFFERENT signed-in user, not only anonymous.
    assert is_strategy_visible(published, None, caller_user_id="user_stranger") is True
    assert is_strategy_reasoning_visible(published, None, caller_user_id="user_stranger") is False


def test_reasoning_predicate_grants_example_rows_to_everyone():
    """House-curated demo content: reasoning IS the demo. Anonymous callers
    already get exactly this on /quant for every curated library row."""
    from archimedes.services.strategy_visibility import is_strategy_reasoning_visible

    example = {"is_example": True, "is_published": False, "owner_user_id": None, "owner_wallet": None}
    assert is_strategy_reasoning_visible(example, None, caller_user_id=None) is True


def test_reasoning_predicate_grants_owners_on_both_ownership_tiers():
    from archimedes.services.strategy_visibility import is_strategy_reasoning_visible

    canonical = {"is_example": False, "is_published": True, "owner_user_id": "user_a", "owner_wallet": "0xabc"}
    assert is_strategy_reasoning_visible(canonical, None, caller_user_id="user_a") is True
    # Canonical ownership is exclusive: a matching WALLET must not grant a row
    # that carries an owner_user_id (else canonical identity is bypassable).
    assert is_strategy_reasoning_visible(canonical, "0xabc", caller_user_id=None) is False

    legacy = {"is_example": False, "is_published": True, "owner_user_id": None, "owner_wallet": "0xABC"}
    assert is_strategy_reasoning_visible(legacy, "  0xabc ", caller_user_id=None) is True
    assert is_strategy_reasoning_visible(legacy, "0xdef", caller_user_id=None) is False
    assert is_strategy_reasoning_visible(legacy, None, caller_user_id=None) is False


def test_reasoning_predicate_denies_none_row():
    from archimedes.services.strategy_visibility import is_strategy_reasoning_visible

    assert is_strategy_reasoning_visible(None, "0xabc", caller_user_id="user_a") is False


# ══════════════════════════════════════════════════════════════════════════
# 2. GET /{id}/debate — the transcript leak named in #1557
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("caller", [None, _W_STRANGER], ids=["anonymous", "different-user"])
async def test_published_debate_transcript_never_reaches_a_non_owner(caller):
    """THE #1557 ACCEPTANCE TEST.

    The row is PUBLISHED — the exact state that made ``is_strategy_visible``
    return True for everybody. An anonymous visitor and a signed-in stranger
    must both get 404 and must never see a word of the transcript.
    """
    sid = "rsn00000000000001"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=True)
    _mk_passport(sid)
    _mk_debate_transcript(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/strategies/{sid}/debate",
            cookies=_siwe_cookies(caller) if caller else None,
        )

    assert resp.status_code == 404, "a published row's debate transcript must 404 for a non-owner"
    # Asserted on the raw text, not just the field: a refactor that free-forms
    # the transcript into another key still trips this.
    assert _BULL_SENTINEL not in resp.text
    assert _BEAR_SENTINEL not in resp.text


async def test_published_debate_transcript_still_reaches_its_owner():
    """POSITIVE CONTROL for the test above — and the reason its 404s mean
    something. Same fixture, same published row: the owner gets 200 and both
    sentinels back, so the transcript demonstrably exists and is reachable.
    Without this, the 404s could be a missing row or a broken fixture."""
    sid = "rsn00000000000002"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=True)
    _mk_passport(sid)
    _mk_debate_transcript(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}/debate", cookies=_siwe_cookies(_W_OWNER))

    assert resp.status_code == 200
    assert resp.json()["strategy_id"] == sid
    assert _BULL_SENTINEL in resp.text
    assert _BEAR_SENTINEL in resp.text


async def test_example_row_debate_transcript_stays_public():
    """House content is not user reasoning: an ``is_example`` row's transcript
    stays anonymously readable, matching the ``is_curated`` short-circuit the
    provider-backed path has always had."""
    sid = "rsn00000000000003"
    _mk_strategy(sid, example=True)
    _mk_debate_transcript(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}/debate")

    assert resp.status_code == 200
    assert _BULL_SENTINEL in resp.text


# ══════════════════════════════════════════════════════════════════════════
# 3. GET /{id}/returns — the full daily-return series
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("caller", [None, _W_STRANGER], ids=["anonymous", "different-user"])
async def test_published_returns_series_never_reaches_a_non_owner(caller):
    """The per-day series is the raw backtest output — enough to reconstruct
    positions and clone the strategy. Headline stats stay on the public card;
    the series does not."""
    sid = "rsn00000000000004"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=True)
    _mk_passport(sid)
    _mk_backtest(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/strategies/{sid}/returns",
            cookies=_siwe_cookies(caller) if caller else None,
        )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Strategy not found", (
        "must be the existence-hiding 404, not 'no persisted returns' — the "
        "latter would confirm the strategy exists to a non-owner"
    )
    for value in _RETURNS:
        assert str(value) not in resp.text


async def test_published_returns_series_still_reaches_its_owner():
    """POSITIVE CONTROL: the series exists and the owner gets all of it."""
    sid = "rsn00000000000005"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=True)
    _mk_passport(sid)
    _mk_backtest(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}/returns", cookies=_siwe_cookies(_W_OWNER))

    assert resp.status_code == 200
    body = resp.json()
    assert body["n"] == len(_RETURNS)
    assert body["daily_returns"] == pytest.approx(_RETURNS, rel=1e-9)


async def test_example_row_returns_series_stays_public():
    """/quant fetches exactly this, anonymously, for every curated library row.
    Gating ``is_example`` here would break a live public page."""
    sid = "rsn00000000000006"
    _mk_strategy(sid, example=True)
    _mk_backtest(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}/returns")

    assert resp.status_code == 200
    assert resp.json()["daily_returns"] == pytest.approx(_RETURNS, rel=1e-9)


# ══════════════════════════════════════════════════════════════════════════
# 4. The CARD must survive — this is a reasoning gate, not a takedown
# ══════════════════════════════════════════════════════════════════════════


async def test_published_card_stays_public_for_anonymous_callers():
    """The anti-regression half of the matrix, and the anchor that makes every
    404 above meaningful: the SAME published row whose reasoning is now
    owner-only still serves its card — name, papers, methodology, metrics — to
    an anonymous visitor. If this ever 404s, the fix has broken the
    leaderboard/library/detail pages instead of protecting reasoning."""
    sid = "rsn00000000000007"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=True)
    _mk_passport(sid)
    _mk_debate_transcript(sid)
    _mk_backtest(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert body["methodology_summary"] == "Public methodology writeup"
    # …but no reasoning rides along on the card.
    assert body["brief_intent"] is None
    assert _BULL_SENTINEL not in resp.text


async def test_private_row_card_still_404s_for_non_owners():
    """Unchanged #850 behaviour — pinned here so a future edit to the shared
    predicate cannot loosen the card gate while everyone watches the reasoning
    gate."""
    sid = "rsn00000000000008"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=False)
    _mk_passport(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        anon = await client.get(f"/api/strategies/{sid}")
        stranger = await client.get(f"/api/strategies/{sid}", cookies=_siwe_cookies(_W_STRANGER))
        owner = await client.get(f"/api/strategies/{sid}", cookies=_siwe_cookies(_W_OWNER))

    assert anon.status_code == 404
    assert stranger.status_code == 404
    assert owner.status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# 5. paper_routes._spec_for_strategy — the executable DSL spec
# ══════════════════════════════════════════════════════════════════════════
#
# Tested at the helper rather than through POST /api/paper/deployments: the
# gate under test is this function, and the route around it drags in real
# market-data advancement that has nothing to do with the authorization
# question.


def _spec_call(sid: str, *, caller_wallet: str | None, caller_user_id: str | None):
    from archimedes.api import paper_routes

    with db.get_session() as session:
        return paper_routes._spec_for_strategy(session, sid, caller_wallet, caller_user_id)


_SPEC = {
    "name": _SPEC_SENTINEL,
    "asset_universe": ["SPY"],
    "rebalance_frequency": "daily",
    "entry": {"gt": ["momentum_20", 0]},
    "exit": {"lt": ["momentum_20", -0.99]},
}


@pytest.mark.parametrize(
    ("caller_wallet", "caller_user_id"),
    [(None, None), (None, "legacy-test:0x0000000000000000000000000000000000000f02")],
    ids=["anonymous", "different-user"],
)
def test_published_strategy_spec_is_not_snapshottable_by_a_non_owner(caller_wallet, caller_user_id):
    """A published card is not a licence to snapshot the executable logic into
    someone else's paper ledger. Fails closed until a marketplace/licensing
    flow opens it deliberately."""
    from fastapi import HTTPException

    sid = "rsn00000000000009"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=True, spec=_SPEC)

    with pytest.raises(HTTPException) as exc:
        _spec_call(sid, caller_wallet=caller_wallet, caller_user_id=caller_user_id)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Strategy not found"


def test_published_strategy_spec_still_snapshottable_by_its_owner():
    """POSITIVE CONTROL: the spec is really stored and really returned — so the
    404s above are the gate, not a missing/undecodable spec column (which would
    raise a 422 'no_strategy_spec' instead and is a different failure)."""
    sid = "rsn00000000000010"
    _mk_strategy(sid, owner_user=_legacy_user_id(_W_OWNER), published=True, spec=_SPEC)

    spec = _spec_call(sid, caller_wallet=None, caller_user_id=_legacy_user_id(_W_OWNER))
    assert spec["name"] == _SPEC_SENTINEL


def test_example_strategy_spec_stays_snapshottable_by_anyone():
    """House demo content stays paper-tradeable by any signed-in user — the
    quickstart journey depends on it."""
    sid = "rsn00000000000011"
    _mk_strategy(sid, example=True, spec=_SPEC)

    spec = _spec_call(sid, caller_wallet=None, caller_user_id="legacy-test:0xsomeone")
    assert spec["name"] == _SPEC_SENTINEL
