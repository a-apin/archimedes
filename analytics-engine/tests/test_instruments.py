from archimedes_analytics_engine.instruments import (
    OPERATION_TO_SYMBOL,
    resolve_operations,
)


def test_operations_include_required_assets() -> None:
    assert set(OPERATION_TO_SYMBOL.keys()) == {
        "SPY",
        "NIKKEI",
        "GOLD",
        "TREASURY",
        "OIL",
    }


def test_resolve_operations_rejects_unknown() -> None:
    try:
        resolve_operations(["SPY", "BAD"])
        raise AssertionError("Expected ValueError")
    except ValueError as exc:
        assert "Unsupported operation" in str(exc)
