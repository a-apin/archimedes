"""Tests for the visitor-insights instrument — geography + device class (#787).

Hermetic — mocks the Redis boundary. Covers the store (record + read + fail-safe),
country normalization, the device-class derivation (CloudFront headers + UA
fallback), and the humans-only capture gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from archimedes.api.visitor_insights import _device_class, record_visitor_insight
from archimedes.services.visitor_insights_store import (
    DEVICE_CLASSES,
    VisitorInsightsStore,
    _norm_country,
)


def _mock_redis(pfcounts=None, members=None):
    pipe = MagicMock()
    pipe.pfadd = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.sadd = MagicMock(return_value=pipe)
    pipe.pfcount = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=pfcounts if pfcounts is not None else [])
    r = MagicMock()
    r.pipeline = MagicMock(return_value=pipe)
    r.smembers = AsyncMock(return_value=members or set())
    return r, pipe


def _req(headers=None):
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace(visitor_id="vid-1"))


# ─── country normalization ───────────────────────────────────────────────


def test_norm_country_valid():
    assert _norm_country("us") == "US"
    assert _norm_country("DE") == "DE"


def test_norm_country_invalid_or_missing():
    assert _norm_country(None) == "ZZ"
    assert _norm_country("") == "ZZ"
    assert _norm_country("USA") == "ZZ"  # not 2 letters
    assert _norm_country("1!") == "ZZ"


# ─── device class derivation ─────────────────────────────────────────────


def test_device_class_cloudfront_headers_win():
    assert _device_class(_req({"cloudfront-is-mobile-viewer": "true"})) == "mobile"
    assert _device_class(_req({"cloudfront-is-tablet-viewer": "true"})) == "tablet"
    assert _device_class(_req({"cloudfront-is-desktop-viewer": "true"})) == "desktop"
    assert _device_class(_req({"cloudfront-is-smarttv-viewer": "true"})) == "tv"


def test_device_class_ua_fallback():
    assert _device_class(_req({"user-agent": "Mozilla/5.0 (iPhone) Mobile"})) == "mobile"
    assert _device_class(_req({"user-agent": "Mozilla/5.0 (iPad)"})) == "tablet"
    assert _device_class(_req({"user-agent": "Mozilla/5.0 (Macintosh)"})) == "desktop"
    assert _device_class(_req({})) == "unknown"


# ─── store record + read ─────────────────────────────────────────────────


async def test_record_pfadds_country_and_device_and_indexes():
    store = VisitorInsightsStore()
    r, pipe = _mock_redis()
    store._get_redis = AsyncMock(return_value=r)

    await store.record("US", "mobile", "vid-1")

    # 4 pfadds: country total + device total + country day + device day
    assert pipe.pfadd.call_count == 4
    # country code indexed for enumeration on read
    pipe.sadd.assert_called_once()
    assert pipe.sadd.call_args.args[1] == "US"
    pipe.execute.assert_awaited_once()


async def test_record_normalizes_bad_country_and_device():
    store = VisitorInsightsStore()
    r, pipe = _mock_redis()
    store._get_redis = AsyncMock(return_value=r)
    await store.record("not-a-country", "watch", "vid-1")
    # bad country → ZZ, bad device → unknown (in the key names)
    keys = [c.args[0] for c in pipe.pfadd.call_args_list]
    assert any(k.endswith(":ZZ") for k in keys)
    assert any(":device:" in k and k.endswith(":unknown") for k in keys)


async def test_record_noop_without_visitor_id():
    store = VisitorInsightsStore()
    r, _ = _mock_redis()
    store._get_redis = AsyncMock(return_value=r)
    await store.record("US", "mobile", "")
    r.pipeline.assert_not_called()


async def test_record_fails_open():
    store = VisitorInsightsStore()
    store._get_redis = AsyncMock(side_effect=ConnectionError("down"))
    await store.record("US", "mobile", "vid-1")  # must not raise


async def test_get_insights_reads_countries_and_devices():
    store = VisitorInsightsStore()
    # smembers → {US, DE}; first pipeline (countries) → [10, 4]; second (devices, 5
    # classes in DEVICE_CLASSES order) → [7, 1, 6, 0, 0]
    r, pipe = _mock_redis(members={"US", "DE"})
    pipe.execute = AsyncMock(side_effect=[[10, 4], [7, 1, 6, 0, 0]])
    store._get_redis = AsyncMock(return_value=r)

    countries, devices = await store.get_insights()

    assert countries == {"DE": 10, "US": 4}  # sorted(codes) = [DE, US]
    assert devices == dict(zip(DEVICE_CLASSES, [7, 1, 6, 0, 0], strict=False))


async def test_get_insights_fails_open():
    store = VisitorInsightsStore()
    store._get_redis = AsyncMock(side_effect=ConnectionError("down"))
    countries, devices = await store.get_insights()
    assert countries == {}
    assert devices == dict.fromkeys(DEVICE_CLASSES, 0)


# ─── capture gate (humans only) ──────────────────────────────────────────


async def test_capture_skips_agents(monkeypatch):
    calls = {"n": 0}

    class FakeStore:
        async def record(self, *a):
            calls["n"] += 1

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.visitor_insights_store.VisitorInsightsStore", FakeStore)
    await record_visitor_insight(_req({"cloudfront-viewer-country": "US"}), is_agent=True)
    assert calls["n"] == 0  # agents are NOT recorded — keep geography human-only


async def test_capture_records_humans(monkeypatch):
    recorded = {}

    class FakeStore:
        async def record(self, country, device, vid):
            recorded["args"] = (country, device, vid)

        async def close(self):
            pass

    monkeypatch.setattr("archimedes.services.visitor_insights_store.VisitorInsightsStore", FakeStore)
    req = _req({"cloudfront-viewer-country": "DE", "cloudfront-is-mobile-viewer": "true"})
    await record_visitor_insight(req, is_agent=False)
    assert recorded["args"] == ("DE", "mobile", "vid-1")


# ─── one-source-of-truth reconciliation (issue #830) ──────────────────────
#
# The funnel `landed` population and the geo/device population MUST be the same
# set of distinct visitors, keyed on the same archimedes_vid. A tiny in-memory
# HLL (distinct-set) Redis lets us drive N synthetic visitors through BOTH stores
# and assert country-sum == device-sum == funnel `landed`.


class _FakeHLLRedis:
    """Minimal in-memory Redis with HLL semantics (PFADD/PFCOUNT via a set) + SADD.

    Mirrors the boundary the stores use (``pipeline`` → ``pfadd``/``pfcount``/
    ``sadd``/``expire`` → ``execute``). PFCOUNT is exact here (a set), which is
    fine for a small deterministic reconciliation test.
    """

    def __init__(self) -> None:
        self.hll: dict[str, set[str]] = {}
        self.sets: dict[str, set[str]] = {}

    def pipeline(self):
        return _FakePipe(self)

    async def smembers(self, key: str):
        return set(self.sets.get(key, set()))


class _FakePipe:
    def __init__(self, r: _FakeHLLRedis) -> None:
        self._r = r
        self._ops: list = []

    def pfadd(self, key: str, member: str):
        self._ops.append(("pfadd", key, member))
        return self

    def pfcount(self, key: str):
        self._ops.append(("pfcount", key, None))
        return self

    def sadd(self, key: str, member: str):
        self._ops.append(("sadd", key, member))
        return self

    def expire(self, *a, **kw):
        self._ops.append(("expire", None, None))
        return self

    async def execute(self):
        results: list = []
        for op, key, member in self._ops:
            if op == "pfadd":
                self._r.hll.setdefault(key, set()).add(member)
                results.append(1)
            elif op == "pfcount":
                results.append(len(self._r.hll.get(key, set())))
            elif op == "sadd":
                self._r.sets.setdefault(key, set()).add(member)
                results.append(1)
            else:
                results.append(True)
        self._ops = []
        return results


async def test_landed_population_reconciles_funnel_and_geo_device():
    """N distinct landed visitors → country-sum == device-sum == funnel `landed`.

    Drives the SAME visitor ids through the funnel store (`landed`) and the
    visitor-insights store (country + device), on a shared HLL Redis, then reads
    both back. The three distinct counts must agree — that is the invariant the
    single-source-of-truth reconciliation guarantees (issue #830).
    """
    from unittest.mock import AsyncMock

    from archimedes.services.funnel_store import FunnelStore

    shared = _FakeHLLRedis()

    funnel = FunnelStore()
    funnel._get_redis = AsyncMock(return_value=shared)
    insights = VisitorInsightsStore()
    insights._get_redis = AsyncMock(return_value=shared)

    n = 7
    for i in range(n):
        vid = f"vid-{i}"
        # Same trigger (landed), same dedup key (vid), for both surfaces.
        await funnel.record("landed", vid)
        # Country cycles US/DE, device cycles mobile/desktop — the SUM across
        # buckets must still equal the distinct landed population.
        country = "US" if i % 2 == 0 else "DE"
        device = "mobile" if i % 3 == 0 else "desktop"
        await insights.record(country, device, vid)

    landed = (await funnel.get_totals())["landed"]
    countries, devices = await insights.get_insights()
    country_sum = sum(countries.values())
    device_sum = sum(devices.values())

    assert landed == n
    assert country_sum == n
    assert device_sum == n
    assert country_sum == device_sum == landed


async def test_beacon_path_records_both_funnel_and_visitor_insight():
    """The JS-gated `landed` beacon endpoint records funnel AND geo/device (issue #830).

    Confirms the single trigger drives both surfaces with the same visitor id —
    this is the wiring that keeps the two distinct-visitor counts in lockstep.
    """
    from archimedes.api.metrics_routes import record_funnel_event
    from archimedes.models.telemetry import FunnelEventRequest

    funnel_calls: list = []
    insight_calls: list = []

    async def _fake_record_funnel(request, stage):
        funnel_calls.append((getattr(request.state, "visitor_id", None), stage))

    async def _fake_record_insight(request, is_agent=False):
        insight_calls.append(getattr(request.state, "visitor_id", None))

    import archimedes.api.metrics_routes as mr

    orig_funnel = mr.record_funnel
    orig_insight = mr.record_visitor_insight
    mr.record_funnel = _fake_record_funnel
    mr.record_visitor_insight = _fake_record_insight
    try:
        req = _req({"user-agent": "Mozilla/5.0"})
        req.state.visitor_id = "vid-beacon"
        await record_funnel_event(FunnelEventRequest(stage="landed"), req)
    finally:
        mr.record_funnel = orig_funnel
        mr.record_visitor_insight = orig_insight

    # Same vid drove both surfaces off the single landed beacon.
    assert funnel_calls == [("vid-beacon", "landed")]
    assert insight_calls == ["vid-beacon"]


async def test_non_landed_beacon_records_neither():
    """A non-emittable stage records nothing (only `landed` is client-emittable)."""
    import archimedes.api.metrics_routes as mr
    from archimedes.api.metrics_routes import record_funnel_event
    from archimedes.models.telemetry import FunnelEventRequest

    calls = {"n": 0}

    async def _boom(*a, **kw):
        calls["n"] += 1

    orig_funnel = mr.record_funnel
    orig_insight = mr.record_visitor_insight
    mr.record_funnel = _boom
    mr.record_visitor_insight = _boom
    try:
        out = await record_funnel_event(FunnelEventRequest(stage="wallet_connected"), _req())
    finally:
        mr.record_funnel = orig_funnel
        mr.record_visitor_insight = orig_insight

    assert out == {"recorded": False}
    assert calls["n"] == 0


async def test_browser_ua_no_session_is_recorded_by_the_classifier():
    """A browser-UA-no-session request (open-demo human) IS recorded (issue #830).

    Pins label-to-behavior: the classifier's open-demo default is `human`, and the
    beacon path DOES record such a visitor — so the docstring must NOT claim the
    population is "real visitors, not crawlers". The companion docstring assertion
    below proves the over-claiming language is gone.
    """
    recorded = {}

    class FakeStore:
        async def record(self, country, device, vid):
            recorded["args"] = (country, device, vid)

        async def close(self):
            pass

    import archimedes.services.visitor_insights_store as vstore

    orig = vstore.VisitorInsightsStore
    vstore.VisitorInsightsStore = FakeStore
    try:
        # Browser UA, NO session, NO CloudFront country header → open-demo human,
        # device via UA fallback. This is exactly the population the old docstring
        # falsely claimed was excluded.
        req = _req({"user-agent": "Mozilla/5.0 (Macintosh)"})
        await record_visitor_insight(req)  # default is_agent=False (beacon call)
    finally:
        vstore.VisitorInsightsStore = orig

    # It WAS recorded (country ZZ via missing header, device desktop via UA).
    assert recorded["args"][2] == "vid-1"
    assert recorded["args"][1] == "desktop"


def test_store_docstring_does_not_overclaim_real_visitors():
    """The store docstring must not claim 'real visitors, not datacenter crawler IPs' (#830)."""
    import archimedes.services.visitor_insights_store as vstore

    doc = (vstore.__doc__ or "") + (vstore.VisitorInsightsStore.__doc__ or "")
    assert "real visitors, not datacenter" not in doc
    assert "human-ish" not in doc


def test_capture_helper_docstring_does_not_overclaim():
    """The capture helper docstring must not claim it excludes crawlers as 'real visitors' (#830)."""
    import archimedes.api.visitor_insights as vmod

    doc = vmod.__doc__ or ""
    assert "real visitors, not datacenter" not in doc
