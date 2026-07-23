"""Tests for CircleSigner — Circle Developer-Controlled Wallet signing (#738).

Target: backend/archimedes/chain/circle_signer.py
The signer encrypts the entity secret with Circle's RSA public key, submits a
contract execution, and polls until a terminal state. It holds funds-adjacent
authority (it is the wallet that signs vault rebalance / ownership txs), so the
configured/unconfigured gate, the submit path, and the poll loop must all be
exercised.

Hermetic: the aiohttp HTTP boundary is mocked; the RSA encrypt helper is mocked
where a real key would otherwise be needed. No network, no Circle, no Arc RPC.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain.circle_signer import CircleSigner, _encrypt_entity_secret

# ── Helpers ───────────────────────────────────────────────────


def _mock_session() -> MagicMock:
    """An aiohttp.ClientSession whose `get`/`post` return async context managers.

    Each call to `_set_get`/`_set_post` installs the response object the next
    `async with session.get(...)` / `session.post(...)` should yield.
    """
    session = MagicMock()

    def _cm(resp):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    session._cm = _cm
    return session


def _session_context(session: MagicMock) -> MagicMock:
    """Wrap a session in the `async with aiohttp.ClientSession() as s` CM."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _resp(status: int, body: dict) -> MagicMock:
    resp = MagicMock(status=status)
    resp.json = AsyncMock(return_value=body)
    return resp


@pytest.fixture
def configured(monkeypatch) -> CircleSigner:
    monkeypatch.setenv("CIRCLE_API_KEY", "test-api-key")
    monkeypatch.setenv("CIRCLE_ENTITY_SECRET", "ab" * 32)
    monkeypatch.setenv("WALLET_ID", "wallet-uuid")
    return CircleSigner()


# ── is_configured ─────────────────────────────────────────────


class TestIsConfigured:
    def test_true_when_all_creds_present(self, configured):
        assert configured.is_configured is True

    def test_false_when_any_cred_missing(self, monkeypatch):
        monkeypatch.delenv("CIRCLE_API_KEY", raising=False)
        monkeypatch.delenv("CIRCLE_ENTITY_SECRET", raising=False)
        monkeypatch.delenv("WALLET_ID", raising=False)
        assert CircleSigner().is_configured is False


# ── _get_public_key ───────────────────────────────────────────


class TestGetPublicKey:
    async def test_fetches_and_caches(self, configured):
        session = _mock_session()
        resp = _resp(200, {"data": {"publicKey": "PEM-DATA"}})
        session.get = MagicMock(return_value=session._cm(resp))

        key = await configured._get_public_key(session)
        assert key == "PEM-DATA"
        # Second call uses the cache — no second HTTP get.
        session.get.reset_mock()
        key2 = await configured._get_public_key(session)
        assert key2 == "PEM-DATA"
        session.get.assert_not_called()

    async def test_non_200_returns_none(self, configured):
        session = _mock_session()
        session.get = MagicMock(return_value=session._cm(_resp(403, {"error": "denied"})))
        assert await configured._get_public_key(session) is None


# ── execute_contract ──────────────────────────────────────────


class TestExecuteContract:
    async def test_raises_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("CIRCLE_API_KEY", raising=False)
        signer = CircleSigner()
        with pytest.raises(RuntimeError, match="not configured"):
            await signer.execute_contract("0xVault", "setAgent(address)", ["0xabc"])

    async def test_happy_path_submits_then_polls_to_complete(self, configured):
        session = _mock_session()
        # 1) public key fetch, 2) POST contractExecution (201), then polling GET.
        key_resp = _resp(200, {"data": {"publicKey": "PEM"}})
        submit_resp = _resp(201, {"data": {"id": "circle-tx-1"}})
        session.get = MagicMock(return_value=session._cm(key_resp))
        session.post = MagicMock(return_value=session._cm(submit_resp))

        with (
            patch("archimedes.chain.circle_signer.aiohttp.ClientSession", return_value=_session_context(session)),
            patch("archimedes.chain.circle_signer._encrypt_entity_secret", return_value="ciphertext"),
            patch.object(configured, "_poll_transaction", AsyncMock(return_value="0xONCHAIN")),
        ):
            result = await configured.execute_contract(
                "0xVault", "setTargetAllocations(address[],uint256[])", [["0xT"], [10000]]
            )
        assert result == "0xONCHAIN"
        session.post.assert_called_once()

    async def test_public_key_failure_raises(self, configured):
        session = _mock_session()
        session.get = MagicMock(return_value=session._cm(_resp(500, {})))
        with (
            patch("archimedes.chain.circle_signer.aiohttp.ClientSession", return_value=_session_context(session)),
            patch("archimedes.chain.circle_signer._encrypt_entity_secret", return_value="ciphertext"),
            pytest.raises(RuntimeError, match="public key"),
        ):
            await configured.execute_contract("0xVault", "setAgent(address)", ["0xabc"])

    async def test_non_201_submit_raises(self, configured):
        session = _mock_session()
        session.get = MagicMock(return_value=session._cm(_resp(200, {"data": {"publicKey": "PEM"}})))
        session.post = MagicMock(return_value=session._cm(_resp(400, {"error": "bad"})))
        with (
            patch("archimedes.chain.circle_signer.aiohttp.ClientSession", return_value=_session_context(session)),
            patch("archimedes.chain.circle_signer._encrypt_entity_secret", return_value="ciphertext"),
            pytest.raises(RuntimeError, match="contract execution failed"),
        ):
            await configured.execute_contract("0xVault", "setAgent(address)", ["0xabc"])


# ── _poll_transaction ─────────────────────────────────────────


class TestPollTransaction:
    async def test_returns_tx_hash_on_complete(self, configured):
        session = _mock_session()
        body = {"data": {"transactions": [{"id": "tx-1", "state": "COMPLETE", "txHash": "0xHASH"}]}}
        session.get = MagicMock(return_value=session._cm(_resp(200, body)))
        result = await configured._poll_transaction(session, "tx-1")
        assert result == "0xHASH"

    async def test_raises_on_failed_terminal_state(self, configured):
        session = _mock_session()
        body = {"data": {"transactions": [{"id": "tx-1", "state": "FAILED", "txHash": ""}]}}
        session.get = MagicMock(return_value=session._cm(_resp(200, body)))
        with pytest.raises(RuntimeError, match="ended in FAILED"):
            await configured._poll_transaction(session, "tx-1")

    async def test_times_out_after_max_polls(self, configured):
        session = _mock_session()
        # Tx never reaches terminal state → loop exhausts and raises.
        body = {"data": {"transactions": [{"id": "tx-1", "state": "PROCESSING", "txHash": ""}]}}
        session.get = MagicMock(return_value=session._cm(_resp(200, body)))
        with (
            patch("archimedes.chain.circle_signer._MAX_POLLS", 2),
            patch("archimedes.chain.circle_signer.asyncio.sleep", AsyncMock()),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            await configured._poll_transaction(session, "tx-1")

    async def test_poll_query_scoped_to_wallet(self, configured):
        """The poll GET must filter by walletIds so the target tx can't fall off
        an unfiltered global page (#941).

        Without the walletIds filter, Circle's GET /transactions returns the
        newest txs across every wallet capped at pageSize; with >50 in flight
        the real tx drops off the window and polling false-times-out on a tx
        that actually completed. This asserts the filter is on the request URL.
        """
        session = _mock_session()
        body = {"data": {"transactions": [{"id": "tx-1", "state": "COMPLETE", "txHash": "0xHASH"}]}}
        session.get = MagicMock(return_value=session._cm(_resp(200, body)))

        result = await configured._poll_transaction(session, "tx-1")

        assert result == "0xHASH"
        called_url = session.get.call_args.args[0]
        assert "walletIds=wallet-uuid" in called_url


# ── sign_and_broadcast ────────────────────────────────────────


class TestSignAndBroadcast:
    async def test_raises_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("CIRCLE_API_KEY", raising=False)
        signer = CircleSigner()
        with pytest.raises(RuntimeError, match="not configured"):
            await signer.sign_and_broadcast({"to": "0x", "value": 0})

    async def test_signs_and_broadcasts_via_arc_rpc(self, configured):
        session = _mock_session()
        sign_resp = _resp(201, {"data": {"signedTransaction": "0xabcd", "txHash": ""}})
        session.post = MagicMock(return_value=session._cm(sign_resp))

        mock_chain = MagicMock()
        sent_hash = MagicMock()
        sent_hash.hex = MagicMock(return_value="0xBROADCAST")
        mock_chain.w3.eth.send_raw_transaction = AsyncMock(return_value=sent_hash)

        with (
            patch("archimedes.chain.circle_signer.aiohttp.ClientSession", return_value=_session_context(session)),
            patch("archimedes.chain.client.chain_client", mock_chain),
        ):
            result = await configured.sign_and_broadcast({"to": "0xabc", "value": 0})
        assert result == "0xBROADCAST"
        mock_chain.w3.eth.send_raw_transaction.assert_awaited_once()

    async def test_sign_failure_raises(self, configured):
        session = _mock_session()
        session.post = MagicMock(return_value=session._cm(_resp(422, {"error": "bad tx"})))
        with (
            patch("archimedes.chain.circle_signer.aiohttp.ClientSession", return_value=_session_context(session)),
            pytest.raises(RuntimeError, match="sign failed"),
        ):
            await configured.sign_and_broadcast({"to": "0xabc", "value": 0})

    async def test_dict_transaction_is_json_encoded_not_python_repr(self, configured):
        """A dict tx_object must be sent as valid JSON (Circle's documented
        TransactionObject format — see developer-controlled-wallets.yaml),
        not Python's str() repr. A repr like "{'nonce': '0x5', ...}" (single
        quotes) is not valid JSON and is not a format Circle's sign endpoint
        accepts."""
        session = _mock_session()
        sign_resp = _resp(201, {"data": {"signedTransaction": "0xabcd", "txHash": ""}})
        session.post = MagicMock(return_value=session._cm(sign_resp))

        mock_chain = MagicMock()
        sent_hash = MagicMock()
        sent_hash.hex = MagicMock(return_value="0xBROADCAST")
        mock_chain.w3.eth.send_raw_transaction = AsyncMock(return_value=sent_hash)

        tx = {"nonce": "0x5", "to": "0xabc", "value": "0x0", "gas": "0x5208", "chainId": "0x4cef52"}

        with (
            patch("archimedes.chain.circle_signer.aiohttp.ClientSession", return_value=_session_context(session)),
            patch("archimedes.chain.client.chain_client", mock_chain),
        ):
            await configured.sign_and_broadcast(tx)

        sent_payload = session.post.call_args.kwargs["json"]
        sent_transaction = sent_payload["transaction"]

        # Must be valid JSON (json.loads must not raise) round-tripping to
        # the original dict — the old str(tx_object) repr fails this.
        assert json.loads(sent_transaction) == tx
        # Guard against a Python-repr regression directly: repr() uses
        # single-quoted keys/values, which is never valid JSON syntax.
        assert "'" not in sent_transaction


# ── _encrypt_entity_secret ────────────────────────────────────


class TestEncryptEntitySecret:
    def test_round_trips_with_real_rsa_key(self):
        """The encrypt helper must produce a base64 ciphertext that decrypts back
        to the entity secret with the matching private key — proving the OAEP
        padding + key handling is correct (no network needed)."""
        import base64

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

        secret_hex = "ab" * 32
        ciphertext_b64 = _encrypt_entity_secret(secret_hex, public_pem)
        decrypted = private_key.decrypt(
            base64.b64decode(ciphertext_b64),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        assert decrypted == bytes.fromhex(secret_hex)
