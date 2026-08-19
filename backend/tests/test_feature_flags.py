import pytest
from archimedes.feature_flags import FeatureFlags, require_feature, resolve_feature_flags
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient


def test_quant_defaults_off_in_production_and_on_elsewhere():
    assert resolve_feature_flags({"APP_ENV": "production"}).quant is False
    assert resolve_feature_flags({"APP_ENV": "development"}).quant is True
    assert resolve_feature_flags({"APP_ENV": "test"}).quant is True


def test_explicit_quant_flag_overrides_environment():
    assert resolve_feature_flags({"APP_ENV": "production", "FEATURE_QUANT": "true"}).quant is True
    assert resolve_feature_flags({"APP_ENV": "development", "FEATURE_QUANT": "false"}).quant is False


def test_invalid_feature_flag_fails_closed():
    with pytest.raises(ValueError, match="FEATURE_QUANT"):
        resolve_feature_flags({"FEATURE_QUANT": "sometimes"})


def test_disabled_feature_api_gate_returns_not_found():
    with pytest.raises(HTTPException) as exc:
        require_feature("quant", FeatureFlags(quant=False))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_disabled_quant_feature_hides_computation_apis(monkeypatch):
    monkeypatch.setenv("FEATURE_QUANT", "false")
    from archimedes.api.account_auth import CurrentUser, require_current_user
    from archimedes.main import app

    app.dependency_overrides[require_current_user] = lambda: CurrentUser(
        id="user-1", name="User", email="user@example.test", email_verified=True
    )
    calls = (
        ("POST", "/api/portfolio/optimize"),
        ("POST", "/api/portfolio/parameter-sweep"),
        ("GET", "/api/risk/cvar"),
        ("GET", "/api/risk/greeks"),
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for method, path in calls:
                response = await client.request(method, path, json={} if method == "POST" else None)
                assert response.status_code == 404, path
    finally:
        app.dependency_overrides.pop(require_current_user, None)
