"""Session cache for the Better Auth cookie ``archimedes login`` obtains.

Stored at ``~/.config/archimedes/session.json``, mode ``600`` — this file holds a live
session credential, so it gets the same treatment any other credential store in this repo
does (compare ``.env`` staying gitignored, ``~/.arc-canteen/`` team credentials). The value
cached here is opaque to the CLI: it is whatever cookie(s) ``POST /api/auth/sign-in/email``
sets, forwarded verbatim on later requests. The CLI never parses or validates the cookie
itself — same division of responsibility as the server side, which also never parses it and
just forwards it to Better Auth's ``/api/auth/get-session``
(``backend/archimedes/api/account_auth.py``, ``_fetch_session``).

**Two names, one cookie.** Better Auth issues :data:`SECURE_SESSION_COOKIE_NAME`
(``__Secure-better-auth.session_token``) on any deploy with ``useSecureCookies: true``
(``auth/auth.js`` sets that from ``production`` — so every HTTPS deploy, including
``archimedes-arc.com``) and the bare :data:`SESSION_COOKIE_NAME` everywhere else, because
the ``__Secure-`` prefix cannot legally be set without TLS. This module and
``archimedes_mcp.credentials`` both go through :func:`pick_session_cookie` rather than a
single hardcoded name, so they work against local HTTP and production alike, and store
back exactly the name+value pair that was actually issued.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SESSION_COOKIE_NAME = "better-auth.session_token"
"""The bare cookie name Better Auth issues on sign-in over plain HTTP — matching the
fragment ``backend/archimedes/api/account_auth.py``'s ``_SESSION_COOKIE_FRAGMENT`` looks
for (``"better-auth.session_token="``, which matches this name as a substring of either
form below). This is the ONLY name a local ``docker compose`` stack can ever set: the
``__Secure-`` prefix is a browser/client-enforced rule (RFC 6265bis) forbidding it without
TLS, so local HTTP keeps using this bare name."""

SECURE_SESSION_COOKIE_NAME = f"__Secure-{SESSION_COOKIE_NAME}"
"""The name Better Auth issues instead, in production. ``auth/auth.js`` sets
``useSecureCookies: production``, which prefixes every auth cookie with ``__Secure-``
when the deploy is production — so ``archimedes-arc.com`` never sets the bare name above,
it sets this one. A client that only recognized :data:`SESSION_COOKIE_NAME` could sign in
against prod (``POST /api/auth/sign-in/email`` 200s) and then find no session cookie in
the response at all — the #1653-adjacent P0 this module fixes."""

SESSION_COOKIE_NAMES = (SECURE_SESSION_COOKIE_NAME, SESSION_COOKIE_NAME)
"""Both cookie names this CLI (and anything built on :func:`load_session`) accepts, in
preference order. Secure-prefixed first because that is the one a real deployment
actually sets; the bare name second so local HTTP keeps working. Never both at once in
practice — a given host's Better Auth config sets exactly one — but checking in this
order means whichever one shows up is picked without the caller having to know which
environment it is talking to."""


def pick_session_cookie(cookies) -> tuple[str, str] | None:
    """The session cookie in ``cookies``, as ``(name, value)`` — or ``None`` if neither
    name in :data:`SESSION_COOKIE_NAMES` is present with a non-empty string value.

    ``cookies`` is anything with a ``.get(name)`` — an ``httpx.Cookies`` (reading a
    sign-in response) or a plain ``dict`` (reading the cached session file) both work.
    Checked in :data:`SESSION_COOKIE_NAMES` order, so the ``__Secure-`` prefixed name
    wins when both are somehow present; returns the first match, never a merge of both.
    """
    for name in SESSION_COOKIE_NAMES:
        value = cookies.get(name)
        if isinstance(value, str) and value:
            return name, value
    return None


def session_path() -> Path:
    """Where the session cache lives.

    Built from ``Path.home()`` rather than hardcoded, so tests can point it at a tmp
    directory by setting ``HOME`` (``Path.home()`` reads that on POSIX) instead of
    monkeypatching this function.
    """
    return Path.home() / ".config" / "archimedes" / "session.json"


def load_session() -> dict | None:
    """The cached session, or ``None`` if there isn't a usable one.

    Never raises. A missing file, a permissions error, a corrupt JSON body, or a body
    missing its cookie jar are all just "not logged in" from the caller's point of view —
    the CLI can always fall back to telling the user to run ``archimedes login``.
    """
    path = session_path()
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    cookies = data.get("cookies")
    if not isinstance(cookies, dict) or not cookies:
        return None
    return data


def save_session(*, api_url: str, cookies: dict[str, str], email: str) -> Path:
    """Cache the session cookie(s) at :func:`session_path`, mode 600.

    Uses ``os.open`` with an explicit mode rather than ``Path.write_text`` so the file is
    never briefly world- or group-readable between creation and a follow-up ``chmod`` — and
    still ``chmod``s afterward, in case the path already existed with looser permissions
    from an older version (``O_TRUNC`` reuses an existing file's mode, it does not reset it).
    """
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"api_url": api_url, "cookies": cookies, "email": email}, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    path.chmod(0o600)
    return path


__all__ = [
    "SECURE_SESSION_COOKIE_NAME",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_NAMES",
    "load_session",
    "pick_session_cookie",
    "save_session",
    "session_path",
]
