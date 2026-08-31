"""Regression checks for fresh-clone Docker Compose startup."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_AUTH_SECRET = "test-only-better-auth-secret-at-least-32-characters"


def _template_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.example").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _brace_block(text: str, search_from: int) -> str:
    """Return the first brace-balanced `{...}` block at/after `search_from`.

    Used to pin a *block* rather than a substring: `assertIn("healthCheck", ...)`
    over a whole Terraform file matches the word in a comment just as happily as
    a real declaration, so a guard built on it can pass while the block it
    claims to check has been deleted.
    """
    open_idx = text.index("{", search_from)
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx : i + 1]
    raise AssertionError(f"unbalanced braces starting at offset {open_idx}")


def _compose_config(*, local: bool) -> dict:
    template = (ROOT / ".env.example").read_text()
    # Full-line substitution, not a prefix replace: .env.example now ships a
    # non-empty placeholder value (PLACEHOLDER_SECRET in auth/auth.js), so a
    # bare `.replace("BETTER_AUTH_SECRET=", "BETTER_AUTH_SECRET=<value>", 1)`
    # would only prepend TEST_AUTH_SECRET onto that placeholder instead of
    # overriding it — the resulting line would end up concatenated, not equal
    # to TEST_AUTH_SECRET. re.sub with MULTILINE replaces the whole line.
    template = re.sub(
        r"^BETTER_AUTH_SECRET=.*$", f"BETTER_AUTH_SECRET={TEST_AUTH_SECRET}", template, count=1, flags=re.MULTILINE
    )
    command = ["docker", "compose"]
    if local:
        template = template.replace("\nCOMPOSE_PROFILES=localdb\n", "\nCOMPOSE_PROFILES=localdb,runners\n", 1)
    else:
        template = template.replace("\nCOMPOSE_PROFILES=localdb\n", "\nCOMPOSE_PROFILES=runners\n", 1)
        template = "\n".join(
            line for line in template.splitlines() if not line.startswith(("COMPOSE_FILE=", "ARCHIMEDES_HTTP_PORT="))
        )
        command.extend(("-f", "docker-compose.yml"))

    with tempfile.TemporaryDirectory() as directory:
        temp_root = Path(directory)
        (temp_root / ".env").write_text(template)
        for name in ("docker-compose.yml", "docker-compose.local.yml"):
            source = ROOT / name
            if source.exists():
                (temp_root / name).write_text(source.read_text())
        result = subprocess.run(
            [*command, "config", "--format", "json"],
            cwd=temp_root,
            env={"HOME": str(Path.home()), "PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        raise AssertionError(f"docker compose config failed:\n{result.stderr}")
    return json.loads(result.stdout)


class TestLocalSetupContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.env = _template_env()
        cls.config = _compose_config(local=True)
        cls.production_config = _compose_config(local=False)

    def test_template_publishes_rootless_safe_http_port(self) -> None:
        self.assertIn("COMPOSE_FILE", self.env)
        self.assertEqual(self.env["COMPOSE_FILE"], "docker-compose.yml:docker-compose.local.yml")
        self.assertIn("ARCHIMEDES_HTTP_PORT", self.env)
        host_port = int(self.env["ARCHIMEDES_HTTP_PORT"])
        self.assertGreaterEqual(host_port, 1024)
        self.assertEqual(
            self.config["services"]["nginx"]["ports"],
            [{"mode": "ingress", "target": 8080, "published": str(host_port), "protocol": "tcp"}],
        )

    def test_composed_auth_secret_equals_the_test_override_exactly(self) -> None:
        """_compose_config's TEST_AUTH_SECRET substitution must fully REPLACE
        whatever .env.example ships for BETTER_AUTH_SECRET, not concatenate
        onto it. .env.example now ships a non-empty public placeholder
        (auth/auth.js PLACEHOLDER_SECRET) for a verbatim `cp .env.example .env`
        to work, so a naive prefix-replace of "BETTER_AUTH_SECRET=" would
        prepend TEST_AUTH_SECRET onto that placeholder instead of overriding
        it. The composed value reaching the auth service must be exactly
        TEST_AUTH_SECRET — nothing appended, nothing left over."""
        self.assertEqual(
            self.config["services"]["auth"]["environment"]["BETTER_AUTH_SECRET"],
            TEST_AUTH_SECRET,
        )

    def test_local_apps_wait_for_successful_schema_migration(self) -> None:
        self.assertIn("migrate", self.config["services"])
        migrate = self.config["services"]["migrate"]
        self.assertEqual(migrate["profiles"], ["localdb"])
        self.assertEqual(
            migrate["command"],
            ["python", "-m", "archimedes.scripts.alembic_migrate_preflight"],
        )
        for service_name in ("auth", "backend", "oracle", "agent", "kb-runner"):
            dependencies = self.config["services"][service_name]["depends_on"]
            self.assertIn("migrate", dependencies, service_name)
            dependency = dependencies["migrate"]
            self.assertEqual(dependency["condition"], "service_completed_successfully")
            self.assertTrue(dependency["required"])

    def test_production_model_keeps_local_services_disabled(self) -> None:
        self.assertEqual(
            set(self.production_config["services"]),
            {"auth", "backend", "nginx", "oracle", "agent", "kb-runner"},
        )
        self.assertNotIn("migrate", self.production_config["services"])
        self.assertEqual(self.production_config["services"]["nginx"]["ports"][0]["published"], "80")

    def test_nginx_proxies_backend_docs_through_sole_ingress(self) -> None:
        nginx = (ROOT / "nginx/nginx.conf").read_text()
        self.assertIn("location ^~ /docs", nginx)
        self.assertIn("proxy_pass http://backend_api;", nginx)
        self.assertIn("location = /openapi.json", nginx)
        self.assertIn("proxy_pass http://backend_api/openapi.json;", nginx)

    def test_ecs_nginx_healthcheck_matches_a_real_nginx_conf_location(self) -> None:
        # #1309 review: infra/ecs.tf's nginx container healthCheck hard-depends
        # on a path defined in a DIFFERENT file (nginx/nginx.conf) with no
        # guard tying them together — an unrelated nginx.conf edit that drops
        # or renames /nginx-health would silently turn every nginx container
        # UNHEALTHY (ECS would keep killing/replacing it), a self-inflicted
        # deploy outage on the exact service issue #1309 is about. Mirrors
        # test_nginx_proxies_backend_docs_through_sole_ingress above, which
        # pins the /docs pairing the same way.
        nginx = (ROOT / "nginx/nginx.conf").read_text()
        ecs_tf = (ROOT / "infra/ecs.tf").read_text()

        # 1. nginx.conf actually defines the container-local health location,
        # answering 200 directly (never proxied — see the location's own
        # comment on why: /health does real backend work and must not be
        # hit by a frequent container-level poller).
        self.assertIn("location = /nginx-health {", nginx)
        loc_start = nginx.index("location = /nginx-health {")
        loc_end = nginx.index("}", loc_start)
        nginx_health_block = nginx[loc_start:loc_end]
        self.assertIn("return 200", nginx_health_block)
        self.assertNotIn("proxy_pass", nginx_health_block)

        # 2. Slice out the nginx CONTAINER definition specifically, rather
        # than searching ecs.tf as a whole. The file also defines `backend`
        # and `auth` containers that already have their own healthChecks, so
        # a file-wide match would stay green with the nginx block deleted
        # outright — which is the exact regression this guard exists to
        # catch. `^\s*name\s*=\s*"` only matches a container/resource `name`
        # attribute at the start of its line; the inline `{ name = "NGINX_
        # ENVSUBST_FILTER", ... }` environment entries have `{ ` in front and
        # so cannot bound the slice early.
        container_name_starts = [m.start() for m in re.finditer(r'^\s*name\s*=\s*"', ecs_tf, re.MULTILINE)]
        nginx_decl = re.search(r'^\s*name\s*=\s*"nginx"\s*$', ecs_tf, re.MULTILINE)
        self.assertIsNotNone(nginx_decl, 'infra/ecs.tf defines no container named "nginx"')
        assert nginx_decl is not None  # narrow for the type checker
        nginx_start = nginx_decl.start()
        nginx_end = next((s for s in container_name_starts if s > nginx_start), len(ecs_tf))
        nginx_container = ecs_tf[nginx_start:nginx_end]

        # 3. That container declares an actual `healthCheck = { ... }` BLOCK.
        # Anchored to the start of a line so the many prose mentions of
        # "healthCheck" in the surrounding comment cannot satisfy it, and the
        # block is then brace-matched so every assertion below is scoped to
        # the declaration itself rather than to anything else in the file.
        hc_decl = re.search(r"^\s*healthCheck\s*=\s*\{", nginx_container, re.MULTILINE)
        self.assertIsNotNone(
            hc_decl,
            'the "nginx" container in infra/ecs.tf declares no healthCheck block — it is the ALB '
            "target, so without one it does not participate in the task's deployment healthStatus (#1309)",
        )
        assert hc_decl is not None  # narrow for the type checker
        health_check = _brace_block(nginx_container, hc_decl.start())

        # 4. The command inside THAT block hits the nginx-local path, with a
        # boundary so a typo'd extension ("/nginx-health" -> "/nginx-healthz")
        # is caught rather than silently satisfying an unanchored "contains".
        probe = re.search(r'http://127\.0\.0\.1:(\d+)/nginx-health(?=[\s"])', health_check)
        self.assertIsNotNone(
            probe,
            "the nginx healthCheck command does not probe http://127.0.0.1:<port>/nginx-health; "
            f"block was: {health_check}",
        )
        assert probe is not None  # narrow for the type checker
        probe_port = probe.group(1)

        # 5. Tie that port back to the port nginx actually listens on. The
        # healthCheck is container-local, so if nginx.conf's `listen` moves
        # off this port the check polls a closed socket, every nginx container
        # goes UNHEALTHY, and with `deployment_circuit_breaker { rollback =
        # true }` every deploy rolls back — a self-inflicted outage on the
        # service #1309 is about. Also pinned against the container's own
        # portMappings, which is what the ALB target group forwards to.
        self.assertRegex(
            nginx,
            rf"(?m)^\s*listen\s+{re.escape(probe_port)};",
            f"infra/ecs.tf's nginx healthCheck probes port {probe_port}, but nginx/nginx.conf has no "
            f"`listen {probe_port};` — the check would poll a closed socket",
        )
        self.assertRegex(
            nginx_container,
            rf"containerPort\s*=\s*{re.escape(probe_port)}\b",
            f"infra/ecs.tf's nginx healthCheck probes port {probe_port}, which is not the container's "
            "published containerPort",
        )

    def test_nginx_gates_insights_exactly_like_every_other_app_path(self) -> None:
        """PR #1437 review (2026-08-30), reversing this test's round-2 form.

        The admin-only `/app/insights` dashboard must be served behind the
        SAME `auth_request /_auth_session` boundary as every other `/app`
        path, so that an anonymous GET for it is indistinguishable from an
        anonymous GET for any unknown `/app` URL.

        The round-2 version of this test asserted the opposite — that the
        insights location must NOT carry `auth_request` — on the theory that
        a 302 to `/sign-in?next=/app/insights` would itself confirm a
        privileged page exists there. That was backwards. `^~ /app` below is
        a catch-all prefix with a SPA fallback, so nginx answers an unknown
        `/app` path with exactly that same 302; the redirect distinguishes
        nothing. The ungated carve-out was the actual leak:

            ungated: anonymous GET /app/insights -> 200 HTML
                     anonymous GET /app/library  -> 302 /sign-in
                     anonymous GET /app/nonsense -> 302 /sign-in

        — a pre-auth existence oracle, readable before a single line of
        client JS runs.

        `insights` still must stay OUT of `ANON_APP_PAGES` in
        ui/src/routes.js, and the real authorization is still the
        server-side `require_platform_admin` check inside
        `/api/metrics/private/whoami`, which nginx does not touch.

        Config-parse test in the idiom of this file's other nginx checks:
        it reads the real deployed `nginx/nginx.conf` and asserts on the
        parsed location block rather than on a substring of the whole file.
        """
        nginx = (ROOT / "nginx/nginx.conf").read_text()
        anchor = "location = /app/insights {"
        insights_index = nginx.find(anchor)
        self.assertNotEqual(
            insights_index,
            -1,
            f"anchor {anchor!r} not found in nginx.conf — insights must be declared as an "
            "EXACT-match location (a `^~` prefix would also capture /app/insightsfoo), and "
            "this test's anchor has drifted from the real file",
        )
        gated_app_index = nginx.index("location ^~ /app {")
        self.assertLess(
            insights_index,
            gated_app_index,
            "the /app/insights block must be declared (and read, for anyone auditing "
            "this file top-to-bottom) before the catch-all gated ^~ /app block",
        )
        insights_block = nginx[insights_index : nginx.index("}", insights_index) + 1]
        self.assertIn(
            "auth_request /_auth_session;",
            insights_block,
            "the insights location must sit behind the SAME auth_request boundary as "
            "every other /app path — without it, an anonymous GET returns 200 while an "
            "anonymous GET for any other /app path 302s, which is an existence oracle "
            "at the layer before any client JS runs",
        )
        self.assertIn(
            "error_page 401 = @sign_in;",
            insights_block,
            "auth_request without the 401 handler emits a bare 401 instead of the "
            "@sign_in 302 every other /app path emits — a different kind of oracle, "
            "not a fix",
        )
        self.assertIn("try_files $uri $uri/ /index.html;", insights_block)

        # The whole property is INDISTINGUISHABILITY from the catch-all, so
        # assert the two blocks actually agree on the gate rather than just
        # checking insights in isolation: a future edit that dropped
        # auth_request from `^~ /app` would leave this test green while the
        # boundary it describes no longer existed anywhere.
        gated_block = nginx[gated_app_index : nginx.index("}", gated_app_index) + 1]
        for directive in ("auth_request /_auth_session;", "error_page 401 = @sign_in;"):
            self.assertIn(directive, gated_block)

        # And there must be no OTHER insights location re-opening the hole —
        # nginx's exact-match `= /app/insights` wins over any `^~` prefix, but
        # a `^~ /app/insights` sibling would still catch /app/insights/... and
        # /app/insightsfoo, both of which must stay gated.
        self.assertNotIn(
            "location ^~ /app/insights",
            nginx,
            "a `^~ /app/insights` prefix location would take /app/insights/* and "
            "/app/insightsfoo back off the auth boundary",
        )

    def test_nginx_webmanifest_mime_override_is_additive_not_nested_in_server(self) -> None:
        """#1380: stock nginx `mime.types` has no `.webmanifest` entry, so a
        served `site.webmanifest` fell back to `default_type`
        (application/octet-stream) instead of `application/manifest+json`.

        The override MUST live at this file's top level (http context, via
        the conf.d splice documented in the file header) rather than nested
        inside `server {}`. A `types {}` block does not merge into an
        ancestor context's type map when declared in a child context — it
        replaces it outright for that context, so declaring this inside
        `server {}` would silently blank the content type of every other
        static asset (.css/.js/.html) served by that block back to
        `default_type` too. Verified live with nginx 1.31.2 (the pinned
        image): moving this exact line inside `server {}` turned
        `/style.css` and `/` from their correct types into
        `application/octet-stream`. Placed here, at the same http-context
        level where the base image's own `include mime.types;` already ran
        (before this conf.d fragment is spliced in), it appends to that
        existing map instead — the anti-goal's "additive override only"."""
        nginx = (ROOT / "nginx/nginx.conf").read_text()
        override = "types { application/manifest+json webmanifest; }"
        self.assertIn(override, nginx)
        override_index = nginx.index(override)
        server_block_anchor = "server {\n    listen 8080;"
        server_block_index = nginx.find(server_block_anchor)
        self.assertNotEqual(
            server_block_index,
            -1,
            f"anchor {server_block_anchor!r} not found in nginx.conf — this "
            "test's anchor has drifted from the real file and needs updating, "
            "not a ValueError from nginx.index() with no context",
        )
        self.assertLess(
            override_index,
            server_block_index,
            "the .webmanifest MIME override must precede `server {}` (http "
            "context) — nested inside it, `types {}` replaces rather than "
            "extends the inherited MIME map, breaking every other static "
            "asset's content type",
        )
        # The above only pins the POSITION of this one override line — it
        # says nothing about any OTHER `types {}` block. A second `types {}`
        # declared inside `server {}` (this override left untouched at http
        # level) still replaces the inherited MIME map for every other
        # static asset that block serves — verified live against the pinned
        # image (nginxinc/nginx-unprivileged:1.31.2-alpine): with any
        # `types {}` inside `server {}`, /style.css, /bundle.js, and / all
        # degrade to application/octet-stream. Guard the whole hazard class,
        # not just this one line's position.
        self.assertNotIn(
            "types {",
            nginx[server_block_index:],
            "no `types {}` block may appear inside `server {}` — it "
            "replaces rather than extends the inherited MIME map for every "
            "static asset that block serves",
        )


if __name__ == "__main__":
    unittest.main()
