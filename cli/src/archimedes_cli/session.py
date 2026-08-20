"""Session cache for the Better Auth cookie ``archimedes login`` obtains.

Stored at ``~/.config/archimedes/session.json``, mode ``600`` — this file holds a live
session credential, so it gets the same treatment any other credential store in this repo
does (compare ``.env`` staying gitignored, ``~/.arc-canteen/`` team credentials). The value
cached here is opaque to the CLI: it is whatever cookie(s) ``POST /api/auth/sign-in/email``
sets, forwarded verbatim on later requests. The CLI never parses or validates the cookie
itself — same division of responsibility as the server side, which also never parses it and
just forwards it to Better Auth's ``/api/auth/get-session``
(``backend/archimedes/api/account_auth.py``, ``_fetch_session``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SESSION_COOKIE_NAME = "better-auth.session_token"
"""The cookie Better Auth issues on sign-in, matching the fragment
``backend/archimedes/api/account_auth.py``'s ``_SESSION_COOKIE_FRAGMENT`` looks for
(``"better-auth.session_token="``)."""


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


__all__ = ["SESSION_COOKIE_NAME", "load_session", "save_session", "session_path"]
