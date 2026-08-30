"""Archive-then-prune tooling for the ``backtest_results`` table (v8 Lane 3.1).

CONTEXT (verified 2026-08-30): ``backtest_results`` is ~6.3GB across 14,857 rows
for 96 strategies (~155 runs/strategy). ``artifact_json`` averages ~349KB/row and
``equity_curve_json`` averages ~63KB/row — those two TEXT columns are almost the
entire table's size. ``deflated_sharpe_ratio`` (a proxy for "this row was scored
by the rigor gate") is NULL on 93% of rows — the other ~7% are gate-relevant and
must never be pruned.

Owner-chosen policy (option (a), 2026-08-30): **archive to S3, then prune** —
never delete-without-archiving. This script is READ-ONLY against the database
and makes no S3 calls unless one of ``--archive`` / ``--prune`` is passed
explicitly; the default mode (``--plan``, also what running with no flags does)
only reports what the other two modes would do.

KEEP POLICY — per ``strategy_id``, a row survives if ANY of:
  1. It is among the ``--keep-recent-n`` (default 5) most recent rows for that
     strategy_id (``ORDER BY created_at DESC, id DESC`` — the same tie-break
     ``backtest_repository.get_daily_returns`` /
     ``backtest_repository.latest_backtests_by_strategy`` already use for
     "the current row for a strategy", reused here rather than inventing a
     second definition of "recent").
  2. ``deflated_sharpe_ratio IS NOT NULL`` (gate-relevant — the rigor gate
     graded this exact row; see ``models/backtest_store.py``).
  3. It is the row "referenced by" a ``strategy_passports`` row. THE LINKAGE
     COLUMN (read from the models, not guessed): ``strategy_passports`` has NO
     foreign key onto ``backtest_results.id`` — ``models/strategy_passport_record.py``
     denormalizes a *snapshot* of one backtest onto the passport row instead
     (``sharpe_ratio``, ``cagr``, ``deflated_sharpe_ratio``, ``backtest_start``/
     ``_end``, ...; see that file's "Backtest results (denormalized for query
     speed)" section). The only real linkage between the two tables is
     ``strategy_passports.id == backtest_results.strategy_id`` — confirmed by
     ``passport_loader.get_passport()`` (``filter_by(id=strategy_id)``) and
     every ``backtest_repository`` function filtering the same table by the
     same ``strategy_id`` string. The denormalized snapshot on a passport is
     always written from whatever ``backtest_repository`` treats as canonical
     for that strategy_id at ingest/refresh time (``get_daily_returns``,
     ``update_rigor_gate_fields``, ``latest_backtests_by_strategy`` all define
     "canonical" as the latest row by the same ``created_at DESC, id DESC``
     order). So "the row referenced by a strategy_passports row" is
     operationalized here as: **the latest backtest_results row for every
     strategy_id that has a strategy_passports row** — the conservative
     reading (if a passport's displayed numbers came from any backtest_results
     row, it was this one), and testable without guessing at float-equality
     matches against the denormalized snapshot columns.

Rule 3 is a strict subset of rule 1 whenever ``--keep-recent-n >= 1`` (the
latest row for a strategy IS its most recent row) — it is kept as an explicit,
separately-computed and separately-reported rule anyway, both because it is
what the spec asks for and because it stays correct if ``--keep-recent-n`` is
ever set to 0 for a strategy class that should still protect its passport-facing
row.

Usage (run from the repo root; ``PYTHONPATH=backend`` makes ``archimedes``
importable)::

    PYTHONPATH=backend python -m archimedes.scripts.archive_backtest_results
    PYTHONPATH=backend python -m archimedes.scripts.archive_backtest_results --plan --json
    ARCHIVE_BUCKET=archimedes-backtest-archive-prod \\
        PYTHONPATH=backend python -m archimedes.scripts.archive_backtest_results --archive
    ARCHIVE_BUCKET=archimedes-backtest-archive-prod \\
        PYTHONPATH=backend python -m archimedes.scripts.archive_backtest_results --prune

``--archive`` and ``--prune`` are a PAIR, and the pairing is by S3 prefix
date. Pass the same explicit ``--run-date YYYY-MM-DD`` to both halves of one
operator run: the default is "today in UTC", so an archive that starts at
23:58Z and a prune that starts at 00:03Z resolve different prefixes and the
prune refuses (correctly — it cannot find *its* manifest) after a full archive
has already been paid for.

See ``docs/runbooks/backtest-results-retention.md`` for the full operator
procedure, including the VACUUM step this script deliberately does not run.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from archimedes.db import get_session, init_db
from archimedes.models.backtest_store import BacktestResultRecord
from archimedes.models.strategy_passport_record import StrategyPassportRecord

logger = logging.getLogger(__name__)

DEFAULT_KEEP_RECENT_N = 5
# Archive batches are held ENTIRELY in memory: the batch's rows are loaded with
# their ~412KB/row artifact_json + equity_curve_json payloads, serialized to
# one JSONL string, and gzipped — several copies of the batch coexist at the
# peak, so RSS grows several times faster than the raw row bytes.
#
# Measured 2026-08-30, one batch of production-sized rows (349KB artifact_json
# + 63KB equity_curve_json each), peak process RSS during run_archive:
#     batch_size=100 -> 283MB peak (+128MB over baseline), 41MB of raw rows
#     batch_size=500 -> 942MB peak (+595MB over baseline), 206MB of raw rows
# 100 is the default because a ~1GB spike is a real OOM risk at the 512MB-1GB
# task sizes this runs under; operators with known headroom raise it with
# --batch-size. Re-measure rather than trusting these figures if the schema's
# per-row payload changes.
ARCHIVE_BATCH_SIZE = 100
# Prune batches carry no row payloads (ids in, DELETE out), so this is about
# statement size, not memory, and can stay larger.
PRUNE_BATCH_SIZE = 500
S3_PREFIX = "backtest-results-archive"

# Verified 2026-08-19 column-size averages (see module docstring) — used only
# to produce a human-readable size ESTIMATE in --plan output. Never used to
# decide what gets archived/pruned; that decision is always row-count-exact.
_AVG_ARTIFACT_JSON_BYTES = 349_000
_AVG_EQUITY_CURVE_JSON_BYTES = 63_000


# ─────────────────────────── keep-policy ────────────────────────────────


@dataclass
class KeepSetBreakdown:
    """The three keep-rules' id sets, kept separate for reporting even though
    they overlap — see module docstring on rule 3 being a subset of rule 1."""

    recent_ids: set[int] = field(default_factory=set)
    gate_ids: set[int] = field(default_factory=set)
    passport_ids: set[int] = field(default_factory=set)

    @property
    def keep_ids(self) -> set[int]:
        return self.recent_ids | self.gate_ids | self.passport_ids


def _recent_n_ids(session: Session, keep_recent_n: int) -> set[int]:
    """The ``keep_recent_n`` most recent backtest_results.id values PER strategy_id.

    Window-function rank, matching ``backtest_repository.latest_backtests_by_strategy``'s
    approach exactly (including selecting only the ``id`` column so this never
    drags the ~412KB/row artifact_json + equity_curve_json payloads through the
    client for rows we are merely deciding whether to keep). Works on Postgres
    and SQLite >= 3.25, same as that function.
    """
    if keep_recent_n <= 0:
        return set()

    ranked = select(
        BacktestResultRecord.id.label("id"),
        func.row_number()
        .over(
            partition_by=BacktestResultRecord.strategy_id,
            order_by=(
                BacktestResultRecord.created_at.desc(),
                BacktestResultRecord.id.desc(),
            ),
        )
        .label("rank"),
    ).subquery()
    stmt = select(ranked.c.id).where(ranked.c.rank <= keep_recent_n)
    return {row[0] for row in session.execute(stmt).all()}


def _gate_relevant_ids(session: Session) -> set[int]:
    """Every row the rigor gate actually scored — see module docstring rule 2."""
    stmt = select(BacktestResultRecord.id).where(BacktestResultRecord.deflated_sharpe_ratio.isnot(None))
    return {row[0] for row in session.execute(stmt).all()}


def _passport_referenced_ids(session: Session) -> set[int]:
    """The latest backtest_results row for every strategy_id with a passport —
    see module docstring rule 3 for why this is the correct operationalization
    of "referenced by a strategy_passports row" given no direct FK exists."""
    passport_strategy_ids = {row[0] for row in session.execute(select(StrategyPassportRecord.id)).all()}
    if not passport_strategy_ids:
        return set()

    ranked = select(
        BacktestResultRecord.id.label("id"),
        BacktestResultRecord.strategy_id.label("strategy_id"),
        func.row_number()
        .over(
            partition_by=BacktestResultRecord.strategy_id,
            order_by=(
                BacktestResultRecord.created_at.desc(),
                BacktestResultRecord.id.desc(),
            ),
        )
        .label("rank"),
    ).subquery()
    stmt = select(ranked.c.id).where(
        ranked.c.rank == 1,
        ranked.c.strategy_id.in_(passport_strategy_ids),
    )
    return {row[0] for row in session.execute(stmt).all()}


def compute_keep_breakdown(session: Session, keep_recent_n: int = DEFAULT_KEEP_RECENT_N) -> KeepSetBreakdown:
    """Compute the full keep-set, split by rule, for reporting and for use by
    both --archive (what to skip) and --prune (what must never be deleted)."""
    return KeepSetBreakdown(
        recent_ids=_recent_n_ids(session, keep_recent_n),
        gate_ids=_gate_relevant_ids(session),
        passport_ids=_passport_referenced_ids(session),
    )


def doomed_ids(session: Session, keep_recent_n: int = DEFAULT_KEEP_RECENT_N) -> list[int]:
    """Every backtest_results.id NOT covered by any keep-rule, ascending — the
    candidates for archive-then-prune. Ascending order makes batching
    deterministic and re-runs resumable/comparable."""
    keep = compute_keep_breakdown(session, keep_recent_n).keep_ids
    all_ids = {row[0] for row in session.execute(select(BacktestResultRecord.id)).all()}
    return sorted(all_ids - keep)


# ─────────────────────────── --plan ────────────────────────────────


def build_plan(session: Session, keep_recent_n: int = DEFAULT_KEEP_RECENT_N) -> dict[str, Any]:
    """Read-only report of what --archive followed by --prune would do. Never
    touches S3 and never mutates the database."""
    breakdown = compute_keep_breakdown(session, keep_recent_n)
    total = session.execute(select(func.count(BacktestResultRecord.id))).scalar_one()
    doomed = sorted(({row[0] for row in session.execute(select(BacktestResultRecord.id)).all()}) - breakdown.keep_ids)
    estimated_freed_bytes = len(doomed) * (_AVG_ARTIFACT_JSON_BYTES + _AVG_EQUITY_CURVE_JSON_BYTES)

    return {
        "total_rows": total,
        "keep_recent_n": keep_recent_n,
        "keep": {
            "recent_n_count": len(breakdown.recent_ids),
            "gate_relevant_count": len(breakdown.gate_ids),
            "passport_referenced_count": len(breakdown.passport_ids),
            "union_count": len(breakdown.keep_ids),
        },
        "doomed_count": len(doomed),
        "estimated_freed_bytes": estimated_freed_bytes,
        "estimated_freed_bytes_note": (
            f"doomed_count * (avg artifact_json {_AVG_ARTIFACT_JSON_BYTES}B + "
            f"avg equity_curve_json {_AVG_EQUITY_CURVE_JSON_BYTES}B) — a size ESTIMATE from "
            "2026-08-19 column averages, not a per-row measurement."
        ),
    }


def render_plan_text(plan: dict[str, Any]) -> str:
    keep = plan["keep"]
    mb = plan["estimated_freed_bytes"] / (1024 * 1024)
    lines = [
        "backtest_results retention plan (read-only — nothing was changed)",
        f"  total rows:                 {plan['total_rows']}",
        f"  keep — recent N={plan['keep_recent_n']} per strategy: {keep['recent_n_count']}",
        f"  keep — gate-relevant (DSR not null): {keep['gate_relevant_count']}",
        f"  keep — passport-referenced:  {keep['passport_referenced_count']}",
        f"  keep — union (dedup'd):      {keep['union_count']}",
        f"  DOOMED (would archive+prune): {plan['doomed_count']}",
        f"  estimated space freed:      ~{mb:,.1f} MB ({plan['estimated_freed_bytes_note']})",
    ]
    return "\n".join(lines)


# ─────────────────────────── row (de)serialization ────────────────────────


def _row_to_dict(row: BacktestResultRecord) -> dict[str, Any]:
    """Every column, JSON-safe (datetimes/dates as ISO strings)."""
    out: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime | date):
            value = value.isoformat()
        out[column.name] = value
    return out


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _gzip_jsonl(rows: list[dict[str, Any]]) -> bytes:
    payload = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows) + "\n"
    return gzip.compress(payload.encode("utf-8"))


def _chunk(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


# ─────────────────────────── S3 client ────────────────────────────────


class ArchiveBucketNotConfigured(RuntimeError):
    """Raised when --archive/--prune is invoked without ARCHIVE_BUCKET set."""


def _require_bucket() -> str:
    import os

    bucket = os.environ.get("ARCHIVE_BUCKET")
    if not bucket:
        raise ArchiveBucketNotConfigured(
            "ARCHIVE_BUCKET is not set — refusing to run --archive/--prune. "
            "Set ARCHIVE_BUCKET=<bucket-name> explicitly; there is no default, "
            "because a default would let this script silently write to (or, on "
            "prune, silently believe it verified) the wrong bucket."
        )
    return bucket


def _s3_client():
    import os

    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("s3", region_name=region)


def _manifest_key(prefix_date: str) -> str:
    return f"{S3_PREFIX}/{prefix_date}/manifest.json"


def _batch_key(prefix_date: str, batch_index: int) -> str:
    return f"{S3_PREFIX}/{prefix_date}/batch-{batch_index:05d}.jsonl.gz"


# ─────────────────────────── --archive ────────────────────────────────


def run_archive(
    session: Session,
    *,
    keep_recent_n: int = DEFAULT_KEEP_RECENT_N,
    batch_size: int = ARCHIVE_BATCH_SIZE,
    run_date: date | None = None,
    s3_client=None,
) -> dict[str, Any]:
    """Stream every doomed row to gzipped JSONL batches in S3, plus a manifest.

    Never deletes anything. Raises ArchiveBucketNotConfigured if ARCHIVE_BUCKET
    is unset (checked before any S3 call).
    """
    bucket = _require_bucket()
    client = s3_client or _s3_client()
    run_date = run_date or datetime.now(UTC).date()
    prefix_date = run_date.isoformat()

    ids = doomed_ids(session, keep_recent_n)
    batches_meta: list[dict[str, Any]] = []
    total_archived = 0

    for batch_index, id_batch in enumerate(_chunk(ids, batch_size)):
        rows = (
            session.query(BacktestResultRecord)
            .filter(BacktestResultRecord.id.in_(id_batch))
            .order_by(BacktestResultRecord.id.asc())
            .all()
        )
        # Row-count guard: every id we asked for must come back, or the batch
        # is silently incomplete (a row deleted out from under us between
        # doomed_ids() and this query, e.g. by a concurrent run).
        if len(rows) != len(id_batch):
            found = {r.id for r in rows}
            missing = sorted(set(id_batch) - found)
            raise RuntimeError(
                f"archive batch {batch_index}: expected {len(id_batch)} rows, found {len(rows)} "
                f"(missing ids: {missing}) — refusing to write a short batch as if it were complete"
            )

        payload = _gzip_jsonl([_row_to_dict(r) for r in rows])
        key = _batch_key(prefix_date, batch_index)
        sha256 = _sha256_hex(payload)
        client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/gzip")

        batches_meta.append(
            {
                "batch_index": batch_index,
                "key": key,
                "row_count": len(rows),
                "sha256": sha256,
                "row_ids": [r.id for r in rows],
            }
        )
        total_archived += len(rows)
        logger.info("archived batch %d: %d rows -> s3://%s/%s (sha256=%s)", batch_index, len(rows), bucket, key, sha256)

    manifest = {
        "date": prefix_date,
        "generated_at": datetime.now(UTC).isoformat(),
        "keep_recent_n": keep_recent_n,
        "total_rows": total_archived,
        "batch_count": len(batches_meta),
        "batches": batches_meta,
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    manifest_key = _manifest_key(prefix_date)
    client.put_object(Bucket=bucket, Key=manifest_key, Body=manifest_bytes, ContentType="application/json")
    logger.info(
        "wrote manifest s3://%s/%s (%d rows across %d batches)", bucket, manifest_key, total_archived, len(batches_meta)
    )

    return {
        "bucket": bucket,
        "date": prefix_date,
        "manifest_key": manifest_key,
        "total_rows": total_archived,
        "batch_count": len(batches_meta),
    }


# ─────────────────────────── --prune ────────────────────────────────


class ManifestNotFound(RuntimeError):
    """No matching archive manifest for today exists in S3 — prune refuses."""


class ManifestVerificationFailed(RuntimeError):
    """A manifest exists but it cannot be trusted: a batch's re-downloaded bytes
    don't match its recorded sha256, a batch's actual row ids don't match the
    row_ids the manifest claims for it, or the manifest's own row-count
    bookkeeping is inconsistent."""


def _batch_row_ids(bucket: str, key: str, batch_bytes: bytes) -> list[int]:
    """Decompress one archived batch and return the row ids it actually contains.

    Every failure mode here (not gzip, not UTF-8, not JSONL, a line with no
    ``id``) means the object is not a batch this tool wrote, so it cannot be
    used as proof that anything was archived — all of them raise
    ManifestVerificationFailed rather than propagating a raw decode error.
    """
    try:
        text = gzip.decompress(batch_bytes).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise ManifestVerificationFailed(
            f"s3://{bucket}/{key} is not readable as gzipped UTF-8 JSONL ({exc}) — refusing to prune"
        ) from exc

    ids: list[int] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            ids.append(int(row["id"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ManifestVerificationFailed(
                f"s3://{bucket}/{key} line {lineno} is not an archived row with an integer id "
                f"({exc}) — refusing to prune"
            ) from exc
    return ids


def _load_and_verify_manifest(client, bucket: str, prefix_date: str) -> dict[str, Any]:
    """Read back the run-date's manifest and verify it — internal row-count
    bookkeeping, then each batch byte-for-byte (sha256), then each batch's
    actual CONTENT (the row ids inside the gzip) — before trusting it as proof
    that every row it names is safely archived.

    This is the guard: --prune without a verified manifest for the run date
    must refuse, unconditionally, with no override flag — a guard that can be
    bypassed by a flag is not a guard.
    """
    from botocore.exceptions import ClientError

    manifest_key = _manifest_key(prefix_date)
    try:
        obj = client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(obj["Body"].read())
    except ClientError as exc:
        raise ManifestNotFound(
            f"no archive manifest at s3://{bucket}/{manifest_key} — refusing to prune. Run "
            f"--archive --run-date {prefix_date} first (or, if the archive ran under a "
            "different UTC date because the run straddled midnight, pass that date here)."
        ) from exc

    declared_total = manifest.get("total_rows")
    batches = manifest.get("batches", [])
    sum_batch_rows = sum(b["row_count"] for b in batches)
    if declared_total != sum_batch_rows:
        raise ManifestVerificationFailed(
            f"manifest total_rows={declared_total} does not match sum of its own "
            f"batches' row_count={sum_batch_rows} — manifest is internally inconsistent, refusing to prune"
        )

    for b in batches:
        try:
            batch_obj = client.get_object(Bucket=bucket, Key=b["key"])
            batch_bytes = batch_obj["Body"].read()
        except ClientError as exc:
            raise ManifestVerificationFailed(
                f"manifest references s3://{bucket}/{b['key']} but it could not be read back: {exc}"
            ) from exc
        actual_sha256 = _sha256_hex(batch_bytes)
        if actual_sha256 != b["sha256"]:
            raise ManifestVerificationFailed(
                f"s3://{bucket}/{b['key']} sha256 mismatch: manifest says {b['sha256']}, "
                f"actual object is {actual_sha256} — refusing to prune against unverified archive data"
            )

        declared_ids = b.get("row_ids", [])
        if len(declared_ids) != b["row_count"]:
            raise ManifestVerificationFailed(
                f"batch {b['batch_index']} declares row_count={b['row_count']} but lists "
                f"{len(declared_ids)} row_ids — refusing to prune"
            )

        # CONTENT verification, not bytes-only. A matching sha256 proves the
        # object in S3 is byte-identical to whatever the manifest was written
        # against — it does NOT prove that object contains the rows the
        # manifest claims. A batch re-uploaded by a second archive run (with
        # its sha refreshed in the manifest, or a manifest hand-edited after a
        # partial re-run) can pass the byte check while naming row_ids that
        # were never in it — and prune deletes off those row_ids. So decompress
        # and compare the ids actually present against the ids the manifest
        # licenses us to delete.
        contained_ids = _batch_row_ids(bucket, b["key"], batch_bytes)
        if sorted(contained_ids) != sorted(declared_ids):
            missing = sorted(set(declared_ids) - set(contained_ids))
            extra = sorted(set(contained_ids) - set(declared_ids))
            raise ManifestVerificationFailed(
                f"s3://{bucket}/{b['key']} content does not match the manifest's row_ids: "
                f"{len(missing)} id(s) the manifest claims are archived are NOT in the object "
                f"(first 20: {missing[:20]}), {len(extra)} id(s) in the object are unlisted "
                f"(first 20: {extra[:20]}) — refusing to prune rows this archive does not actually contain"
            )

    return manifest


def run_prune(
    session: Session,
    *,
    keep_recent_n: int = DEFAULT_KEEP_RECENT_N,
    batch_size: int = PRUNE_BATCH_SIZE,
    run_date: date | None = None,
    s3_client=None,
) -> dict[str, Any]:
    """Delete only rows that (a) a verified today's manifest says were
    archived, AND (b) are not in the CURRENT keep-set (recomputed fresh, not
    trusted from archive time — defense in depth against the keep policy
    protecting a row that did not exist, or did not qualify, when it was
    archived).

    Refuses outright (raises) if no verified manifest for today exists. This
    is the load-bearing guard: prune without archive-first must be impossible,
    not merely default-off.
    """
    bucket = _require_bucket()
    client = s3_client or _s3_client()
    run_date = run_date or datetime.now(UTC).date()
    prefix_date = run_date.isoformat()

    manifest = _load_and_verify_manifest(client, bucket, prefix_date)

    archived_ids: list[int] = []
    for b in manifest["batches"]:
        archived_ids.extend(b["row_ids"])
    archived_ids = sorted(set(archived_ids))

    current_keep = compute_keep_breakdown(session, keep_recent_n).keep_ids
    to_delete = sorted(set(archived_ids) - current_keep)
    protected_since_archive = sorted(set(archived_ids) & current_keep)
    if protected_since_archive:
        logger.warning(
            "%d archived row(s) now match the CURRENT keep policy and will be skipped "
            "(never deleting a keep-row, even one already archived): %s",
            len(protected_since_archive),
            protected_since_archive[:20],
        )

    deleted_total = 0
    for id_batch in _chunk(to_delete, batch_size):
        deleted = (
            session.query(BacktestResultRecord)
            .filter(BacktestResultRecord.id.in_(id_batch))
            .delete(synchronize_session=False)
        )
        session.commit()
        if deleted != len(id_batch):
            # Row-count accounting must be exact: a short batch means some id
            # was already gone (double-run?) or the delete silently missed
            # rows — either way, surface it instead of reporting a clean number.
            logger.warning(
                "prune batch: asked to delete %d ids, DB reports %d deleted (some ids already absent?)",
                len(id_batch),
                deleted,
            )
        deleted_total += deleted
        logger.info("pruned batch: %d rows deleted (running total %d)", deleted, deleted_total)

    return {
        "bucket": bucket,
        "date": prefix_date,
        "manifest_total_rows": manifest["total_rows"],
        "archived_ids_count": len(archived_ids),
        "skipped_now_kept_count": len(protected_since_archive),
        "deleted_count": deleted_total,
    }


_VACUUM_GUIDANCE = """
Pruning deletes rows but does not reclaim disk space or shrink table bloat —
Postgres/Aurora needs a VACUUM pass to do that, and this script deliberately
does NOT run it (VACUUM FULL / a plain autovacuum-triggering VACUUM can hold
locks and generate significant I/O; that decision belongs to an operator
watching the table, not a cron'd deletion script). After a prune, run:

    VACUUM (VERBOSE, ANALYZE) backtest_results;

on the primary, during a low-traffic window. If bloat is severe enough that a
plain VACUUM's dead-tuple reclaim isn't enough (it reclaims space for reuse by
future rows but does not shrink the file on disk), consider `pg_repack` instead
of `VACUUM FULL` — pg_repack rebuilds the table without holding an exclusive
lock for the whole operation, which VACUUM FULL does. See
docs/runbooks/backtest-results-retention.md for the full procedure.
""".strip()


# ─────────────────────────── CLI ────────────────────────────────


def _positive_int(value: str) -> int:
    """argparse type for --batch-size: a batch size of 0 or less is not a
    smaller batch, it is a broken one (``_chunk`` cannot step by 0, and a
    negative step silently yields nothing at all — which on --prune would
    report a clean run that deleted nothing while claiming success)."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {parsed}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Archive-then-prune tooling for backtest_results (v8 Lane 3.1). "
            "Read-only by default — --archive and --prune are the only modes that mutate "
            "anything (S3 writes and DB deletes respectively), and both require explicit "
            "flags plus ARCHIVE_BUCKET. --plan (with or without --json) only reports."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Read-only report (default mode).")
    mode.add_argument("--archive", action="store_true", help="Archive doomed rows to S3. Requires ARCHIVE_BUCKET.")
    mode.add_argument(
        "--prune",
        action="store_true",
        help="Delete archived rows from the DB. Requires a verified manifest for the run date in S3.",
    )
    parser.add_argument(
        "--keep-recent-n",
        type=int,
        default=DEFAULT_KEEP_RECENT_N,
        help=f"Rows to keep per strategy_id regardless of any other rule (default {DEFAULT_KEEP_RECENT_N}).",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=ARCHIVE_BATCH_SIZE,
        help=(
            f"Rows per batch (default {ARCHIVE_BATCH_SIZE}). On --archive this is the memory "
            "knob: a whole batch's rows (~412KB each) are held in memory, serialized, and "
            f"gzipped at once — measured peak RSS {ARCHIVE_BATCH_SIZE} rows: 283MB, 500 rows: "
            "942MB. On --prune it is the ids-per-DELETE statement size. Raise it only with "
            "known memory headroom."
        ),
    )
    parser.add_argument(
        "--run-date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "UTC date whose S3 prefix to write (--archive) or read (--prune). Defaults to "
            "today in UTC. Pass it explicitly when a run straddles UTC midnight: without it, "
            "an --archive that started on day N and a --prune that starts minutes later on "
            "day N+1 look for different prefixes, and the prune refuses because 'its' "
            "manifest does not exist. Pin both halves of one run to the same date."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args(argv)
    init_db()

    mode = "prune" if args.prune else "archive" if args.archive else "plan"

    with get_session() as session:
        if mode == "plan":
            plan = build_plan(session, keep_recent_n=args.keep_recent_n)
            print(json.dumps(plan, indent=2) if args.json else render_plan_text(plan))
            return 0

        if mode == "archive":
            try:
                result = run_archive(
                    session,
                    keep_recent_n=args.keep_recent_n,
                    batch_size=args.batch_size,
                    run_date=args.run_date,
                )
            except ArchiveBucketNotConfigured as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(
                json.dumps(result, indent=2)
                if args.json
                else f"archived {result['total_rows']} rows to s3://{result['bucket']}/{result['manifest_key']}"
            )
            return 0

        # prune
        try:
            result = run_prune(
                session,
                keep_recent_n=args.keep_recent_n,
                batch_size=args.batch_size,
                run_date=args.run_date,
            )
        except ArchiveBucketNotConfigured as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except (ManifestNotFound, ManifestVerificationFailed) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        print(
            json.dumps(result, indent=2)
            if args.json
            else (
                f"pruned {result['deleted_count']} rows "
                f"(manifest had {result['manifest_total_rows']}, "
                f"{result['skipped_now_kept_count']} skipped as now-kept)"
            )
        )
        print()
        print(_VACUUM_GUIDANCE)
        return 0


if __name__ == "__main__":
    sys.exit(main())
