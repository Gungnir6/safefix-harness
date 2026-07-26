from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from safefix.cli import main
from safefix.web.app import AppDependencies, create_app
from tests.web.test_api import FakeService


ROOT = Path(__file__).parents[2]


def test_required_delivery_files_and_ci_job_exist() -> None:
    for name in (
        "Dockerfile",
        ".dockerignore",
        ".gitlab-ci.yml",
        ".github/workflows/ci.yml",
        "README.md",
        "render.yaml",
    ):
        assert (ROOT / name).is_file()

    gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    for job in ("unit-test:", "lint-type:", "secret-scan:", "image-build:"):
        assert job in gitlab


def test_gitlab_ci_uses_dependency_proxy_images() -> None:
    pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))

    expected = {
        "unit-test": "python:3.12-slim",
        "lint-type": "python:3.12-slim",
        "secret-scan": "zricethezav/gitleaks:v8.24.2",
        "image-build": "docker:27-cli",
    }
    prefix = "${CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX}/"
    for job, suffix in expected.items():
        image = pipeline[job]["image"]
        assert image["name"] == prefix + suffix
        assert "pull_policy" not in image
    service = pipeline["image-build"]["services"][0]
    assert service["name"] == prefix + "docker:27-dind"
    assert service["alias"] == "docker"
    assert "pull_policy" not in service


def test_readme_has_required_submission_sections() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in (
        "Installation",
        "Usage",
        "Credentials",
        "Public Demo",
        "Distribution",
        "Project Structure",
        "Security Boundaries",
        "Known Limitations",
        "Architecture",
        "Deployment",
        "Third-Party Licenses",
    ):
        assert f"## {heading}" in readme


def test_ignore_and_package_metadata_cover_secrets_and_assets() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.pem", "*.sqlite3", "*.log", "dist/"):
        assert pattern in ignored
    assert "!.env.example" in ignored

    package = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'safefix = "safefix.cli:main"' in package
    assert 'safefix-demo = "safefix.demo:main"' in package
    assert '"examples/python_bug" = "safefix/_fixtures/python_bug"' in package


def test_container_is_non_root_and_has_healthcheck() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in dockerfile
    assert "USER safefix" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert 'CMD ["safefix", "serve", "--public-demo"' in dockerfile


def test_health_endpoint_is_ready() -> None:
    client = TestClient(create_app(AppDependencies(service=FakeService())))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_default_serve_builds_public_demo_app(monkeypatch) -> None:
    calls: list[tuple[object, str, int]] = []

    def fake_run(app: object, *, host: str, port: int) -> None:
        calls.append((app, host, port))

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["serve", "--public-demo"]) == 0
    app, host, port = calls[0]
    assert host == "127.0.0.1"
    assert port == 8000
    assert TestClient(app).get("/health").status_code == 200
