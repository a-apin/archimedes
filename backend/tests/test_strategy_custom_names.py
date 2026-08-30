"""User-chosen strategy names (dbrowneup/strategy-custom-names).

Two contracts under test:

  1. Schema — ``GenerateBrief.name`` is optional, sanitized (strip + collapse
     internal whitespace), treats empty/whitespace-only as unset (None), and
     rejects control characters and names longer than 80 chars.
  2. Pipeline — ``run_generation`` applies ``brief.name`` to the WINNING
     candidate only, at the single ``_resolve_name`` choke point, before
     persistence. Considered-rejects keep their auto-derived names so a
     generation never produces N identically-named strategies.

Hermetic by construction: fixture pipeline (GENERATION_PIPELINE_FIXTURE=1 —
no LLM, no network, static backtester skipped), in-memory JobStore stand-in,
and ``_persist_candidate`` monkeypatched (no DB). Pattern copied from
``test_rigor_deploy_gate_contract.py``.
"""

from __future__ import annotations

import pytest
from archimedes.agents.generation_pipeline import _resolve_name, run_generation
from archimedes.api.generate_schemas import GenerateBrief
from pydantic import ValidationError

# ── 1. Schema validation ──────────────────────────────────────────────────


class TestGenerateBriefName:
    def test_name_defaults_to_none(self):
        assert GenerateBrief(intent="momentum").name is None

    def test_valid_name_is_kept(self):
        assert GenerateBrief(intent="momentum", name="My Alpha").name == "My Alpha"

    def test_name_is_stripped_and_whitespace_collapsed(self):
        brief = GenerateBrief(intent="momentum", name="  My   Alpha \t\n Strategy  ")
        assert brief.name == "My Alpha Strategy"

    @pytest.mark.parametrize("raw", [None, "", "   ", " \t \n "])
    def test_empty_or_whitespace_only_treated_as_none(self, raw):
        assert GenerateBrief(intent="momentum", name=raw).name is None

    def test_max_length_80_accepted(self):
        assert GenerateBrief(intent="momentum", name="x" * 80).name == "x" * 80

    def test_over_80_chars_rejected(self):
        with pytest.raises(ValidationError, match="at most 80"):
            GenerateBrief(intent="momentum", name="x" * 81)

    @pytest.mark.parametrize("bad", ["nul\x00name", "esc\x1bname", "del\x7fname"])
    def test_control_chars_rejected(self, bad):
        # \t/\n are whitespace → collapsed, not rejected; non-whitespace control
        # chars (NUL, ESC, DEL) survive collapsing and must hard-fail.
        with pytest.raises(ValidationError, match="control characters"):
            GenerateBrief(intent="momentum", name=bad)

    def test_non_string_rejected(self):
        with pytest.raises(ValidationError):
            GenerateBrief(intent="momentum", name=123)


# ── 2. _resolve_name choke point ──────────────────────────────────────────


class TestResolveName:
    def test_prefers_brief_name(self):
        brief = GenerateBrief(intent="momentum", name="My Alpha")
        assert _resolve_name(brief, "Auto Blend — momentum") == "My Alpha"

    def test_falls_back_to_default_when_unset(self):
        brief = GenerateBrief(intent="momentum")
        assert _resolve_name(brief, "Auto Blend — momentum") == "Auto Blend — momentum"


# ── 3. Pipeline: winner gets the user's name; rejects keep auto names ─────


@pytest.fixture(autouse=True)
def force_fixture_path(monkeypatch):
    """No LLM / network / static backtest — drive the deterministic fixture pipeline."""
    monkeypatch.setenv("GENERATION_PIPELINE_FIXTURE", "1")


class _FakeStore:
    """In-memory JobStore stand-in (mirrors test_rigor_deploy_gate_contract.py)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.status: list[tuple[str, dict | None, str]] = []
        self.current_status: str | None = None

    async def push_event(self, job_id, payload):
        self.events.append(payload)
        return len(self.events)

    async def update_status(self, job_id, status, *, result=None, error=""):
        self.status.append((status, result, error))
        self.current_status = status

    async def update_terminal_status(self, job_id, status, *, result=None, error=""):
        """Mirrors ``JobStore.update_terminal_status`` (#1355): a no-op once
        ``cancelled`` has already been recorded."""
        if self.current_status == "cancelled":
            return False
        await self.update_status(job_id, status, result=result, error=error)
        return True


@pytest.fixture
def persisted(monkeypatch):
    """Capture what _persist_candidate would write, without a DB."""
    captured: list = []

    async def _fake_persist(c, _brief, *, owner_wallet=None):  # mirrors _persist_candidate(c, brief, *, owner_wallet)
        # Snapshot immutable values AT persist time — appending the mutable
        # candidate object would let a later rename slip past these assertions
        # (Copilot, #849).
        captured.append((c.candidate_id, c.strategy_name))
        return (f"strat_{c.candidate_id}", f"0x{c.candidate_id}")

    monkeypatch.setattr("archimedes.agents.generation_pipeline._persist_candidate", _fake_persist)
    return captured


async def test_winner_gets_user_name_rejects_keep_auto_names(persisted):
    store = _FakeStore()
    brief = GenerateBrief(intent="dual regime trend following", name="Dan's Custom Alpha")
    # Default dual_regime=True → bull + bear candidates; exactly one is selected.
    await run_generation(job_id="job_names", brief=brief, store=store)

    result = store.status[-1][1]
    assert result is not None
    cands = result["candidates"]
    assert len(cands) == 2

    winners = [c for c in cands if c["selected"]]
    rejects = [c for c in cands if not c["selected"]]
    assert len(winners) == 1 and len(rejects) == 1

    # Winner carries the user's name verbatim.
    assert winners[0]["strategy_name"] == "Dan's Custom Alpha"
    # Rejects keep their auto-derived name (no duplicate-name confusion).
    assert rejects[0]["strategy_name"] != "Dan's Custom Alpha"
    assert "—" in rejects[0]["strategy_name"]  # fixture auto-name template

    # K=1 (Phase-3): ONLY the winner is persisted as a strategy, carrying the
    # user's name; the reject lives in the candidates payload + episodic
    # proposals, never as a StrategyRecord.
    persisted_names = [name for _cid, name in persisted]
    assert persisted_names == ["Dan's Custom Alpha"]


async def test_no_name_keeps_auto_names_for_all(persisted):
    store = _FakeStore()
    brief = GenerateBrief(intent="dual regime trend following")
    await run_generation(job_id="job_no_name", brief=brief, store=store)

    result = store.status[-1][1]
    assert result is not None
    for c in result["candidates"]:
        # Auto-derived fixture names embed the intent snippet + risk appetite.
        assert "dual regime trend following" in c["strategy_name"]
    # K=1: exactly the WINNER is persisted, under its auto-derived name.
    selected = [c for c in result["candidates"] if c["selected"]]
    assert len(persisted) == 1 and len(selected) == 1
    assert persisted[0][1] == selected[0]["strategy_name"]
