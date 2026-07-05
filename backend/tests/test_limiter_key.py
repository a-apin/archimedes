"""Regression tests for the rate-limiter key function (#908).

Behind nginx, slowapi's get_remote_address returned request.client.host — the
nginx peer IP — so every caller shared one bucket and per-IP limits were
effectively global. The limiter must key on the real client IP (the nginx-set
X-Real-IP header). Hermetic: no server, no Redis — the key function is called
directly on lightweight fake requests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def _req(x_real_ip: str | None = None, peer: str = "10.0.0.2") -> MagicMock:
    r = MagicMock()
    r.headers = {"x-real-ip": x_real_ip} if x_real_ip is not None else {}
    r.client = SimpleNamespace(host=peer)
    return r


def test_limiter_keys_on_real_client_ip():
    from archimedes.api.limiter import limiter
    from archimedes.services.generation_quota import client_ip

    # The limiter is wired to the shared client_ip resolver, not get_remote_address.
    assert limiter._key_func is client_ip

    # Same real client (X-Real-IP), different nginx socket peers → same bucket.
    a = _req(x_real_ip="1.2.3.4", peer="10.0.0.2")
    b = _req(x_real_ip="1.2.3.4", peer="10.0.0.9")
    assert limiter._key_func(a) == limiter._key_func(b) == "1.2.3.4"

    # Different real clients → different buckets, so a per-IP limit actually bites.
    c = _req(x_real_ip="5.6.7.8", peer="10.0.0.2")
    assert limiter._key_func(c) != limiter._key_func(a)

    # No X-Real-IP (local / non-proxied) → falls back to the socket peer.
    d = _req(x_real_ip=None, peer="127.0.0.1")
    assert limiter._key_func(d) == "127.0.0.1"
