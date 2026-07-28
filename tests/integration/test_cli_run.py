from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from safefix.cli import main
from safefix.cli_runner import load_mock_actions
from safefix.config import ConfigError, default_settings_yaml


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "examples" / "mock_repair.jsonl"
ACTION_IDS = (
    "list-1",
    "read-1",
    "validate-1",
    "patch-1",
    "validate-2",
    "finish-1",
)


def _project(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    shutil.copytree(ROOT / "examples" / "python_bug", source)
    return source


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "safefix.yaml"
    config.write_text(default_settings_yaml(), encoding="utf-8")
    return config


def _arguments(
    tmp_path: Path,
    source: Path,
    *,
    in_place: bool = False,
    json_output: bool = False,
) -> list[str]:
    arguments = [
        "run",
        str(source),
        "--task",
        "修复失败的加法测试",
        "--config",
        str(_config(tmp_path)),
        "--provider",
        "mock",
        "--mock-script",
        str(SCRIPT),
        "--data-dir",
        str(tmp_path / "data"),
    ]
    if in_place:
        arguments.append("--in-place")
    if json_output:
        arguments.append("--json")
    return arguments


def test_cli_mock_run_repairs_isolated_copy_and_preserves_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _project(tmp_path)
    original = (source / "calculator.py").read_text(encoding="utf-8")

    result = main(_arguments(tmp_path, source))

    assert result == 0
    assert (source / "calculator.py").read_text(encoding="utf-8") == original
    copied = next((tmp_path / "data").glob("runs/*/workspace/calculator.py"))
    assert "return left + right" in copied.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "验证失败" in output
    assert "验证通过" in output
    assert "SUCCESS" in output
    action_positions = [output.index(f'"id": "{action_id}"') for action_id in ACTION_IDS]
    assert action_positions == sorted(action_positions)


def test_cli_mock_run_in_place_changes_disposable_source(
    tmp_path: Path,
) -> None:
    source = _project(tmp_path)

    result = main(_arguments(tmp_path, source, in_place=True))

    assert result == 0
    assert "return left + right" in (source / "calculator.py").read_text(
        encoding="utf-8"
    )


def test_cli_mock_json_output_is_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _project(tmp_path)

    result = main(_arguments(tmp_path, source, json_output=True))

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCESS"


def test_mock_loader_materializes_only_documented_fixture_sha(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "calculator.py").write_bytes(b"fixture\n")
    script = tmp_path / "actions.jsonl"
    script.write_text(
        '{"type":"apply_patch","path":"calculator.py",'
        '"expected_sha256":"{CALCULATOR_SHA256}"}\n',
        encoding="utf-8",
    )

    actions = load_mock_actions(script, workspace)

    expected = hashlib.sha256(b"fixture\n").hexdigest()
    assert json.loads(actions[0])["expected_sha256"] == expected


@pytest.mark.parametrize(
    "contents",
    (
        "",
        "\n \n",
        "[]\n",
        '{"type":"finish"}\nnot-json\n',
        '{"type":"finish","summary":"{UNKNOWN_TOKEN}"}\n',
        '{"type":"finish","summary":"{unknown-token}"}\n',
        '{"type":"finish","summary":NaN}\n',
    ),
)
def test_mock_loader_rejects_blank_non_object_invalid_or_unknown_placeholder(
    tmp_path: Path,
    contents: str,
) -> None:
    script = tmp_path / "actions.jsonl"
    script.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_mock_actions(script, tmp_path)


def test_mock_loader_rejects_missing_or_escaping_fixture_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for path in ("calculator.py", "../outside.py"):
        script = tmp_path / f"{path.replace('/', '_')}.jsonl"
        script.write_text(
            json.dumps(
                {
                    "type": "apply_patch",
                    "path": path,
                    "expected_sha256": "{CALCULATOR_SHA256}",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_mock_actions(script, workspace)


def test_mock_loader_bounds_bytes_and_action_count(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.jsonl"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    too_many = tmp_path / "too-many.jsonl"
    too_many.write_text("{}\n" * 1001, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_mock_actions(oversized, tmp_path)
    with pytest.raises(ConfigError):
        load_mock_actions(too_many, tmp_path)
