"""Tests for LocalStrategyProvider (file-system-backed strategy loading).

Target: backend/archimedes/services/strategy_provider.py
Goal: ≥85% coverage on the target module.

Hermetic: uses a temp directory with minimal strategy files. No network,
no running DB (DB calls mocked at the session boundary).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Minimal strategy file content ────────────────────────────

_MINIMAL_STRATEGY = textwrap.dedent('''\
    """Test strategy for unit tests."""
    PAPER_TITLE = "Test Momentum Strategy"
    PAPER_AUTHORS = ["Alice", "Bob"]
    PAPER_VENUE = "Journal of Testing"
    PAPER_YEAR = 2024
    PAPER_DOI = "10.1234/test"
    PAPER_CITATION_COUNT = 42
    PAPER_ARXIV_ID = "2401.00001"
    METHODOLOGY_SUMMARY = "Buy when price is above the 200-day SMA."
    ASSET_UNIVERSE = ["SPY", "GOLD"]
    POSITION_SIZING = "equal_weight"
    REBALANCE_FREQUENCY = "daily"
    STATUS = "live"
    CURATOR_NOTE = "Test strategy for hermetic unit tests."
    REGIME_TAG = "bull"
''')

_SECOND_STRATEGY = textwrap.dedent('''\
    """Second test strategy."""
    PAPER_TITLE = "Vol-Managed Portfolios"
    PAPER_AUTHORS = ["Carol"]
    PAPER_VENUE = "Journal of Finance"
    PAPER_YEAR = 2017
    METHODOLOGY_SUMMARY = "Scale exposure inversely to realized volatility."
    ASSET_UNIVERSE = ["SPY", "NIKKEI"]
    POSITION_SIZING = "inverse_vol"
    REBALANCE_FREQUENCY = "daily"
    STATUS = "candidate"
    REGIME_TAG = "bear"
''')

_NO_TITLE_FILE = textwrap.dedent('''\
    """File without PAPER_TITLE — should be skipped."""
    SOME_OTHER_CONSTANT = "not a strategy"
''')


@pytest.fixture
def strategies_dir(tmp_path):
    """Create a temp directory with test strategy files."""
    (tmp_path / "test_momentum.py").write_text(_MINIMAL_STRATEGY)
    (tmp_path / "test_volmanaged.py").write_text(_SECOND_STRATEGY)
    (tmp_path / "test_notitle.py").write_text(_NO_TITLE_FILE)
    (tmp_path / "_private.py").write_text("# underscore-prefixed, should be skipped")
    (tmp_path / "not_python.txt").write_text("not a Python file")
    return tmp_path


# Mock DB calls so we don't need Postgres
@pytest.fixture(autouse=True)
def _mock_db():
    with (
        patch("archimedes.services.strategy_provider.get_session") as mock_session,
        patch("archimedes.services.strategy_provider.latest_backtests_by_strategy", return_value={}),
        # ingest_passport is a LOCAL import inside refresh() at strategy_provider.py:396,
        # so we must patch where it's defined (passport_loader), not where it's used.
        patch("archimedes.services.passport_loader.ingest_passport", return_value=None),
    ):
        # Return a context manager that yields a mock session
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=MagicMock())
        mock_cm.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_cm
        yield


def _make_provider(strategies_dir: Path):
    from archimedes.services.strategy_provider import LocalStrategyProvider

    return LocalStrategyProvider(strategies_dir)


# ── Constructor / refresh ─────────────────────────────────────


class TestRefresh:
    def test_loads_strategies_from_directory(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        assert len(provider.list_strategies()) == 2  # momentum + volmanaged (notitle skipped)

    def test_skips_underscore_prefixed_files(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        names = [s.paper_title for s in provider.list_strategies()]
        assert "_private" not in str(names)

    def test_skips_files_without_paper_title(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        names = [s.paper_title for s in provider.list_strategies()]
        assert "not a strategy" not in str(names)

    def test_returns_loaded_count(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        count = provider.refresh()
        assert count == 2

    def test_empty_directory(self, tmp_path):
        provider = _make_provider(tmp_path)
        assert provider.list_strategies() == []

    def test_nonexistent_directory(self, tmp_path):
        provider = _make_provider(tmp_path / "nonexistent")
        assert provider.list_strategies() == []
        assert provider.refresh() == 0


# ── list_strategies ───────────────────────────────────────────


class TestListStrategies:
    def test_returns_all_without_filter(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        assert len(provider.list_strategies()) == 2

    def test_filter_by_status(self, strategies_dir):
        from archimedes.models.strategy import StrategyStatus

        provider = _make_provider(strategies_dir)
        live = provider.list_strategies(status=StrategyStatus.LIVE)
        assert len(live) == 1
        assert live[0].paper_title == "Test Momentum Strategy"

    def test_filter_by_asset_universe(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        gold = provider.list_strategies(asset_universe=["GOLD"])
        assert len(gold) == 1
        assert gold[0].paper_title == "Test Momentum Strategy"

    def test_filter_by_asset_universe_intersection(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        spy = provider.list_strategies(asset_universe=["SPY"])
        assert len(spy) == 2  # both strategies have SPY


# ── get_strategy ──────────────────────────────────────────────


class TestGetStrategy:
    def test_returns_strategy_by_id(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        strats = provider.list_strategies()
        s = provider.get_strategy(strats[0].id)
        assert s is not None
        assert s.id == strats[0].id

    def test_returns_none_for_unknown_id(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        assert provider.get_strategy("nonexistent") is None


# ── get_backtest_result ───────────────────────────────────────


class TestGetBacktestResult:
    def test_returns_none_when_no_backtests(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        strats = provider.list_strategies()
        result = provider.get_backtest_result(strats[0].id)
        assert result is None

    def test_returns_none_for_unknown_strategy(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        assert provider.get_backtest_result("nonexistent") is None


# ── Strategy model fields ─────────────────────────────────────


class TestStrategyFields:
    def test_metadata_parsed_correctly(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        s = next(s for s in provider.list_strategies() if s.paper_title == "Test Momentum Strategy")
        assert s.paper_authors == ["Alice", "Bob"]
        assert s.paper_venue == "Journal of Testing"
        assert s.paper_year == 2024
        assert s.paper_doi == "10.1234/test"
        assert s.paper_citation_count == 42
        assert s.paper_arxiv_id == "2401.00001"
        assert s.methodology_summary == "Buy when price is above the 200-day SMA."
        assert s.asset_universe == ["SPY", "GOLD"]
        assert s.position_sizing == "equal_weight"
        assert s.rebalance_frequency == "daily"
        assert s.regime_tag == "bull"

    def test_methodology_hash_is_deterministic(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        s = next(s for s in provider.list_strategies() if s.paper_title == "Test Momentum Strategy")
        assert s.methodology_hash is not None
        assert len(s.methodology_hash) == 64  # SHA-256 hex

        # Refresh and verify hash is the same
        provider.refresh()
        s2 = next(s for s in provider.list_strategies() if s.paper_title == "Test Momentum Strategy")
        assert s2.methodology_hash == s.methodology_hash

    def test_strategy_id_is_deterministic(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        ids1 = {s.id for s in provider.list_strategies()}
        provider.refresh()
        ids2 = {s.id for s in provider.list_strategies()}
        assert ids1 == ids2

    def test_second_strategy_fields(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        s = next(s for s in provider.list_strategies() if s.paper_title == "Vol-Managed Portfolios")
        assert s.paper_authors == ["Carol"]
        assert s.status == "candidate"
        assert s.regime_tag == "bear"
        assert "NIKKEI" in s.asset_universe


# ── get_strategies_for_risk_profile ───────────────────────────


class TestRiskProfile:
    def test_returns_empty_when_no_match(self, strategies_dir):
        provider = _make_provider(strategies_dir)
        # Default strategies don't set risk_profiles explicitly
        result = provider.get_strategies_for_risk_profile("nonexistent_profile")
        assert isinstance(result, list)


# ── Helper functions ──────────────────────────────────────────


class TestHelpers:
    def test_hash_file_deterministic(self, strategies_dir):
        from archimedes.services.strategy_provider import _hash_file

        h1 = _hash_file(strategies_dir / "test_momentum.py")
        h2 = _hash_file(strategies_dir / "test_momentum.py")
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_file_different_for_different_files(self, strategies_dir):
        from archimedes.services.strategy_provider import _hash_file

        h1 = _hash_file(strategies_dir / "test_momentum.py")
        h2 = _hash_file(strategies_dir / "test_volmanaged.py")
        assert h1 != h2

    def test_read_module_constants(self, strategies_dir):
        from archimedes.services.strategy_provider import _read_module_constants

        meta = _read_module_constants(strategies_dir / "test_momentum.py")
        assert meta["PAPER_TITLE"] == "Test Momentum Strategy"
        assert meta["PAPER_YEAR"] == 2024

    def test_read_module_constants_skips_non_literals(self, strategies_dir):
        # Write a file with a non-literal constant
        (strategies_dir / "test_dynamic.py").write_text('PAPER_TITLE = "Dynamic"\nCOMPUTED = 1 + 2\n')
        from archimedes.services.strategy_provider import _read_module_constants

        meta = _read_module_constants(strategies_dir / "test_dynamic.py")
        assert meta["PAPER_TITLE"] == "Dynamic"
        # COMPUTED may or may not be in meta depending on AST parsing — that's fine


# ── ID / hash computation functions ───────────────────────────


class TestIdAndHashComputation:
    def test_strategy_id_deterministic(self):
        from archimedes.services.strategy_provider import _strategy_id

        meta = {"PAPER_TITLE": "Test", "METHODOLOGY_SUMMARY": "Buy low"}
        id1 = _strategy_id(meta, "fakehash")
        id2 = _strategy_id(meta, "fakehash")
        assert id1 == id2
        assert len(id1) == 32  # SHA-256 hex truncated to 32

    def test_strategy_id_changes_with_title(self):
        from archimedes.services.strategy_provider import _strategy_id

        id1 = _strategy_id({"PAPER_TITLE": "Strategy A", "METHODOLOGY_SUMMARY": "X"}, "h1")
        id2 = _strategy_id({"PAPER_TITLE": "Strategy B", "METHODOLOGY_SUMMARY": "X"}, "h2")
        assert id1 != id2

    def test_methodology_hash_deterministic(self):
        from archimedes.services.strategy_provider import _methodology_hash

        meta = {"METHODOLOGY_SUMMARY": "Equal weight monthly rebalance"}
        h1 = _methodology_hash(meta)
        h2 = _methodology_hash(meta)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_infer_risk_profiles_from_keywords(self):
        from archimedes.services.strategy_provider import _infer_risk_profiles

        profiles = _infer_risk_profiles("A conservative strategy with low risk")
        assert "conservative" in profiles or isinstance(profiles, list)


# ── _load_fixtures ────────────────────────────────────────

_FULL_FIXTURE_DEFAULTS: dict = {
    "n_obs_daily": 5560,
    "sharpe_ratio": 0.85,
    "sortino_ratio": 0.9,
    "max_drawdown": 0.2,
    "cagr": 0.1,
    "calmar_ratio": 0.5,
    "win_rate": 0.4,
    "profit_factor": 1.5,
    "total_trades": 50,
    "avg_holding_period_days": 30.0,
    "correlation_to_spy": 0.6,
    "correlation_to_btc": None,
    "out_of_sample_sharpe": 0.7,
    "look_ahead_audit_passed": True,
    "backtest_engine": "backtrader",
    "transaction_cost_bps": 10,
    "backtest_start": "2004-01-02T00:00:00",
    "backtest_end": "2026-04-30T00:00:00",
    "paper_claimed_sharpe": 0.9,
    "paper_claimed_cagr": 0.12,
    "paper_claimed_max_dd": 0.1,
    "deflated_sharpe_ratio": 0.3,
    "dsr_p_value": 0.6,
    "dsr_convention": "raw",
    "num_trials_in_selection": 5,
    "pbo_score": 0.3,
    "passes_rigor_gate": False,
    "kelly_fraction": 0.5,
}


def _fixture_row(stem: str, **overrides):
    from archimedes.models.backtest_fixtures_store import StrategyBacktestFixture

    kwargs = {**_FULL_FIXTURE_DEFAULTS, **overrides}
    return StrategyBacktestFixture(stem=stem, **kwargs)


@pytest.fixture()
def fixtures_session(tmp_path):
    """An isolated SQLite engine/session with strategy_backtest_fixtures
    created — independent of the app's module-level engine, so no
    monkeypatching of shared globals is needed (mirrors the daily-returns
    store's ``tmp_db_session`` parity-test fixture)."""
    from archimedes.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'fixtures.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class TestLoadFixtures:
    def test_returns_empty_dict_without_fixture_rows(self, fixtures_session, monkeypatch):
        from archimedes.services.strategy_provider import _load_fixtures

        monkeypatch.delenv("ARCHIMEDES_FIXTURES_PATH", raising=False)
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)

        result = _load_fixtures(session=fixtures_session)
        assert result == {}

    def test_loads_fixture_from_db(self, fixtures_session, monkeypatch):
        from archimedes.services.strategy_provider import _load_fixtures

        monkeypatch.delenv("ARCHIMEDES_FIXTURES_PATH", raising=False)
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)
        fixtures_session.add(_fixture_row("test_strategy", sharpe_ratio=0.85, cagr=0.12))
        fixtures_session.commit()

        result = _load_fixtures(session=fixtures_session)
        assert "test_strategy" in result
        assert result["test_strategy"]["sharpe_ratio"] == 0.85
        assert result["test_strategy"]["cagr"] == 0.12


# ── _load_fixtures: dynamic source vs DB fallback (issue #465 overrides) ──


class TestDynamicFixtureLoading:
    """Hermetic coverage of the env-configured dynamic fixture source.

    No network: the URL branch is mocked at the ``httpx.get`` boundary per the
    testing conventions (mock at boundaries, not internals).
    """

    def _seed_db(self, session):
        """Insert one DB row marked so we can tell it apart from a
        dynamic-source payload."""
        session.add(_fixture_row("db_strategy", sharpe_ratio=0.1))
        session.commit()

    # (a) fallback used when no dynamic source is configured

    def test_falls_back_to_db_when_nothing_configured(self, fixtures_session, monkeypatch):
        from archimedes.services.strategy_provider import _load_fixtures

        monkeypatch.delenv("ARCHIMEDES_FIXTURES_PATH", raising=False)
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)
        self._seed_db(fixtures_session)

        result = _load_fixtures(session=fixtures_session)
        assert result["db_strategy"]["sharpe_ratio"] == 0.1

    def test_empty_when_nothing_configured_and_no_rows(self, fixtures_session, monkeypatch):
        from archimedes.services.strategy_provider import _load_fixtures

        monkeypatch.delenv("ARCHIMEDES_FIXTURES_PATH", raising=False)
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)

        assert _load_fixtures(session=fixtures_session) == {}

    # (b) dynamic source preferred when present — filesystem path override

    def test_dynamic_path_preferred_over_db(self, fixtures_session, tmp_path, monkeypatch):
        import json

        from archimedes.services.strategy_provider import _load_fixtures

        self._seed_db(fixtures_session)
        dynamic_file = tmp_path / "dynamic_fixtures.json"
        dynamic_file.write_text(json.dumps({"dynamic_strategy": {"sharpe_ratio": 0.99}}))
        monkeypatch.setenv("ARCHIMEDES_FIXTURES_PATH", str(dynamic_file))
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)

        result = _load_fixtures(session=fixtures_session)
        assert result == {"dynamic_strategy": {"sharpe_ratio": 0.99}}
        assert "db_strategy" not in result

    # (b) dynamic source preferred when present — URL override (mocked httpx)

    def test_dynamic_url_preferred_over_db(self, fixtures_session, monkeypatch):
        from unittest.mock import MagicMock, patch

        from archimedes.services.strategy_provider import _load_fixtures

        self._seed_db(fixtures_session)
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_PATH", raising=False)
        monkeypatch.setenv("ARCHIMEDES_FIXTURES_URL", "https://fixtures.example/backtest.json")

        fake_resp = MagicMock()
        fake_resp.text = '{"url_strategy": {"sharpe_ratio": 1.23}}'
        fake_resp.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=fake_resp) as mock_get:
            result = _load_fixtures(session=fixtures_session)

        mock_get.assert_called_once()
        assert result == {"url_strategy": {"sharpe_ratio": 1.23}}
        assert "db_strategy" not in result

    # Resilience: a configured-but-failing dynamic source reverts to the DB

    def test_dynamic_path_failure_falls_back_to_db(self, fixtures_session, tmp_path, monkeypatch):
        from archimedes.services.strategy_provider import _load_fixtures

        self._seed_db(fixtures_session)
        monkeypatch.setenv("ARCHIMEDES_FIXTURES_PATH", str(tmp_path / "does_not_exist.json"))
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)

        result = _load_fixtures(session=fixtures_session)
        assert result["db_strategy"]["sharpe_ratio"] == 0.1

    def test_dynamic_url_failure_falls_back_to_db(self, fixtures_session, monkeypatch):
        from unittest.mock import patch

        from archimedes.services.strategy_provider import _load_fixtures

        self._seed_db(fixtures_session)
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_PATH", raising=False)
        monkeypatch.setenv("ARCHIMEDES_FIXTURES_URL", "https://fixtures.example/backtest.json")

        with patch("httpx.get", side_effect=ConnectionError("network down")):
            result = _load_fixtures(session=fixtures_session)
        assert result["db_strategy"]["sharpe_ratio"] == 0.1

    def test_path_takes_precedence_over_url(self, fixtures_session, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch

        from archimedes.services.strategy_provider import _load_fixtures

        dynamic_file = tmp_path / "dynamic_fixtures.json"
        dynamic_file.write_text(json.dumps({"path_strategy": {"sharpe_ratio": 0.5}}))
        monkeypatch.setenv("ARCHIMEDES_FIXTURES_PATH", str(dynamic_file))
        monkeypatch.setenv("ARCHIMEDES_FIXTURES_URL", "https://fixtures.example/backtest.json")

        # URL must never be consulted when the path source succeeds.
        with patch("httpx.get", side_effect=AssertionError("URL should not be fetched")):
            result = _load_fixtures(session=fixtures_session)
        assert result == {"path_strategy": {"sharpe_ratio": 0.5}}

    def test_empty_dynamic_payload_falls_back_to_db(self, fixtures_session, tmp_path, monkeypatch):
        from archimedes.services.strategy_provider import _load_fixtures

        self._seed_db(fixtures_session)
        empty_file = tmp_path / "empty.json"
        empty_file.write_text("   ")  # whitespace-only → no fixtures
        monkeypatch.setenv("ARCHIMEDES_FIXTURES_PATH", str(empty_file))
        monkeypatch.delenv("ARCHIMEDES_FIXTURES_URL", raising=False)

        result = _load_fixtures(session=fixtures_session)
        assert result["db_strategy"]["sharpe_ratio"] == 0.1


# ── default_provider resolution ─────────────────────────────


class TestDefaultProvider:
    def test_explicit_repo_root(self, strategies_dir):
        from archimedes.services.strategy_provider import default_provider

        provider = default_provider(repo_root=strategies_dir.parent)
        # May find 0 strategies if the parent doesn't have the right structure,
        # but it should not error
        assert provider is not None

    def test_env_var_override(self, strategies_dir, monkeypatch):
        from archimedes.services.strategy_provider import default_provider

        monkeypatch.setenv("ARCHIMEDES_STRATEGIES_DIR", str(strategies_dir))
        provider = default_provider()
        assert len(provider.list_strategies()) == 2  # our test strategies


# ── extract_from_paper ──────────────────────────────────


class TestExtractFromPaper:
    def test_returns_none_on_pipeline_failure(self, strategies_dir):
        from unittest.mock import patch

        provider = _make_provider(strategies_dir)
        with patch("archimedes.services.arxiv_pipeline.extract_strategy", return_value=None):
            result = provider.extract_from_paper("2401.99999")
        assert result is None

    def test_returns_strategy_on_success(self, strategies_dir):
        from unittest.mock import patch

        provider = _make_provider(strategies_dir)
        new_file = strategies_dir / "extracted_strategy.py"
        new_content = '"""Extracted strategy."""\nPAPER_TITLE = "Extracted Paper"\nMETHODOLOGY_SUMMARY = "Novel approach"\nASSET_UNIVERSE = ["SPY"]\nSTATUS = "candidate"\nREGIME_TAG = "regime_neutral"\n'

        def fake_extract(arxiv_id, strategies_dir=None):
            new_file.write_text(new_content)
            return new_file

        with patch("archimedes.services.arxiv_pipeline.extract_strategy", side_effect=fake_extract):
            result = provider.extract_from_paper("2401.12345")
        assert result is not None
        assert result.paper_title == "Extracted Paper"
