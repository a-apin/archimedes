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

Deployability is fail-closed; rendering is honest
-------------------------------------------------
These two are separate questions and this module answers them separately, because
collapsing them is how "we didn't check" turns into "we checked and it failed" on
a user's screen.

* **Deploy** — :attr:`DslLookAheadAudit.passed` is ``True`` for
  ``passed_structural`` and nothing else. ``passed_declared_only`` blocks the
  gate exactly as hard as ``failed`` does. An unfinished audit is not evidence,
  so a strategy resting on one must not reach live funds. There is no
  "probably fine" tier.

* **Render** — :attr:`DslLookAheadAudit.render_state` is a *different* value with
  three cases: ``passed`` / ``not_checked`` / ``failed``. ``passed_declared_only``
  renders ``not_checked``, never ``failed``. Telling a user their strategy
  *failed* a look-ahead audit that never ran is a false accusation, and it is the
  same defect as the reverse — it destroys the information the four-state
  ``pass``/``fail``/``pending``/``degenerate`` convention exists to preserve
  (``docs/architectural-principles.md`` § fail-soft). The label carried into the
  UI says "NOT AUDITED … does not pass the gate", which is both halves of the
  truth: no accusation, no free pass.

The pair is deliberate and must stay decoupled: ``blocks_deploy`` is what the
gate reads, ``render_state`` is what a surface reads, and neither is derivable
from a single boolean.
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

# ── How a status RENDERS (deliberately not how it gates) ──────────────
#
# See the module docstring's "Deployability is fail-closed; rendering is honest".
# `passed_declared_only` blocks deploy AND renders `not_checked`. Any surface
# that maps it to `failed` is telling the user their strategy was caught leaking
# when nothing looked.

RENDER_PASSED = "passed"
RENDER_NOT_CHECKED = "not_checked"
RENDER_FAILED = "failed"

_RENDER_STATE_BY_STATUS: dict[str, str] = {
    PASSED_STRUCTURAL: RENDER_PASSED,
    PASSED_DECLARED_ONLY: RENDER_NOT_CHECKED,
    FAILED: RENDER_FAILED,
}

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
#: subscript on them is a bar offset. ``self.datas[]`` is the rendering
#: :func:`_dotted` produces for ``self.datas[<anything>]`` — see
#: ``_FEED_CONTAINERS``.
_LINE_ROOTS = ("self.data", "self.datas[]")

#: Attribute chains rooted at ``self`` that hold line objects but are ordinary
#: containers themselves — subscripting/iterating them yields a line, and their
#: own key is NOT a bar offset.
_LINE_CONTAINERS = ("self._indicators",)

#: Sequences of *data feeds*. ``self.datas[i]`` picks the i-th feed; ``i`` is a
#: feed index, NOT a bar offset, so classifying it as one would make a perfectly
#: causal multi-feed interpreter (``self.datas[1].close[0]``) fail the audit and
#: take the whole gate down. The index is skipped; everything hanging off it
#: (``self.datas[1].close[...]``) is still audited, because ``_dotted`` collapses
#: the feed index to ``[]`` and ``self.datas[]`` is a line root above.
_FEED_CONTAINERS = ("self.datas",)

#: Annotation suffixes that mark a parameter as a backtrader line, so the
#: parameter name becomes a line local inside that function.
_LINE_ANNOTATIONS = ("LineSeries", "LineIterator", "LineBuffer", "LineActions", "Lines")


def _dotted(node: ast.AST, *, collapse_feed_index: bool = False) -> str | None:
    """Render an attribute chain (``self.data.close``) as a dotted string.

    With ``collapse_feed_index``, a subscript on a known feed container is
    rendered as ``[]`` and the walk continues through it, so
    ``self.datas[0].close`` becomes ``self.datas[].close``. That is what lets the
    verifier audit reads *downstream* of a feed pick while ignoring the pick
    itself.
    """
    parts: list[str] = []
    cur: ast.AST = node
    while True:
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
            continue
        if collapse_feed_index and isinstance(cur, ast.Subscript):
            inner = _dotted(cur.value)
            if inner is not None and inner in _FEED_CONTAINERS:
                parts.append("[]")
                cur = cur.value
                continue
            return None
        break
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    rendered: list[str] = []
    for part in reversed(parts):
        if part == "[]":
            if not rendered:
                return None
            rendered[-1] += "[]"
        else:
            rendered.append(part)
    return ".".join(rendered)


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


#: Names proven non-negative, mapped to the source line from which the proof
#: holds (0 = for the whole lexical scope). See :class:`_InterpreterAccessVisitor`.
NonNegEnv = dict[str, int]


def _is_int_const(node: ast.AST) -> int | None:
    """The integer value of ``node`` when it is a plain int literal, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _provably_nonnegative(node: ast.AST, env: NonNegEnv, lineno: int) -> bool:
    """Can ``node`` be PROVEN ``>= 0`` at source line ``lineno``?

    This is the whole difference between "the verifier refuses to guess" as a
    docstring and as a behaviour. Only three things prove it:

      * a non-negative integer literal;
      * a name in ``env`` — a ``range()``-bound loop/comprehension variable, or a
        parameter the enclosing function guards with ``if <name> < <k>: raise``
        (``k >= 0``) *earlier in the same function body*;
      * addition or multiplication of two things that are themselves proven.

    Everything else — a bare unproven name, a call, an attribute, a subtraction,
    a module constant that might be negative — is NOT proven, and an unproven
    offset is treated exactly like a disproved one.
    """
    const = _is_int_const(node)
    if const is not None:
        return const >= 0
    if isinstance(node, ast.Name):
        proven_from = env.get(node.id)
        return proven_from is not None and lineno > proven_from
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        return _provably_nonnegative(node.left, env, lineno) and _provably_nonnegative(node.right, env, lineno)
    return False


def _classify_offset(node: ast.AST, env: NonNegEnv | None = None, lineno: int | None = None) -> _OffsetVerdict:
    """Prove a bar-offset expression is ``<= 0`` (bar t or earlier).

    backtrader's convention: ``line[0]`` is *now*, ``line[-n]`` is *n bars ago*,
    and ``line[+n]`` would be a future bar. So the whole proof obligation is
    "this offset expression can never be positive".

    Accepted:
      * a non-positive integer constant — ``[0]``, ``[-3]``;
      * the negation of a **provably non-negative** expression — ``[-3]``,
        ``[-i]`` where ``i`` is bound by ``range(20)``, ``[-period]`` inside a
        function that raises for ``period < 1``;
      * subtraction of a **provably non-negative** expression from an offset
        already proven ``<= 0`` — ``[-i - 1]``, ``[-i - j]`` when both ``i`` and
        ``j`` are proven.

    Everything else is REJECTED: a bare name, an addition, a positive constant, a
    slice, a call, and — the case this verifier used to wave through —
    ``[-<anything>]`` whose operand is not provably non-negative. ``_SHIFT = -1``
    at module scope makes ``self.data.close[-_SHIFT]`` a read of bar *t+1*; the
    old rule accepted every ``USub`` on sight, so a mutated interpreter passed
    clean. The verifier refuses to guess: an offset it cannot prove is treated
    exactly like one it disproved.
    """
    env = env if env is not None else {}
    lineno = lineno if lineno is not None else getattr(node, "lineno", 0)

    const = _is_int_const(node)
    if const is not None:
        if const > 0:
            return _OffsetVerdict(False, f"positive bar offset [{const}] reads a FUTURE bar")
        return _OffsetVerdict(True)
    if isinstance(node, ast.Constant):
        return _OffsetVerdict(False, f"non-integer bar offset {node.value!r}")

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        if _provably_nonnegative(node.operand, env, lineno):
            return _OffsetVerdict(True)
        return _OffsetVerdict(
            False,
            f"unprovable bar offset [{ast.unparse(node)}] — "
            f"{ast.unparse(node.operand)!r} is not provably non-negative, so the offset "
            "cannot be shown <= 0 (treated as a FUTURE read)",
        )

    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Sub):
            # (proven <= 0) - (proven >= 0) stays <= 0.
            left = _classify_offset(node.left, env, lineno)
            if not left.ok:
                return left
            right = node.right
            if isinstance(right, ast.UnaryOp) and isinstance(right.op, ast.USub):
                return _OffsetVerdict(False, "subtracting a negative bar offset moves FORWARD in time")
            right_const = _is_int_const(right)
            if right_const is not None and right_const < 0:
                return _OffsetVerdict(False, "subtracting a negative constant moves FORWARD in time")
            if not _provably_nonnegative(right, env, lineno):
                return _OffsetVerdict(
                    False,
                    f"unprovable bar offset [{ast.unparse(node)}] — the subtrahend "
                    f"{ast.unparse(right)!r} is not provably non-negative, so the "
                    "subtraction may move FORWARD in time",
                )
            return _OffsetVerdict(True)
        return _OffsetVerdict(False, f"unprovable bar-offset arithmetic ({type(node.op).__name__})")

    return _OffsetVerdict(False, f"unprovable bar offset expression ({type(node).__name__})")


# ── Non-negativity proofs the verifier is allowed to use ──────────────


def _range_bound_names(iter_node: ast.AST, env: NonNegEnv, lineno: int) -> bool:
    """True when iterating ``iter_node`` can only yield non-negative integers.

    Only ``range(...)`` counts, and only when every argument is itself provably
    non-negative. ``range(-5, 5)`` yields negatives, so ``line[-i]`` over it
    would read forward; it is not proven. ``range(5, 0, -1)`` is rejected too —
    conservative, since a negative step is not provably non-negative.
    """
    if not (isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Name) and iter_node.func.id == "range"):
        return False
    if iter_node.keywords or not 1 <= len(iter_node.args) <= 3:
        return False
    return all(_provably_nonnegative(a, env, lineno) for a in iter_node.args)


def _guarded_nonnegative_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> NonNegEnv:
    """Names a top-level ``if <name> < <k>: raise`` guard proves ``>= 0`` after it.

    Only guards written as a **top-level statement of the function body** count,
    with a body that is exactly one ``raise`` and no ``else``. That is a sound
    (if crude) dominance argument: a top-level statement either raises or falls
    through, and every statement lexically after it in the same body — at any
    nesting depth — runs only on the fall-through. The proof is recorded with the
    guard's line number so an access *above* the guard does not get to use it.

    ``_make_indicator``'s ``if period < 1: raise DSLError(...)`` is what makes
    ``data_line(-period)`` provable; without it that offset is unproven and the
    whole audit fails closed.
    """
    proven: NonNegEnv = {}
    params = {a.arg for a in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]}
    for stmt in func.body:
        if not (isinstance(stmt, ast.If) and len(stmt.body) == 1 and isinstance(stmt.body[0], ast.Raise)):
            continue
        if stmt.orelse:
            continue
        test = stmt.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1):
            continue
        left, op, right = test.left, test.ops[0], test.comparators[0]
        if not isinstance(left, ast.Name) or left.id not in params:
            continue
        bound = _is_int_const(right)
        if bound is None:
            continue
        # Fall-through of `if x < k: raise` is `x >= k`; of `if x <= k: raise` is
        # `x >= k + 1`. Either proves `x >= 0` when that lower bound is >= 0.
        if isinstance(op, ast.Lt):
            lower_bound = bound
        elif isinstance(op, ast.LtE):
            lower_bound = bound + 1
        else:
            continue
        if lower_bound >= 0:
            proven[left.id] = stmt.lineno
    return proven


def _is_line_container(node: ast.AST) -> bool:
    dotted = _dotted(node)
    return dotted is not None and dotted in _LINE_CONTAINERS


def _is_feed_container(node: ast.AST) -> bool:
    """``self.datas`` — a sequence of data feeds, indexed by FEED, not by bar."""
    dotted = _dotted(node)
    return dotted is not None and dotted in _FEED_CONTAINERS


def _is_line_expr(node: ast.AST, line_locals: frozenset[str]) -> bool:
    """True when ``node`` evaluates to a backtrader line object."""
    if isinstance(node, ast.Name):
        return node.id in line_locals
    if isinstance(node, ast.Attribute):
        dotted = _dotted(node, collapse_feed_index=True)
        if dotted is None:
            # An attribute hanging off a name we know holds a line
            # (``feed.close`` where ``feed = self.datas[1]``). Conservative: more
            # expressions get bar-offset-checked, never fewer.
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            return isinstance(root, ast.Name) and root.id in line_locals
        if dotted in _LINE_CONTAINERS or dotted in _FEED_CONTAINERS:
            return False
        if any(dotted == r or dotted.startswith(r + ".") for r in _LINE_ROOTS):
            return True
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and root.id in line_locals
    if isinstance(node, ast.Subscript):
        # self._indicators[alias] -> a line; self.datas[1] -> a feed (line-ish);
        # self.data.close[0] -> a float.
        return _is_line_container(node.value) or _is_feed_container(node.value)
    return False


#: Bound on the alias fixpoint below. Each round can only add names, and the
#: module has a handful, so this never binds in practice; it exists so a
#: pathological input cannot spin.
_LINE_LOCAL_FIXPOINT_ROUNDS = 8


def _collect_line_locals(tree: ast.AST) -> frozenset[str]:
    """Every name that anywhere in the module holds a backtrader line.

    **Order-independent by construction.** The previous single-pass visitor
    bound aliases as it walked, so ``line[1]`` textually *above* its
    ``line = self.data.close`` binding was never checked at all — a leak could
    hide behind nothing more than statement order. This runs the binding rules to
    a fixpoint over the whole tree first, so the checking pass sees the same set
    of aliases no matter where they appear.

    The set is flat (module-wide, not per-scope) and that is deliberate: a name
    holding a line in one function is never a non-line in another here, and a
    false membership only makes MORE expressions get bar-offset-checked. The
    conservative direction is toward over-auditing, never under-auditing.
    """
    known: set[str] = set()

    def bind(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            known.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                bind(elt)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                if _annotation_is_line(arg.annotation):
                    known.add(arg.arg)

    for _ in range(_LINE_LOCAL_FIXPOINT_ROUNDS):
        before = len(known)
        frozen = frozenset(known)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if _is_line_expr(node.value, frozen):
                    for tgt in node.targets:
                        bind(tgt)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                it = node.iter
                # ``for alias, ind in self._indicators.items():`` binds ``ind``.
                if (
                    isinstance(it, ast.Call)
                    and isinstance(it.func, ast.Attribute)
                    and it.func.attr in ("items", "values")
                    and _is_line_container(it.func.value)
                ):
                    if it.func.attr == "values":
                        bind(node.target)
                    elif isinstance(node.target, (ast.Tuple, ast.List)) and len(node.target.elts) == 2:
                        bind(node.target.elts[1])
                elif _is_feed_container(it):
                    # ``for feed in self.datas:`` — each element IS a feed.
                    bind(node.target)
                # Iterating a line container itself yields keys: nothing to bind.
        if len(known) == before:
            break
    return frozenset(known)


class _InterpreterAccessVisitor(ast.NodeVisitor):
    """Collect and classify every bar-indexed read in the interpreter module.

    Two passes, not one:

    1. :func:`_collect_line_locals` resolves every line alias in the tree to a
       fixpoint, so the check below is independent of statement order.
    2. This walk classifies each bar-indexed read, carrying a *scoped* set of
       names proven non-negative (``range()``-bound loop variables, guarded
       parameters) that :func:`_classify_offset` may use in its proof.

    The two sets pull in opposite directions on purpose. Line aliases are flat
    and over-inclusive (audit more). Non-negativity proofs are scoped and
    under-inclusive — they reset at every function boundary and never leak out of
    a loop body — because a wrong membership *there* would let a forward read
    through.
    """

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.sites: list[str] = []
        self._line_locals: frozenset[str] = frozenset()
        self._nonneg: NonNegEnv = {}

    def visit(self, node: ast.AST) -> Any:  # ast.NodeVisitor entry point
        if isinstance(node, ast.Module):
            self._line_locals = _collect_line_locals(node)
        return super().visit(node)

    # -- scoped non-negativity ------------------------------------------

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # A nested def does NOT inherit the enclosing function's proofs: a
        # closure can be called at any time, so the enclosing guard's dominance
        # argument does not carry into it.
        outer, self._nonneg = self._nonneg, _guarded_nonnegative_params(node)
        try:
            self.generic_visit(node)
        finally:
            self._nonneg = outer

    visit_FunctionDef = _visit_function  # ast.NodeVisitor dispatch name
    visit_AsyncFunctionDef = _visit_function

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        added = self._bind_range_target(node.iter, node.target)
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            for name in added:
                self._nonneg.pop(name, None)
        for stmt in node.orelse:
            self.visit(stmt)

    visit_For = _visit_for  # ast.NodeVisitor dispatch name
    visit_AsyncFor = _visit_for

    def _bind_range_target(self, iter_node: ast.AST, target: ast.AST) -> list[str]:
        """Mark a ``for x in range(<non-negative>)`` variable as proven ``>= 0``."""
        lineno = getattr(iter_node, "lineno", 0)
        if not _range_bound_names(iter_node, self._nonneg, lineno):
            return []
        if not isinstance(target, ast.Name) or target.id in self._nonneg:
            return []
        self._nonneg[target.id] = 0
        return [target.id]

    def _visit_comprehension(self, node: ast.AST) -> None:
        added: list[str] = []
        try:
            for gen in node.generators:  # type: ignore[attr-defined]
                self.visit(gen.iter)
                added.extend(self._bind_range_target(gen.iter, gen.target))
                for cond in gen.ifs:
                    self.visit(cond)
            for child in ("elt", "key", "value"):
                sub = getattr(node, child, None)
                if sub is not None:
                    self.visit(sub)
        finally:
            for name in added:
                self._nonneg.pop(name, None)

    visit_ListComp = _visit_comprehension  # ast.NodeVisitor dispatch name
    visit_SetComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension
    visit_DictComp = _visit_comprehension

    # -- the actual checks ----------------------------------------------

    def visit_Subscript(self, node: ast.Subscript) -> None:  # ast.NodeVisitor dispatch name
        loc = f"line {node.lineno}"
        if _is_feed_container(node.value):
            # ``self.datas[i]`` — a FEED index, not a bar offset. Skipped on
            # purpose (see _FEED_CONTAINERS): treating it as a bar offset would
            # fail a causal multi-feed interpreter and take the gate down for
            # every strategy. Reads downstream of it are still audited.
            self.generic_visit(node)
            return
        if _is_line_expr(node.value, self._line_locals):
            src = _dotted(node.value, collapse_feed_index=True) or "<line>"
            verdict = _classify_offset(node.slice, self._nonneg, node.lineno)
            self.sites.append(f"{loc}: {src}[...]")
            if not verdict.ok:
                self.violations.append(f"{loc}: {src}[...] — {verdict.why}")
        elif not _is_line_container(node.value):
            # Catch-all backstop for a line alias this visitor did not model:
            # ANY positive-constant subscript on a self-rooted expression is
            # suspicious, because bar 0 is "now" on every backtrader line.
            dotted = _dotted(node.value)
            if dotted is not None and (dotted == "self" or dotted.startswith("self.")):
                verdict = _classify_offset(node.slice, self._nonneg, node.lineno)
                if not verdict.ok and isinstance(node.slice, ast.Constant):
                    self.violations.append(f"{loc}: {dotted}[...] — {verdict.why}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # ast.NodeVisitor dispatch name
        # backtrader's delayed-line accessor: ``data_line(-period)``.
        if _is_line_expr(node.func, self._line_locals) and len(node.args) == 1 and not node.keywords:
            loc = f"line {node.lineno}"
            src = _dotted(node.func, collapse_feed_index=True) or "<line>"
            verdict = _classify_offset(node.args[0], self._nonneg, node.lineno)
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
    def blocks_deploy(self) -> bool:
        """Fail-closed deployability: anything short of a structural pass blocks.

        ``passed_declared_only`` blocks exactly as hard as ``failed``. This is
        the same fact as ``not self.passed``, named so the call sites that mean
        "may this reach live funds?" read as that question rather than as a
        double negative — and so it stays visibly separate from
        :attr:`render_state`, which answers a different one.
        """
        return not self.passed

    @property
    def render_state(self) -> str:
        """What a user-facing surface should SHOW: passed / not_checked / failed.

        Not the same axis as :attr:`blocks_deploy`. ``passed_declared_only``
        blocks the gate but renders ``not_checked``, because nothing about this
        strategy failed a look-ahead audit — no audit finished. Rendering it as
        ``failed`` would be an accusation the evidence does not support, and it
        would erase the difference between "we found a leak" and "we could not
        look", which is precisely the distinction the four-state rigor
        convention exists to keep.
        """
        return _RENDER_STATE_BY_STATUS.get(self.status, RENDER_NOT_CHECKED)

    @property
    def not_run_reason(self) -> str | None:
        """Why the audit did not run, for a NOT_RUN-style detail line.

        ``None`` when the audit reached a real verdict either way — a caller must
        not turn a genuine PASS or a genuine FAIL into a "not run" note.
        """
        if self.status != PASSED_DECLARED_ONLY:
            return None
        return "; ".join(self.reasons) or "structural look-ahead audit not completed"

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
            # Rendering axis, deliberately distinct from the gating one: a
            # consumer that only has `status` must not have to know that
            # `passed_declared_only` means "not checked", never "failed".
            "render_state": self.render_state,
            "blocks_deploy": self.blocks_deploy,
            "reasons": list(self.reasons),
            "declared_intent": self.declared_intent,
            "interpreter_verified": self.interpreter_verified,
            "broker_cheat_check": self.broker_cheat_check,
            "surface_version": self.surface_version,
            "label": self.label,
        }


def not_run_reason_from_verdict(rigor_verdict: dict[str, Any]) -> str | None:
    """NOT_RUN reason for a persisted rigor-verdict blob, or ``None``.

    The blob form of :attr:`DslLookAheadAudit.not_run_reason`, for the persistence
    path that carries a dict rather than the audit object. ``None`` for a real
    pass, a real fail, and for a pre-change row with no ``look_ahead_audit`` key —
    an old row's ``False`` is not evidence that nothing ran, so it keeps the
    plain FAIL rendering rather than being retro-labelled "not checked".
    """
    if rigor_verdict.get("look_ahead_audit") != PASSED_DECLARED_ONLY:
        return None
    reasons = rigor_verdict.get("look_ahead_reasons") or ()
    return "; ".join(str(r) for r in reasons) or "structural look-ahead audit not completed"


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
    # ``getattr``, not ``spec.look_ahead_safe``. The declared flag is a RECORD of
    # what the generator claimed; this audit does not depend on it and must not
    # depend on its existence either. A follow-on change deletes the field from
    # the DSL outright — when it goes, ``declared_intent`` becomes ``None`` and
    # every verdict here is unchanged, because the flag never had a vote. Reading
    # the attribute directly would have made that deletion a breaking change to
    # the audit and coupled the two merges together for no reason.
    declared_raw = getattr(spec, "look_ahead_safe", None) if spec is not None else None
    declared = None if declared_raw is None else bool(declared_raw)

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
