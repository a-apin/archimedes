# Repro rig — issue #1632 (backend SIGABRTs in `psycopg2` `do_executemany`)

A local, prod-shaped harness for the crash where the production backend dies
with a bare `Fatal Python error: Aborted` — no glibc message, no C++
`terminate`, no OpenSSL error string — inside `psycopg2`'s `do_executemany`
during the OHLCV cache-write commit on the paper-replay tick.

The flag added in #1725 is the tourniquet. This directory is the diagnosis: it
drives the exact crash path in a loop and A/Bs the leading mechanism.

## The hypothesis under test

The shipped image installs `psycopg2-binary` (`backend/requirements-base.txt`),
the wheel flavour that bundles its own OpenSSL — the one
[psycopg2's own documentation warns against for production][psycopg2-docs] for
precisely this reason. The result is **three different OpenSSL builds in one
address space**:

Measured by `openssl_inventory.py` against the image built from
`backend/Dockerfile` (2026-09-01, `linux/arm64`) — **5 mappings, 2 distinct
OpenSSL builds**:

| library | build | who uses it |
| --- | --- | --- |
| `/usr/lib/*/libssl.so.3` + `libcrypto.so.3` | OpenSSL 3.5.6 | the interpreter's `_ssl` → aiohttp, web3, httpx, boto3 |
| `psycopg2_binary.libs/libssl-ad00b19a.so.3` + `libcrypto-a90fc9c6.so.3` | OpenSSL 3.5.6 — *a separate build with separate global state* | the wheel's bundled `libpq` → the TLS session to Aurora |
| `psycopg2_binary.libs/libcrypto-6aa7cfbd.so.1.1.1k` | **OpenSSL 1.1.1k FIPS (2021)** | the wheel's bundled krb5 / ldap stack |

The same tool against the source-built variant reports **2 mappings, 1 build** —
the exposure is gone, which is what makes it a usable A/B control.

Prod's `DATABASE_URL` (`infra/outputs.tf`) carries no `sslmode`, so libpq
defaults to `prefer` and *does* negotiate TLS against Aurora — the bundled
OpenSSL is live, not merely resident.

That is a hypothesis to confirm or kill, not a conclusion. Run the A/B.

[psycopg2-docs]: https://www.psycopg.org/docs/install.html#psycopg-vs-psycopg-binary

## What the harness drives

The narrowest slice that still contains the whole prod failure path:

```
paper_advance_loop            asyncio.to_thread → DEFAULT executor thread
  └─ replay → fetch_real_panel → _fetch_one
       └─ CachingMarketDataProvider.get_daily_ohlcv       ← real, shipped code
            └─ read miss → inner fetch → _write_cached_ohlcv
                 └─ session.commit()  →  psycopg2 do_executemany  →  ABORT
```

The **only** substitution is the vendor: `_ReplayFrameProvider` returns a
recorded 2 600-bar OHLCV frame instead of calling yfinance, so the loop is
deterministic, offline, and spends its time on the cache write rather than on
HTTP. `CachingMarketDataProvider`, `_read_cached_ohlcv`, `_write_cached_ohlcv`,
`archimedes.db`'s engine, and the `AssetDailyBar` mapping are all the shipped
implementations.

### The write that actually reaches `do_executemany` is the UPDATE

Measured, not assumed — the harness counts `do_executemany` and `do_execute`
separately and prints the statements it saw:

| `_write_cached_ohlcv` branch | what SQLAlchemy 2.0 emits | psycopg2 entry point |
| --- | --- | --- |
| new rows (`session.add`) | one `INSERT … VALUES (…),(…) RETURNING id` via *insertmanyvalues* | `do_execute` |
| existing rows (attribute set) | `UPDATE asset_daily_bars SET … WHERE id = %(id)s` batched by PK | **`do_executemany`** |

So an insert-only loop scores thousands of cache writes and **zero** calls to
the frame prod aborted in. Measured on a 40 s run: `REPRO_TICKER_MODE=rotate`
(all-INSERT) → `do_executemany: 0`; the default `mixed` → 41 calls, 106 600
param rows, every one of them `UPDATE asset_daily_bars`.

That narrows the prod crash: it happens re-writing a **warm** cache entry, not
priming a cold one. The harness refuses to report "no crash" from a run whose
`do_executemany` counter is zero — it exits 3 and calls the run a NON-RESULT.

Three further things are deliberately faithful to prod, because the mechanism
may depend on all of them:

1. **The process image.** The harness runs inside the image built from
   `backend/Dockerfile` — never bare-metal. The bundled libssl only exists
   inside the pip wheel, and `import archimedes.main` loads the same ~286 C
   extensions (ckzg, greenlet, uvloop, torch, …) prod loads.
2. **TLS on the DB connection.** The repo's `docker-compose.yml` `localdb`
   postgres runs `ssl=off`, which would leave the bundled libssl untouched. The
   compose file here is otherwise the same service (`postgres:18-alpine`,
   matching prod Aurora 18.3) with TLS switched on, and the harness prints
   `pg_stat_ssl` at startup so a silent plaintext regression is visible.
3. **Concurrent interpreter-side TLS.** aiohttp clients hammer a loopback TLS
   server (new `SSLContext` + new session every round) on the event loop while
   the writer threads commit — the co-activity that would make two OpenSSL
   copies collide. `uvloop` is installed, as `uvicorn[standard]` does in prod.

## Running it

```bash
scripts/repro/issue-1632/run.sh              # full A/B, 30 min per variant
scripts/repro/issue-1632/run.sh binary       # prod flavour only
scripts/repro/issue-1632/run.sh source       # psycopg2-from-source only
REPRO_DURATION_S=120 scripts/repro/issue-1632/run.sh   # smoke test
```

Requires Docker. First run builds the ~2.7 GB backend image (cached after).
Logs and per-variant verdicts land in `.logs/` (gitignored); the self-signed
postgres cert lands in `.certs/` (gitignored).

Standalone evidence, no database needed:

```bash
docker run --rm -v "$PWD/scripts/repro/issue-1632:/repro:ro" \
  archimedes-repro1632:binary python /repro/openssl_inventory.py
```

### The A/B

`Dockerfile.psycopg2-source` derives from the *already-built* prod image and
swaps only the psycopg2 flavour — rebuilding it from source against the system
`libpq`/`libssl`. torch, web3, aiohttp and every other layer stay byte-identical
between the two halves, so a difference in outcome is attributable to psycopg2
and nothing else. Each variant gets a virgin postgres data directory
(`down -v` between runs), so the halves cannot contaminate each other.

**Note for the fix PR:** compiling psycopg2 in `python:3.12-slim` needs
`libc6-dev` on top of `gcc libpq-dev`. `backend/Dockerfile`'s builder stage
installs only `gcc libpq-dev` today and gets away with it because
`psycopg2-binary` is a prebuilt wheel that never compiles. Switching the
requirement without adding `libc6-dev` fails the image build with
`Python.h:23: fatal error: stdlib.h: No such file or directory`.

## Knobs

| env | default | meaning |
| --- | --- | --- |
| `REPRO_DURATION_S` | `1800` | wall-clock budget per variant |
| `REPRO_WRITERS` | `2` | concurrent commit threads on the default executor |
| `REPRO_BARS` | `2600` | rows per cache write (the `executemany` batch size) |
| `REPRO_TLS_CLIENTS` | `8` | concurrent aiohttp TLS clients |
| `REPRO_TICKER_MODE` | `mixed` | `rotate` = all-INSERT batches, `fixed` = all-UPDATE, `mixed` = both |
| `REPRO_RECYCLE_EVERY` | `25` | `engine.dispose()` every N writes (forces fresh libpq TLS handshakes); `0` disables |
| `REPRO_TLS_URLS` | *(local server)* | comma-separated HTTPS URLs to hammer instead of the loopback server |
| `REPRO_DISABLE_MITIGATION` | `1` | `1` drives the pre-#1725 crash shape; `0` leaves the tourniquet on |

Every knob must also appear in `docker-compose.repro.yml`'s `environment:`
block — one set on the host but missing there silently never reaches the
container, and the run reports the default while looking like it honoured you.

### The #1725 tourniquet, and why it is off by default

The mitigation (#1725/#1728) landed *inside* the function this harness drives:
`_write_cached_ohlcv` now flushes every `_OHLCV_WRITE_CHUNK_ROWS` (500) rows,
and `get_daily_ohlcv` wraps the write+commit in a process-wide
`_OHLCV_CACHE_WRITE_LOCK`. Both target exactly the batch size and the
concurrency the harness exists to stress, so running against them measures the
tourniquet rather than the wound. `configure_mitigation()` therefore neuters
both by default and says so in the log.

Verified in both directions on a 40 s run — mean `executemany` batch size is
the observable:

| | log line | mean rows / `do_executemany` |
| --- | --- | --- |
| `REPRO_DISABLE_MITIGATION=1` (default) | `mitigation: DISABLED … pre-#1725 crash shape` | **2600** (= `REPRO_BARS`, one batch per frame) |
| `REPRO_DISABLE_MITIGATION=0` | `mitigation: LEFT ON — flush every 500 rows` | **433** (under the 500 cap) |

Both lookups are `hasattr`-guarded, so this file keeps working once the
mitigation is deleted — which is the point of finding the real cause.

`MARKET_DATA_CACHE_TTL_HOURS=0` is set in the compose file so every read is a
freshness miss and every iteration therefore reaches the commit.

## Reading the result

`run.sh` prints a per-variant verdict: exit code (134 = SIGABRT, 139 = SIGSEGV,
3 = non-result), elapsed vs budget, cache-write count, **`do_executemany` call
count**, libssl copies loaded, and the `Fatal Python error` block if one
appeared. Check the `do_executemany` line first: if it is 0, nothing else in
the verdict means anything.

### First run — 2026-09-01, `linux/arm64`

Run at base `e43abe9f`, i.e. **before** the #1725/#1728 mitigation landed on
`main`. On this branch the default `REPRO_DISABLE_MITIGATION=1` reproduces that
same shape — confirmed by the mean batch size below matching `REPRO_BARS`
exactly (2600 rows per `do_executemany`, one batch per frame).

| | A: `psycopg2-binary` (prod) | B: psycopg2 from source |
| --- | --- | --- |
| wall clock | 1823 s of 1800 s | 1815 s of 1800 s |
| cache writes | 3 664 | 4 794 |
| `do_executemany` | **1 685** (4 381 000 param rows) | **2 206** (5 735 600 param rows) |
| statement | `UPDATE asset_daily_bars` ×1685 | `UPDATE asset_daily_bars` ×2206 |
| `do_execute` | 13 415 | 17 546 |
| aiohttp TLS requests | 290 051 | 311 955 |
| libpq connection | `ssl=True`, TLSv1.3, `TLS_AES_256_GCM_SHA384` | same |
| libssl/libcrypto mappings | **5** (2 distinct OpenSSL builds) | **2** (1 build) |
| C extensions | 287 | 287 |
| errors | 0 | 0 |
| **aborted?** | **no** | **no** |

**Neither variant reproduced the crash.** The dual-OpenSSL condition is
confirmed *present* in the prod image, but this harness did not show it to be
*sufficient*: 1 685 calls through the exact `do_executemany` frame, on a TLS
libpq connection, concurrent with 290 k interpreter-side TLS requests, did not
abort. The hypothesis is neither confirmed nor killed — what is killed is the
idea that the co-activity alone is enough on arm64 against a local postgres.
See the fidelity gaps below for what to vary next.

**A non-repro is a result.** If neither variant aborts inside its budget, report
that plainly along with what was varied — do not tune the harness until
something breaks. Known fidelity gaps to state alongside any non-repro:

- **Architecture.** This runs `linux/arm64` (Apple silicon); prod Fargate is
  `linux/amd64`. The wheel bundles OpenSSL on both, but the binaries differ.
- **Server.** Local `postgres:18-alpine` vs Aurora 18.3 behind a VPC network —
  different TLS peer, different latency, different idle-timeout behaviour.
- **Load shape.** No FastAPI request traffic, no LLM calls, no on-chain RPC.
- **The B image is not a pure one-variable swap.** Its `apt-get update &&
  install` layer also moves the system OpenSSL 3.5.6 → 3.5.7 and libpq
  170009 → 170011. Both are patch-level bumps and neither is the psycopg2
  flavour, but a difference in outcome between the halves would need this ruled
  out before being attributed to the flavour alone.
