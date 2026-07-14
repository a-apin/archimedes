"""Contract-address settings: core fields (T2.3) + SSOT-driven synth/oracle maps (#764).

``ChainSettings`` reads the CORE contract addresses from ``ARC_<FIELD>`` env vars (pydantic
fields, with a deployed-Arc-testnet default), and resolves the per-synth token + oracle
addresses **SSOT-driven** over ``universe.ON_CHAIN_SYNTHS`` — each from
``ARC_<SYMBOL>_ADDRESS`` / ``ARC_<SYMBOL>_ORACLE_ADDRESS`` (env, loaded from ``.env`` by
``main.load_dotenv``) else a committed transitional default — **excluding undeployed
synths** so consumers only ever see deployed synths with non-empty addresses (#764).

Hermetic: every ``ChainSettings`` is constructed with ``_env_file=None`` and every ``ARC_*``
var this module touches is stripped via ``monkeypatch`` (which cleans up), so neither an
ambient ``.env`` nor a developer's shell can leak in. No network, no Arc RPC.
"""

from __future__ import annotations

import pytest
from archimedes.chain.client import _ORACLE_DEFAULTS, _SYNTH_DEFAULTS, ChainSettings
from archimedes.universe import COMPLIANCE_FLAGGED_SINGLE_STOCKS, ON_CHAIN_SYNTHS

# Core (non-synth) address fields — still individual pydantic fields (env_prefix="ARC_").
CORE_NONEMPTY_DEFAULTS = {"usdc_address": "0x3600000000000000000000000000000000000000"}
CORE_EMPTY_DEFAULT_FIELDS = [
    "amm_router_address",
    "synthetic_factory_address",
    "vault_factory_address",
    "reasoning_trace_registry_address",
    "asset_registry_address",
    "strategy_registry_address",
]
CORE_ENV_VAR_FOR_FIELD = {
    "usdc_address": "ARC_USDC_ADDRESS",
    "amm_router_address": "ARC_AMM_ROUTER_ADDRESS",
    "synthetic_factory_address": "ARC_SYNTHETIC_FACTORY_ADDRESS",
    "vault_factory_address": "ARC_VAULT_FACTORY_ADDRESS",
    "reasoning_trace_registry_address": "ARC_REASONING_TRACE_REGISTRY_ADDRESS",
    "asset_registry_address": "ARC_ASSET_REGISTRY_ADDRESS",
    "strategy_registry_address": "ARC_STRATEGY_REGISTRY_ADDRESS",
}

# Pinned ANCHOR literals — deliberately NOT imported from the module under test — so an
# unintended change to a committed default address is CAUGHT, instead of being silently
# re-read from the same source the assertion compares against. Post-T3.2 the committed
# defaults are the FULL 281-synth deploy-address SSOT (data/synthetic_addresses.json);
# these two anchors spot-check it. Exhaustive parity/well-formed coverage is in
# test_synthetic_addresses.py. (Copilot review #765)
PINNED_SYNTH_DEFAULTS = {
    "sSPY": "0xc71c4a7ce89bf45d90b0a56eed9cd842eebf2d5e",
    "sBTC": "0xa6ddc0ace2b9a6a305d5aedef7432c4e48be35dc",
}
PINNED_ORACLE_DEFAULTS = {
    "sSPY": "0x0c3270ee60f7144cdb95541101e6f6fa3b8061d7",
    "sBTC": "0x40c895c92515f0d73ed6ce6b4e8a8960cba765a9",
}

# Any SSOT symbol with no committed default = "undeployed pre-redeploy". Selected
# dynamically (not hard-coded to e.g. sAGG) so the exclusion tests stay valid if
# the SSOT/default set changes — a symbol gaining a default or being removed no
# longer breaks them. (Copilot review #765)
_UNDEPLOYED_SYNTHS = [s for s in ON_CHAIN_SYNTHS if s not in _SYNTH_DEFAULTS]


@pytest.fixture
def clean_env(monkeypatch):
    """Strip every ARC_* var this module reads — core fields + per-synth token/oracle vars
    for the whole SSOT — so the "defaults / excluded when unset" assertions see a truly
    empty override surface."""
    for env_var in CORE_ENV_VAR_FOR_FIELD.values():
        monkeypatch.delenv(env_var, raising=False)
    for sym in ON_CHAIN_SYNTHS:
        monkeypatch.delenv(f"ARC_{sym.upper()}_ADDRESS", raising=False)
        monkeypatch.delenv(f"ARC_{sym.upper()}_ORACLE_ADDRESS", raising=False)
    return monkeypatch


# ── Core fields (T2.3) — unchanged pydantic behaviour ─────────────────────────


class TestCoreFields:
    def test_usdc_nonempty_default(self, clean_env):
        s = ChainSettings(_env_file=None)
        assert s.usdc_address == CORE_NONEMPTY_DEFAULTS["usdc_address"]

    def test_empty_default_fields_are_empty(self, clean_env):
        s = ChainSettings(_env_file=None)
        for field in CORE_EMPTY_DEFAULT_FIELDS:
            assert getattr(s, field) == "", f"{field} should default to ''"

    def test_each_core_field_overridable(self, clean_env):
        # Distinct sentinel per field so a cross-wired mapping can't pass.
        for i, (field, env_var) in enumerate(CORE_ENV_VAR_FOR_FIELD.items()):
            sentinel = f"0x{i:040x}"
            clean_env.setenv(env_var, sentinel)
            s = ChainSettings(_env_file=None)
            assert getattr(s, field) == sentinel, f"{env_var} did not override {field}"
            clean_env.delenv(env_var, raising=False)


# ── SSOT-driven synth / oracle maps (#764) ────────────────────────────────────


class TestSsotSynthMaps:
    def test_maps_use_defaults_for_in_ssot_synths(self, clean_env):
        """Post-T3.2 the committed defaults are the FULL 281-synth deploy-address SSOT,
        so every ON_CHAIN_SYNTHS symbol resolves (not just a demo handful)."""
        s = ChainSettings(_env_file=None)
        # Anchor pins FIRST: catch an accidental change to a known committed default
        # (these literals are NOT re-read from the module under test). Exhaustive
        # parity/well-formedness is asserted in test_synthetic_addresses.py.
        for sym, addr in PINNED_SYNTH_DEFAULTS.items():
            assert s.synth_addresses[sym] == addr, f"{sym} synth default changed unexpectedly"
        for sym, addr in PINNED_ORACLE_DEFAULTS.items():
            assert s.oracle_addresses[sym] == addr, f"{sym} oracle default changed unexpectedly"
        # New invariant (address convergence): the maps now cover the WHOLE universe.
        assert set(s.synth_addresses) == set(ON_CHAIN_SYNTHS)
        assert set(s.oracle_addresses) == set(ON_CHAIN_SYNTHS)
        # Every committed default is faithfully surfaced.
        for sym, addr in _SYNTH_DEFAULTS.items():
            assert s.synth_addresses[sym] == addr, f"{sym} synth default missing"
        for sym, addr in _ORACLE_DEFAULTS.items():
            assert s.oracle_addresses[sym] == addr, f"{sym} oracle default missing"

    def test_client_known_synths_match_ssot(self, clean_env):
        """The map keys are always a SUBSET of the SSOT (no sTSLA/sNVDA-style drift).

        Pre-redeploy it's the deployed subset (the defaults); post-redeploy env fills the
        rest — but it can never contain a non-SSOT symbol.
        """
        s = ChainSettings(_env_file=None)
        assert set(s.synth_addresses) <= set(ON_CHAIN_SYNTHS)
        assert set(s.oracle_addresses) <= set(ON_CHAIN_SYNTHS)

    def test_compliance_single_stocks_absent(self, clean_env):
        s = ChainSettings(_env_file=None)
        assert "sTSLA" not in s.synth_addresses
        assert "sNVDA" not in s.synth_addresses
        assert not (set(s.synth_addresses) & COMPLIANCE_FLAGGED_SINGLE_STOCKS)

    def test_undeployed_synth_excluded(self, clean_env):
        """A SSOT synth with no committed default and no env override is filtered out."""
        if not _UNDEPLOYED_SYNTHS:
            pytest.skip("every ON_CHAIN_SYNTHS symbol has a committed default — nothing to exclude")
        sym = _UNDEPLOYED_SYNTHS[0]
        s = ChainSettings(_env_file=None)
        assert sym not in s.synth_addresses

    def test_env_makes_undeployed_synth_appear(self, clean_env):
        if not _UNDEPLOYED_SYNTHS:
            pytest.skip("every ON_CHAIN_SYNTHS symbol has a committed default")
        sym = _UNDEPLOYED_SYNTHS[0]
        override = "0xAAAA000000000000000000000000000000000001"
        clean_env.setenv(f"ARC_{sym.upper()}_ADDRESS", override)
        s = ChainSettings(_env_file=None)
        assert s.synth_addresses[sym] == override

    def test_env_overrides_default(self, clean_env):
        synth_override = "0xBBBB000000000000000000000000000000000002"
        oracle_override = "0xCCCC000000000000000000000000000000000003"
        clean_env.setenv("ARC_SSPY_ADDRESS", synth_override)
        clean_env.setenv("ARC_SSPY_ORACLE_ADDRESS", oracle_override)
        s = ChainSettings(_env_file=None)
        assert s.synth_addresses["sSPY"] == synth_override
        assert s.oracle_addresses["sSPY"] == oracle_override

    def test_values_are_never_empty(self, clean_env):
        """Every value in the maps is a non-empty address — the invariant consumers that
        iterate synth_addresses.items() rely on (they don't all filter empties)."""
        s = ChainSettings(_env_file=None)
        assert s.synth_addresses, "expected the transitional defaults to be present"
        assert all(v for v in s.synth_addresses.values())
        assert all(v for v in s.oracle_addresses.values())
