"""#882: import_daily_returns.py must not silently collide malformed records
on the literal stem key ``"unknown"``.

Before #882, ``import_records`` did ``stem = rec.get("stem") or "unknown"``:
any file missing a ``stem`` key (or with a blank one) silently fell back to
the same placeholder key, so multiple malformed files in one import run would
overwrite each other's rows under that shared key instead of failing loudly.
#863 fixed the identical failure mode in the sibling
``import_backtest_fixtures.py``'s ``_load_records`` (fail-fast, collect every
error, refuse a partial import before any DB write); this test proves the
same shape now holds here.

Hermetic: exercises ``_load_records`` directly (pure — reads only the tmp
JSON files this test writes, no DB session, no ``archimedes.db`` singleton
engine involved), so it needs no network, Postgres, Redis, or the
``DATABASE_URL`` monkeypatch dance other tests need to work around
``archimedes.db``'s module-level ``engine``/``SessionLocal`` being bound once
per process. ``import_daily_returns.py`` lives in ``backend/scripts/``,
which is not an importable package (unlike ``archimedes.scripts``), so it is
loaded by file path via ``importlib`` — same pattern
``test_strategy_ownership.py`` uses for ``scripts/purge_orphan_generated.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_daily_returns.py"


def _load_import_daily_returns_module():
    spec = importlib.util.spec_from_file_location("import_daily_returns", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def import_daily_returns():
    return _load_import_daily_returns_module()


def _write(store_dir: Path, filename: str, payload) -> None:
    (store_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def _valid_record(stem: str, n_days: int = 3) -> dict:
    return {
        "stem": stem,
        "dates": [f"2026-01-{i + 1:02d}" for i in range(n_days)],
        "daily_returns": [0.001 * (i + 1) for i in range(n_days)],
        "data_vintage": "2026-07-04",
    }


class TestLoadRecordsStemValidation:
    def test_two_missing_stem_files_do_not_collide_on_unknown(self, tmp_path, import_daily_returns) -> None:
        """The core #882 regression: two files each missing ``stem`` must not
        both silently resolve to the same ``"unknown"`` key — the loader must
        refuse the whole import instead."""
        rec_a = _valid_record("placeholder_a")
        del rec_a["stem"]
        rec_b = _valid_record("placeholder_b")
        del rec_b["stem"]
        _write(tmp_path, "a.json", rec_a)
        _write(tmp_path, "b.json", rec_b)

        with pytest.raises(SystemExit) as exc_info:
            import_daily_returns._load_records(tmp_path)

        message = str(exc_info.value)
        assert "a.json" in message
        assert "b.json" in message
        assert "unknown" not in message

    def test_two_blank_stem_files_do_not_collide_on_unknown(self, tmp_path, import_daily_returns) -> None:
        """A blank/whitespace-only stem is just as much a collision risk as a
        missing key (``"" or "unknown"`` is also falsy) — must be rejected too."""
        _write(tmp_path, "a.json", _valid_record("   "))
        _write(tmp_path, "b.json", _valid_record(""))

        with pytest.raises(SystemExit) as exc_info:
            import_daily_returns._load_records(tmp_path)

        message = str(exc_info.value)
        assert "a.json" in message
        assert "b.json" in message

    def test_non_dict_stem_field_rejected(self, tmp_path, import_daily_returns) -> None:
        rec = _valid_record("whatever")
        rec["stem"] = 12345
        _write(tmp_path, "a.json", rec)

        with pytest.raises(SystemExit) as exc_info:
            import_daily_returns._load_records(tmp_path)
        assert "a.json" in str(exc_info.value)

    def test_non_dict_record_rejected(self, tmp_path, import_daily_returns) -> None:
        _write(tmp_path, "a.json", ["not", "a", "dict"])

        with pytest.raises(SystemExit) as exc_info:
            import_daily_returns._load_records(tmp_path)
        assert "a.json" in str(exc_info.value)

    def test_valid_records_pass_through_with_real_stems(self, tmp_path, import_daily_returns) -> None:
        _write(tmp_path, "a.json", _valid_record("strategy_a"))
        _write(tmp_path, "b.json", _valid_record("strategy_b"))

        records = import_daily_returns._load_records(tmp_path)

        stems = {rec["stem"] for rec in records}
        assert stems == {"strategy_a", "strategy_b"}

    def test_one_bad_file_fails_the_whole_batch_not_just_itself(self, tmp_path, import_daily_returns) -> None:
        """Atomicity: a single malformed file must refuse the entire import,
        not silently drop just itself and proceed with the rest."""
        _write(tmp_path, "good.json", _valid_record("strategy_good"))
        bad = _valid_record("strategy_bad")
        del bad["stem"]
        _write(tmp_path, "bad.json", bad)

        with pytest.raises(SystemExit) as exc_info:
            import_daily_returns._load_records(tmp_path)
        assert "bad.json" in str(exc_info.value)

    def test_mismatched_dates_and_returns_still_rejected(self, tmp_path, import_daily_returns) -> None:
        """Pre-#882 behavior for this check must survive the rewrite."""
        rec = _valid_record("strategy_a", n_days=3)
        rec["daily_returns"] = rec["daily_returns"][:2]
        _write(tmp_path, "a.json", rec)

        with pytest.raises(SystemExit) as exc_info:
            import_daily_returns._load_records(tmp_path)
        assert "a.json" in str(exc_info.value)
