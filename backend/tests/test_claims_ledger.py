"""``docs/claims-ledger.md`` must cite code that exists and describe a tree that exists.

The ledger is the file that says which public claims are true. A ledger whose citations
have rotted is worse than no ledger: it reads as verification, and it is the thing a
reviewer reaches for instead of re-reading the code. So the citations are executable.

Same guard shape as ``test_agent_quickstart_drift.py`` — parse one committed markdown file
and assert its assertions against the tree — with two properties that are specific to a
ledger and are the reason this file is not just a link checker:

**Self-retiring exemptions.** The ledger cites one path that does not exist yet
(``docs/adr/market-data-sourcing.md``, landing with #1218) and declares it in a
``claims-ledger:pending-paths`` comment. ``test_pending_paths_have_not_landed_yet``
asserts each of those is *still absent*. When the ADR merges, this file goes red and the
row above it must be re-pointed at real evidence. An exemption that cannot expire is a
hole; this one closes itself.

**Open over-claims are pinned to the tree, not just described.** The ledger's
``OVER-CLAIMED`` rows assert that a specific sentence is still live on a specific surface.
``test_open_overclaims_are_still_present`` pins each one. Fixing the copy therefore turns
this test red — deliberately: the same change that scrubs the sentence has to move the row
from ``OVER-CLAIMED`` to ``CHANGED``, which is the whole point of keeping a ledger rather
than a memo.

Hermetic: reads committed files off disk. No DB, no Redis, no RPC, no network, no
``.env``, and no import of ``archimedes``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER = REPO_ROOT / "docs" / "claims-ledger.md"
DOCS_INDEX = REPO_ROOT / "docs" / "README.md"

# The statuses a row is allowed to carry. Adding one is a deliberate act — a new word is a
# new promise to the reader about what the row means — so it goes here and in the ledger's
# own "How to read a row" table, together.
ALLOWED_STATUSES = frozenset({"TRUE", "CHANGED", "RETRACTED", "OVER-CLAIMED", "PENDING ADR MERGE"})

# A citation: a backticked repo-relative path, optionally `:line` or `:line-line`. The
# extension list is what keeps `GET /api/selection-bias/gate` and other backticked
# slash-bearing prose out — those name routes, not files, and there is nothing on disk to
# resolve them against.
#
# A directory component is REQUIRED, with the four repo-root docs as the named exception.
# That is what lets the ledger keep writing a bare `agent.json` or `live_rigor_gate.py` as
# shorthand for a path it already gave in full, without those shorthands being read as
# citations to files at the repo root that do not exist.
_CITATION_RE = re.compile(
    r"`((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|jsx|js|md|json|txt|sol|html|xml|yml|yaml|toml|sh|css)"
    r"|(?:README|CLAUDE|SETUP|AGENTS)\.md)"
    r"(?::(\d+)(?:-\d+)?)?`"
)

_PENDING_BLOCK_RE = re.compile(r"<!--\s*claims-ledger:pending-paths(.*?)-->", re.DOTALL)
_PENDING_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+\.[a-z]+$")

# Symbols the ledger's TRUE rows lean on by name. A rename that silently invalidates a row
# is exactly the rot this file exists to catch, and a path-existence check cannot see it.
_SYMBOL_PINS: tuple[tuple[str, str], ...] = (
    ("backend/archimedes/services/live_rigor_gate.py", "def verdict_from_returns"),
    ("backend/archimedes/services/live_rigor_gate.py", 'DEGENERATE = "degenerate"'),
    ("backend/archimedes/services/rigor_evaluator.py", "DEFAULT_BOARD_FDR_LEVEL = 0.05"),
    ("backend/archimedes/services/rigor_evaluator.py", "def compute_board_level_fdr"),
    ("backend/archimedes/api/leaderboard_schemas.py", "class BoardLevelFdr"),
    ("backend/archimedes/api/rigor_verify_routes.py", "verdict_capped"),
    ("backend/archimedes/api/corpus_routes.py", "kb_artifact_not_found"),
    ("backend/archimedes/api/wallet_routes.py", "_CHALLENGE_TTL = timedelta(minutes=5)"),
    ("backend/archimedes/services/generation_payment.py", 'os.getenv("GENERATION_PAYMENT_REQUIRED")'),
    ("backend/archimedes/agents/generation_pipeline.py", "mirrored on-chain in v1.5"),
    ("backend/archimedes/chain/agent_runner.py", "_commit_trace"),
    ("backend/archimedes/chain/agent_runner.py", "_reveal_trace"),
    ("ui/src/routes.js", "ANON_APP_PAGES"),
    ("ui/src/featureFlags.js", "ROADMAP_SURFACES_ENABLED"),
)

# The ledger records that board-level FDR MOVED to the leaderboard (#1564/#1580), so the
# per-strategy gate module must not define the model again. Narrow on purpose: the file
# still *mentions* the old field names in the comment that records the move, and a blunt
# substring ban would fail on that comment while proving nothing.
_ABSENCE_PIN = ("backend/archimedes/api/selection_bias_routes.py", "class BoardLevelFdr")

# The sentences the ledger's OVER-CLAIMED rows say are still live. See the module
# docstring: fixing one of these SHOULD break this test.
_OPEN_OVERCLAIMS: tuple[tuple[str, str], ...] = (
    ("README.md", "non-custodial vault on the Arc testnet"),
    ("ui/public/llms.txt", "executed in non-custodial USDC"),
    ("ui/public/.well-known/agent.json", "executed in a non-custodial USDC vault"),
    ("ui/index.html", "records the whole decision on Arc public testnet"),
    ("docs/user-stories.md", "into your non-custodial vault on Arc"),
)


def _ledger_text() -> str:
    assert LEDGER.exists(), f"{LEDGER} is missing — the doc index promises it"
    return LEDGER.read_text(encoding="utf-8")


def _citations() -> list[tuple[str, int | None]]:
    return [(m.group(1), int(m.group(2)) if m.group(2) else None) for m in _CITATION_RE.finditer(_ledger_text())]


def _pending_paths() -> list[str]:
    block = _PENDING_BLOCK_RE.search(_ledger_text())
    assert block is not None, "the claims-ledger:pending-paths block is missing — it is part of the contract"
    return [line.strip() for line in block.group(1).splitlines() if _PENDING_PATH_RE.match(line.strip())]


def _claim_rows() -> list[list[str]]:
    """Ledger rows: a markdown table row with a claim, a status, and evidence.

    Two-column tables (the "How to read a row" legend) and separator rows are not claims
    and are skipped here rather than special-cased at each call site.
    """
    rows: list[list[str]] = []
    for line in _ledger_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= {"-", ":"}:
            continue
        if cells[1] == "Status":
            continue
        rows.append(cells)
    return rows


class TestLedgerCitationsResolve:
    """Every path the ledger cites is a real file, at a real line."""

    def test_every_cited_path_exists(self):
        pending = set(_pending_paths())
        missing = [path for path, _ in _citations() if path not in pending and not (REPO_ROOT / path).is_file()]
        assert not missing, (
            "claims-ledger.md cites paths that do not exist: "
            + ", ".join(sorted(set(missing)))
            + ". Fix the citation, or declare the path in the claims-ledger:pending-paths block."
        )

    def test_every_cited_line_is_inside_its_file(self):
        pending = set(_pending_paths())
        out_of_range: list[str] = []
        for path, line in _citations():
            if line is None or path in pending:
                continue
            target = REPO_ROOT / path
            if not target.is_file():
                continue  # reported by the test above; do not double-report
            length = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            if line > length:
                out_of_range.append(f"{path}:{line} (file has {length} lines)")
        assert not out_of_range, "claims-ledger.md cites lines past end of file: " + ", ".join(out_of_range)

    def test_the_citation_parser_is_not_vacuous(self):
        """A ledger that cites nothing would pass every check above."""
        assert len(_citations()) >= 40, f"only {len(_citations())} citations parsed — the parser or the ledger broke"


class TestPendingExemptionsRetireThemselves:
    def test_pending_paths_have_not_landed_yet(self):
        landed = [p for p in _pending_paths() if (REPO_ROOT / p).is_file()]
        assert not landed, (
            "these paths now exist and are no longer 'pending': "
            + ", ".join(landed)
            + ". Re-point the ledger row at the real evidence and drop the exemption."
        )

    def test_the_pending_block_is_parsed(self):
        assert _pending_paths(), "the pending-paths block parsed empty — the exemption check would be vacuous"


class TestLedgerRowsSayOnlyWhatTheyMay:
    def test_every_row_carries_a_declared_status(self):
        bad = [(row[0][:70], row[1]) for row in _claim_rows() if row[1].strip("`") not in ALLOWED_STATUSES]
        assert not bad, (
            "rows with an undeclared status: "
            + "; ".join(f"{claim!r} -> {status!r}" for claim, status in bad)
            + f". Allowed: {sorted(ALLOWED_STATUSES)}"
        )

    def test_every_row_cites_a_file(self):
        """An evidence cell that names only an issue number is a pointer, not evidence.

        The failure this catches is the one the ledger is for: a row that reads as verified
        because it cites *something*, where the something is a link to a discussion.
        """
        uncited = [row[0][:70] for row in _claim_rows() if not _CITATION_RE.search(row[2])]
        assert not uncited, "rows with no file citation in the evidence column: " + "; ".join(repr(c) for c in uncited)

    def test_the_row_parser_is_not_vacuous(self):
        rows = _claim_rows()
        assert len(rows) >= 20, f"only {len(rows)} claim rows parsed — the parser or the ledger broke"

    def test_status_vocabulary_rejects_a_hedge(self):
        """Anti-vacuity for the status check: the allowed set must actually exclude things.

        A vocabulary that admits any word is not a vocabulary. These are the hedges a
        ledger drifts toward when nobody is enforcing it.
        """
        for hedge in ("MOSTLY TRUE", "TRUE-ISH", "SOFTEN", "Keep", "true"):
            assert hedge not in ALLOWED_STATUSES

    def test_the_legend_documents_exactly_the_allowed_statuses(self):
        text = _ledger_text()
        for status in ALLOWED_STATUSES:
            assert f"| `{status}` |" in text, f"{status} is allowed by this test but not explained in the ledger"


class TestLedgerClaimsMatchTheTree:
    def test_cited_symbols_still_exist(self):
        missing = [
            f"{path} :: {needle}"
            for path, needle in _SYMBOL_PINS
            if needle not in (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        ]
        assert not missing, (
            "claims-ledger.md rows lean on symbols that are gone: "
            + "; ".join(missing)
            + ". The claim may still be true, but the row's evidence is not."
        )

    def test_board_fdr_model_did_move_off_the_per_strategy_gate(self):
        path, needle = _ABSENCE_PIN
        text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        assert needle not in text, (
            f"{needle} is defined in {path} again — the ledger says #1564 moved it to the "
            "leaderboard. Either the move was reverted or the ledger row is wrong."
        )

    def test_open_overclaims_are_still_present(self):
        fixed = [
            f"{path} :: {needle}"
            for path, needle in _OPEN_OVERCLAIMS
            if needle not in (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        ]
        assert not fixed, (
            "these over-claims are gone from the tree: "
            + "; ".join(fixed)
            + ". Good — now move the matching claims-ledger.md row from OVER-CLAIMED to CHANGED "
            "and record what fixed it."
        )


class TestLedgerIsIndexed:
    def test_docs_index_links_the_ledger(self):
        """`docs/README.md` says a doc not listed there does not exist. Hold it to that."""
        assert "claims-ledger.md" in DOCS_INDEX.read_text(encoding="utf-8"), (
            "docs/README.md has no row for claims-ledger.md — add one in the same commit"
        )
