#!/usr/bin/env python3
"""Evidence tool for #1632: what OpenSSL builds does the prod process image
actually load, and which one does each consumer use?

Run it inside a candidate image — it needs no database, no network, no compose:

    docker run --rm archimedes-repro1632:binary python /repro/openssl_inventory.py

(``run.sh`` mounts this directory at /repro; standalone, mount it yourself:
``docker run --rm -v "$PWD:/repro:ro" IMAGE python /repro/openssl_inventory.py``)

It imports the prod graph, then reports every distinct libssl/libcrypto mapped
into the address space and asks each one, via ``OpenSSL_version()``/
``SSLeay_version()``, what build it is. Three different OpenSSL builds in one
process — which is what ``psycopg2-binary`` produces here — is the condition
psycopg2's own docs warn about for production use.
"""

from __future__ import annotations

import ctypes
import ssl
import sys


def mapped_crypto_libs() -> list[str]:
    seen: dict[str, None] = {}
    with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            path = line.rsplit(" ", 1)[-1].strip()
            if path.rsplit("/", 1)[-1].startswith(("libssl", "libcrypto")):
                seen[path] = None
    return sorted(seen)


def version_of(path: str) -> str:
    """Ask a specific libcrypto/libssl which OpenSSL build it is."""
    try:
        lib = ctypes.CDLL(path, mode=ctypes.RTLD_LOCAL)
    except OSError as exc:
        return f"<dlopen failed: {exc}>"
    for sym in ("OpenSSL_version", "SSLeay_version"):  # 3.x name, then 1.1.x name
        try:
            fn = getattr(lib, sym)
        except AttributeError:
            continue
        fn.restype = ctypes.c_char_p
        fn.argtypes = [ctypes.c_int]
        try:
            return fn(0).decode()  # 0 == OPENSSL_VERSION
        except Exception as exc:  # pragma: no cover - diagnostic only
            return f"<{sym} raised {exc!r}>"
    return "<no version symbol>"


def main() -> int:
    print(f"python              : {sys.version.split()[0]}")
    print(f"interpreter _ssl uses: {ssl.OPENSSL_VERSION}")

    print("\nimporting the prod graph (archimedes.main + aiohttp + web3)…")
    for mod in ("archimedes.main", "aiohttp", "web3"):
        try:
            __import__(mod)
        except Exception as exc:
            print(f"  WARNING: import {mod} failed: {exc!r}")

    try:
        import psycopg2

        print(f"\npsycopg2            : {psycopg2.__version__}")
        print(f"psycopg2 libpq      : {psycopg2.__libpq_version__}")
    except Exception as exc:
        print(f"\npsycopg2            : IMPORT FAILED {exc!r}")

    libs = mapped_crypto_libs()
    print(f"\n{len(libs)} distinct libssl/libcrypto mapping(s) in this process:")
    builds: set[str] = set()
    for path in libs:
        ver = version_of(path)
        builds.add(ver)
        origin = "psycopg2-binary wheel" if "psycopg2_binary.libs" in path else "system / interpreter"
        print(f"  {path}")
        print(f"      build : {ver}")
        print(f"      origin: {origin}")

    print(f"\ndistinct OpenSSL builds co-resident: {len(builds)}")
    for b in sorted(builds):
        print(f"  - {b}")
    if len(builds) > 1:
        print(
            "\nVERDICT: multiple OpenSSL builds share this address space. This is the\n"
            "         configuration psycopg2 documents as unsupported in production."
        )
    else:
        print("\nVERDICT: single OpenSSL build — no dual-OpenSSL exposure in this image.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
