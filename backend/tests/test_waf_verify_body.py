"""The 8 KB ceiling on POST /api/rigor/verify — at the edge and in the app (#1749).

`infra/waf.tf` attached `AWSManagedRulesCommonRuleSet` to the ALB in full BLOCK
mode with no `rule_action_override` and no `scope_down_statement`. Its
`SizeRestrictions_BODY` rule blocks any body over the WAF body-inspection limit
for a REGIONAL web ACL — 8,192 bytes. `POST /api/rigor/verify` serialises one
JSON object per bar and had no `max_length`, so a real returns series crossed
8 KB at ~163 rows. Dogfood 2026-09-01 (agent-cli), deterministic:

    160 rows / 8,076 B -> 200     MEASURED
    165 rows / 8,326 B -> 403     MEASURED (HTML from awselb/2.0, not FastAPI)
    252 rows           -> 403     MEASURED, every time  <- ONE TRADING YEAR

(50.5 B/row including the envelope, from those two measured points; the 8,192
limit is crossed at ~163 rows.)

The endpoint that exists to give a DSR/OOS verdict statistical power rejected
exactly the sample size that gives it power.

The fix is deliberately SCOPED, and these tests exist to keep it scoped:

  * `SizeRestrictions_BODY` is overridden to `count` on the CRS group — and
    NOTHING ELSE is. A blanket `override_action { count {} }` on the group, or a
    second `rule_action_override`, would silently disable real controls;
  * a custom rule at priority 11 (immediately after the CRS group, before
    known-bad-inputs at 20) re-imposes the 8,192-byte ceiling for every path
    EXCEPT the one literal path `/api/rigor/verify`. `positional_constraint =
    "EXACTLY"` matters: widening it to a `/api/*` prefix would hand every API
    route an unlimited body, which is the DoS/parser-abuse control the managed
    rule was there to provide;
  * the application, not the edge, owns the real ceiling — `RigorVerifyRequest`
    caps `returns` at 2,600 rows (a decade of daily bars) and fails closed with
    a 422 that names the limit.

`terraform validate` cannot catch any of this. A deleted override, an override
on the wrong rule name, a prefix-widened exception and a custom rule ordered
ahead of the managed group are all syntactically valid HCL.

Hermetic: reads one file from the repo and exercises the pydantic model plus the
router over an in-process ASGI transport. No AWS, no terraform binary, no
network, no DB.

Run:
    /path/to/env/bin/pytest backend/tests/test_waf_verify_body.py -q
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from archimedes.api import account_auth, rigor_verify_routes
from archimedes.api.rigor_verify_routes import _MAX_RETURN_ROWS, RigorVerifyRequest
from fastapi import FastAPI
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
WAF_TF = REPO_ROOT / "infra" / "waf.tf"

# The AWS body-inspection limit for a REGIONAL web ACL. The managed rule and the
# custom replacement must agree on it; spelled out so a drifting constant fails.
REGIONAL_BODY_INSPECTION_LIMIT = 8192

# The one managed rule this change is permitted to soften, and the one literal
# path it is permitted to soften it for.
OVERRIDDEN_RULE = "SizeRestrictions_BODY"
VERIFY_PATH = "/api/rigor/verify"

CUSTOM_RULE_NAME = "oversize-body-except-rigor-verify"
CRS_RULE_NAME = "aws-core-rules"
CRS_GROUP = "AWSManagedRulesCommonRuleSet"

# The managed groups that must keep blocking, untouched, at their existing
# priorities. The custom rule has to sit between the CRS group and these.
DOWNSTREAM_MANAGED_PRIORITIES = {"aws-known-bad-inputs": 20, "aws-ip-reputation": 30, "aws-sqli": 40}


# ── Minimal HCL reader ───────────────────────────────────────────────────
#
# Brace-matching over comment-stripped text. Enough to isolate a `rule { ... }`
# body and assert on its contents; deliberately not a full HCL parser.


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _brace_block(text: str, marker: str, *, start: int = 0) -> str:
    """Body of the first `{...}` block at/after `marker`, braces matched."""
    i = text.find(marker, start)
    assert i != -1, f"no {marker!r} block found — the scoped WAF fix for #1749 is gone"
    j = text.index("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j + 1 : k]
    raise AssertionError(f"unterminated block after {marker!r}")


def _rule_block(name: str) -> str:
    """The `rule { ... }` body whose `name = "<name>"` line it contains."""
    text = _strip_comments(WAF_TF.read_text())
    for match in re.finditer(r"\brule\s*\{", text):
        body = _brace_block(text, "rule", start=match.start())
        if re.search(rf'^\s*name\s*=\s*"{re.escape(name)}"\s*$', body, re.MULTILINE):
            return body
    raise AssertionError(f"no rule named {name!r} in infra/waf.tf")


def _priority(rule_body: str) -> int:
    match = re.search(r"^\s*priority\s*=\s*(\d+)\s*$", rule_body, re.MULTILINE)
    assert match, "rule has no priority"
    return int(match.group(1))


def _collapse(text: str) -> str:
    """Whitespace-insensitive form, so HCL formatting is not part of the assert."""
    return re.sub(r"\s+", " ", text).strip()


# ── 1. The override exists, on the right rule, in the right group ────────


def test_size_restrictions_body_is_overridden_to_count_on_the_crs_group():
    crs = _rule_block(CRS_RULE_NAME)
    group = _brace_block(crs, "managed_rule_group_statement")
    assert f'name = "{CRS_GROUP}"' in _collapse(group).replace('name= "', 'name = "')

    override = _brace_block(group, "rule_action_override")
    collapsed = _collapse(override)
    assert f'name = "{OVERRIDDEN_RULE}"' in collapsed, f"the CRS override must name {OVERRIDDEN_RULE}; got: {collapsed}"
    assert "count {}" in _collapse(_brace_block(override, "action_to_use")), (
        "the override must set the action to count {} (keeps the metric/label while "
        "the custom rule at priority 11 carries the block)"
    )


def test_the_crs_group_itself_is_still_in_block_mode():
    """`override_action { none {} }` — softening the GROUP would disable CRS wholesale."""
    crs = _rule_block(CRS_RULE_NAME)
    assert "none {}" in _collapse(_brace_block(crs, "override_action"))


def test_no_other_managed_rule_is_overridden_anywhere_in_the_web_acl():
    """Exactly one rule_action_override in the file, and it is SizeRestrictions_BODY."""
    text = _strip_comments(WAF_TF.read_text())
    names: list[str] = []
    for match in re.finditer(r"\brule_action_override\s*\{", text):
        body = _brace_block(text, "rule_action_override", start=match.start())
        found = re.search(r'name\s*=\s*"([^"]+)"', body)
        names.append(found.group(1) if found else "<unnamed>")
    assert names == [OVERRIDDEN_RULE], f"exactly one managed rule may be overridden ({OVERRIDDEN_RULE}); found {names}"


# ── 2. The custom rule re-imposes the ceiling, with an EXACT-path exception ──


def test_custom_rule_blocks_oversize_bodies():
    rule = _rule_block(CUSTOM_RULE_NAME)
    assert "block {}" in _collapse(_brace_block(rule, "action")), "the replacement rule must BLOCK"

    size = _brace_block(rule, "size_constraint_statement")
    collapsed = _collapse(size)
    assert 'comparison_operator = "GT"' in collapsed
    assert f"size = {REGIONAL_BODY_INSPECTION_LIMIT}" in collapsed, (
        f"the replacement must keep the managed rule's own {REGIONAL_BODY_INSPECTION_LIMIT}-byte ceiling"
    )
    body = _collapse(_brace_block(size, "body"))
    assert 'oversize_handling = "MATCH"' in body, (
        "on an ALB only the first 8 KB reaches WAF, so the GT comparison alone can "
        'never fire; oversize_handling = "MATCH" is what blocks a >8 KB body'
    )


def test_the_exception_is_exactly_the_verify_path_and_is_negated():
    rule = _rule_block(CUSTOM_RULE_NAME)
    negated = _brace_block(rule, "not_statement")
    byte_match = _collapse(_brace_block(negated, "byte_match_statement"))
    assert f'search_string = "{VERIFY_PATH}"' in byte_match, (
        f"the exception must be the literal path {VERIFY_PATH}; got: {byte_match}"
    )
    assert 'positional_constraint = "EXACTLY"' in byte_match, (
        "EXACTLY, never a prefix — a /api/* widening would lift the body ceiling off every API route"
    )
    assert "uri_path {}" in byte_match, "the exception must match on uri_path"
    # The negation has to be ANDed with the size test, not left dangling: an
    # or_statement here would block every request to every other path.
    assert "and_statement" in _collapse(rule)


def test_no_wildcard_or_prefix_exception_anywhere_in_the_custom_rule():
    """Adversarial: the literal shapes a widening would take."""
    collapsed = _collapse(_rule_block(CUSTOM_RULE_NAME))
    for widened in ('"/api/*"', '"/api/"', '"/api"', '"/api/rigor/"', "STARTS_WITH", "CONTAINS"):
        assert widened not in collapsed, f"exception widened to {widened} — every other route loses the 8 KB ceiling"


# ── 3. Ordering ──────────────────────────────────────────────────────────


def test_custom_rule_is_evaluated_after_the_managed_group_and_before_the_rest():
    crs_priority = _priority(_rule_block(CRS_RULE_NAME))
    custom_priority = _priority(_rule_block(CUSTOM_RULE_NAME))
    assert custom_priority > crs_priority, (
        "WAF evaluates by ascending priority; the replacement must run AFTER the group whose rule it replaces"
    )
    for name, expected in DOWNSTREAM_MANAGED_PRIORITIES.items():
        downstream = _priority(_rule_block(name))
        assert downstream == expected, f"{name} priority moved ({downstream} != {expected})"
        assert custom_priority < downstream, f"the replacement must run before {name}, not be buried behind it"


# ── 4. The application owns the real ceiling ─────────────────────────────


def _rows(n: int) -> list[dict]:
    base = datetime(2024, 1, 1)
    return [
        {"date": (base + timedelta(days=i)).date().isoformat(), "daily_return": round(-0.0031 + i * 1e-6, 6)}
        for i in range(n)
    ]


def test_boundary_measurement_from_the_issue_still_holds():
    """One trading year really does exceed the 8 KB edge limit — the whole premise."""
    year = len(json.dumps({"returns": _rows(252), "trials": 1}, separators=(",", ":")).encode())
    assert year > REGIONAL_BODY_INSPECTION_LIMIT, (
        f"252 rows serialise to {year} B; the issue's premise is that this exceeds {REGIONAL_BODY_INSPECTION_LIMIT}"
    )
    assert 8_192 < year < 20_000, f"252 rows = {year} B, outside the measured band"


def test_a_decade_of_daily_bars_fits_under_the_cap():
    assert _MAX_RETURN_ROWS == 2600
    assert _MAX_RETURN_ROWS >= 10 * 252, "the cap must admit ten years of daily bars"
    payload = len(json.dumps({"returns": _rows(_MAX_RETURN_ROWS), "trials": 1}, separators=(",", ":")).encode())
    assert 100_000 < payload < 200_000, (
        f"a full-cap payload is {payload} B; the ~135 KB claim in the code comment is wrong"
    )


def test_schema_accepts_one_trading_year():
    model = RigorVerifyRequest(returns=_rows(252), trials=1)
    assert len(model.returns) == 252


def test_schema_accepts_exactly_the_cap():
    assert len(RigorVerifyRequest(returns=_rows(_MAX_RETURN_ROWS)).returns) == _MAX_RETURN_ROWS


def test_schema_rejects_one_row_over_the_cap_with_a_message_that_names_the_limit():
    with pytest.raises(ValidationError) as excinfo:
        RigorVerifyRequest(returns=_rows(_MAX_RETURN_ROWS + 1))
    message = str(excinfo.value)
    assert str(_MAX_RETURN_ROWS) in message
    assert str(_MAX_RETURN_ROWS + 1) in message, "the error must say how many rows were sent"


def test_max_length_is_declared_on_the_field_as_the_backstop():
    """The validator produces the message; max_length is the contract in the OpenAPI schema."""
    schema = RigorVerifyRequest.model_json_schema()
    assert schema["properties"]["returns"]["maxItems"] == _MAX_RETURN_ROWS
    assert schema["properties"]["returns"]["minItems"] == 1


# ── 5. …and returns a 422 over HTTP, not a truncation or a silent accept ──


@pytest.fixture()
def app():
    application = FastAPI()
    application.middleware("http")(account_auth.better_auth_session_middleware)
    application.include_router(rigor_verify_routes.rigor_verify_router)
    return application


def _sign_in(monkeypatch, user_id: str = "user-1"):
    async def fetch(_request):
        return {
            "user": {"id": user_id, "name": user_id, "email": f"{user_id}@example.com", "emailVerified": True},
            "session": {"id": f"s-{user_id}", "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        }

    monkeypatch.setattr(account_auth, "_fetch_session", fetch)


@pytest.mark.asyncio
async def test_over_cap_payload_is_a_422_over_http(app, monkeypatch):
    _sign_in(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"cookie": "better-auth.session_token=opaque", "host": "archimedes-arc.com"},
    ) as client:
        resp = await client.post("/api/rigor/verify", json={"returns": _rows(_MAX_RETURN_ROWS + 1), "trials": 1})
    assert resp.status_code == 422
    assert str(_MAX_RETURN_ROWS) in json.dumps(resp.json())
