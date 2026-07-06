"""Security tests for arxiv_pipeline.render_strategy_module (#920).

The rendered module is later exec_module'd by the analytics-engine strategy
loader, so any attacker-controlled arXiv metadata (a malicious paper title or
id) interpolated raw into the module docstring / CURATOR_NOTE literal would be
a remote code-execution vector. These tests render a module from hostile
metadata and prove the injection does NOT execute.
"""

from __future__ import annotations

from archimedes.services.arxiv_pipeline import PaperMeta, _doc_safe, render_strategy_module


def _render(title: str, arxiv_id: str = "1234.5678") -> str:
    meta = PaperMeta(
        arxiv_id=arxiv_id,
        title=title,
        authors=["A. Author"],
        abstract="abstract",
        year=2020,
        doi=None,
    )
    return render_strategy_module(meta, {"asset_universe": ["SPY"]}, extraction_llm="test-model")


def test_doc_safe_neutralizes_triple_quotes_and_backslashes():
    out = _doc_safe('"""\nimport os\n"""')
    assert '"""' not in out.replace('\\"', "")  # every quote escaped
    assert "\n" not in out  # newlines collapsed


def test_malicious_title_does_not_execute_on_exec():
    # A title that tries to close the docstring and define a global.
    payload = '"""\nINJECTED_BY_TITLE = 1\n"""'
    source = _render(payload)

    compile(source, "<rendered>", "exec")  # must be syntactically valid
    ns: dict = {}
    exec(source, ns)

    assert "INJECTED_BY_TITLE" not in ns, "docstring breakout executed injected code"
    # repr()-based constant round-trips the original title exactly.
    assert ns["PAPER_TITLE"] == payload


def test_malicious_arxiv_id_does_not_break_curator_note():
    # An id trying to break out of the CURATOR_NOTE "..." literal.
    payload = '"; INJECTED_BY_ID = 1; "'
    source = _render("Benign Title", arxiv_id=payload)

    compile(source, "<rendered>", "exec")
    ns: dict = {}
    exec(source, ns)

    assert "INJECTED_BY_ID" not in ns, "CURATOR_NOTE breakout executed injected code"
    assert ns["PAPER_ARXIV_ID"] == payload
    assert "CURATOR_NOTE" in ns and payload in ns["CURATOR_NOTE"]


def test_benign_metadata_unchanged():
    # Legitimate metadata (no quotes/backslashes/newlines) passes through.
    assert _doc_safe("Time Series Momentum") == "Time Series Momentum"
    assert _doc_safe("2211.01234") == "2211.01234"
