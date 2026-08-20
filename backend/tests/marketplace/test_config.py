"""Unit tests for archimedes.marketplace.config.payments_halted (#1240)."""

from __future__ import annotations

from archimedes.marketplace.config import payments_halted


def test_unset_is_not_halted(monkeypatch):
    monkeypatch.delenv("PAYMENTS_HALT", raising=False)
    assert payments_halted() is False


def test_truthy_values_halt(monkeypatch):
    for value in ("true", "TRUE", "True", "1", "yes", "YES"):
        monkeypatch.setenv("PAYMENTS_HALT", value)
        assert payments_halted() is True, f"{value!r} should be truthy"


def test_falsy_and_garbage_values_do_not_halt(monkeypatch):
    for value in ("false", "0", "no", "", "banana"):
        monkeypatch.setenv("PAYMENTS_HALT", value)
        assert payments_halted() is False, f"{value!r} should not halt"


def test_reads_fresh_every_call_not_cached(monkeypatch):
    """The whole point: unlike payments_dry_run (snapshotted once into
    MarketService.__init__), this must reflect the CURRENT env on every call."""
    monkeypatch.delenv("PAYMENTS_HALT", raising=False)
    assert payments_halted() is False
    monkeypatch.setenv("PAYMENTS_HALT", "true")
    assert payments_halted() is True
    monkeypatch.setenv("PAYMENTS_HALT", "false")
    assert payments_halted() is False
