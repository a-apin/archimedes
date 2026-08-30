"""Two-chain configuration seam: payments vs execution (#1240).

The Arc mainnet cutover puts the payment rail (USDC / x402 / Gateway /
PaymentSplitter) on mainnet while vault, synth, AMM and oracle execution stays
on testnet. Until then both roles resolve to the single ``chain_id`` /
``arc_rpc_url`` pair, and this module's first job is to prove that an
unconfigured environment is bit-for-bit unchanged.

Its second job is the guard: a chain id set without its RPC (or the reverse) is
rejected at construction rather than quietly completed from the other chain. On
the payments side that fallback would settle real USDC against an endpoint
nobody selected while reporting a chain nobody selected either.

Hermetic: every ``ChainSettings`` is built with ``_env_file=None`` and every
variable touched here is set through ``monkeypatch``, so no ambient ``.env`` or
developer shell reaches in. No network, no RPC.
"""

from __future__ import annotations

import pytest
from archimedes.chain.client import ChainSettings
from pydantic import ValidationError

_SINGLE_CHAIN_ID = 5042002
_SINGLE_RPC = "https://rpc.testnet.arc.network"

# The four variables the split is configured through. env_prefix is "ARC_", so
# a field named ``payments_chain_id`` is ARC_PAYMENTS_CHAIN_ID on the wire.
_SPLIT_VARS = [
    "ARC_PAYMENTS_CHAIN_ID",
    "ARC_PAYMENTS_RPC_URL",
    "ARC_EXECUTION_CHAIN_ID",
    "ARC_EXECUTION_RPC_URL",
]


@pytest.fixture(autouse=True)
def _strip_split_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient split configuration leaks into any test here."""
    for var in [*_SPLIT_VARS, "ARC_CHAIN_ID", "ARC_ARC_RPC_URL"]:
        monkeypatch.delenv(var, raising=False)


def _settings(**kwargs) -> ChainSettings:
    return ChainSettings(_env_file=None, **kwargs)


class TestUnconfiguredIsUnchanged:
    """An environment that sets nothing must behave exactly as it does today."""

    def test_both_roles_resolve_to_the_single_chain(self) -> None:
        s = _settings()
        assert s.payments_chain.chain_id == _SINGLE_CHAIN_ID
        assert s.execution_chain.chain_id == _SINGLE_CHAIN_ID
        assert s.payments_chain.rpc_url == _SINGLE_RPC
        assert s.execution_chain.rpc_url == _SINGLE_RPC

    def test_an_inherited_chain_is_not_marked_explicit(self) -> None:
        """`explicit` is the whole point of the flag: inherited must read False.

        Fails against returning a hard-coded ``explicit=True``, which would tell
        a caller a chain had been chosen for this role when nothing had.
        """
        s = _settings()
        assert s.payments_chain.explicit is False
        assert s.execution_chain.explicit is False

    def test_one_chain_is_not_a_split(self) -> None:
        assert _settings().is_split_chain is False

    def test_the_legacy_fields_keep_the_execution_chain(self) -> None:
        """`chain_id` / `arc_rpc_url` still mean the chain the contracts are on.

        Every signing call site (executor, trace_publisher, strategy_publisher)
        reads ``settings.chain_id``. Repointing it at the payments chain would
        sign execution transactions for the wrong network, so this pins it.
        """
        s = _settings(payments_chain_id=999, payments_rpc_url="https://payments.example")
        assert s.chain_id == s.execution_chain.chain_id
        assert s.arc_rpc_url == s.execution_chain.rpc_url


class TestSplitResolution:
    def test_payments_moves_while_execution_stays(self) -> None:
        """The exact mainnet-cutover shape: payments elsewhere, execution here."""
        s = _settings(payments_chain_id=999, payments_rpc_url="https://payments.example")
        assert s.payments_chain.chain_id == 999
        assert s.payments_chain.rpc_url == "https://payments.example"
        assert s.payments_chain.explicit is True
        # Execution untouched, and still marked inherited.
        assert s.execution_chain.chain_id == _SINGLE_CHAIN_ID
        assert s.execution_chain.explicit is False
        assert s.is_split_chain is True

    def test_execution_can_move_independently(self) -> None:
        s = _settings(execution_chain_id=31337, execution_rpc_url="https://exec.example")
        assert s.execution_chain.chain_id == 31337
        assert s.execution_chain.explicit is True
        assert s.payments_chain.chain_id == _SINGLE_CHAIN_ID
        assert s.is_split_chain is True

    def test_both_set_to_the_same_id_is_explicit_but_not_a_split(self) -> None:
        """Deliberately pinning both to one chain is a decision, not a split.

        Fails against deriving `explicit` from `is_split_chain` — the two answer
        different questions, and conflating them would report a pinned
        single-chain deployment as having inherited its configuration.
        """
        s = _settings(
            payments_chain_id=5042002,
            payments_rpc_url="https://a.example",
            execution_chain_id=5042002,
            execution_rpc_url="https://b.example",
        )
        assert s.payments_chain.explicit is True
        assert s.execution_chain.explicit is True
        assert s.is_split_chain is False


class TestHalfConfiguredSplitIsRejected:
    """The guard. Each case must raise, naming the variable that completes it."""

    @pytest.mark.parametrize(
        ("kwargs", "expected_var"),
        [
            ({"payments_chain_id": 999}, "ARC_PAYMENTS_RPC_URL"),
            ({"payments_rpc_url": "https://payments.example"}, "ARC_PAYMENTS_CHAIN_ID"),
            ({"execution_chain_id": 31337}, "ARC_EXECUTION_RPC_URL"),
            ({"execution_rpc_url": "https://exec.example"}, "ARC_EXECUTION_CHAIN_ID"),
        ],
    )
    def test_each_half_is_refused(self, kwargs: dict, expected_var: str) -> None:
        with pytest.raises(ValidationError) as exc:
            _settings(**kwargs)
        assert expected_var in str(exc.value), (
            f"the error must name {expected_var}, the variable that completes the pair"
        )

    def test_a_complete_pair_is_accepted(self) -> None:
        """Anti-vacuity for the guard above: the same fields, both halves set, pass.

        Without this a validator that rejected everything would satisfy every
        rejection case and look like a working guard.
        """
        s = _settings(payments_chain_id=999, payments_rpc_url="https://payments.example")
        assert s.payments_chain.chain_id == 999


class TestTheEnvironmentVariableNamesAreReal:
    """Constructing with kwargs proves nothing about the wire names.

    ``env_prefix = "ARC_"`` means the variable for ``payments_chain_id`` is
    ARC_PAYMENTS_CHAIN_ID. These tests go through the environment so a renamed
    field or a changed prefix fails here rather than in a deploy, where the
    symptom is a silently ignored variable and a chain that never moved.
    """

    def test_payments_vars_are_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARC_PAYMENTS_CHAIN_ID", "999")
        monkeypatch.setenv("ARC_PAYMENTS_RPC_URL", "https://payments.example")
        s = _settings()
        assert s.payments_chain.chain_id == 999
        assert s.payments_chain.rpc_url == "https://payments.example"
        assert s.payments_chain.explicit is True

    def test_execution_vars_are_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARC_EXECUTION_CHAIN_ID", "31337")
        monkeypatch.setenv("ARC_EXECUTION_RPC_URL", "https://exec.example")
        s = _settings()
        assert s.execution_chain.chain_id == 31337
        assert s.execution_chain.explicit is True

    def test_a_half_set_environment_refuses_to_construct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The guard has to fire on the env path too, not only on kwargs.

        This is the path a real deployment takes: someone adds
        ARC_PAYMENTS_CHAIN_ID to ecs.tf and forgets the endpoint.
        """
        monkeypatch.setenv("ARC_PAYMENTS_CHAIN_ID", "999")
        with pytest.raises(ValidationError, match="ARC_PAYMENTS_RPC_URL"):
            _settings()


class TestBlankValuesFromTheEnvTemplate:
    """`.env.example` ships all four blank, so blank must mean unset.

    ``cp .env.example .env`` is the documented first step in SETUP.md. It puts
    ``ARC_PAYMENTS_CHAIN_ID=`` into the environment as an empty string, which
    pydantic will not parse as an int. Without the coercion these tests pin, the
    backend refuses to start for anyone who follows the setup instructions while
    continuing to work for anyone whose .env predates the template change — the
    worst shape of environment bug, because the people who can reproduce it are
    exactly the new contributors least able to diagnose it.
    """

    def test_blank_chain_ids_are_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fails against dropping the mode="before" coercion (int_parsing error)."""
        for var in _SPLIT_VARS:
            monkeypatch.setenv(var, "")
        s = _settings()
        assert s.payments_chain.chain_id == _SINGLE_CHAIN_ID
        assert s.execution_chain.chain_id == _SINGLE_CHAIN_ID
        assert s.is_split_chain is False

    def test_whitespace_only_is_also_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stray space after `=` is the same mistake with a harder-to-see cause."""
        monkeypatch.setenv("ARC_PAYMENTS_CHAIN_ID", "   ")
        assert _settings().payments_chain.chain_id == _SINGLE_CHAIN_ID

    def test_a_blank_id_beside_a_real_rpc_is_still_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The coercion must not swallow a genuinely half-configured split.

        Blank id + real endpoint is the half-configured case, not the unset one.
        Fails against a coercion that returns None and lets the guard pass.
        """
        monkeypatch.setenv("ARC_PAYMENTS_CHAIN_ID", "")
        monkeypatch.setenv("ARC_PAYMENTS_RPC_URL", "https://payments.example")
        with pytest.raises(ValidationError, match="ARC_PAYMENTS_CHAIN_ID"):
            _settings()
