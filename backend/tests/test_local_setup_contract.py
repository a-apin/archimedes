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

    def test_nginx_serves_insights_shell_without_auth_request_gate(self) -> None:
        """PR #1437 round 2: the admin-only `/app/insights` dashboard must
        never bounce an anonymous or non-admin visitor to `/sign-in` at the
        nginx layer — that would itself confirm a privileged page exists at
        that path, before App.jsx's real `whoami` probe ever runs. Without a
        dedicated `^~ /app/insights` carve-out, `/app/insights` fell through
        to the auth_request-gated `^~ /app` block below and 302'd every
        anonymous GET to `/sign-in?next=/app/insights`, making App.jsx's
        `route.page === 'insights'` bypass dead code the deployed edge never
        reached (round-2 finding). This is NOT an anonymous-browse carve-out
        like Explore/Leaderboard/Corpus/strategy — `insights` must stay OUT
        of `ANON_APP_PAGES` in ui/src/routes.js; the real authorization is
        still the server-side `require_platform_admin` check inside
        `/api/metrics/private/whoami`, which this block does not touch.
        """
        nginx = (ROOT / "nginx/nginx.conf").read_text()
        insights_index = nginx.index("location ^~ /app/insights")
        gated_app_index = nginx.index("location ^~ /app {")
        self.assertLess(
            insights_index,
            gated_app_index,
            "the /app/insights carve-out must be declared (and read, for anyone auditing "
            "this file top-to-bottom) before the catch-all gated ^~ /app block",
        )
        insights_block = nginx[insights_index : nginx.index("}", insights_index) + 1]
        self.assertNotIn(
            "auth_request",
            insights_block,
            "the insights carve-out must NOT auth_request-gate the shell — gating "
            "belongs to the client-side whoami probe, not nginx",
        )
        self.assertIn("try_files $uri $uri/ /index.html;", insights_block)

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
