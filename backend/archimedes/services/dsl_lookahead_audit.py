"""Real look-ahead audit for DSL-interpreted (fusion/debate-generated) strategies.

Why this module exists
----------------------
The strategy DSL carries a ``look_ahead_safe`` boolean that the **LLM declares
about its own output**. A self-declared boolean is not an audit: nothing outside
the generator ever checked it, and the value was nevertheless persisted, gated
on, and surfaced as if it were one. This module replaces that with a check the
generator does not control.

What is actually proven, and what is not
----------------------------------------
The DSL is a *closed* language: :mod:`archimedes.services.strategy_dsl` validates
against fixed enums of indicators, comparison/logic operators, price operands,
position-sizing types and rebalance cadences, and
:func:`archimedes.services.dsl_to_backtrader.interpret_spec` compiles exactly
those constructs — no ``eval``/``exec``, no arbitrary code. That closure is what
makes look-ahead safety **provable by construction**, in two steps:

1. **Audit the interpreter's data access once** (:func:`verify_interpreter_surface`).
   An AST pass over ``dsl_to_backtrader.py`` classifies every read of a
   backtrader *line* (``self.data.<line>``, an indicator handle, a line-typed
   parameter) and proves each one carries a bar offset ``<= 0`` — bar *t* or
   earlier. A positive constant offset (``self.data.close[1]``), an unprovable
   offset expression, or an addition to an offset is a violation and fails the
   audit loudly. The same pass records whether the DSL's closed enums have grown
   past the set this module has audited.

2. **Assert the spec stays inside that verified surface**
   (:func:`audit_dsl_strategy`). Every indicator, operator, operand, sizing type,
   cadence and parameter-variant period in a validated
   :class:`~archimedes.services.strategy_dsl.StrategySpec` is checked against the
   audited surface. A construct the interpreter audit never covered — an unknown
   indicator, an operator outside the enum, an indicator alias the interpreter
   would never bind, a variant period ``< 1`` — is **out of surface** and FAILS.
   It is never silently waved through.

Alongside the structural proof, the broker-level *cheat-on-close / cheat-on-open*
check (:func:`broker_cheat_check_passed`) is wired into the DSL backtest path.
That check is about execution timing, not signal logic — it is the same narrow
guard ``analytics_engine.engine._lookahead_audit_passed`` performs — and it is
**necessary but nowhere near sufficient** on its own. Both must hold.

Honest limits, stated rather than hidden:

* The three backtrader built-ins (``SimpleMovingAverage``,
  ``ExponentialMovingAverage``, ``RSI``) are library code. This module proves the
  interpreter *feeds them* only bar-``t``-and-earlier lines and *reads* their
  output at offset ``0``; it does not re-derive backtrader's own causality.
* Data-feed correctness (a CSV whose rows are misaligned in time) is a provenance
  question, handled by ``fusion_evaluator``'s ``data_source`` / ``admissible``
  legs, not here.

Three-state result
------------------
``passed_structural``    — steps 1 and 2 both hold and the broker execution-timing
                           check ran and passed. This is the ONLY state that
                           satisfies the gate's LEAK criterion.
``passed_declared_only`` — the structural audit could not be completed (no
                           validated spec, or no broker check was performed), so
                           the only thing supporting the claim is the LLM's own
                           declaration. Explicitly **NOT** a pass for the gate.
``failed``               — a real violation: an out-of-surface construct, an
                           interpreter that reads future bars, or a broker
                           configured to cheat.
"""

from __future__ import annotations

import ast
import inspect
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from archimedes.services import strategy_dsl
from archimedes.services.strategy_dsl import StrategySpec

logger = logging.getLogger(__name__)

# ── Three-state audit status ──────────────────────────────────────────

PASSED_STRUCTURAL = "passed_structural"
PASSED_DECLARED_ONLY = "passed_declared_only"
FAILED = "failed"

#: Every status this module can return, for callers that validate/serialize it.
AUDIT_STATUSES = frozenset({PASSED_STRUCTURAL, PASSED_DECLARED_ONLY, FAILED})

#: Bumped whenever the audited surface below changes in a way that alters what
#: ``passed_structural`` means. Persisted alongside the verdict so an old row is
#: distinguishable from one graded under the current surface.
VERIFIED_SURFACE_VERSION = "dsl-la-1"

# ── The audited surface ───────────────────────────────────────────────
#
# These are the constructs whose interpreter implementation has been read and
# proven bar-t-and-earlier. They are pinned HERE rather than imported from
# strategy_dsl on purpose: if someone widens the DSL's enums without re-auditing
# the interpreter, the two sets diverge and every spec using the new construct
# fails this audit instead of inheriting a pass it never earned.

VERIFIED_INDICATORS = frozenset({"sma", "ema", "rsi", "realized_vol", "momentum"})
VERIFIED_COMPARISON_OPS = frozenset({"gt", "lt", "gte", "lte"})
VERIFIED_LOGIC_OPS = frozenset({"and", "or", "not"})
VERIFIED_PRICE_OPERANDS = frozenset({"close", "open", "high", "low", "volume"})
VERIFIED_POSITION_SIZING = frozenset(
    {
        "full_invested_when_in_market",
        "equal_weight",
        "inverse_vol",
        "volatility_target",
    }
)
VERIFIED_REBALANCE_FREQUENCIES = frozenset({"daily", "weekly", "monthly"})

#: ``realized_vol`` is in the DSL enum but ``_make_indicator`` raises ``DSLError``
#: for it — it has no interpreter implementation to audit. A spec naming it never
#: reaches a backtest, so it is recorded here rather than silently treated as
#: proven.
_UNIMPLEMENTED_INDICATORS = frozenset({"realized_vol"})


# ── Interpreter bar-offset verifier (AST) ─────────────────────────────

#: Attribute chains rooted at ``self`` that ARE backtrader line objects, so a
#: subscript on them is a bar offset.
_LINE_ROOTS = ("self.data", "self.datas")

#: Attribute chains rooted at ``self`` that hold line objects but are ordinary
#: containers themselves — subscripting/iterating them yields a line, and their
#: own key is NOT a bar offset.
_LINE_CONTAINERS = ("self._indicators",)

#: Annotation suffixes that mark a parameter as a backtrader line, so the
#: parameter name becomes a line local inside that function.
_LINE_ANNOTATIONS = ("LineSeries", "LineIterator", "LineBuffer", "LineActions", "Lines")


def _dotted(node: ast.AST) -> str | None:
    """Render an attribute chain (``self.data.close``) as a dotted string."""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    return ".".join(reversed(parts))


def _annotation_is_line(ann: ast.AST | None) -> bool:
    dotted = _dotted(ann) if ann is not None else None
    if dotted is None:
        return False
    return dotted.rsplit(".", 1)[-1] in _LINE_ANNOTATIONS


class _OffsetVerdict:
    """Why an offset expression was accepted or rejected."""

    __slots__ = ("ok", "why")

    def __init__(self, ok: bool, why: str = "") -> None:
        self.ok = ok
        self.why = why


def _classify_offset(node: ast.AST) -> _OffsetVerdict:
    """Prove a bar-offset expression is ``<= 0`` (bar t or earlier).

    backtrader's convention: ``line[0]`` is *now*, ``line[-n]`` is *n bars ago*,
    and ``line[+n]`` would be a future bar. So the whole proof obligation is
    "this offset expression can never be positive".

    Accepted:
      * a non-positive integer constant — ``[0]``, ``[-3]``;
      * a unary negation of anything non-negated — ``[-i]``, ``[-period]``;
      * subtraction whose left side is already proven non-positive — ``[-i - 1]``.

    Everything else — a bare name, an addition, a positive constant, a slice, a
    call — is REJECTED. The verifier refuses to guess: an offset it cannot prove
    is treated exactly like one it disproved.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            return _OffsetVerdict(False, f"non-integer bar offset {node.value!r}")
        if node.value > 0:
            return _OffsetVerdict(False, f"positive bar offset [{node.value}] reads a FUTURE bar")
        return _OffsetVerdict(True)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        # -(-x) would be positive; refuse to unwrap a double negation.
        if isinstance(node.operand, ast.UnaryOp) and isinstance(node.operand.op, ast.USub):
            return _OffsetVerdict(False, "double-negated bar offset cannot be proven non-positive")
        return _OffsetVerdict(True)

    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Sub):
            # (proven <= 0) - (anything non-negative) stays <= 0. The right side
            # must itself be a plain non-negative constant or a name; a negated
            # right side would ADD.
            left = _classify_offset(node.left)
            if not left.ok:
                return left
            right = node.right
            if isinstance(right, ast.UnaryOp) and isinstance(right.op, ast.USub):
                return _OffsetVerdict(False, "subtracting a negative bar offset moves FORWARD in time")
            if isinstance(right, ast.Constant) and isinstance(right.value, int) and right.value < 0:
                return _OffsetVerdict(False, "subtracting a negative constant moves FORWARD in time")
            return _OffsetVerdict(True)
        return _OffsetVerdict(False, f"unprovable bar-offset arithmetic ({type(node.op).__name__})")

    return _OffsetVerdict(False, f"unprovable bar offset expression ({type(node).__name__})")


class _InterpreterAccessVisitor(ast.NodeVisitor):
    """Collect and classify every bar-indexed read in the interpreter module."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.sites: list[str] = []
        # Names known to hold a backtrader line, per enclosing scope. A single
        # flat set is deliberate: the module is small and a name that holds a
        # line in one function is never a non-line in another, so flat is both
        # sufficient and strictly more conservative (more expressions get
        # checked, never fewer).
        self._line_locals: set[str] = set()

    # -- line detection -------------------------------------------------

    def _is_line_container(self, node: ast.AST) -> bool:
        dotted = _dotted(node)
        return dotted is not None and dotted in _LINE_CONTAINERS

    def _is_line_expr(self, node: ast.AST) -> bool:
        """True when ``node`` evaluates to a backtrader line object."""
        if isinstance(node, ast.Name):
            return node.id in self._line_locals
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted is None:
                return False
            if dotted in _LINE_CONTAINERS:
                return False
            # self.data / self.datas and any line attribute hanging off them.
            return any(dotted == root or dotted.startswith(root + ".") for root in _LINE_ROOTS)
        if isinstance(node, ast.Subscript):
            # self._indicators[alias] -> a line; self.data.close[0] -> a value.
            return self._is_line_container(node.value)
        return False

    # -- alias tracking -------------------------------------------------

    def _bind_line_targets(self, target: ast.AST) -> None:
        """Record ``target`` (a Name or a tuple of Names) as holding a line."""
        if isinstance(target, ast.Name):
            self._line_locals.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_line_targets(elt)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if _annotation_is_line(arg.annotation):
                self._line_locals.add(arg.arg)
        self.generic_visit(node)

    visit_FunctionDef = _visit_function  # ast.NodeVisitor dispatch name
    visit_AsyncFunctionDef = _visit_function

    def visit_Assign(self, node: ast.Assign) -> None:  # ast.NodeVisitor dispatch name
        if self._is_line_expr(node.value):
            for tgt in node.targets:
                self._bind_line_targets(tgt)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # ast.NodeVisitor dispatch name
        # ``for alias, ind in self._indicators.items():`` binds ``ind`` to a line.
        it = node.iter
        if (
            isinstance(it, ast.Call)
            and isinstance(it.func, ast.Attribute)
            and it.func.attr in ("items", "values")
            and self._is_line_container(it.func.value)
        ):
            if it.func.attr == "values":
                self._bind_line_targets(node.target)
            elif isinstance(node.target, (ast.Tuple, ast.List)) and len(node.target.elts) == 2:
                self._bind_line_targets(node.target.elts[1])
        elif self._is_line_container(it):
            # Iterating the container itself yields keys, not lines — nothing to bind.
            pass
        self.generic_visit(node)

    # -- the actual checks ----------------------------------------------

    def visit_Subscript(self, node: ast.Subscript) -> None:  # ast.NodeVisitor dispatch name
        loc = f"line {node.lineno}"
        if self._is_line_expr(node.value):
            src = _dotted(node.value) or "<line>"
            verdict = _classify_offset(node.slice)
            self.sites.append(f"{loc}: {src}[...]")
            if not verdict.ok:
                self.violations.append(f"{loc}: {src}[...] — {verdict.why}")
        elif not self._is_line_container(node.value):
            # Catch-all backstop for a line alias this visitor did not model:
            # ANY positive-constant subscript on a self-rooted expression is
            # suspicious, because bar 0 is "now" on every backtrader line.
            dotted = _dotted(node.value)
            if dotted is not None and (dotted == "self" or dotted.startswith("self.")):
                verdict = _classify_offset(node.slice)
                if not verdict.ok and isinstance(node.slice, ast.Constant):
                    self.violations.append(f"{loc}: {dotted}[...] — {verdict.why}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # ast.NodeVisitor dispatch name
        # backtrader's delayed-line accessor: ``data_line(-period)``.
        if self._is_line_expr(node.func) and len(node.args) == 1 and not node.keywords:
            loc = f"line {node.lineno}"
            src = _dotted(node.func) or "<line>"
            verdict = _classify_offset(node.args[0])
            self.sites.append(f"{loc}: {src}(...)")
            if not verdict.ok:
                self.violations.append(f"{loc}: {src}(...) — {verdict.why}")
        self.generic_visit(node)


@dataclass(frozen=True)
class InterpreterSurfaceAudit:
    """Result of auditing the DSL interpreter's own data access."""

    #: True when every bar-indexed read in the interpreter is provably ``<= 0``.
    bar_access_verified: bool
    #: Human-readable violations; empty iff ``bar_access_verified``.
    violations: tuple[str, ...] = ()
    #: Number of bar-indexed read sites the AST pass actually classified. Zero
    #: would mean the pass matched nothing — a broken verifier, not a clean one.
    sites_checked: int = 0
    #: DSL enum members that have grown past the audited surface, per enum.
    unverified_extensions: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.bar_access_verified and self.sites_checked > 0


@lru_cache(maxsize=1)
def verify_interpreter_surface() -> InterpreterSurfaceAudit:
    """Audit ``dsl_to_backtrader``'s data access once, and cache the result.

    This is step 1 of the proof: every read the interpreter performs on a
    backtrader line carries a bar offset of ``0`` (now) or negative (earlier).
    Cached because the source cannot change within a process; the cache is
    cleared by :func:`reset_interpreter_surface_cache` in tests.
    """
    try:
        from archimedes.services import dsl_to_backtrader

        source = inspect.getsource(dsl_to_backtrader)
        tree = ast.parse(source)
    except Exception as exc:  # pragma: no cover - unreadable source is a hard fail
        logger.error("DSL interpreter surface audit could not read the interpreter source: %s", exc)
        return InterpreterSurfaceAudit(
            bar_access_verified=False,
            violations=(f"interpreter source unavailable for audit: {exc}",),
            sites_checked=0,
        )

    visitor = _InterpreterAccessVisitor()
    visitor.visit(tree)

    extensions: dict[str, tuple[str, ...]] = {}
    for enum_name, live, verified in (
        ("indicators", strategy_dsl.INDICATOR_NAMES, VERIFIED_INDICATORS),
        ("comparison_ops", strategy_dsl.COMPARISON_OPS, VERIFIED_COMPARISON_OPS),
        ("logic_ops", strategy_dsl.LOGIC_OPS, VERIFIED_LOGIC_OPS),
        # _PRICE_OPERANDS is module-private but it IS the enum this audit has to
        # compare against — reading it here is the drift check, not a leak.
        ("price_operands", strategy_dsl._PRICE_OPERANDS, VERIFIED_PRICE_OPERANDS),  # noqa: SLF001
        ("position_sizing", strategy_dsl.POSITION_SIZING_TYPES, VERIFIED_POSITION_SIZING),
        ("rebalance_frequencies", strategy_dsl.REBALANCE_FREQUENCIES, VERIFIED_REBALANCE_FREQUENCIES),
    ):
        extra = tuple(sorted(set(live) - set(verified)))
        if extra:
            extensions[enum_name] = extra
            logger.warning(
                "DSL enum %r has grown past the audited look-ahead surface: %s — "
                "specs using these constructs will FAIL the structural audit until "
                "the interpreter is re-audited and dsl_lookahead_audit is updated.",
                enum_name,
                extra,
            )

    audit = InterpreterSurfaceAudit(
        bar_access_verified=not visitor.violations,
        violations=tuple(visitor.violations),
        sites_checked=len(visitor.sites),
        unverified_extensions=extensions,
    )
    if not audit.ok:
        logger.error(
            "DSL interpreter surface audit FAILED (%d bar-access sites checked): %s",
            audit.sites_checked,
            audit.violations or ("no bar-access sites found — the verifier matched nothing",),
        )
    return audit


def reset_interpreter_surface_cache() -> None:
    """Clear the cached interpreter audit (tests that patch the interpreter).

    Tolerates ``verify_interpreter_surface`` having been monkeypatched with a
    plain callable that has no ``cache_clear`` — a test tearing down such a patch
    should not raise on its way out.
    """
    clear = getattr(verify_interpreter_surface, "cache_clear", None)
    if clear is not None:
        clear()


# ── Broker execution-timing check ─────────────────────────────────────


def broker_cheat_check_passed(cerebro: Any) -> bool:
    """Broker execution-timing check: cheat-on-close / cheat-on-open must be OFF.

    ``coc``/``coo`` let the broker fill an order at the SAME bar's close/open that
    produced the signal — the signal is generated from a price the fill then
    receives, which is look-ahead at the execution layer even when the signal
    logic itself is clean.

    This is the narrow guard ``analytics_engine.engine._lookahead_audit_passed``
    performs, wired into the DSL backtest path so both engines charge the same
    check. It is **necessary and not sufficient**: on its own it says nothing
    about signal logic, which is what :func:`audit_dsl_strategy`'s structural
    verifier covers. A cerebro that does not expose the broker params fails
    closed — an unverifiable broker is not a verified one.
    """
    try:
        params = cerebro.broker.p
    except AttributeError:
        logger.warning("broker cheat check: cerebro exposes no broker params — failing closed")
        return False
    coc = bool(getattr(params, "coc", False))
    coo = bool(getattr(params, "coo", False))
    if coc or coo:
        logger.error(
            "broker cheat check FAILED: cheat_on_close=%s cheat_on_open=%s — the broker "
            "would fill at the same bar that generated the signal",
            coc,
            coo,
        )
    return not coc and not coo


# ── Spec-level structural audit ───────────────────────────────────────


@dataclass(frozen=True)
class DslLookAheadAudit:
    """The audited look-ahead verdict for one DSL strategy.

    ``status`` is the persisted/gated value. ``declared_intent`` is the LLM's own
    ``look_ahead_safe`` boolean, kept only as a record of what the generator
    claimed — it never decides ``status``.
    """

    status: str
    reasons: tuple[str, ...] = ()
    declared_intent: bool | None = None
    interpreter_verified: bool = False
    broker_cheat_check: bool | None = None
    surface_version: str = VERIFIED_SURFACE_VERSION

    @property
    def passed(self) -> bool:
        """The LEAK criterion. ONLY a completed structural audit passes.

        ``passed_declared_only`` is deliberately falsy: an LLM's self-declaration
        is not evidence, so a strategy backed by nothing else must not clear the
        gate's look-ahead leg.
        """
        return self.status == PASSED_STRUCTURAL

    @property
    def label(self) -> str:
        """Honest one-line summary for the passport / API."""
        if self.status == PASSED_STRUCTURAL:
            return (
                "PASS (structural): every indicator and condition in this spec is inside the "
                f"audited DSL surface {self.surface_version}, whose interpreter provably reads "
                "only bar t and earlier; broker cheat-on-close/open is off"
            )
        if self.status == PASSED_DECLARED_ONLY:
            detail = "; ".join(self.reasons) or "structural audit not completed"
            return f"NOT AUDITED (LLM self-declared look_ahead_safe only — does not pass the gate): {detail}"
        detail = "; ".join(self.reasons) or "look-ahead violation"
        return f"FAIL: {detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "declared_intent": self.declared_intent,
            "interpreter_verified": self.interpreter_verified,
            "broker_cheat_check": self.broker_cheat_check,
            "surface_version": self.surface_version,
            "label": self.label,
        }


def _parse_alias(alias: str) -> tuple[str, int] | None:
    """Split ``"sma_200"`` into ``("sma", 200)``; ``None`` when unparseable."""
    parts = alias.rsplit("_", 1)
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _audit_indicator_alias(alias: str, reasons: list[str]) -> None:
    parsed = _parse_alias(alias)
    if parsed is None:
        reasons.append(f"indicator alias {alias!r} is not a '<name>_<period>' the interpreter can bind")
        return
    name, period = parsed
    if name not in VERIFIED_INDICATORS:
        reasons.append(
            f"indicator {name!r} (alias {alias!r}) is outside the audited surface {VERIFIED_SURFACE_VERSION}"
        )
        return
    if name in _UNIMPLEMENTED_INDICATORS:
        reasons.append(f"indicator {name!r} (alias {alias!r}) has no audited interpreter implementation")
        return
    if period < 1:
        # ``momentum`` compiles to ``data_line / data_line(-period) - 1``; a
        # period of 0 makes that offset bar t itself, and a negative period
        # makes it a FUTURE bar.
        reasons.append(f"indicator period {period} in alias {alias!r} does not resolve to a strictly past bar")


def _audit_condition(cond: Any, path: str, known_aliases: set[str], reasons: list[str]) -> None:
    """Walk a condition tree, rejecting anything outside the audited surface."""
    if not isinstance(cond, dict) or len(cond) != 1:
        reasons.append(f"{path}: condition is not a single-operator node the interpreter can evaluate")
        return

    op = next(iter(cond))
    args = cond[op]

    if op in VERIFIED_LOGIC_OPS:
        if op == "not":
            _audit_condition(args, f"{path}.not", known_aliases, reasons)
            return
        if not isinstance(args, list):
            reasons.append(f"{path}: {op!r} operands are not a list")
            return
        for i, child in enumerate(args):
            _audit_condition(child, f"{path}.{op}[{i}]", known_aliases, reasons)
        return

    if op in VERIFIED_COMPARISON_OPS:
        if not isinstance(args, list) or len(args) != 2:
            reasons.append(f"{path}: {op!r} does not take exactly 2 operands")
            return
        for arg in args:
            if isinstance(arg, bool):
                reasons.append(f"{path}: boolean operand {arg!r} is not a numeric the interpreter compares")
                continue
            if isinstance(arg, (int, float)):
                continue
            if not isinstance(arg, str):
                reasons.append(f"{path}: operand {arg!r} is neither a number nor a named series")
                continue
            if arg in VERIFIED_PRICE_OPERANDS:
                continue
            if arg in known_aliases:
                continue
            # An alias the spec never declared is never bound into ``_bar_values``,
            # so the interpreter would compare against the raw string. Whatever it
            # reads, it is not an audited bar-t series.
            reasons.append(
                f"{path}: operand {arg!r} is outside the audited surface "
                f"(not a price operand and not a declared indicator of this spec)"
            )
        return

    reasons.append(f"{path}: operator {op!r} is outside the audited surface {VERIFIED_SURFACE_VERSION}")


def audit_dsl_strategy(
    spec: StrategySpec | None,
    *,
    broker_cheat_check: bool | None = None,
) -> DslLookAheadAudit:
    """The real look-ahead audit for a DSL-interpreted strategy.

    Args:
        spec: The **validated** :class:`StrategySpec` that will be (or was)
            interpreted. ``None`` means there is nothing to verify structurally —
            the verdict degrades to ``passed_declared_only``, which does not pass
            the gate.
        broker_cheat_check: Result of :func:`broker_cheat_check_passed` for the
            cerebro that ran this strategy. ``False`` FAILS outright. ``None``
            means the check never ran, so the audit is incomplete and degrades to
            ``passed_declared_only`` rather than claiming a structural pass.

    Returns:
        A :class:`DslLookAheadAudit`. Only ``passed_structural`` satisfies the
        gate's LEAK criterion.
    """
    declared = bool(spec.look_ahead_safe) if spec is not None else None

    if broker_cheat_check is False:
        return DslLookAheadAudit(
            status=FAILED,
            reasons=("broker is configured to cheat on close/open — orders fill on the signal's own bar",),
            declared_intent=declared,
            interpreter_verified=False,
            broker_cheat_check=False,
        )

    if spec is None:
        return DslLookAheadAudit(
            status=PASSED_DECLARED_ONLY,
            reasons=("no validated StrategySpec supplied — nothing to verify structurally",),
            declared_intent=None,
            interpreter_verified=False,
            broker_cheat_check=broker_cheat_check,
        )

    surface = verify_interpreter_surface()
    if not surface.ok:
        return DslLookAheadAudit(
            status=FAILED,
            reasons=("DSL interpreter failed its own bar-access audit:", *surface.violations)
            if surface.violations
            else ("DSL interpreter bar-access audit matched no read sites — the verifier is broken",),
            declared_intent=declared,
            interpreter_verified=False,
            broker_cheat_check=broker_cheat_check,
        )

    reasons: list[str] = []

    if spec.rebalance_frequency not in VERIFIED_REBALANCE_FREQUENCIES:
        reasons.append(
            f"rebalance_frequency {spec.rebalance_frequency!r} is outside the audited surface "
            f"{VERIFIED_SURFACE_VERSION}"
        )

    sizing_type = spec.position_sizing.get("type") if isinstance(spec.position_sizing, dict) else None
    if sizing_type not in VERIFIED_POSITION_SIZING:
        reasons.append(f"position_sizing.type {sizing_type!r} is outside the audited surface")

    for alias in spec.indicators:
        _audit_indicator_alias(alias, reasons)

    known_aliases = set(spec.indicators)
    _audit_condition(spec.entry, "entry", known_aliases, reasons)
    _audit_condition(spec.exit, "exit", known_aliases, reasons)

    # Parameter variants become interpreter periods via ``interpret_variant``, so
    # they are part of the compiled surface and get the same period proof.
    if spec.parameter_variants:
        for key, values in spec.parameter_variants.items():
            if key not in known_aliases:
                reasons.append(f"parameter_variants[{key!r}] does not name a declared indicator of this spec")
                continue
            base = _parse_alias(key)
            base_name = base[0] if base else key
            for v in values:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    reasons.append(f"parameter_variants[{key!r}] value {v!r} is not a numeric period")
                    continue
                _audit_indicator_alias(f"{base_name}_{int(v)}", reasons)

    if reasons:
        return DslLookAheadAudit(
            status=FAILED,
            reasons=tuple(reasons),
            declared_intent=declared,
            interpreter_verified=True,
            broker_cheat_check=broker_cheat_check,
        )

    if broker_cheat_check is None:
        return DslLookAheadAudit(
            status=PASSED_DECLARED_ONLY,
            reasons=(
                "spec is inside the verified surface, but no broker execution-timing "
                "(cheat-on-close/open) check was performed for this run",
            ),
            declared_intent=declared,
            interpreter_verified=True,
            broker_cheat_check=None,
        )

    return DslLookAheadAudit(
        status=PASSED_STRUCTURAL,
        reasons=(),
        declared_intent=declared,
        interpreter_verified=True,
        broker_cheat_check=True,
    )
