"""Regression checks for fresh-clone Docker Compose startup."""

from __future__ import annotations

import json
import os
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
    template = template.replace("BETTER_AUTH_SECRET=", f"BETTER_AUTH_SECRET={TEST_AUTH_SECRET}", 1)
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


if __name__ == "__main__":
    unittest.main()
