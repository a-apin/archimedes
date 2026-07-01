"""Visitor insights — distinct-visitor geography + device class (issue #787).

We have zero promotion and a zero-conversion problem, so understanding WHO lands
on the site (where from, on what kind of device) is high-value signal. This
records, per day, the distinct visitors broken down by:

  - **country** — the ISO-3166 code CloudFront geolocates from the viewer IP and
    forwards as the ``CloudFront-Viewer-Country`` header (the ALB/origin can't see
    the real client IP — CloudFront masks it — so this header is the only clean
    geo source);
  - **device class** — mobile / tablet / desktop / tv, from CloudFront's
    ``CloudFront-Is-*-Viewer`` headers (falling back to a User-Agent sniff).

Distinct counts use Redis HyperLogLog keyed on the anonymous ``archimedes_vid``
(no PII, no raw-id retention) — same privacy-friendly approach as the funnel.

Population (issue #830): this is recorded from the **same JS-gated ``landed``
beacon population the conversion funnel uses** — one source of truth for
"distinct visitor" across funnel, geography, and device. The beacon is emitted
by the SPA's JavaScript, which non-JS crawlers don't execute, so the recorded
population is browser-rendered visitors — NOT every server-side human-classified
request (that population leaks browser-UA bots through the open-demo default and
was the source of the 17-vs-50 discrepancy this issue reconciles). It is still an
UA/beacon-derived signal, not a verified-identity count; distinct *users* are the
wallet count in ``user_profiles``.

Mirrors ``services/funnel_store.py`` / ``telemetry_store.py``: same
``redis.asyncio`` convention and **fail-safe by construction** — every method
swallows Redis errors; instrumentation never turns a request into a 5xx.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_PREFIX = "archimedes:visitors"
# Day buckets self-expire after 90 days (trend history without unbounded growth).
_DAY_TTL_SECONDS = 90 * 24 * 60 * 60

DEVICE_CLASSES = ("mobile", "tablet", "desktop", "tv", "unknown")
_UNKNOWN_COUNTRY = "ZZ"  # ISO 3166 user-assigned code — "unknown / not provided"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _norm_country(raw: str | None) -> str:
    """Normalize a CloudFront-Viewer-Country value to a 2-letter upper code or ZZ."""
    if not raw:
        return _UNKNOWN_COUNTRY
    code = raw.strip().upper()
    return code if len(code) == 2 and code.isalpha() else _UNKNOWN_COUNTRY


class VisitorInsightsStore:
    """Fail-safe Redis wrapper for distinct-visitor geo + device counters."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or REDIS_URL
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    # ─── Record (write path — JS-gated `landed` beacon, issue #830) ──────

    async def record(self, country: str | None, device: str, visitor_id: str) -> None:
        """Record one landed visit's country + device for ``visitor_id``. Never raises."""
        if not visitor_id:
            return
        cc = _norm_country(country)
        dev = device if device in DEVICE_CLASSES else "unknown"
        try:
            r = await self._get_redis()
            day = _today()
            country_day = f"{_PREFIX}:country:day:{day}:{cc}"
            device_day = f"{_PREFIX}:device:day:{day}:{dev}"
            pipe = r.pipeline()
            # All-time distinct (no TTL).
            pipe.pfadd(f"{_PREFIX}:country:total:{cc}", visitor_id)
            pipe.pfadd(f"{_PREFIX}:device:total:{dev}", visitor_id)
            # Per-day distinct (TTL'd).
            pipe.pfadd(country_day, visitor_id)
            pipe.expire(country_day, _DAY_TTL_SECONDS, nx=True)
            pipe.pfadd(device_day, visitor_id)
            pipe.expire(device_day, _DAY_TTL_SECONDS, nx=True)
            # Index of which country codes have been seen, so reads can enumerate
            # them without SCANning the keyspace.
            pipe.sadd(f"{_PREFIX}:countries", cc)
            await pipe.execute()
        except Exception as exc:
            logger.debug("visitor insight record failed (%s/%s): %s", cc, dev, exc)

    # ─── Read (exposure path — GET /api/metrics/visitors) ────────────────

    async def get_insights(self) -> tuple[dict[str, int], dict[str, int]]:
        """Return ``(countries, devices)`` all-time distinct-visitor maps. Zeros on error."""
        try:
            r = await self._get_redis()
            seen = await r.smembers(f"{_PREFIX}:countries")
            countries: dict[str, int] = {}
            if seen:
                codes = sorted(seen)
                pipe = r.pipeline()
                for cc in codes:
                    pipe.pfcount(f"{_PREFIX}:country:total:{cc}")
                for cc, n in zip(codes, await pipe.execute(), strict=False):
                    countries[cc] = int(n or 0)
            pipe = r.pipeline()
            for dev in DEVICE_CLASSES:
                pipe.pfcount(f"{_PREFIX}:device:total:{dev}")
            devices = {dev: int(n or 0) for dev, n in zip(DEVICE_CLASSES, await pipe.execute(), strict=False)}
            return countries, devices
        except Exception as exc:
            logger.debug("visitor insight read failed: %s", exc)
            return {}, dict.fromkeys(DEVICE_CLASSES, 0)

    async def close(self) -> None:
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception as exc:
                logger.debug("visitor insight store close failed: %s", exc)
            self._redis = None
