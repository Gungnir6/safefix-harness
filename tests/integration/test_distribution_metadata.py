from __future__ import annotations

import tomllib
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from safefix.cli import main
from safefix.web.app import AppDependencies, create_app
from tests.web.test_api import FakeService


ROOT = Path(__file__).parents[2]


def test_runtime_dependencies_include_the_demo_validator() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    requirements = metadata["project"]["dependencies"]
    dependency_names = {
        canonicalize_name(Requirement(requirement).name)
        for requirement in requirements
    }

    assert "pytest" in dependency_names


def test_required_delivery_files_and_ci_job_exist() -> None:
    for name in (
        ".gitlab-ci.yml",
        ".github/workflows/ci.yml",
        "README.md",
    ):
        assert (ROOT / name).is_file()

    gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    for job in ("unit-test:", "lint-type:", "secret-scan:"):
        assert job in gitlab

    assert not (ROOT / "Dockerfile").exists()
    assert not (ROOT / "render.yaml").exists()


def test_gitlab_ci_uses_dependency_proxy_images() -> None:
    pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))

    expected = {
        "unit-test": "python:3.12-slim",
        "lint-type": "python:3.12-slim",
        "secret-scan": "zricethezav/gitleaks:v8.24.2",
    }
    prefix = "${CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX}/"
    for job, suffix in expected.items():
        image = pipeline[job]["image"]
        assert image["name"] == prefix + suffix
        assert "pull_policy" not in image

    assert pipeline["stages"] == ["test", "quality"]
    assert "image-build" not in pipeline


def test_github_pull_request_secret_scan_receives_token() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["test-quality"]["steps"]
    gitleaks_step = next(
        step for step in steps if step.get("uses") == "gitleaks/gitleaks-action@v2"
    )

    assert gitleaks_step["env"]["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


def test_readme_has_required_submission_sections() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in (
        "Installation",
        "Usage",
        "Credentials",
        "Distribution",
        "Project Structure",
        "Security Boundaries",
        "Known Limitations",
        "Architecture",
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
    assert (ROOT / "examples" / "mock_repair.jsonl").is_file()
    assert (
        '"examples/mock_repair.jsonl" = "safefix/_fixtures/mock_repair.jsonl"'
        in package
    )


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
