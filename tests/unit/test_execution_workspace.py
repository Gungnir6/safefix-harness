import json
from pathlib import Path
from uuid import UUID

import pytest

from safefix.execution_workspace import (
    WorkspacePreparationError,
    default_data_dir,
    prepare_workspace,
    record_run_id,
)


def test_isolated_workspace_is_persistent_and_excludes_sensitive_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=do-not-copy\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".venv").mkdir()

    prepared = prepare_workspace(
        source,
        tmp_path / "data",
        in_place=False,
        sensitive_patterns=(".env", ".env.*", "**/*.pem"),
    )

    assert prepared.mode == "isolated"
    assert prepared.path != source
    assert prepared.path == tmp_path / "data" / "runs" / prepared.execution_id / "workspace"
    assert (prepared.path / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (prepared.path / ".env").exists()
    assert not (prepared.path / ".git").exists()
    assert not (prepared.path / ".venv").exists()
    assert prepared.metadata_path == (
        tmp_path / "data" / "runs" / prepared.execution_id / "execution.json"
    )
    metadata = json.loads(prepared.metadata_path.read_text(encoding="utf-8"))
    assert metadata["execution_id"] == prepared.execution_id
    assert metadata["source"] == str(source.resolve())
    assert metadata["mode"] == "isolated"
    assert metadata["created_at"]
    assert metadata["run_id"] is None


def test_in_place_workspace_uses_resolved_source_without_copy(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()

    prepared = prepare_workspace(
        source,
        tmp_path / "data",
        in_place=True,
        sensitive_patterns=(".env",),
    )

    assert prepared.mode == "in_place"
    assert prepared.path == source.resolve()
    assert prepared.metadata_path is None


def test_isolated_workspace_excludes_file_symlink_to_outside_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    outside_secret = tmp_path / "outside.env"
    outside_secret.write_text("SECRET=must-not-copy\n", encoding="utf-8")
    link = source / "config"
    try:
        link.symlink_to(outside_secret)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    prepared = prepare_workspace(
        source, tmp_path / "data", in_place=False, sensitive_patterns=()
    )

    assert not (prepared.path / "config").exists()


def test_isolated_workspace_excludes_directory_symlink_to_outside_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "secret.txt").write_text("must-not-copy\n", encoding="utf-8")
    link = source / "linked-directory"
    try:
        link.symlink_to(outside_directory, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    prepared = prepare_workspace(
        source, tmp_path / "data", in_place=False, sensitive_patterns=()
    )

    assert not (prepared.path / "linked-directory").exists()
    assert not (prepared.path / "linked-directory" / "secret.txt").exists()


@pytest.mark.parametrize("project_name", ("missing", "regular-file"))
def test_prepare_workspace_rejects_missing_or_non_directory_project(
    tmp_path: Path, project_name: str
) -> None:
    project = tmp_path / project_name
    if project_name == "regular-file":
        project.write_text("not a project", encoding="utf-8")

    with pytest.raises(WorkspacePreparationError):
        prepare_workspace(project, tmp_path / "data", in_place=False, sensitive_patterns=())


def test_prepare_workspace_rejects_data_directory_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()

    with pytest.raises(WorkspacePreparationError):
        prepare_workspace(
            source,
            source / "data",
            in_place=False,
            sensitive_patterns=(),
        )


@pytest.mark.parametrize(
    "relative_parts",
    (
        (),
        ("data",),
        ("missing", ".."),
        ("missing", "..", "data"),
    ),
    ids=("source", "source-child", "resolved-source", "resolved-child"),
)
def test_in_place_workspace_rejects_resolved_data_directory_inside_workspace(
    tmp_path: Path,
    relative_parts: tuple[str, ...],
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    data_dir = source.joinpath(*relative_parts)
    resolved_data_dir = data_dir.resolve(strict=False)

    with pytest.raises(
        WorkspacePreparationError,
        match="data directory must not be inside the project",
    ):
        prepare_workspace(
            source / ".." / source.name,
            data_dir,
            in_place=True,
            sensitive_patterns=(),
        )

    assert not (resolved_data_dir / "safefix.sqlite3").exists()


def test_prepare_workspace_refuses_existing_execution_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import safefix.execution_workspace as execution_workspace

    source = tmp_path / "project"
    source.mkdir()
    execution_id = UUID("00000000-0000-0000-0000-000000000001")
    (tmp_path / "data" / "runs" / str(execution_id)).mkdir(parents=True)
    monkeypatch.setattr(execution_workspace.uuid, "uuid4", lambda: execution_id)

    with pytest.raises(WorkspacePreparationError):
        prepare_workspace(source, tmp_path / "data", in_place=False, sensitive_patterns=())


@pytest.mark.parametrize(
    ("environment", "platform", "home", "expected"),
    (
        (
            {"SAFEFIX_DATA_DIR": "configured-data"},
            "win32",
            Path("C:/unused"),
            Path("configured-data").resolve(),
        ),
        (
            {"LOCALAPPDATA": "local-app-data"},
            "win32",
            Path("C:/unused"),
            Path("local-app-data").resolve() / "SafeFix",
        ),
        (
            {"XDG_DATA_HOME": "xdg-data"},
            "linux",
            Path("/unused"),
            Path("xdg-data").resolve() / "safefix",
        ),
        ({}, "linux", Path("/home/test"), Path("/home/test/.local/share/safefix")),
    ),
    ids=("override", "windows", "xdg", "home-fallback"),
)
def test_default_data_dir_uses_documented_precedence(
    environment: dict[str, str], platform: str, home: Path, expected: Path
) -> None:
    assert (
        default_data_dir(environment=environment, platform=platform, home=home)
        == expected
    )


def test_record_run_id_updates_isolated_metadata_and_skips_in_place(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    isolated = prepare_workspace(
        source, tmp_path / "data", in_place=False, sensitive_patterns=()
    )
    in_place = prepare_workspace(
        source, tmp_path / "data", in_place=True, sensitive_patterns=()
    )

    record_run_id(isolated, "run-123")
    record_run_id(in_place, "ignored")

    assert (
        json.loads(isolated.metadata_path.read_text(encoding="utf-8"))["run_id"]  # type: ignore[union-attr]
        == "run-123"
    )
