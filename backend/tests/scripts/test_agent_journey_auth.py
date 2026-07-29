"""Canonical account auth and optional wallet-link coverage for agent harness."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_agent_journey():
    path = _REPO_ROOT / "scripts" / "agent_journey.py"
    spec = importlib.util.spec_from_file_location("agent_journey", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_journey"] = module
    spec.loader.exec_module(module)
    return module


aj = _load_agent_journey()


def _response(method: str, path: str, status: int, data: object) -> httpx.Response:
    return httpx.Response(status, json=data, request=httpx.Request(method, f"https://test{path}"))


class FakeClient:
    def __init__(self, *, gets=None, posts=None):
        self.gets = list(gets or [])
        self.posts = list(posts or [])
        self.post_calls: list[tuple[str, dict]] = []

    def get(self, path):
        assert self.gets, f"unexpected GET {path}"
        expected_path, response = self.gets.pop(0)
        assert path == expected_path
        return response

    def post(self, path, *, json):
        self.post_calls.append((path, json))
        assert self.posts, f"unexpected POST {path}"
        expected_path, response = self.posts.pop(0)
        assert path == expected_path
        return response


def test_step_auth_signs_in_with_environment_credentials(monkeypatch):
    monkeypatch.setenv("ARCHIMEDES_EMAIL", "agent@example.test")
    monkeypatch.setenv("ARCHIMEDES_PASSWORD", "correct horse battery staple")
    client = FakeClient(
        posts=[
            (
                "/api/auth/sign-in/email",
                _response("POST", "/api/auth/sign-in/email", 200, {"user": {"email": "agent@example.test"}}),
            )
        ],
        gets=[
            (
                "/api/auth/get-session",
                _response(
                    "GET",
                    "/api/auth/get-session",
                    200,
                    {"user": {"id": "u1", "email": "agent@example.test"}, "session": {"id": "s1"}},
                ),
            )
        ],
    )

    assert aj.step_auth(client, "https://test", ephemeral=False) == "agent@example.test"
    assert client.post_calls == [
        (
            "/api/auth/sign-in/email",
            {"email": "agent@example.test", "password": "correct horse battery staple"},
        )
    ]


def test_step_auth_ephemeral_creates_then_signs_in(monkeypatch):
    monkeypatch.delenv("ARCHIMEDES_EMAIL", raising=False)
    monkeypatch.delenv("ARCHIMEDES_PASSWORD", raising=False)
    client = FakeClient(
        posts=[
            ("/api/auth/sign-up/email", _response("POST", "/api/auth/sign-up/email", 200, {"user": {"id": "u1"}})),
            ("/api/auth/sign-in/email", _response("POST", "/api/auth/sign-in/email", 200, {"user": {"id": "u1"}})),
        ],
        gets=[
            (
                "/api/auth/get-session",
                _response(
                    "GET",
                    "/api/auth/get-session",
                    200,
                    {"user": {"id": "u1", "email": "ephemeral@example.test"}, "session": {"id": "s1"}},
                ),
            )
        ],
    )

    email = aj.step_auth(client, "https://test", ephemeral=True)

    assert email and email.endswith("@example.test")
    signup = client.post_calls[0][1]
    signin = client.post_calls[1][1]
    assert signup["email"] == signin["email"] == email
    assert signup["password"] == signin["password"]
    assert len(signup["password"]) >= 12


def test_step_auth_without_credentials_fails_before_request(monkeypatch):
    monkeypatch.delenv("ARCHIMEDES_EMAIL", raising=False)
    monkeypatch.delenv("ARCHIMEDES_PASSWORD", raising=False)
    client = FakeClient()

    assert aj.step_auth(client, "https://test", ephemeral=False) is None
    assert client.post_calls == []


def test_wallet_link_signs_exact_server_message(monkeypatch):
    account = Account.create()
    monkeypatch.setenv("AGENT_WALLET_KEY", account.key.hex())
    message = "server-built EIP-4361 message\nwith exact bindings"
    client = FakeClient(
        posts=[
            (
                "/api/wallets/challenge",
                _response(
                    "POST", "/api/wallets/challenge", 200, {"message": message, "expires_at": "2026-07-28T00:00:00Z"}
                ),
            ),
            (
                "/api/wallets/verify",
                _response(
                    "POST",
                    "/api/wallets/verify",
                    200,
                    {"address": account.address.lower(), "chain_id": 5042002},
                ),
            ),
        ]
    )

    assert aj.step_wallet_link(client, ephemeral=False) == account.address
    verify_body = client.post_calls[1][1]
    assert verify_body["message"] == message
    recovered = Account.recover_message(encode_defunct(text=message), signature=verify_body["signature"])
    assert recovered == account.address
