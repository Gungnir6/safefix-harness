from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_render_blueprint_deploys_the_public_demo_container() -> None:
    blueprint_path = ROOT / "render.yaml"
    dockerfile_path = ROOT / "Dockerfile"

    assert blueprint_path.is_file()
    assert dockerfile_path.is_file()

    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    assert blueprint == {
        "services": [
            {
                "type": "web",
                "name": "safefix-public-demo",
                "runtime": "docker",
                "plan": "free",
                "dockerfilePath": "./Dockerfile",
                "healthCheckPath": "/health",
                "autoDeployTrigger": "commit",
            }
        ]
    }

    dockerfile = dockerfile_path.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in dockerfile
    assert 'CMD ["safefix", "serve", "--public-demo"' in dockerfile
