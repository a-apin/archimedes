#!/usr/bin/env python3
"""Reproduction harness for issue #1632 — prod backend SIGABRTs inside
``psycopg2`` ``do_executemany`` on the paper-replay tick.

WHAT IT DRIVES
--------------
The exact prod crash path, narrowed to its smallest faithful slice:

    paper_advance_loop  (asyncio.to_thread → DEFAULT executor thread)
      └─ replay → fusion_market_data.fetch_real_panel → _fetch_one
           └─ market_data_provider.CachingMarketDataProvider.get_daily_ohlcv
                └─ read miss → inner fetch → _write_cached_ohlcv(session, …)
                     └─ session.commit()   ←  psycopg2 do_executemany  ←  ABORT

The vendor call is the only thing replaced: ``_ReplayFrameProvider`` returns a
*recorded* OHLCV frame (deterministic, offline) so the loop hammers the cache
write instead of yfinance. Everything below the provider — the real
``CachingMarketDataProvider``, the real ``_read_cached_ohlcv`` /
``_write_cached_ohlcv``, the real ``archimedes.db`` engine and its psycopg2
driver, the real ``AssetDailyBar`` ORM mapping — is the shipped code.

WHY THE CO-ACTIVITY MATTERS (the hypothesis under test)
-------------------------------------------------------
The prod image loads *three* OpenSSL crypto builds into one address space:

  * ``/lib/*/libcrypto.so.3`` + ``libssl.so.3``   — the interpreter's, used by
    ``_ssl`` and therefore by aiohttp / web3 / httpx / boto3.
  * ``psycopg2_binary.libs/libcrypto-*.so.3`` + ``libssl-*.so.3`` — bundled in
    the ``psycopg2-binary`` wheel, used by its bundled ``libpq`` for the TLS
    session to Aurora (prod ``DATABASE_URL`` carries no ``sslmode``, so libpq
    defaults to ``prefer`` and *does* negotiate TLS).
  * ``psycopg2_binary.libs/libcrypto-*.so.1.1.1k`` — OpenSSL 1.1.1, dragged in
    by the wheel's bundled krb5/ldap stack.

``psycopg2``'s own documentation warns against the binary wheel in production
for exactly this reason. So the harness deliberately runs interpreter-side TLS
churn (aiohttp client + a local TLS server, both on the interpreter's OpenSSL)
*concurrently* with the psycopg2 commit loop, on the same default executor
layout prod uses. If the abort is a dual-OpenSSL symbol clash, the co-activity
is load-bearing and this is where it shows up.

A NON-REPRO IS A RESULT. If neither variant aborts, say so — do not tune the
harness until something breaks.

USAGE
-----
    scripts/repro/issue-1632/run.sh              # full A/B, 30 min per variant
    REPRO_DURATION_S=120 scripts/repro/issue-1632/run.sh   # smoke

Knobs (all env, all optional):
    REPRO_DURATION_S      wall-clock budget per variant           (1800)
    REPRO_WRITERS         concurrent commit threads               (2)
    REPRO_BARS            rows per cache write (executemany size) (2600)
    REPRO_TLS_CLIENTS     concurrent aiohttp TLS clients          (8)
    REPRO_TICKER_MODE     rotate | fixed | mixed                  (mixed)
    REPRO_RECYCLE_EVERY   engine.dispose() every N writes, 0=off  (25)
    REPRO_TLS_URLS        comma-separated https URLs to hammer
                          instead of the built-in local TLS server
    REPRO_DISABLE_MITIGATION  1 = drive the pre-#1725 crash shape (default),
                          0 = leave the #1725/#1728 tourniquet on

Every knob must ALSO be listed in docker-compose.repro.yml's `environment:`
block, or it silently never reaches the container.
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import os
import random
import ssl
import sys
import threading
import time
import traceback
from datetime import UTC, date, datetime, timedelta

# Fatal-signal tracebacks (this is what produced prod's "Fatal Python error:
# Aborted" frame pointing at do_executemany). Must be enabled before anything
# else can abort.
faulthandler.enable()

STARTED = time.monotonic()
_STOP = threading.Event()
_COUNTS: dict[str, int] = {
    "writes": 0,
    "rows": 0,
    "tls": 0,
    "errors": 0,
    # do_executemany is the frame prod aborted in. Counted separately from
    # do_execute because they are DIFFERENT psycopg2 entry points and only one
    # of them is the crash site — see instrument_dbapi().
    "executemany": 0,
    "executemany_rows": 0,
    "execute": 0,
}
_EXECUTEMANY_STATEMENTS: dict[str, int] = {}
_COUNTS_LOCK = threading.Lock()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - STARTED:8.1f}s] {msg}", flush=True)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


DURATION_S = float(_int_env("REPRO_DURATION_S", 1800))
WRITERS = _int_env("REPRO_WRITERS", 2)
BARS = _int_env("REPRO_BARS", 2600)
TLS_CLIENTS = _int_env("REPRO_TLS_CLIENTS", 8)
TICKER_MODE = os.environ.get("REPRO_TICKER_MODE", "mixed")
RECYCLE_EVERY = _int_env("REPRO_RECYCLE_EVERY", 25)


# --------------------------------------------------------------------------
# 0. Environment fingerprint — the "how many libssl copies" evidence.
# --------------------------------------------------------------------------


def _loaded_crypto_libs() -> list[str]:
    """Distinct libssl/libcrypto mappings in this process, from /proc/self/maps."""
    found: dict[str, None] = {}
    try:
        with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.rsplit(" ", 1)
                path = parts[-1].strip()
                base = path.rsplit("/", 1)[-1]
                if base.startswith(("libssl", "libcrypto")):
                    found[path] = None
    except OSError:
        pass
    return sorted(found)


def _count_c_extensions() -> int:
    return sum(1 for m in list(sys.modules.values()) if getattr(m, "__file__", "") and str(m.__file__).endswith(".so"))


def fingerprint(tag: str) -> None:
    log(f"===== OpenSSL fingerprint ({tag}) =====")
    log(f"python            : {sys.version.split()[0]}  ({os.uname().machine})")
    log(f"ssl.OPENSSL_VERSION: {ssl.OPENSSL_VERSION}")
    try:
        import psycopg2

        log(f"psycopg2          : {psycopg2.__version__}")
        log(f"psycopg2 libpq    : {psycopg2.__libpq_version__}")
        log(f"psycopg2 module   : {psycopg2.__file__}")
        import psycopg2._psycopg as _pg

        log(f"psycopg2 _psycopg : {_pg.__file__}")
    except Exception as exc:  # pragma: no cover - diagnostic only
        log(f"psycopg2          : IMPORT FAILED {exc!r}")
    libs = _loaded_crypto_libs()
    log(f"libssl/libcrypto mappings loaded: {len(libs)}")
    for path in libs:
        log(f"    {path}")
    log(f"C extensions loaded: {_count_c_extensions()}")
    log("=" * 46)


# --------------------------------------------------------------------------
# 1. Load the prod process image (all ~230 C extensions), like uvicorn does.
# --------------------------------------------------------------------------


def load_app_modules() -> None:
    """Import what the prod backend imports, so the address space matches.

    Deliberately imports ``archimedes.main`` — the FastAPI app module whose
    transitive imports pull in web3/ckzg/greenlet/uvloop/torch/… That import
    graph is half the hypothesis: it is what puts the interpreter's OpenSSL in
    the same process as psycopg2's bundled one.
    """
    log("importing archimedes.main (prod import graph)…")
    t0 = time.monotonic()
    try:
        import archimedes.main  # noqa: F401
    except Exception as exc:
        log(f"WARNING: archimedes.main import failed ({exc!r}); continuing with a narrower graph")
        traceback.print_exc()
    # Belt and braces: the TLS-side consumers, imported explicitly so a
    # main.py refactor can never silently drop them out of the repro.
    for mod in ("aiohttp", "web3", "ssl", "sqlalchemy", "psycopg2"):
        try:
            __import__(mod)
        except Exception as exc:
            log(f"WARNING: import {mod} failed: {exc!r}")
    log(f"import graph loaded in {time.monotonic() - t0:.1f}s")


# --------------------------------------------------------------------------
# 2. The recorded OHLCV frame + a provider that serves it (no vendor calls).
# --------------------------------------------------------------------------


def recorded_frame(n_bars: int, seed: int = 1632):
    """A deterministic, realistic daily OHLCV frame — the shape
    ``fetch_ohlcv`` returns (DatetimeIndex; Open/High/Low/Close/Volume)."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(date.today()) - pd.Timedelta(days=1), periods=n_bars)
    steps = rng.normal(0.0004, 0.011, size=n_bars)
    close = 100.0 * np.exp(np.cumsum(steps))
    spread = np.abs(rng.normal(0.004, 0.002, size=n_bars)) * close
    open_ = close - rng.normal(0, 0.5, size=n_bars) * spread
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + spread,
            "Low": np.minimum(open_, close) - spread,
            "Close": close,
            "Volume": rng.integers(1_000_000, 90_000_000, size=n_bars).astype(float),
        },
        index=idx,
    )


class _ReplayFrameProvider:
    """Stands in for the yfinance/tiingo vendor: the ONLY substitution in the
    harness. Returns the recorded frame so every cache miss becomes a full
    ``_write_cached_ohlcv`` + ``commit`` — i.e. a ``do_executemany``."""

    def __init__(self, frame) -> None:
        self._frame = frame

    # The signatures keep the real MarketDataProvider parameter names (the
    # arguments are deliberately ignored — that IS the stand-in), hence the
    # ARG002 suppressions.
    def get_daily_ohlcv(self, ticker: str, start: str, end: str):  # noqa: ARG002
        return self._frame

    # Never reached by this harness (it drives get_daily_ohlcv only); present
    # so the object is a complete stand-in if the driver is extended.
    def get_daily_close_batch(self, tickers, period):  # noqa: ARG002
        return dict.fromkeys(tickers, self._frame["Close"])

    def get_intraday_quote(self, ticker):  # noqa: ARG002
        return None

    def get_intraday_quotes_batch(self, tickers):  # noqa: ARG002
        return {}

    def get_series(self, ticker, period, interval):  # noqa: ARG002
        return self._frame["Close"]


# --------------------------------------------------------------------------
# 3. The commit loop — runs on the DEFAULT executor, as paper_advance_loop does.
# --------------------------------------------------------------------------


def instrument_dbapi() -> None:
    """Count ``do_executemany`` vs ``do_execute`` calls.

    THE GUARD ON THIS WHOLE HARNESS. Prod aborted inside psycopg2's
    ``do_executemany``; a run that only ever reaches ``do_execute`` is not
    exercising the crash frame, however busy it looks. SQLAlchemy 2.0 routes
    ORM INSERTs that need the generated PK back through *insertmanyvalues* —
    one rendered ``INSERT … VALUES (…), (…) RETURNING id`` via ``do_execute``,
    NOT ``executemany`` — so an insert-only loop can score thousands of
    "writes" and zero calls to the frame under test. Bulk UPDATE-by-PK is what
    still goes through ``do_executemany``, which is why REPRO_TICKER_MODE
    defaults to `mixed` (it produces both).

    If the executemany counter stays at zero, the run is a non-result, and the
    harness says so loudly rather than reporting a reassuring "no crash".
    """
    # Patch the CONCRETE dialect class, not DefaultDialect: PGDialect_psycopg2
    # overrides do_executemany (that is where its execute_values /
    # execute_batch modes live), so a DefaultDialect patch never fires and the
    # counter reads a permanent, silent zero.
    from archimedes.db import engine

    dialect_cls = type(engine.dialect)
    log(f"instrumenting dialect class {dialect_cls.__module__}.{dialect_cls.__name__}")

    orig_many = dialect_cls.do_executemany
    orig_one = dialect_cls.do_execute

    def counting_executemany(self, cursor, statement, parameters, context=None):
        with _COUNTS_LOCK:
            _COUNTS["executemany"] += 1
            with contextlib.suppress(TypeError):  # parameters is not always sized
                _COUNTS["executemany_rows"] += len(parameters)
            key = " ".join(str(statement).split()[:4])
            _EXECUTEMANY_STATEMENTS[key] = _EXECUTEMANY_STATEMENTS.get(key, 0) + 1
        return orig_many(self, cursor, statement, parameters, context)

    def counting_execute(self, cursor, statement, parameters, context=None):
        with _COUNTS_LOCK:
            _COUNTS["execute"] += 1
        return orig_one(self, cursor, statement, parameters, context)

    dialect_cls.do_executemany = counting_executemany  # type: ignore[method-assign]
    dialect_cls.do_execute = counting_execute  # type: ignore[method-assign]
    log("instrumented do_executemany / do_execute")


def configure_mitigation() -> None:
    """Turn the #1725/#1728 tourniquet off (default) or leave it on.

    That mitigation landed INSIDE the function this harness drives: it flushes
    ``_write_cached_ohlcv`` every ``_OHLCV_WRITE_CHUNK_ROWS`` (500) rows and
    wraps the write+commit in the process-wide ``_OHLCV_CACHE_WRITE_LOCK``.
    Both are deliberately aimed at the batch size and the concurrency this
    harness exists to stress, so running against them measures the tourniquet,
    not the wound.

    Default is therefore OFF — the harness reproduces the shape the fleet was
    actually crashing in. ``REPRO_DISABLE_MITIGATION=0`` leaves it on, which is
    how you check whether the tourniquet holds.

    Both attributes are looked up defensively: this file must keep working
    after the mitigation is deleted, which is the whole point of finding the
    real cause.
    """
    from archimedes.services import market_data_provider as mdp

    raw = os.environ.get("REPRO_DISABLE_MITIGATION", "1").strip().lower()
    disable = raw not in ("0", "false", "no")

    has_chunk = hasattr(mdp, "_OHLCV_WRITE_CHUNK_ROWS")
    has_lock = hasattr(mdp, "_OHLCV_CACHE_WRITE_LOCK")
    if not (has_chunk or has_lock):
        log("mitigation: absent from this checkout (pre-#1725, or already reverted) — nothing to configure")
        return
    if not disable:
        log(f"mitigation: LEFT ON — flush every {getattr(mdp, '_OHLCV_WRITE_CHUNK_ROWS', '?')} rows, write lock held")
        return

    if has_chunk:
        # Larger than any frame, so `pending >= chunk` never trips and the whole
        # frame goes to the server as ONE executemany, as it did pre-#1725.
        mdp._OHLCV_WRITE_CHUNK_ROWS = 10**9  # noqa: SLF001 — reaching into the mitigation is the point
    if has_lock:
        # nullcontext is reentrant and thread-safe, so one instance serves every
        # writer thread.
        mdp._OHLCV_CACHE_WRITE_LOCK = contextlib.nullcontext()  # noqa: SLF001 — same
    log(
        f"mitigation: DISABLED for this run (chunking={'off' if has_chunk else 'n/a'}, "
        f"write lock={'off' if has_lock else 'n/a'}) — driving the pre-#1725 crash shape"
    )


def ensure_schema() -> None:
    from archimedes.db import engine
    from archimedes.models.asset_daily_bars import AssetDailyBar

    AssetDailyBar.__table__.create(bind=engine, checkfirst=True)
    log(f"schema ready on {engine.url.render_as_string(hide_password=True)}")

    # PROOF that psycopg2's bundled OpenSSL is actually doing TLS on this
    # connection — the whole hypothesis rests on it. Prod's DATABASE_URL
    # carries no sslmode either, so libpq's default `prefer` is what decides;
    # if this prints ssl=False the harness is NOT reproducing prod's setup.
    from sqlalchemy import text

    with engine.connect() as conn:
        try:
            row = conn.execute(
                text("SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            ).first()
            log(f"libpq connection TLS: ssl={row[0]} version={row[1]} cipher={row[2]}")
            if not row[0]:
                log(
                    "WARNING: connection is PLAINTEXT — the bundled libssl is not exercised; fidelity to prod is broken"
                )
        except Exception as exc:
            log(f"WARNING: could not read pg_stat_ssl ({exc!r})")


def prune(keep_symbol_prefix: str) -> None:
    """Keep the table bounded so the repro is memory/disk stable over 30 min."""
    from archimedes.db import get_session
    from archimedes.models.asset_daily_bars import AssetDailyBar
    from sqlalchemy import delete

    session = get_session()
    try:
        session.execute(delete(AssetDailyBar).where(AssetDailyBar.symbol.like(f"{keep_symbol_prefix}%")))
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def writer_thread(worker: int, frame) -> None:
    """One paper-replay-tick equivalent, on repeat."""
    from archimedes.db import engine
    from archimedes.services.market_data_provider import CachingMarketDataProvider

    provider = CachingMarketDataProvider(_ReplayFrameProvider(frame), source_name="yfinance")
    start = (date.today() - timedelta(days=int(BARS * 1.5))).isoformat()
    end = date.today().isoformat()
    prefix = f"RPRO{worker}_"
    i = 0
    while not _STOP.is_set():
        i += 1
        if TICKER_MODE == "fixed":
            ticker = f"{prefix}FIX"
        elif TICKER_MODE == "rotate":
            ticker = f"{prefix}{i}"
        else:  # mixed: alternate fresh-insert executemany and update executemany
            ticker = f"{prefix}FIX" if i % 2 else f"{prefix}{i}"

        try:
            # THE CRASH PATH. read miss → provider fetch → _write_cached_ohlcv
            # → session.commit() → psycopg2 do_executemany.
            out = provider.get_daily_ohlcv(ticker, start, end)
            with _COUNTS_LOCK:
                _COUNTS["writes"] += 1
                _COUNTS["rows"] += 0 if out is None else len(out)
        except Exception as exc:
            with _COUNTS_LOCK:
                _COUNTS["errors"] += 1
            log(f"writer{worker}: iteration {i} raised {type(exc).__name__}: {exc}")

        if RECYCLE_EVERY and i % RECYCLE_EVERY == 0:
            # Force fresh libpq TLS handshakes (prod gets these from pool
            # recycle / Aurora idle timeouts). Handshakes are where the
            # bundled OpenSSL does its heaviest work.
            engine.dispose()
            prune(prefix)
            log(f"writer{worker}: {i} iterations, engine recycled")


# --------------------------------------------------------------------------
# 4. Interpreter-side TLS churn, concurrent with the commits.
# --------------------------------------------------------------------------


def start_local_tls_server() -> str:
    """A loopback HTTPS server on the interpreter's OpenSSL.

    Used instead of the public internet so the harness is deterministic and
    works offline; ``REPRO_TLS_URLS`` overrides it when hammering real hosts
    is wanted.
    """
    import http.server
    import tempfile

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .sign(key, hashes.SHA256())
    )
    tmp = tempfile.mkdtemp(prefix="repro1632-")
    pem = os.path.join(tmp, "server.pem")
    with open(pem, "wb") as fh:
        fh.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
            )
        )
        fh.write(cert.public_bytes(serialization.Encoding.PEM))

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # PROTOCOL_TLS_SERVER alone still permits TLS 1.0/1.1 (CodeQL
    # py/insecure-protocol). Pinning the floor is also the more faithful
    # setting: the libpq side of this harness negotiates TLS 1.3 against
    # postgres, so a 1.0-capable loopback server would be exercising OpenSSL
    # paths prod never touches.
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(pem)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True, name="repro-tls-server").start()
    url = f"https://127.0.0.1:{srv.server_address[1]}/ping"
    log(f"local TLS server up at {url}")
    return url


async def tls_client(url: str, n: int) -> None:
    """Hammer TLS on the interpreter's OpenSSL: new SSLContext + new session
    every round, so context creation/teardown (not just record I/O) churns."""
    import aiohttp

    while not _STOP.is_set():
        ctx = ssl.create_default_context()
        # Verification off ONLY because the peer is this process's own
        # throwaway self-signed loopback server (start_local_tls_server), which
        # exists so the harness needs no network. The handshake, the cipher
        # negotiation and the OpenSSL state churn under test are unaffected by
        # skipping chain validation. Point REPRO_TLS_URLS at a real host and
        # this becomes a genuine trust bypass — do not copy this pattern into
        # anything that talks to a peer it did not create.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            connector = aiohttp.TCPConnector(ssl=ctx, limit=4, force_close=True)
            async with aiohttp.ClientSession(connector=connector) as sess:
                for _ in range(20):
                    if _STOP.is_set():
                        break
                    async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        await resp.read()
                    with _COUNTS_LOCK:
                        _COUNTS["tls"] += 1
        except Exception as exc:
            with _COUNTS_LOCK:
                _COUNTS["errors"] += 1
            if n == 0:
                log(f"tls client: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.5)
        await asyncio.sleep(0)


async def heartbeat() -> None:
    last = 0.0
    while not _STOP.is_set():
        await asyncio.sleep(15)
        with _COUNTS_LOCK:
            snap = dict(_COUNTS)
        log(
            f"alive: writes={snap['writes']} rows={snap['rows']} "
            f"executemany={snap['executemany']} (rows={snap['executemany_rows']}) "
            f"execute={snap['execute']} tls={snap['tls']} errors={snap['errors']} "
            f"(+{snap['writes'] - last:.0f} writes since last)"
        )
        if snap["writes"] >= 4 and snap["executemany"] == 0:
            log(
                "!!! do_executemany has NOT been called — this run is NOT exercising the "
                "#1632 crash frame. Treat any 'no crash' as a non-result."
            )
        last = snap["writes"]


async def main_async() -> int:
    url = os.environ.get("REPRO_TLS_URLS")
    urls = [u.strip() for u in url.split(",") if u.strip()] if url else [start_local_tls_server()]

    frame = recorded_frame(BARS)
    log(f"recorded frame: {len(frame)} bars {frame.index[0].date()} → {frame.index[-1].date()}")
    configure_mitigation()
    instrument_dbapi()
    ensure_schema()

    loop = asyncio.get_running_loop()
    # asyncio.to_thread → the DEFAULT executor. This is precisely how
    # paper_advance_loop runs advance_all(), and it is the layout under test:
    # psycopg2 committing on a worker thread while the event loop does TLS.
    writers = [loop.run_in_executor(None, writer_thread, w, frame) for w in range(WRITERS)]
    clients = [asyncio.create_task(tls_client(urls[i % len(urls)], i)) for i in range(TLS_CLIENTS)]
    beat = asyncio.create_task(heartbeat())

    log(f"running for {DURATION_S:.0f}s: {WRITERS} writer thread(s), {TLS_CLIENTS} TLS client(s), {BARS} bars/write")
    try:
        await asyncio.sleep(DURATION_S)
    finally:
        _STOP.set()
        beat.cancel()
        for c in clients:
            c.cancel()
        await asyncio.gather(*clients, beat, return_exceptions=True)
        await asyncio.gather(*writers, return_exceptions=True)

    fingerprint("end of run")
    with _COUNTS_LOCK:
        snap = dict(_COUNTS)
        stmts = dict(_EXECUTEMANY_STATEMENTS)
    log(
        f"SURVIVED {DURATION_S:.0f}s — writes={snap['writes']} rows={snap['rows']} "
        f"tls={snap['tls']} errors={snap['errors']}"
    )
    # Mean batch size is the observable that PROVES which shape ran: the
    # #1725 mitigation caps a flush at 500 rows, the pre-#1725 path sends the
    # whole frame (REPRO_BARS) in one go. If this prints ~500 when the log
    # claims the mitigation is disabled, the knob did not take.
    mean_rows = (snap["executemany_rows"] / snap["executemany"]) if snap["executemany"] else 0
    log(
        f"do_executemany calls: {snap['executemany']} (param rows: {snap['executemany_rows']}, "
        f"mean {mean_rows:.0f} rows/call)"
    )
    for stmt, n in sorted(stmts.items(), key=lambda kv: -kv[1]):
        log(f"    {n:>7} x  {stmt}")
    log(f"do_execute calls    : {snap['execute']}")
    if snap["executemany"] == 0:
        log("VERDICT: NON-RESULT — do_executemany was never reached; the #1632 frame was not exercised")
        return 3
    log("VERDICT: no abort in this variant/run")
    return 0


def main() -> int:
    random.seed(1632)
    fingerprint("before app import")
    load_app_modules()
    fingerprint("after app import")
    try:
        import uvloop  # prod runs uvicorn[standard] → uvloop

        uvloop.install()
        log("event loop: uvloop (matches prod uvicorn[standard])")
    except Exception:
        log("event loop: asyncio default (uvloop unavailable)")
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
