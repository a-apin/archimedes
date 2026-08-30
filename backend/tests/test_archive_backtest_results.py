"""Hermetic tests for archive_backtest_results.py (v8 Lane 3.1).

Covers:
  - keep-policy correctness: gate rows and recent-N survive per strategy_id,
    and the passport-referenced rule acts as an independent safety net when
    keep_recent_n=0 (its one case of non-redundant behavior — see the
    script's module docstring on rule 3 being a strict subset of rule 1
    otherwise, and its documented limitation re: superseded snapshot rows).
  - archive-before-prune enforcement: --prune without a verified manifest for
    today MUST refuse (ManifestNotFound) — a guard demonstration.
  - batch accounting: --archive splits doomed rows into the requested batch
    size and the manifest's row counts sum correctly; --prune deletes exactly
    the archived, currently-non-kept rows and leaves every keep-row intact.

Uses a real tmp-file SQLite DB via ``tests.db_isolation.redirect_to_tmp_sqlite``
(the idiom `test_api_routes.py::_use_tmp_db` uses) since the module under test
calls ``archimedes.db.get_session()`` / ``init_db()`` directly. S3 is faked
in-process (no network, no moto, no real boto3 client) — the fake speaks the
exact subset of the boto3 S3 client surface this module calls
(``put_object`` / ``get_object``), including raising ``botocore.exceptions.ClientError``
on a missing key so the real ``except ClientError`` branches are exercised.

Run: env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest \
       backend/tests/test_archive_backtest_results.py -q
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from archimedes.db import get_session, init_db
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.strategy_passport_record import StrategyPassportRecord
from archimedes.scripts import archive_backtest_results as script
from botocore.exceptions import ClientError

from tests.db_isolation import redirect_to_tmp_sqlite


@pytest.fixture
def _use_tmp_db(tmp_path):
    """Point archimedes.db at a fresh tmp-file SQLite DB for this test only.

    Same idiom as ``test_api_routes.py::_use_tmp_db`` — see
    ``tests/db_isolation.py`` for why a plain ``monkeypatch.setenv`` +
    ``init_db()`` does NOT work (archimedes.db builds its engine once at
    import time).
    """
    for _ in redirect_to_tmp_sqlite(tmp_path):
        init_db()
        yield


# ─────────────────────────── fakes ────────────────────────────────


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """In-memory stand-in for the boto3 S3 client surface this module uses."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str | None = None) -> None:
        self.objects[(Bucket, Key)] = Body
        self.put_calls.append((Bucket, Key))

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
                "GetObject",
            ) from None
        return {"Body": _FakeBody(data)}


# ─────────────────────────── row helpers ────────────────────────────────


def _mk_backtest(
    session,
    *,
    strategy_id: str,
    content_hash: str,
    created_at: datetime,
    deflated_sharpe_ratio: float | None = None,
    source_pipeline: str = "test_pipeline",
) -> BacktestResultRecord:
    row = BacktestResultRecord(
        strategy_id=strategy_id,
        content_hash=content_hash,
        source_pipeline=source_pipeline,
        created_at=created_at,
        computed_at=created_at,
        deflated_sharpe_ratio=deflated_sharpe_ratio,
        artifact_json=json.dumps({"marker": content_hash}),
    )
    session.add(row)
    session.flush()
    return row


def _mk_passport(session, strategy_id: str) -> StrategyPassportRecord:
    row = StrategyPassportRecord(id=strategy_id)
    session.add(row)
    session.flush()
    return row


_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _t(days: int) -> datetime:
    """A deterministic, strictly-increasing timestamp N days after a fixed base."""
    return _BASE_TIME + timedelta(days=days)


# ─────────────────────────── keep-policy correctness ────────────────────


class TestKeepPolicy:
    def test_recent_n_keeps_only_the_newest_per_strategy(self, _use_tmp_db):
        with get_session() as session:
            # strategy "a" has 7 runs; keep_recent_n=5 should keep the 5 newest.
            rows = [_mk_backtest(session, strategy_id="a", content_hash=f"h{i}", created_at=_t(i)) for i in range(7)]
            session.commit()

            breakdown = script.compute_keep_breakdown(session, keep_recent_n=5)

            newest_five_ids = {r.id for r in rows[2:]}  # days 2..6 are the 5 newest
            oldest_two_ids = {r.id for r in rows[:2]}
            assert breakdown.recent_ids == newest_five_ids
            assert breakdown.keep_ids >= newest_five_ids
            assert not (oldest_two_ids & breakdown.keep_ids)

    def test_recent_n_is_per_strategy_not_global(self, _use_tmp_db):
        with get_session() as session:
            # 3 strategies x 3 rows each; keep_recent_n=1 must keep exactly one
            # row PER strategy_id, not just the single globally-newest row.
            for sid in ("a", "b", "c"):
                for i in range(3):
                    _mk_backtest(session, strategy_id=sid, content_hash=f"{sid}-{i}", created_at=_t(i))
            session.commit()

            breakdown = script.compute_keep_breakdown(session, keep_recent_n=1)
            kept_strategy_ids = {
                row.strategy_id
                for row in session.query(BacktestResultRecord).filter(BacktestResultRecord.id.in_(breakdown.recent_ids))
            }
            assert kept_strategy_ids == {"a", "b", "c"}
            assert len(breakdown.recent_ids) == 3

    def test_gate_relevant_rows_survive_regardless_of_age(self, _use_tmp_db):
        with get_session() as session:
            # An old, gate-scored row (deflated_sharpe_ratio NOT NULL) outside
            # the recent-N window must still be kept.
            old_gate_row = _mk_backtest(
                session, strategy_id="a", content_hash="old-gate", created_at=_t(0), deflated_sharpe_ratio=1.23
            )
            for i in range(1, 6):
                _mk_backtest(session, strategy_id="a", content_hash=f"h{i}", created_at=_t(i))
            session.commit()

            breakdown = script.compute_keep_breakdown(session, keep_recent_n=5)
            assert old_gate_row.id in breakdown.gate_ids
            assert old_gate_row.id in breakdown.keep_ids
            assert old_gate_row.id not in breakdown.recent_ids  # confirms rule 2 is doing the work, not rule 1

    def test_non_gate_old_row_is_doomed(self, _use_tmp_db):
        with get_session() as session:
            old_row = _mk_backtest(session, strategy_id="a", content_hash="old", created_at=_t(0))
            for i in range(1, 6):
                _mk_backtest(session, strategy_id="a", content_hash=f"h{i}", created_at=_t(i))
            session.commit()

            doomed = script.doomed_ids(session, keep_recent_n=5)
            assert old_row.id in doomed

    def test_passport_rule_is_a_safety_net_when_recent_n_is_zero(self, _use_tmp_db):
        """Rule 3 is a strict subset of rule 1 whenever keep_recent_n >= 1 (see
        module docstring) — the case where it visibly does independent work is
        keep_recent_n=0: rule 1 protects nothing, so the sole surviving row for
        a passported strategy must come from rule 3 alone."""
        with get_session() as session:
            _mk_passport(session, strategy_id="a")
            only_row = _mk_backtest(session, strategy_id="a", content_hash="h0", created_at=_t(0))
            session.commit()

            breakdown = script.compute_keep_breakdown(session, keep_recent_n=0)
            assert breakdown.recent_ids == set()
            assert only_row.id in breakdown.passport_ids
            assert only_row.id in breakdown.keep_ids

    def test_passport_rule_does_not_reach_back_to_a_superseded_snapshot_row(self, _use_tmp_db):
        """Documents an intentional limitation (see module docstring rule 3):
        strategy_passports denormalizes a SNAPSHOT rather than an FK, and this
        script operationalizes "referenced" as the LATEST row for a passported
        strategy_id — not "any row ever denormalized onto the passport". Once a
        strategy is re-run, its older, now-superseded row is protected only if
        rule 1 (recent-N) or rule 2 (gate-relevant) still covers it; rule 3
        does not reach back for it. This test pins that behavior down as
        deliberate, not an accidental gap.
        """
        with get_session() as session:
            _mk_passport(session, strategy_id="a")
            superseded_row = _mk_backtest(session, strategy_id="a", content_hash="superseded", created_at=_t(0))
            # 5 strictly newer runs push the superseded row out of a recent_n=5 window.
            for i in range(1, 6):
                _mk_backtest(session, strategy_id="a", content_hash=f"h{i}", created_at=_t(i))
            session.commit()

            breakdown = script.compute_keep_breakdown(session, keep_recent_n=5)
            assert superseded_row.id not in breakdown.recent_ids
            assert superseded_row.id not in breakdown.passport_ids
            assert superseded_row.id not in breakdown.keep_ids

    def test_passport_rule_keeps_the_latest_row_for_a_passported_strategy(self, _use_tmp_db):
        with get_session() as session:
            _mk_passport(session, strategy_id="a")
            older = _mk_backtest(session, strategy_id="a", content_hash="older", created_at=_t(0))
            newest = _mk_backtest(session, strategy_id="a", content_hash="newest", created_at=_t(1))
            # A second strategy with NO passport row — its latest row must NOT
            # be pulled in by rule 3.
            unpassported = _mk_backtest(session, strategy_id="b", content_hash="b-only", created_at=_t(0))
            session.commit()

            breakdown = script.compute_keep_breakdown(session, keep_recent_n=0)
            assert breakdown.passport_ids == {newest.id}
            assert older.id not in breakdown.passport_ids
            assert unpassported.id not in breakdown.passport_ids


# ─────────────────────────── archive-before-prune guard ─────────────────


class TestPruneRefusesWithoutManifest:
    def test_prune_without_any_archive_run_refuses(self, _use_tmp_db, monkeypatch):
        monkeypatch.setenv("ARCHIVE_BUCKET", "test-bucket")
        with get_session() as session:
            _mk_backtest(session, strategy_id="a", content_hash="h0", created_at=_t(0))
            session.commit()

            client = FakeS3Client()  # never had --archive run against it
            with pytest.raises(script.ManifestNotFound):
                script.run_prune(session, keep_recent_n=5, s3_client=client)

    def test_prune_with_tampered_manifest_refuses(self, _use_tmp_db, monkeypatch):
        """A manifest exists but a batch's bytes were altered after upload
        (corruption, or a partial re-upload) — sha256 verification must catch it."""
        monkeypatch.setenv("ARCHIVE_BUCKET", "test-bucket")
        with get_session() as session:
            for i in range(3):
                _mk_backtest(session, strategy_id="a", content_hash=f"h{i}", created_at=_t(i))
            session.commit()

            client = FakeS3Client()
            run_date = date(2026, 1, 15)
            script.run_archive(session, keep_recent_n=0, s3_client=client, run_date=run_date)

            # Tamper with the one archived batch's bytes in place.
            batch_keys = [k for (_bucket, k) in client.objects if "manifest.json" not in k]
            assert batch_keys
            tampered_key = ("test-bucket", batch_keys[0])
            client.objects[tampered_key] = client.objects[tampered_key] + b"\x00garbage"

            with pytest.raises(script.ManifestVerificationFailed):
                script.run_prune(session, keep_recent_n=0, s3_client=client, run_date=run_date)

            # And the DB must be untouched — a failed verification must not
            # delete anything.
            assert session.query(BacktestResultRecord).count() == 3


# ─────────────────────────── batch accounting ────────────────────────────


class TestBatchAccounting:
    def test_archive_splits_into_requested_batch_size_and_manifest_sums_correctly(self, _use_tmp_db, monkeypatch):
        monkeypatch.setenv("ARCHIVE_BUCKET", "test-bucket")
        with get_session() as session:
            # 5 doomed rows (all old, no gate, no passport, keep_recent_n=0
            # means nothing is protected by rule 1 either) with a batch size
            # of 2 -> batches of [2, 2, 1].
            rows = [_mk_backtest(session, strategy_id="a", content_hash=f"h{i}", created_at=_t(i)) for i in range(5)]
            session.commit()

            client = FakeS3Client()
            run_date = date(2026, 2, 1)
            result = script.run_archive(session, keep_recent_n=0, batch_size=2, s3_client=client, run_date=run_date)

            assert result["total_rows"] == 5
            assert result["batch_count"] == 3

            manifest_bytes = client.objects[("test-bucket", result["manifest_key"])]
            manifest = json.loads(manifest_bytes)
            assert manifest["total_rows"] == 5
            assert [b["row_count"] for b in manifest["batches"]] == [2, 2, 1]
            assert sum(b["row_count"] for b in manifest["batches"]) == manifest["total_rows"]
            all_archived_ids = sorted(id_ for b in manifest["batches"] for id_ in b["row_ids"])
            assert all_archived_ids == sorted(r.id for r in rows)

            # Each batch's recorded sha256 matches the actual uploaded bytes.
            for b in manifest["batches"]:
                actual = client.objects[("test-bucket", b["key"])]
                assert script._sha256_hex(actual) == b["sha256"]
                # And the payload really is gzip -- decompresses to the right row_count of JSONL lines.
                lines = gzip.decompress(actual).decode("utf-8").strip().splitlines()
                assert len(lines) == b["row_count"]

    def test_prune_deletes_exactly_the_archived_non_kept_rows_and_accounts_for_them(self, _use_tmp_db, monkeypatch):
        monkeypatch.setenv("ARCHIVE_BUCKET", "test-bucket")
        with get_session() as session:
            # Strategy "a" has 7 runs; with keep_recent_n=5 the 2 oldest are
            # doomed and the 5 newest are kept (rule 1).
            all_a = [_mk_backtest(session, strategy_id="a", content_hash=f"a{i}", created_at=_t(i)) for i in range(7)]
            # 5 recent rows for strategy "b" are ALSO protected by keep_recent_n
            # and must survive both archive and prune untouched.
            kept_b = [_mk_backtest(session, strategy_id="b", content_hash=f"k{i}", created_at=_t(i)) for i in range(5)]
            session.commit()
            # Capture ids before pruning expires/deletes the ORM instances.
            doomed_ids = [r.id for r in all_a[:2]]
            kept_ids = {r.id for r in all_a[2:] + kept_b}

            client = FakeS3Client()
            run_date = date(2026, 3, 1)
            archive_result = script.run_archive(
                session, keep_recent_n=5, batch_size=3, s3_client=client, run_date=run_date
            )
            assert archive_result["total_rows"] == 2  # only strategy "a"'s 2 oldest rows are doomed

            prune_result = script.run_prune(session, keep_recent_n=5, s3_client=client, run_date=run_date)
            assert prune_result["deleted_count"] == 2
            assert prune_result["manifest_total_rows"] == 2
            assert prune_result["skipped_now_kept_count"] == 0

            remaining_ids = {r.id for r in session.query(BacktestResultRecord).all()}
            assert remaining_ids == kept_ids
            for did in doomed_ids:
                assert did not in remaining_ids

    def test_prune_never_deletes_a_row_that_became_kept_after_archiving(self, _use_tmp_db, monkeypatch):
        """Defense in depth: if the keep-policy's view of the world changed
        between --archive and --prune (e.g. a new strategy_passports row
        landed pointing at an already-archived row), prune must still refuse
        to delete it even though it is named in a verified manifest."""
        monkeypatch.setenv("ARCHIVE_BUCKET", "test-bucket")
        with get_session() as session:
            row = _mk_backtest(session, strategy_id="a", content_hash="h0", created_at=_t(0))
            session.commit()

            client = FakeS3Client()
            run_date = date(2026, 4, 1)
            script.run_archive(session, keep_recent_n=0, s3_client=client, run_date=run_date)

            # Now a passport shows up for strategy "a" AFTER archiving — this
            # row is now the latest (and only) row for "a", so the current
            # keep policy protects it.
            _mk_passport(session, strategy_id="a")
            session.commit()

            result = script.run_prune(session, keep_recent_n=0, s3_client=client, run_date=run_date)
            assert result["deleted_count"] == 0
            assert result["skipped_now_kept_count"] == 1
            assert session.query(BacktestResultRecord).filter_by(id=row.id).one_or_none() is not None
