"""Shared authenticated request fixture for pre-Better-Auth route tests."""

import time

from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session

TEST_WALLET = "0x" + "12" * 20


def auth_cookies(wallet: str = TEST_WALLET) -> dict[str, str]:
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}
