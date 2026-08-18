"""Every @limiter.limit route must give slowapi a Response to inject into (#1182).

The failure this pins: slowapi's ``_inject_headers`` needs a
``starlette.responses.Response`` to attach rate-limit headers to. A decorated
handler that returns a plain dict/pydantic model and does NOT declare a
``response: Response`` parameter raises
``Exception: parameter `response` must be an instance of starlette.responses.Response``
— **after** the handler has done all its work, converting every SUCCESSFUL
call into a 500. That is precisely how ``GET /api/selection-bias/gate/{id}``
(the passport's "verify this yourself" endpoint) and all three quant-lab
portfolio endpoints were fully down in production while ~3,000 tests stayed
green: the suite runs with the limiter disabled (TESTING), so the decorator's
header-injection path never executes in CI.

Because the runtime path is untestable under TESTING, the invariant is
enforced structurally: an AST walk over every route module, requiring each
``@limiter.limit``-decorated function to either declare a ``response``
parameter or visibly return a Response object. The walk is proven
discriminating by test_the_invariant_catches_a_missing_response_param below.
"""

from __future__ import annotations

import ast
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "archimedes" / "api"


def _limited_functions(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "limit"
                for d in node.decorator_list
            ):
                yield node


def _satisfies_slowapi_contract(func: ast.AST) -> bool:
    params = {a.arg for a in func.args.args + func.args.kwonlyargs}
    if "response" in params:
        return True
    # Returning a Response object directly also satisfies slowapi — it injects
    # into the returned instance. Detect `return XxxResponse(...)` shapes.
    return any(
        isinstance(n, ast.Return)
        and isinstance(n.value, ast.Call)
        and (
            (isinstance(n.value.func, ast.Name) and n.value.func.id.endswith("Response"))
            or (isinstance(n.value.func, ast.Attribute) and n.value.func.attr.endswith("Response"))
        )
        for n in ast.walk(func)
    )


def test_every_rate_limited_route_satisfies_the_slowapi_contract():
    offenders = []
    for path in sorted(API_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in _limited_functions(tree):
            if not _satisfies_slowapi_contract(func):
                offenders.append(f"{path.name}:{func.lineno} {func.name}")
    assert not offenders, (
        "@limiter.limit routes without a `response: Response` param (or a direct "
        f"Response return): {offenders}. slowapi raises AFTER the handler succeeds, "
        "so each of these 500s on every successful production call while the "
        "TESTING-disabled limiter keeps CI green. Add `response: Response  # noqa: ARG001`."
    )


def test_the_invariant_catches_a_missing_response_param():
    """Adversarial pass on the guard itself: the exact pre-fix signature of
    evaluate_strategy_rigor (#1182) must be flagged, and the fixed shape must
    not be."""
    broken = ast.parse(
        "@limiter.limit('10/minute')\n"
        "async def gate(request: Request, strategy_id: str):\n"
        "    return {'ok': True}\n"
    )
    assert [f.name for f in _limited_functions(broken)] == ["gate"]
    assert not _satisfies_slowapi_contract(next(_limited_functions(broken)))

    fixed = ast.parse(
        "@limiter.limit('10/minute')\n"
        "async def gate(request: Request, response: Response, strategy_id: str):\n"
        "    return {'ok': True}\n"
    )
    assert _satisfies_slowapi_contract(next(_limited_functions(fixed)))

    returns_response = ast.parse(
        "@limiter.limit('10/minute')\n"
        "async def stream(request: Request):\n"
        "    return StreamingResponse(gen())\n"
    )
    assert _satisfies_slowapi_contract(next(_limited_functions(returns_response)))
