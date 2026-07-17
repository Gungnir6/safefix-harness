from __future__ import annotations

from pathlib import Path

import pytest

from safefix.config import (
    LLMSettings,
    PolicySettings,
    SafeFixSettings,
    ValidatorSettings,
)
from safefix.domain import (
    AccessKind,
    ApplyPatchAction,
    DecisionOutcome,
    FinishAction,
    ListFilesAction,
    ReadFileAction,
    RiskLevel,
    RunProcessAction,
    RunValidationAction,
    SearchTextAction,
)
from safefix.governance.paths import WorkspaceBoundary
from safefix.governance.policy import PolicyEngine, _program_matches_configuration


def _validator(
    identifier: str, program: str, args: tuple[str, ...]
) -> ValidatorSettings:
    return ValidatorSettings(
        id=identifier,
        kind="test",
        program=program,
        args=args,
        timeout_seconds=120,
        success_exit_codes=frozenset({0}),
        output_limit_bytes=65_536,
    )


@pytest.fixture
def settings() -> SafeFixSettings:
    return SafeFixSettings(
        llm=LLMSettings.model_validate(
            {"endpoint": "https://example.test/v1", "model": "test-model"}
        ),
        validators=(
            _validator("pytest", "pytest", ("-q",)),
            _validator("python-pytest", "python", ("-m", "pytest", "-q")),
            _validator("literal-meta", "echo", ("hello; rm -rf /",)),
            _validator(
                "windows-pytest", r"C:\Tools\special-pytest.exe", ("--windows",)
            ),
            _validator("posix-pytest", "/opt/tools/pytest", ("--posix",)),
        ),
        policy=PolicySettings(
            sensitive_patterns=(".env", "**/*.pem", "**/.ssh/**"),
            allowed_programs=(
                "ruff",
                "python",
                "node",
                "rm",
                "bash",
                "git",
                "pip",
                "npm",
                "mv",
                "cp",
                r"C:\Tools\custom.exe",
                "/opt/tools/custom",
            ),
            denied_programs=("shutdown",),
        ),
    )


@pytest.fixture
def policy(tmp_path: Path, settings: SafeFixSettings) -> PolicyEngine:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    return PolicyEngine(
        settings=settings,
        boundary=WorkspaceBoundary(workspace, settings.policy.sensitive_patterns),
    )


@pytest.mark.parametrize(
    ("program", "args", "expected", "rule"),
    [
        ("pytest", ("-q",), DecisionOutcome.ALLOW, "CMD_CONFIGURED_VALIDATOR"),
        (
            "git",
            ("commit", "-m", "x"),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_GIT_WRITE",
        ),
        (
            "pip",
            ("install", "requests"),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_INSTALL",
        ),
        (
            "sudo",
            ("rm", "-rf", "/"),
            DecisionOutcome.DENY,
            "CMD_PRIVILEGE_ESCALATION",
        ),
    ],
)
def test_process_risk_matrix(
    policy: PolicyEngine,
    program: str,
    args: tuple[str, ...],
    expected: DecisionOutcome,
    rule: str,
) -> None:
    action = RunProcessAction(id="a1", reason="test", program=program, args=args)

    decision = policy.decide(action)

    assert decision.outcome is expected
    assert rule in decision.rule_ids


@pytest.mark.parametrize(
    ("program", "args", "expected", "rule"),
    [
        (
            "PyTest.EXE",
            ("-q",),
            DecisionOutcome.ALLOW,
            "CMD_CONFIGURED_VALIDATOR",
        ),
        (
            r"C:\Windows\System32\SUDO.EXE",
            ("echo", "safe"),
            DecisionOutcome.DENY,
            "CMD_PRIVILEGE_ESCALATION",
        ),
        (
            r"C:\Tools\ShUtDoWn.CmD",
            (),
            DecisionOutcome.DENY,
            "CMD_DENIED_PROGRAM",
        ),
        (
            r"C:\Tools\SUDO.EXE.CMD",
            (),
            DecisionOutcome.DENY,
            "CMD_PRIVILEGE_ESCALATION",
        ),
        (
            "not-sudo-helper",
            (),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_UNCONFIGURED_PROGRAM",
        ),
    ],
)
def test_program_identity_is_normalized_without_substring_matching(
    policy: PolicyEngine,
    program: str,
    args: tuple[str, ...],
    expected: DecisionOutcome,
    rule: str,
) -> None:
    decision = policy.decide(
        RunProcessAction(id="identity", reason="test", program=program, args=args)
    )

    assert decision.outcome is expected
    assert decision.rule_ids == (rule,)


@pytest.mark.parametrize("program", ("ruff", "RUFF.EXE", "RuFf.CmD"))
def test_authorization_bare_program_allows_only_bare_windows_variants(
    policy: PolicyEngine, program: str
) -> None:
    decision = policy.decide(
        RunProcessAction(id="bare-allow", reason="test", program=program)
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule_ids == ("CMD_CONFIGURED_PROGRAM",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        (r"C:\attacker\ruff.exe.cmd", ()),
        (r"C:\Tools\ruff.exe", ()),
        ("/tmp/ruff", ()),
        ("ruff.exe.cmd", ()),
        (r"C:\attacker\pytest.exe", ("-q",)),
    ],
)
def test_authorization_bare_program_rejects_paths_and_double_extensions(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="bare-spoof", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_UNCONFIGURED_PROGRAM",)


@pytest.mark.parametrize(
    ("program", "expected"),
    [
        (r"c:/tools/CUSTOM.EXE", DecisionOutcome.ALLOW),
        (r"D:\Tools\custom.exe", DecisionOutcome.REQUIRE_APPROVAL),
        ("/opt/tools/custom", DecisionOutcome.ALLOW),
        ("/opt/tools/CUSTOM", DecisionOutcome.REQUIRE_APPROVAL),
    ],
)
def test_authorization_full_paths_follow_platform_path_semantics(
    policy: PolicyEngine, program: str, expected: DecisionOutcome
) -> None:
    decision = policy.decide(
        RunProcessAction(id="full-path", reason="test", program=program)
    )

    assert decision.outcome is expected


@pytest.mark.parametrize(
    ("program", "args", "expected"),
    [
        (r"c:/TOOLS/SPECIAL-PYTEST.EXE", ("--windows",), DecisionOutcome.ALLOW),
        (
            r"D:\Tools\special-pytest.exe",
            ("--windows",),
            DecisionOutcome.REQUIRE_APPROVAL,
        ),
        ("/opt/tools/pytest", ("--posix",), DecisionOutcome.ALLOW),
        ("/opt/tools/PYTEST", ("--posix",), DecisionOutcome.REQUIRE_APPROVAL),
    ],
)
def test_authorization_validator_full_path_must_match_exact_configuration(
    policy: PolicyEngine,
    program: str,
    args: tuple[str, ...],
    expected: DecisionOutcome,
) -> None:
    decision = policy.decide(
        RunProcessAction(id="validator-path", reason="test", program=program, args=args)
    )

    assert decision.outcome is expected
    if expected is DecisionOutcome.ALLOW:
        assert decision.rule_ids == ("CMD_CONFIGURED_VALIDATOR",)


def test_denied_program_precedes_other_risk_and_allow_rules(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="denied", reason="test", program="shutdown.exe", args=("/s",)
        )
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_DENIED_PROGRAM",)


@pytest.mark.parametrize(
    ("program", "args", "expected_rule"),
    [
        ("rm", ("-rf", "build"), "CMD_DELETE"),
        ("python", ("-c", "import shutil; shutil.rmtree('build')"), "CMD_DELETE"),
        (
            "node",
            ("-e", "require('fs').rmSync('build', {recursive: true})"),
            "CMD_DELETE",
        ),
    ],
)
def test_delete_rules_precede_configured_program_allow(
    policy: PolicyEngine, program: str, args: tuple[str, ...], expected_rule: str
) -> None:
    decision = policy.decide(
        RunProcessAction(id="delete", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == (expected_rule,)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        (
            "python",
            ("-c", "import subprocess; subprocess.run(['rm', '-rf', 'build'])"),
        ),
        (
            "node",
            ("-e", "require('child_process').execSync('rm -rf build')"),
        ),
    ],
)
def test_inline_subprocess_deletion_requires_approval(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="inline-delete", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_DELETE",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("rm", ("-rf", "/")),
        ("powershell", ("-Command", r"Remove-Item -Recurse C:\Windows")),
        ("python", ("-c", "import shutil; shutil.rmtree('/')")),
        ("node", ("-e", "require('fs').rmSync('C:\\Windows', {recursive:true})")),
    ],
)
def test_root_or_system_destruction_is_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="destroy", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_SYSTEM_DESTRUCTION",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("sh", ("-c", "rm -rf /")),
        ("python", ("-c", "import os; os.system('rm -rf /')")),
    ],
)
def test_indirect_root_deletion_is_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="indirect-root", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_SYSTEM_DESTRUCTION",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("printenv", ()),
        ("cat", (".env",)),
        ("powershell", ("-Command", "Get-Content .ssh/id_ed25519")),
    ],
)
def test_credential_readers_are_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="credential", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_CREDENTIAL_ACCESS",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("python", ("-c", "import os; print(os.environ)")),
        ("node", ("-e", "console.log(process.env)")),
        ("powershell", ("-Command", "Get-ChildItem Env:")),
    ],
)
def test_inline_environment_readers_are_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="inline-credential", reason="test", program=program, args=args
        )
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_CREDENTIAL_ACCESS",)


@pytest.mark.parametrize(
    ("program", "args", "rule"),
    [
        (
            "python",
            ("-c", "import shutil as s; s.rmtree('/')"),
            "CMD_SYSTEM_DESTRUCTION",
        ),
        (
            "python",
            ("-c", "import os as o; print(o.environ)"),
            "CMD_CREDENTIAL_ACCESS",
        ),
        (
            "node",
            ("-e", "require('fs')['rmSync']('/')"),
            "CMD_SYSTEM_DESTRUCTION",
        ),
    ],
)
def test_inline_alias_and_computed_access_cannot_bypass_permanent_denials(
    policy: PolicyEngine, program: str, args: tuple[str, ...], rule: str
) -> None:
    decision = policy.decide(
        RunProcessAction(id="inline-bypass", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == (rule,)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("python", ("-c", "print('safe')")),
        ("node", ("--eval", "console.log('safe')")),
    ],
)
def test_unclassified_inline_code_never_inherits_configured_program_allow(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="inline-fallback", reason="test", program=program, args=args
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_INLINE_CODE",)


@pytest.mark.parametrize(
    ("command", "expected", "rule"),
    [
        ("/bin/rm -rf /", DecisionOutcome.DENY, "CMD_SYSTEM_DESTRUCTION"),
        ("/usr/bin/sudo id", DecisionOutcome.DENY, "CMD_PRIVILEGE_ESCALATION"),
        ("cat .env", DecisionOutcome.DENY, "CMD_CREDENTIAL_ACCESS"),
        ("/usr/bin/pip install x", DecisionOutcome.REQUIRE_APPROVAL, "CMD_INSTALL"),
        ("git push", DecisionOutcome.REQUIRE_APPROVAL, "CMD_GIT_WRITE"),
        ("curl https://example.test", DecisionOutcome.REQUIRE_APPROVAL, "CMD_NETWORK"),
        ("echo safe", DecisionOutcome.REQUIRE_APPROVAL, "CMD_SHELL_COMMAND"),
    ],
)
def test_shell_command_text_is_classified_before_shell_program_allow(
    policy: PolicyEngine,
    command: str,
    expected: DecisionOutcome,
    rule: str,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="shell-command",
            reason="test",
            program="bash",
            args=("-c", command),
        )
    )

    assert decision.outcome is expected
    assert decision.rule_ids == (rule,)


def test_shell_permanent_denial_precedes_earlier_approval_segment(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="shell-priority",
            reason="test",
            program="bash",
            args=("-c", "rm -rf build; /usr/bin/sudo id"),
        )
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_PRIVILEGE_ESCALATION",)


@pytest.mark.parametrize(
    ("program", "args", "rule"),
    [
        ("curl", ("https://example.test",), "CMD_NETWORK"),
        ("npm", ("install",), "CMD_INSTALL"),
        ("git", ("push",), "CMD_GIT_WRITE"),
        ("unlisted-tool", ("--check",), "CMD_UNCONFIGURED_PROGRAM"),
    ],
)
def test_approval_classes(
    policy: PolicyEngine, program: str, args: tuple[str, ...], rule: str
) -> None:
    decision = policy.decide(
        RunProcessAction(id="approval", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.risk_level is RiskLevel.MEDIUM
    assert decision.rule_ids == (rule,)


def test_python_module_installer_requires_approval(policy: PolicyEngine) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="module-install",
            reason="test",
            program="python",
            args=("-m", "pip", "install", "requests"),
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_INSTALL",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("python", ("-m", "ensurepip")),
        ("pip", ("uninstall", "requests")),
        ("npm", ("i", "package")),
    ],
)
def test_installer_variants_precede_configured_program_allow(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="install-variant", reason="test", program=program, args=args
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_INSTALL",)


def test_configured_package_manager_non_install_command_requires_approval(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(id="pip-list", reason="test", program="pip", args=("list",))
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_INSTALL",)


@pytest.mark.parametrize(
    ("args", "expected", "rule"),
    [
        (
            ("config", "user.name", "x"),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_GIT_WRITE",
        ),
        (
            ("-C", "repo", "commit", "-m", "x"),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_GIT_WRITE",
        ),
        (("show", "commit"), DecisionOutcome.ALLOW, "CMD_CONFIGURED_PROGRAM"),
        (("show", "push"), DecisionOutcome.ALLOW, "CMD_CONFIGURED_PROGRAM"),
    ],
)
def test_git_classifies_the_actual_subcommand_only(
    policy: PolicyEngine,
    args: tuple[str, ...],
    expected: DecisionOutcome,
    rule: str,
) -> None:
    decision = policy.decide(
        RunProcessAction(id="git-subcommand", reason="test", program="git", args=args)
    )

    assert decision.outcome is expected
    assert decision.rule_ids == (rule,)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("rm", ("-rf", "/.")),
        ("rm", ("-rf", "//")),
        ("rm", ("-rf", "/lib")),
        ("rm", ("-rf", "/lib64/module")),
        ("rm", ("-rf", "/var/lib/app")),
        ("mv", ("/etc/passwd", "/tmp/passwd")),
        ("cp", ("payload", "/etc/passwd")),
    ],
)
def test_system_path_mutations_are_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="system-path", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_SYSTEM_DESTRUCTION",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("rm", ("-rf", "/tmp/../etc")),
        ("cp", ("payload", "/tmp/../etc/passwd")),
        ("cp", ("payload", r"C:\Temp\..\Windows\System32\x")),
        ("mv", ("payload", "../../etc/passwd")),
        ("cp", ("payload", r"\\server\share\target")),
        ("rm", ("-rf", r"\\?\C:\Windows\System32")),
        ("rm", ("-rf", r"\\.\PhysicalDrive0")),
    ],
)
def test_normalized_system_and_escape_paths_are_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="normalized-system", reason="test", program=program, args=args
        )
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_SYSTEM_DESTRUCTION",)


@pytest.mark.parametrize(
    "command",
    (
        "echo ok & sudo id",
        "echo ok\nsudo id",
        "echo ok\r\nsudo id",
        "command sudo id",
        'sh -c "sudo id"',
        'echo "$(sudo id)"',
        "result=$(sudo id)",
    ),
)
def test_shell_scans_all_wrapped_segments_for_permanent_denials(
    policy: PolicyEngine, command: str
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="shell-nested", reason="test", program="bash", args=("-c", command)
        )
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_PRIVILEGE_ESCALATION",)


@pytest.mark.parametrize(
    ("program", "args", "rule"),
    [
        ("bash", ("./attack.sh",), "CMD_SHELL_COMMAND"),
        ("powershell", ("-File", "attack.ps1"), "CMD_SHELL_COMMAND"),
        ("pwsh", ("-EncodedCommand", "ZQBjAGgAbwA="), "CMD_SHELL_COMMAND"),
        ("node", ("script.js",), "CMD_INLINE_CODE"),
        ("node", (), "CMD_INLINE_CODE"),
        ("python", ("script.py",), "CMD_INLINE_CODE"),
        ("python", (), "CMD_INLINE_CODE"),
        ("python", ("-m", "http.server"), "CMD_INLINE_CODE"),
    ],
)
def test_shell_and_interpreter_execution_modes_never_inherit_program_allow(
    policy: PolicyEngine, program: str, args: tuple[str, ...], rule: str
) -> None:
    decision = policy.decide(
        RunProcessAction(id="opaque-exec", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == (rule,)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("python", ("-",)),
        ("python", ("-i",)),
        ("node", ("--inspect",)),
    ],
)
def test_all_interpreter_entry_modes_require_approval(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="interpreter-mode", reason="test", program=program, args=args
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_INLINE_CODE",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("powershell", ("-Command", "Remove-Item", "-Recurse", r"C:\Windows")),
        ("pwsh", ("-c", "Remove-Item", "-Recurse", r"C:\Windows")),
        ("cmd", ("/c", "del", r"C:\Windows\System32\config")),
    ],
)
def test_windows_shell_command_flags_join_all_remaining_arguments(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="windows-shell", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_SYSTEM_DESTRUCTION",)


@pytest.mark.parametrize(
    ("args", "rule"),
    [
        (("diff", "--output", "result.patch"), "CMD_GIT_WRITE"),
        (("diff", "--output=result.patch"), "CMD_GIT_WRITE"),
        (("--paginate", "log"), "CMD_GIT_COMMAND"),
        (("-p", "show", "HEAD"), "CMD_GIT_COMMAND"),
        (("diff", "--ext-diff"), "CMD_GIT_COMMAND"),
        (("diff", "--textconv"), "CMD_GIT_COMMAND"),
        (("-c", "color.ui=false", "status"), "CMD_GIT_COMMAND"),
    ],
)
def test_git_read_commands_with_side_effect_hooks_require_approval(
    policy: PolicyEngine, args: tuple[str, ...], rule: str
) -> None:
    decision = policy.decide(
        RunProcessAction(id="git-option", reason="test", program="git", args=args)
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == (rule,)


@pytest.mark.parametrize(
    ("args", "rule"),
    [
        (("-c", "core.pager=sudo id", "--paginate", "log"), "CMD_PRIVILEGE_ESCALATION"),
        (("-C", "repo", "credential", "fill"), "CMD_CREDENTIAL_ACCESS"),
    ],
)
def test_git_global_options_cannot_hide_permanent_denials(
    policy: PolicyEngine, args: tuple[str, ...], rule: str
) -> None:
    decision = policy.decide(
        RunProcessAction(id="git-permanent", reason="test", program="git", args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == (rule,)


def test_ambiguous_windows_short_system_path_requires_approval(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="short-path",
            reason="test",
            program="cp",
            args=("payload", r"C:\PROGRA~1\Vendor\target"),
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_AMBIGUOUS_SYSTEM_PATH",)
    assert "PROGRA~1" not in decision.explanation


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("get-content", ("Env:PATH",)),
        ("gc", ("Env:PATH",)),
        ("gci", ("Env:",)),
        (
            "python",
            (
                "-c",
                "print(getattr(__import__('os'), 'environ', {}))",
            ),
        ),
    ],
)
def test_environment_reader_variants_are_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="env-reader", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_CREDENTIAL_ACCESS",)


@pytest.mark.parametrize(
    "command",
    [
        'echo "safe; sudo id"',
        "echo 'safe; sudo id'",
        "echo '$(sudo id)'",
        "echo '`sudo id`'",
        r'echo "\$(sudo id)"',
        r'echo "\`sudo id\`"',
    ],
)
def test_shell_literals_do_not_create_nested_commands(
    policy: PolicyEngine, command: str
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="shell-literal", reason="test", program="bash", args=("-c", command)
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_SHELL_COMMAND",)


def test_apostrophe_inside_double_quotes_does_not_hide_command_substitution(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="quoted-substitution",
            reason="test",
            program="bash",
            args=("-c", 'echo "it\'s $(sudo id)"'),
        )
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_PRIVILEGE_ESCALATION",)


@pytest.mark.parametrize(
    "args",
    [
        ("grep", '--open-files-in-pager="sudo id"', "needle"),
        ("grep", "--open-files-in-pager", "sudo id", "needle"),
        ("grep", "-Osudo id", "needle"),
        ("grep", "-O", "sudo id", "needle"),
    ],
)
def test_git_visible_pager_commands_apply_permanent_rules(
    policy: PolicyEngine, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="git-pager", reason="test", program="git", args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_PRIVILEGE_ESCALATION",)


@pytest.mark.parametrize(
    "args",
    [
        ("grep", "--open-files-in-pager"),
        ("status", "--future-read-option"),
        ("show", "-1"),
    ],
)
def test_git_safe_reads_with_options_default_to_approval(
    policy: PolicyEngine, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="git-read-option", reason="test", program="git", args=args)
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_GIT_COMMAND",)


def test_git_double_dash_keeps_following_dash_argument_positional(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="git-positional",
            reason="test",
            program="git",
            args=("show", "--", "-named-path"),
        )
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule_ids == ("CMD_CONFIGURED_PROGRAM",)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("node", ("--print", "process['env']")),
        ("node", ("-p", 'process["env"]')),
        ("python", ("-c", "import os as o; print(getattr(o, 'environ'))")),
    ],
)
def test_computed_environment_access_is_permanently_denied(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(id="computed-env", reason="test", program=program, args=args)
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("CMD_CREDENTIAL_ACCESS",)


@pytest.mark.parametrize(
    ("program", "args", "expected", "rule"),
    [
        (
            "git",
            ("-c", "alias.pwn=!sudo id", "pwn"),
            DecisionOutcome.DENY,
            "CMD_PRIVILEGE_ESCALATION",
        ),
        (
            "git",
            ("ls-remote", "origin"),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_NETWORK",
        ),
        (
            "git",
            ("unknown-external",),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_GIT_COMMAND",
        ),
        (
            "git-custom",
            (),
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_GIT_COMMAND",
        ),
        (
            "git",
            ("status",),
            DecisionOutcome.ALLOW,
            "CMD_CONFIGURED_PROGRAM",
        ),
    ],
)
def test_git_uses_conservative_read_allowlist_and_scans_alias_commands(
    policy: PolicyEngine,
    program: str,
    args: tuple[str, ...],
    expected: DecisionOutcome,
    rule: str,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="git-conservative", reason="test", program=program, args=args
        )
    )

    assert decision.outcome is expected
    assert decision.rule_ids == (rule,)


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("npm", ("ci",)),
        ("npm", ("run", "build")),
        ("npm", ("publish",)),
        ("npm", ("exec", "tool")),
        ("pip", ("download", "requests")),
        ("pip", ("wheel", "requests")),
        ("uv", ("run", "tool")),
    ],
)
def test_package_manager_programs_default_to_install_approval(
    policy: PolicyEngine, program: str, args: tuple[str, ...]
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="package-default", reason="test", program=program, args=args
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_INSTALL",)


@pytest.mark.parametrize(
    ("platform_name", "configured", "actual", "expected"),
    [
        ("nt", "ruff", "RUFF.EXE", True),
        ("nt", "ruff.exe", "RUFF.EXE", True),
        ("nt", "ruff.exe", "ruff.cmd", False),
        ("posix", "ruff", "ruff", True),
        ("posix", "ruff", "RUFF.EXE", False),
        ("posix", "ruff.exe", "ruff.cmd", False),
    ],
)
def test_bare_authorization_uses_execution_platform_semantics(
    platform_name: str, configured: str, actual: str, expected: bool
) -> None:
    assert (
        _program_matches_configuration(configured, actual, platform_name=platform_name)
        is expected
    )


def test_validator_command_requires_exact_arguments(policy: PolicyEngine) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="mismatch", reason="test", program="pytest", args=("-q", "extra")
        )
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_UNCONFIGURED_PROGRAM",)


def test_shell_metacharacters_are_literal_for_an_exact_validator(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="literal",
            reason="test",
            program="echo",
            args=("hello; rm -rf /",),
        )
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule_ids == ("CMD_CONFIGURED_VALIDATOR",)


def test_shell_metacharacters_do_not_hide_underlying_program_risk(
    policy: PolicyEngine,
) -> None:
    decision = policy.decide(
        RunProcessAction(id="literal", reason="test", program="rm", args=("a;b",))
    )

    assert decision.outcome is DecisionOutcome.REQUIRE_APPROVAL
    assert decision.rule_ids == ("CMD_DELETE",)


def test_configured_non_validator_program_is_allowed(policy: PolicyEngine) -> None:
    decision = policy.decide(
        RunProcessAction(
            id="configured", reason="test", program="RUFF.EXE", args=("check", ".")
        )
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule_ids == ("CMD_CONFIGURED_PROGRAM",)


@pytest.mark.parametrize(
    ("validator_id", "expected", "rule"),
    [
        ("pytest", DecisionOutcome.ALLOW, "VALIDATOR_CONFIGURED"),
        ("PyTest", DecisionOutcome.DENY, "VALIDATOR_UNKNOWN"),
        ("missing", DecisionOutcome.DENY, "VALIDATOR_UNKNOWN"),
    ],
)
def test_validator_id_must_be_configured_exactly(
    policy: PolicyEngine,
    validator_id: str,
    expected: DecisionOutcome,
    rule: str,
) -> None:
    decision = policy.decide(
        RunValidationAction(id="validator", reason="test", validator_id=validator_id)
    )

    assert decision.outcome is expected
    assert decision.rule_ids == (rule,)


class _RecordingBoundary:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AccessKind]] = []

    def resolve(self, candidate: str, access: AccessKind) -> Path:
        self.calls.append((candidate, access))
        return Path(candidate)


class _UnexpectedFailureBoundary:
    def resolve(self, candidate: str, access: AccessKind) -> Path:
        del candidate, access
        raise RuntimeError("unexpected-boundary-failure")


@pytest.mark.parametrize(
    ("action", "path", "access", "rule"),
    [
        (
            ListFilesAction(id="list", reason="test", path="src", limit=100),
            "src",
            AccessKind.LIST,
            "FILE_LIST",
        ),
        (
            ReadFileAction(
                id="read",
                reason="test",
                path="src/a.py",
                start_line=1,
                end_line=200,
            ),
            "src/a.py",
            AccessKind.READ,
            "FILE_READ",
        ),
        (
            SearchTextAction(
                id="search",
                reason="test",
                path="src",
                pattern="x",
                max_results=50,
            ),
            "src",
            AccessKind.SEARCH,
            "FILE_SEARCH",
        ),
        (
            ApplyPatchAction(
                id="write",
                reason="test",
                path="src/a.py",
                expected_sha256="0" * 64,
                old_text="before",
                new_text="after",
                expected_replacements=1,
            ),
            "src/a.py",
            AccessKind.WRITE,
            "FILE_WRITE",
        ),
    ],
)
def test_file_actions_use_the_required_boundary_access(
    settings: SafeFixSettings,
    action: ListFilesAction | ReadFileAction | SearchTextAction | ApplyPatchAction,
    path: str,
    access: AccessKind,
    rule: str,
) -> None:
    boundary = _RecordingBoundary()
    engine = PolicyEngine(settings=settings, boundary=boundary)  # type: ignore[arg-type]

    decision = engine.decide(action)

    assert boundary.calls == [(path, access)]
    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule_ids == (rule,)


def test_boundary_failure_is_denied_without_disclosing_path(
    policy: PolicyEngine,
) -> None:
    rejected = "../private-boundary-marker.txt"
    decision = policy.decide(
        ReadFileAction(
            id="outside",
            reason="test",
            path=rejected,
            start_line=1,
            end_line=200,
        )
    )

    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule_ids == ("FILE_BOUNDARY_DENY",)
    assert rejected not in decision.explanation


def test_unexpected_boundary_exception_is_not_swallowed(
    settings: SafeFixSettings,
) -> None:
    engine = PolicyEngine(
        settings=settings,
        boundary=_UnexpectedFailureBoundary(),  # type: ignore[arg-type]
    )
    action = ReadFileAction(
        id="unexpected-boundary",
        reason="test",
        path="README.md",
        start_line=1,
        end_line=200,
    )

    with pytest.raises(RuntimeError, match="unexpected-boundary-failure"):
        engine.decide(action)


def test_finish_is_allowed(policy: PolicyEngine) -> None:
    decision = policy.decide(
        FinishAction(id="finish", reason="test", summary="work complete")
    )

    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule_ids == ("ACTION_FINISH",)


def test_every_decision_has_explanation_and_stable_rule(policy: PolicyEngine) -> None:
    actions = (
        ReadFileAction(
            id="read",
            reason="test",
            path="README.md",
            start_line=1,
            end_line=200,
        ),
        RunValidationAction(id="validator", reason="test", validator_id="pytest"),
        RunProcessAction(id="process", reason="test", program="unknown"),
        FinishAction(id="finish", reason="test", summary="done"),
    )

    first = [policy.decide(action) for action in actions]
    second = [policy.decide(action) for action in actions]

    assert all(decision.explanation.strip() for decision in first)
    assert all(decision.rule_ids for decision in first)
    assert [decision.rule_ids for decision in first] == [
        decision.rule_ids for decision in second
    ]
