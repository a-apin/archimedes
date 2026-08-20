"""Hermetic tests for Circle DCW provisioning (audit gap G2).

Before these, ``_create_circle_wallet`` measured 1/28 statements covered,
``_find_wallet_by_ref`` 1/12, ``_encrypt_entity_secret`` 1/7 — every
subscriber's custodial wallet is minted in code that had effectively never
run under test, including the double-provision guard.

No network: the module's ``aiohttp`` reference is swapped for a URL-routing
fake session that records every POST. The entity-secret encryption is
exercised for REAL — a test RSA keypair decrypts the posted ciphertext back
to the entity secret, so the OAEP/SHA-256 path is proven, not mocked away.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from archimedes.marketplace import wallet_provisioner as wp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ENTITY_SECRET_HEX = "ab" * 32  # 32 bytes, valid hex — shape of a real entity secret


@pytest.fixture(scope="module")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return key, pem


class _FakeResp:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    """Routes requests by (method, URL fragment); records every POST payload."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.posts: list[dict] = []

    def get(self, url, **kwargs):
        return self._route("GET", url)

    def post(self, url, json=None, **kwargs):
        self.posts.append(json)
        return self._route("POST", url)

    def _route(self, method: str, url: str):
        for (m, frag), resp in self.routes.items():
            if m == method and frag in url:
                return resp
        raise AssertionError(f"unrouted request: {method} {url}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _install(monkeypatch, session: _FakeSession, pem: str | None = None):
    """Point the module's aiohttp at the fake and seed Circle creds."""
    monkeypatch.setattr(wp, "aiohttp", SimpleNamespace(ClientSession=lambda: session))
    monkeypatch.setenv("CIRCLE_API_KEY", "test-api-key")
    monkeypatch.setenv("CIRCLE_ENTITY_SECRET", ENTITY_SECRET_HEX)


def _pubkey_route(pem: str):
    return ("GET", "config/entity/publicKey"), _FakeResp(200, {"data": {"publicKey": pem}})


def _walletset_route(wallet_set_id="ws-default-1"):
    """The GET /v1/w3s/walletSets route used when MARKETPLACE_WALLET_SET_ID is unset."""
    return ("GET", "walletSets"), _FakeResp(200, {"data": {"walletSets": [{"id": wallet_set_id}]}})


def _created_route(wallet_id="w-created-1", address="0xWa11e70000000000000000000000000000000001"):
    # The real 201 body nests the created wallet(s) as a LIST under
    # data.wallets — not a single object under data.wallet.
    return (
        ("POST", "developer/wallets"),
        _FakeResp(201, {"data": {"wallets": [{"id": wallet_id, "address": address}]}}),
    )


class TestCreateCircleWallet:
    @pytest.mark.asyncio
    async def test_happy_path_creates_wallet_and_really_encrypts_the_entity_secret(self, monkeypatch, rsa_keypair):
        key, pem = rsa_keypair
        session = _FakeSession(dict([_pubkey_route(pem), _walletset_route(), _created_route()]))
        _install(monkeypatch, session)

        wallet_id, address = await wp.provision_subscriber_wallet("0xsubid")

        assert (wallet_id, address) == ("w-created-1", "0xWa11e70000000000000000000000000000000001")
        assert len(session.posts) == 1
        payload = session.posts[0]
        assert payload["blockchains"] == [wp.CIRCLE_BLOCKCHAIN]
        assert payload["walletSetId"] == "ws-default-1"
        assert {"name": "ref", "value": "sub:0xsubid"} in payload["metadata"]
        # THE load-bearing assertion: decrypt the posted ciphertext with the
        # test private key and require the original entity secret back. A
        # mutant that skips encryption, encrypts the wrong bytes, or uses the
        # wrong padding cannot produce this plaintext.
        plaintext = key.decrypt(
            base64.b64decode(payload["entitySecretCiphertext"]),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        assert plaintext == bytes.fromhex(ENTITY_SECRET_HEX)

    @pytest.mark.asyncio
    async def test_idempotency_key_is_deterministic_per_ref(self, monkeypatch, rsa_keypair):
        """The docstring's idempotency claim: a retry for the SAME ref must
        reuse the SAME key (so Circle returns the existing wallet), and a
        different ref must get a different key (so wallets never collide)."""
        _, pem = rsa_keypair
        session = _FakeSession(dict([_pubkey_route(pem), _walletset_route(), _created_route()]))
        _install(monkeypatch, session)

        await wp.provision_subscriber_wallet("0xsame")
        await wp.provision_subscriber_wallet("0xsame")
        await wp.provision_publisher_wallet("0xsame")  # different ref namespace: pub: vs sub:

        keys = [p["idempotencyKey"] for p in session.posts]
        assert keys[0] == keys[1], "same ref must reuse the same idempotency key"
        assert keys[2] != keys[0], "a different ref must never share an idempotency key"

    @pytest.mark.asyncio
    async def test_conflict_returns_the_existing_wallet_instead_of_a_duplicate(self, monkeypatch, rsa_keypair):
        """409 = the idempotency key already minted a wallet. The guard must
        surface THAT wallet (found by metadata ref), not raise and not mint."""
        _, pem = rsa_keypair
        listing = _FakeResp(
            200,
            {
                "data": {
                    "wallets": [
                        {"id": "w-other", "address": "0xother", "metadata": [{"name": "ref", "value": "sub:zzz"}]},
                        {
                            "id": "w-existing",
                            "address": "0xExisting",
                            "metadata": [{"name": "ref", "value": "sub:0xdup"}],
                        },
                    ]
                }
            },
        )
        session = _FakeSession(
            {
                ("GET", "config/entity/publicKey"): _FakeResp(200, {"data": {"publicKey": pem}}),
                ("GET", "walletSets"): _FakeResp(200, {"data": {"walletSets": [{"id": "ws-default-1"}]}}),
                ("POST", "developer/wallets"): _FakeResp(409, {"code": "conflict"}),
                ("GET", "developer/wallets"): listing,
            }
        )
        _install(monkeypatch, session)

        wallet_id, address = await wp.provision_subscriber_wallet("0xdup")

        assert (wallet_id, address) == ("w-existing", "0xExisting")
        assert len(session.posts) == 1  # exactly one creation attempt — no duplicate mint

    @pytest.mark.asyncio
    async def test_conflict_with_no_findable_wallet_raises(self, monkeypatch, rsa_keypair):
        _, pem = rsa_keypair
        session = _FakeSession(
            {
                ("GET", "config/entity/publicKey"): _FakeResp(200, {"data": {"publicKey": pem}}),
                ("GET", "walletSets"): _FakeResp(200, {"data": {"walletSets": [{"id": "ws-default-1"}]}}),
                ("POST", "developer/wallets"): _FakeResp(409, {"code": "conflict"}),
                ("GET", "developer/wallets"): _FakeResp(200, {"data": {"wallets": []}}),
            }
        )
        _install(monkeypatch, session)

        with pytest.raises(RuntimeError, match="conflict but could not find"):
            await wp.provision_subscriber_wallet("0xghost")

    @pytest.mark.asyncio
    async def test_api_error_raises_with_status(self, monkeypatch, rsa_keypair):
        _, pem = rsa_keypair
        session = _FakeSession(
            {
                ("GET", "config/entity/publicKey"): _FakeResp(200, {"data": {"publicKey": pem}}),
                ("GET", "walletSets"): _FakeResp(200, {"data": {"walletSets": [{"id": "ws-default-1"}]}}),
                ("POST", "developer/wallets"): _FakeResp(500, {"error": "boom"}),
            }
        )
        _install(monkeypatch, session)

        with pytest.raises(RuntimeError, match="500"):
            await wp.provision_subscriber_wallet("0xerr")

    @pytest.mark.asyncio
    async def test_request_shape_is_blockchains_array_with_wallet_set_id(self, monkeypatch, rsa_keypair):
        """Pins the #1325 request shape against a mocked transport. The
        pre-fix payload (singular `blockchain`, no `walletSetId`) is exactly
        what Circle 400s with 'blockchains field may not be empty' /
        'walletSetId field may not be empty' — this assertion must fail
        against that payload (verified by reverting the fix locally per the
        house guard-demo rule) and pass against the fixed one."""
        _, pem = rsa_keypair
        session = _FakeSession(dict([_pubkey_route(pem), _walletset_route("ws-shape-check"), _created_route()]))
        _install(monkeypatch, session)

        await wp.provision_subscriber_wallet("0xshape")

        payload = session.posts[0]
        assert payload["blockchains"] == [wp.CIRCLE_BLOCKCHAIN]
        assert isinstance(payload["blockchains"], list), "blockchains must be an array, not a bare string"
        assert payload["walletSetId"] == "ws-shape-check"
        assert payload["walletSetId"], "walletSetId must be non-empty"
        assert "blockchain" not in payload, "the old singular `blockchain` key must be gone"

    @pytest.mark.asyncio
    async def test_response_parses_first_entry_of_data_wallets(self, monkeypatch, rsa_keypair):
        """Pins response parsing at data.wallets[0] (a LIST), not data.wallet
        (a single object). Two wallets in the list proves index [0] is used
        deliberately, not just tolerated because len==1."""
        _, pem = rsa_keypair
        multi_wallet_route = (
            ("POST", "developer/wallets"),
            _FakeResp(
                201,
                {
                    "data": {
                        "wallets": [
                            {"id": "w-first", "address": "0xFirst00000000000000000000000000000001"},
                            {"id": "w-second", "address": "0xSecond0000000000000000000000000000002"},
                        ]
                    }
                },
            ),
        )
        session = _FakeSession(dict([_pubkey_route(pem), _walletset_route(), multi_wallet_route]))
        _install(monkeypatch, session)

        wallet_id, address = await wp.provision_subscriber_wallet("0xmulti")

        assert (wallet_id, address) == ("w-first", "0xFirst00000000000000000000000000000001")

    @pytest.mark.asyncio
    async def test_missing_credentials_fail_before_any_network(self, monkeypatch):
        def _no_session():
            raise AssertionError("no credentials — must not open a session")

        monkeypatch.setattr(wp, "aiohttp", SimpleNamespace(ClientSession=_no_session))
        monkeypatch.delenv("CIRCLE_API_KEY", raising=False)
        monkeypatch.delenv("CIRCLE_ENTITY_SECRET", raising=False)

        with pytest.raises(RuntimeError, match="credentials not configured"):
            await wp.provision_subscriber_wallet("0xnocreds")


class TestWalletSetIdResolution:
    @pytest.mark.asyncio
    async def test_wallet_set_id_env_override_skips_the_walletsets_get(self, monkeypatch, rsa_keypair):
        """MARKETPLACE_WALLET_SET_ID takes precedence and must short-circuit before any
        GET /v1/w3s/walletSets call — deliberately no walletSets route is
        registered, so if the code fell through to the GET anyway the fake
        session's unrouted-request assertion would fail this test."""
        _, pem = rsa_keypair
        session = _FakeSession(dict([_pubkey_route(pem), _created_route()]))
        _install(monkeypatch, session)
        monkeypatch.setenv("MARKETPLACE_WALLET_SET_ID", "ws-from-env")

        wallet_id, address = await wp.provision_subscriber_wallet("0xenvoverride")

        assert (wallet_id, address) == ("w-created-1", "0xWa11e70000000000000000000000000000000001")
        assert session.posts[0]["walletSetId"] == "ws-from-env"

    @pytest.mark.asyncio
    async def test_no_wallet_sets_on_account_raises_loudly_and_never_invents_one(self, monkeypatch, rsa_keypair):
        """Zero wallet sets on the account, no env override: must raise
        loudly and must NEVER fall back to inventing/guessing an ID by
        attempting the POST anyway."""
        _, pem = rsa_keypair
        session = _FakeSession(
            {
                ("GET", "config/entity/publicKey"): _FakeResp(200, {"data": {"publicKey": pem}}),
                ("GET", "walletSets"): _FakeResp(200, {"data": {"walletSets": []}}),
            }
        )
        _install(monkeypatch, session)

        with pytest.raises(RuntimeError, match="No Circle wallet sets"):
            await wp.provision_subscriber_wallet("0xnowalletsets")

        assert session.posts == [], "must never attempt wallet creation without a resolved walletSetId"

    @pytest.mark.asyncio
    async def test_no_wallet_sets_listing_failure_raises(self, monkeypatch, rsa_keypair):
        _, pem = rsa_keypair
        session = _FakeSession(
            {
                ("GET", "config/entity/publicKey"): _FakeResp(200, {"data": {"publicKey": pem}}),
                ("GET", "walletSets"): _FakeResp(503, {"error": "unavailable"}),
            }
        )
        _install(monkeypatch, session)

        with pytest.raises(RuntimeError, match="Failed to list Circle wallet sets: 503"):
            await wp.provision_subscriber_wallet("0xlistfail")

        assert session.posts == []


class TestFindWalletByRef:
    @pytest.mark.asyncio
    async def test_matches_on_metadata_ref(self):
        listing = _FakeResp(
            200,
            {
                "data": {
                    "wallets": [
                        {"id": "w-1", "address": "0xA", "metadata": [{"name": "ref", "value": "sub:target"}]},
                    ]
                }
            },
        )
        session = _FakeSession({("GET", "developer/wallets"): listing})

        found = await wp._find_wallet_by_ref(session, "key", "sub:target")

        assert found == {"id": "w-1", "address": "0xA"}

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        listing = _FakeResp(
            200,
            {"data": {"wallets": [{"id": "w-1", "address": "0xA", "metadata": [{"name": "ref", "value": "other"}]}]}},
        )
        session = _FakeSession({("GET", "developer/wallets"): listing})

        assert await wp._find_wallet_by_ref(session, "key", "sub:missing") is None

    @pytest.mark.asyncio
    async def test_non_200_listing_returns_none(self):
        session = _FakeSession({("GET", "developer/wallets"): _FakeResp(503, {})})

        assert await wp._find_wallet_by_ref(session, "key", "sub:x") is None
