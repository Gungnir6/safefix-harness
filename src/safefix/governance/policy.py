from __future__ import annotations

import ntpath
import posixpath
import re
from collections.abc import Sequence
from pathlib import PureWindowsPath

from safefix.config import SafeFixSettings
from safefix.domain import (
    AccessKind,
    Action,
    ApplyPatchAction,
    DecisionOutcome,
    FinishAction,
    ListFilesAction,
    PolicyDecision,
    ReadFileAction,
    RiskLevel,
    RunProcessAction,
    RunValidationAction,
    SearchTextAction,
)
from safefix.governance.paths import (
    PathOutsideWorkspace,
    SensitivePathDenied,
    WorkspaceBoundary,
)


_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")
_PRIVILEGE_PROGRAMS = frozenset({"sudo", "su", "doas", "runas", "pkexec", "gsudo"})
_CREDENTIAL_PROGRAMS = frozenset(
    {
        "env",
        "printenv",
        "keyring",
        "secret-tool",
        "pass",
        "cmdkey",
        "security",
        "ssh-add",
        "git-credential-manager",
    }
)
_FILE_READER_PROGRAMS = frozenset(
    {"cat", "type", "more", "less", "head", "tail", "get-content"}
)
_SHELL_PROGRAMS = frozenset({"cmd", "powershell", "pwsh", "sh", "bash", "zsh", "fish"})
_DELETE_PROGRAMS = frozenset(
    {"rm", "del", "erase", "rmdir", "rd", "unlink", "remove-item"}
)
_NETWORK_PROGRAMS = frozenset(
    {
        "curl",
        "wget",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "telnet",
        "nc",
        "ncat",
        "netcat",
        "rsync",
    }
)
_INSTALL_PROGRAMS = frozenset(
    {
        "pip",
        "pip3",
        "uv",
        "npm",
        "yarn",
        "pnpm",
        "bun",
        "apt",
        "apt-get",
        "yum",
        "dnf",
        "apk",
        "brew",
        "choco",
        "winget",
        "scoop",
        "conda",
        "mamba",
        "gem",
        "cargo",
        "composer",
        "poetry",
        "msiexec",
        "installer",
    }
)
_INSTALL_SUBCOMMANDS = frozenset(
    {
        "install",
        "i",
        "add",
        "sync",
        "update",
        "upgrade",
        "create",
        "uninstall",
        "remove",
    }
)
_GIT_WRITE_SUBCOMMANDS = frozenset(
    {
        "add",
        "am",
        "apply",
        "bisect",
        "branch",
        "checkout",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "config",
        "fetch",
        "init",
        "merge",
        "mv",
        "pull",
        "push",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "submodule",
        "switch",
        "tag",
        "worktree",
    }
)
_SYSTEM_DESTRUCTION_PROGRAMS = frozenset(
    {"format", "mkfs", "shutdown", "reboot", "halt", "poweroff"}
)
_SYSTEM_MUTATION_PROGRAMS = frozenset(
    {"chmod", "chown", "chgrp", "truncate", "icacls", "takeown", "mv", "cp"}
)
_PYTHON_PROGRAM = re.compile(r"^(?:python(?:\d+(?:\.\d+)*)?|py|pypy\d*)$")
_PYTHON_DELETE = re.compile(
    r"(?:os\.(?:remove|unlink|rmdir)|shutil\.rmtree|"
    r"\b[a-zA-Z_]\w*\.rmtree|"
    r"pathlib\.Path\([^)]*\)\.(?:unlink|rmdir)|\.unlink\s*\(|\.rmdir\s*\()",
    re.IGNORECASE,
)
_PYTHON_SUBPROCESS_DELETE = re.compile(
    r"(?:os\.system|subprocess\.(?:run|call|check_call|check_output|popen))"
    r"\s*\([^)]*\b(?:rm|del|erase|rmdir|rd|unlink|remove-item)\b",
    re.IGNORECASE,
)
_NODE_DELETE = re.compile(
    r"(?:\b(?:unlink|unlinkSync|rm|rmSync|rmdir|rmdirSync)\s*\(|"
    r"\.(?:unlink|unlinkSync|rm|rmSync|rmdir|rmdirSync)\s*\(|"
    r"\[\s*['\"](?:unlink|unlinkSync|rm|rmSync|rmdir|rmdirSync)['\"]\s*\]\s*\()",
    re.IGNORECASE,
)
_NODE_SUBPROCESS_DELETE = re.compile(
    r"(?:child_process|require\s*\(\s*['\"]child_process['\"]\s*\))"
    r"[^;]*(?:exec|execSync|spawn|spawnSync)\s*\([^;]*"
    r"\b(?:rm|del|erase|rmdir|rd|unlink|remove-item)\b",
    re.IGNORECASE,
)
_PYTHON_CREDENTIAL_READ = re.compile(
    r"(?:\b[a-zA-Z_]\w*\.environ|os\.getenv\s*\(|keyring\.|"
    r"open\s*\([^)]*(?:\.env|\.ssh|\.pem|credentials?|id_(?:rsa|ed25519)))",
    re.IGNORECASE,
)
_NODE_CREDENTIAL_READ = re.compile(r"\bprocess\.env\b", re.IGNORECASE)
_SHELL_DELETE = re.compile(
    r"(?:^|[\s;&|])(?:rm|del|erase|rmdir|rd|unlink|remove-item)(?:\s|$)",
    re.IGNORECASE,
)
_SENSITIVE_REFERENCE = re.compile(
    r"(?:^|[\\/\s'\"])(?:\.env(?:[.\\/\s'\"]|$)|\.ssh(?:[\\/\s'\"]|$)|"
    r"[^\\/\s'\"]*\.pem(?:[\s'\"]|$)|id_(?:rsa|dsa|ecdsa|ed25519)(?:[.\s'\"]|$)|"
    r"credentials?(?:[.\\/\s'\"]|$))",
    re.IGNORECASE,
)
_ROOT_QUOTED = re.compile(r"(['\"])[\\/]\1")
_SHELL_ROOT_TARGET = re.compile(r"(?:^|[\s;&|'\"])/{1,2}\*?(?:$|[\s;&|'\"),])")
_DRIVE_ROOT = re.compile(r"(?<![\w])['\"]?[a-z]:/[\s'\"]?(?:$|[,;)])", re.IGNORECASE)
_SYSTEM_PATH = re.compile(
    r"(?:^|[\s'\"=(])(?:/(?:etc|usr|bin|sbin|boot|proc|sys|dev|lib|lib64|var)(?:/|[\s'\"),;]|$)|"
    r"[a-z]:/(?:windows|program files|programdata)(?:/|[\s'\"),;]|$))",
    re.IGNORECASE,
)
_COMMAND_TOKEN = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s]+)""")
_COMMAND_SPLIT = re.compile(r"(?:&&|\|\||[;|])")


def _risk_program_identity(program: str) -> str:
    basename = program.strip().strip("'\"").replace("\\", "/").rsplit("/", 1)[-1]
    identity = basename.casefold()
    removed = True
    while removed:
        removed = False
        for suffix in _EXECUTABLE_SUFFIXES:
            if identity.endswith(suffix) and len(identity) > len(suffix):
                identity = identity[: -len(suffix)]
                removed = True
                break
    return identity


def _is_windows_absolute_path(program: str) -> bool:
    return PureWindowsPath(program).is_absolute()


def _is_bare_program(program: str) -> bool:
    return not _is_windows_absolute_path(program) and not any(
        separator in program for separator in ("/", "\\")
    )


def _bare_authorization_identity(program: str) -> str:
    identity = program.casefold()
    for suffix in _EXECUTABLE_SUFFIXES:
        if identity.endswith(suffix) and len(identity) > len(suffix):
            return identity[: -len(suffix)]
    return identity


def _program_matches_configuration(configured: str, actual: str) -> bool:
    configured = configured.strip().strip("'\"")
    actual = actual.strip().strip("'\"")
    if _is_windows_absolute_path(configured):
        if not _is_windows_absolute_path(actual):
            return False
        configured_key = ntpath.normpath(configured).replace("\\", "/").casefold()
        actual_key = ntpath.normpath(actual).replace("\\", "/").casefold()
        return actual_key == configured_key
    if configured.startswith("/"):
        return actual == configured
    if not _is_bare_program(configured) or not _is_bare_program(actual):
        return actual == configured
    return _bare_authorization_identity(actual) == _bare_authorization_identity(
        configured
    )


def _argument_text(args: Sequence[str]) -> str:
    return " ".join(args)


def _first_non_option(args: Sequence[str]) -> str | None:
    for arg in args:
        if not arg.startswith("-"):
            return arg.casefold()
    return None


def _inline_code(identity: str, args: Sequence[str]) -> str | None:
    flags: tuple[str, ...]
    if _PYTHON_PROGRAM.fullmatch(identity):
        flags = ("-c",)
    elif identity in {"node", "nodejs"}:
        flags = ("-e", "--eval")
    else:
        return None
    for index, arg in enumerate(args):
        if arg in flags:
            return args[index + 1] if index + 1 < len(args) else None
        if identity in {"node", "nodejs"} and arg.startswith("--eval="):
            return arg.partition("=")[2]
        if identity in {"node", "nodejs"} and arg.startswith("-e") and len(arg) > 2:
            return arg[2:]
        if (
            _PYTHON_PROGRAM.fullmatch(identity)
            and arg.startswith("-c")
            and len(arg) > 2
        ):
            return arg[2:]
    return None


def _shell_command_text(identity: str, args: Sequence[str]) -> str | None:
    for index, arg in enumerate(args):
        folded = arg.casefold()
        is_command_flag = False
        if identity == "cmd":
            is_command_flag = folded in {"/c", "/k"}
        elif identity in {"powershell", "pwsh"}:
            is_command_flag = folded in {"-command", "-c"}
        elif identity in _SHELL_PROGRAMS:
            is_command_flag = (
                folded.startswith("-")
                and not folded.startswith("--")
                and "c" in folded[1:]
            )
        if is_command_flag:
            return args[index + 1] if index + 1 < len(args) else ""
    return None


def _shell_commands(command_text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    commands: list[tuple[str, tuple[str, ...]]] = []
    for segment in _COMMAND_SPLIT.split(command_text):
        tokens = [
            token.strip("'\"") for token in _COMMAND_TOKEN.findall(segment.strip())
        ]
        while tokens and "=" in tokens[0] and not tokens[0].startswith(("/", "\\")):
            tokens.pop(0)
        if not tokens:
            continue
        commands.append((_risk_program_identity(tokens[0]), tuple(tokens[1:])))
    return tuple(commands)


def _is_delete_command(identity: str, args: Sequence[str]) -> bool:
    if identity in _DELETE_PROGRAMS:
        return True
    code = _inline_code(identity, args)
    if code is not None:
        if _PYTHON_PROGRAM.fullmatch(identity):
            return bool(
                _PYTHON_DELETE.search(code) or _PYTHON_SUBPROCESS_DELETE.search(code)
            )
        return bool(_NODE_DELETE.search(code) or _NODE_SUBPROCESS_DELETE.search(code))
    if identity in _SHELL_PROGRAMS:
        return _SHELL_DELETE.search(_argument_text(args)) is not None
    return False


def _targets_root_or_system(args: Sequence[str]) -> bool:
    for arg in args:
        normalized = arg.strip().strip("'\"").replace("\\", "/").casefold()
        posix_normalized = posixpath.normpath(normalized)
        if (
            normalized in {"/", "/*", "//"}
            or posix_normalized == "/"
            or re.fullmatch(r"[a-z]:/?(?:\*?)", normalized)
        ):
            return True
    text = _argument_text(args).replace("\\", "/")
    return bool(
        _ROOT_QUOTED.search(text)
        or _DRIVE_ROOT.search(text)
        or _SYSTEM_PATH.search(text)
    )


def _is_system_destruction(identity: str, args: Sequence[str]) -> bool:
    if identity in _SYSTEM_DESTRUCTION_PROGRAMS:
        return True
    if identity == "dd":
        text = _argument_text(args).replace("\\", "/").casefold()
        return any(marker in text for marker in ("of=/dev/", "of=//./physicaldrive"))
    if identity in _SHELL_PROGRAMS and _is_delete_command(identity, args):
        text = _argument_text(args).replace("\\", "/")
        if _SHELL_ROOT_TARGET.search(text):
            return True
    code = _inline_code(identity, args)
    if (
        code is not None
        and _is_delete_command(identity, args)
        and _SHELL_ROOT_TARGET.search(code.replace("\\", "/"))
    ):
        return True
    if _targets_root_or_system(args):
        return (
            _is_delete_command(identity, args) or identity in _SYSTEM_MUTATION_PROGRAMS
        )
    return False


def _is_credential_access(identity: str, args: Sequence[str]) -> bool:
    if identity in _CREDENTIAL_PROGRAMS:
        return True
    if identity in {"get-childitem", "gci"} and any(
        arg.casefold().startswith("env:") for arg in args
    ):
        return True
    first = _first_non_option(args)
    if identity == "git" and first is not None and first.startswith("credential"):
        return True
    code = _inline_code(identity, args)
    if code is not None:
        matcher = (
            _PYTHON_CREDENTIAL_READ
            if _PYTHON_PROGRAM.fullmatch(identity)
            else _NODE_CREDENTIAL_READ
        )
        if matcher.search(code):
            return True
    text = _argument_text(args)
    if identity in _FILE_READER_PROGRAMS and _SENSITIVE_REFERENCE.search(text):
        return True
    if identity in _SHELL_PROGRAMS:
        folded = text.casefold()
        reads_environment = bool(
            re.search(r"(?:^|[\s;&|])(?:set|env|printenv)(?:\s|$)", folded)
            or "env:" in folded
        )
        reads_sensitive_file = (
            "get-content" in folded or _SHELL_DELETE.search(folded) is None
        ) and _SENSITIVE_REFERENCE.search(text)
        return reads_environment or bool(reads_sensitive_file)
    return False


def _is_install(identity: str, args: Sequence[str]) -> bool:
    if _PYTHON_PROGRAM.fullmatch(identity):
        for index, arg in enumerate(args[:-1]):
            if arg == "-m" and _risk_program_identity(args[index + 1]) in {
                "pip",
                "pip3",
                "ensurepip",
            }:
                return (
                    any(
                        candidate.casefold() in _INSTALL_SUBCOMMANDS
                        for candidate in args[index + 2 :]
                    )
                    or _risk_program_identity(args[index + 1]) == "ensurepip"
                )
    if identity not in _INSTALL_PROGRAMS:
        return False
    if identity in {"msiexec", "installer"}:
        return True
    return any(arg.casefold() in _INSTALL_SUBCOMMANDS for arg in args)


def _is_git_write(identity: str, args: Sequence[str]) -> bool:
    if identity != "git":
        return False
    index = 0
    value_options = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
    while index < len(args):
        arg = args[index]
        folded = arg.casefold()
        if arg in value_options:
            index += 2
            continue
        if any(folded.startswith(f"{option.casefold()}=") for option in value_options):
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return folded in _GIT_WRITE_SUBCOMMANDS
    return False


class PolicyEngine:
    """Classify structured actions through ordered, deterministic policy rules."""

    def __init__(self, settings: SafeFixSettings, boundary: WorkspaceBoundary) -> None:
        self._settings = settings
        self._boundary = boundary
        self._allowed_programs = settings.policy.allowed_programs
        self._denied_programs = frozenset(
            _risk_program_identity(program)
            for program in settings.policy.denied_programs
        )
        self._validators = tuple(
            (validator.program, validator.args) for validator in settings.validators
        )
        self._validator_ids = frozenset(
            validator.id for validator in settings.validators
        )

    @staticmethod
    def _decision(
        action_id: str,
        outcome: DecisionOutcome,
        rule_id: str,
        explanation: str,
    ) -> PolicyDecision:
        risk = {
            DecisionOutcome.ALLOW: RiskLevel.LOW,
            DecisionOutcome.REQUIRE_APPROVAL: RiskLevel.MEDIUM,
            DecisionOutcome.DENY: RiskLevel.HIGH,
        }[outcome]
        return PolicyDecision(
            action_id=action_id,
            outcome=outcome,
            risk_level=risk,
            rule_ids=(rule_id,),
            explanation=explanation,
        )

    def _decide_file_action(
        self,
        action: ListFilesAction | ReadFileAction | SearchTextAction | ApplyPatchAction,
    ) -> PolicyDecision:
        if isinstance(action, ListFilesAction):
            access, allow_rule = AccessKind.LIST, "FILE_LIST"
        elif isinstance(action, ReadFileAction):
            access, allow_rule = AccessKind.READ, "FILE_READ"
        elif isinstance(action, SearchTextAction):
            access, allow_rule = AccessKind.SEARCH, "FILE_SEARCH"
        else:
            access, allow_rule = AccessKind.WRITE, "FILE_WRITE"
        try:
            self._boundary.resolve(action.path, access)
        except (PathOutsideWorkspace, SensitivePathDenied):
            return self._decision(
                action.id,
                DecisionOutcome.DENY,
                "FILE_BOUNDARY_DENY",
                "The requested file action did not pass the workspace boundary.",
            )
        return self._decision(
            action.id,
            DecisionOutcome.ALLOW,
            allow_rule,
            "The structured file action passed the workspace boundary.",
        )

    def _decide_process(self, action: RunProcessAction) -> PolicyDecision:
        identity = _risk_program_identity(action.program)
        args = action.args
        shell_command = _shell_command_text(identity, args)

        if identity in self._denied_programs:
            return self._decision(
                action.id,
                DecisionOutcome.DENY,
                "CMD_DENIED_PROGRAM",
                "The program is permanently denied by policy configuration.",
            )
        if identity in _PRIVILEGE_PROGRAMS:
            return self._decision(
                action.id,
                DecisionOutcome.DENY,
                "CMD_PRIVILEGE_ESCALATION",
                "Privilege-escalation programs are permanently denied.",
            )
        if shell_command is None and _is_credential_access(identity, args):
            return self._decision(
                action.id,
                DecisionOutcome.DENY,
                "CMD_CREDENTIAL_ACCESS",
                "Credential and secret-reading commands are permanently denied.",
            )
        if shell_command is None and _is_system_destruction(identity, args):
            return self._decision(
                action.id,
                DecisionOutcome.DENY,
                "CMD_SYSTEM_DESTRUCTION",
                "Destructive operations against root or system targets are denied.",
            )

        if shell_command is None and _is_delete_command(identity, args):
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                "CMD_DELETE",
                "Deletion commands require explicit approval.",
            )
        if shell_command is None and _is_install(identity, args):
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                "CMD_INSTALL",
                "Package installation and environment mutation require approval.",
            )
        if shell_command is None and _is_git_write(identity, args):
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                "CMD_GIT_WRITE",
                "Git operations that can change local or remote state require approval.",
            )
        if shell_command is None and identity in _NETWORK_PROGRAMS:
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                "CMD_NETWORK",
                "Network client programs require explicit approval.",
            )

        inline_code = _inline_code(identity, args)
        if inline_code is not None:
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                "CMD_INLINE_CODE",
                "Inline interpreter code requires explicit approval.",
            )

        if shell_command is not None:
            commands = _shell_commands(shell_command)
            for nested_identity, nested_args in commands:
                if nested_identity in self._denied_programs:
                    return self._decision(
                        action.id,
                        DecisionOutcome.DENY,
                        "CMD_DENIED_PROGRAM",
                        "The shell command invokes a program denied by policy.",
                    )
                if nested_identity in _PRIVILEGE_PROGRAMS:
                    return self._decision(
                        action.id,
                        DecisionOutcome.DENY,
                        "CMD_PRIVILEGE_ESCALATION",
                        "The shell command requests privilege escalation.",
                    )
                if _is_credential_access(nested_identity, nested_args):
                    return self._decision(
                        action.id,
                        DecisionOutcome.DENY,
                        "CMD_CREDENTIAL_ACCESS",
                        "The shell command attempts to read credentials or secrets.",
                    )
                if _is_system_destruction(nested_identity, nested_args):
                    return self._decision(
                        action.id,
                        DecisionOutcome.DENY,
                        "CMD_SYSTEM_DESTRUCTION",
                        "The shell command targets a root or system location.",
                    )
            for nested_identity, nested_args in commands:
                if _is_delete_command(nested_identity, nested_args):
                    rule = "CMD_DELETE"
                    explanation = "The shell command performs deletion."
                elif _is_install(nested_identity, nested_args):
                    rule = "CMD_INSTALL"
                    explanation = "The shell command mutates a package environment."
                elif _is_git_write(nested_identity, nested_args):
                    rule = "CMD_GIT_WRITE"
                    explanation = "The shell command performs a Git write operation."
                elif nested_identity in _NETWORK_PROGRAMS:
                    rule = "CMD_NETWORK"
                    explanation = "The shell command invokes a network client."
                else:
                    continue
                return self._decision(
                    action.id,
                    DecisionOutcome.REQUIRE_APPROVAL,
                    rule,
                    explanation,
                )
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                "CMD_SHELL_COMMAND",
                "Shell command text requires explicit approval.",
            )

        if any(
            args == validator_args
            and _program_matches_configuration(validator_program, action.program)
            for validator_program, validator_args in self._validators
        ):
            return self._decision(
                action.id,
                DecisionOutcome.ALLOW,
                "CMD_CONFIGURED_VALIDATOR",
                "The command exactly matches a configured validator.",
            )
        if any(
            _program_matches_configuration(program, action.program)
            for program in self._allowed_programs
        ):
            return self._decision(
                action.id,
                DecisionOutcome.ALLOW,
                "CMD_CONFIGURED_PROGRAM",
                "The program is configured and no earlier risk rule matched.",
            )
        return self._decision(
            action.id,
            DecisionOutcome.REQUIRE_APPROVAL,
            "CMD_UNCONFIGURED_PROGRAM",
            "Programs that are not configured require explicit approval.",
        )

    def decide(self, action: Action) -> PolicyDecision:
        if isinstance(
            action,
            (ListFilesAction, ReadFileAction, SearchTextAction, ApplyPatchAction),
        ):
            return self._decide_file_action(action)
        if isinstance(action, RunProcessAction):
            return self._decide_process(action)
        if isinstance(action, RunValidationAction):
            if action.validator_id in self._validator_ids:
                return self._decision(
                    action.id,
                    DecisionOutcome.ALLOW,
                    "VALIDATOR_CONFIGURED",
                    "The validator identifier exactly matches the configuration.",
                )
            return self._decision(
                action.id,
                DecisionOutcome.DENY,
                "VALIDATOR_UNKNOWN",
                "Unconfigured validator identifiers are denied.",
            )
        if isinstance(action, FinishAction):
            return self._decision(
                action.id,
                DecisionOutcome.ALLOW,
                "ACTION_FINISH",
                "Finishing the task does not perform an external side effect.",
            )
        return self._decision(
            action.id,
            DecisionOutcome.DENY,
            "POLICY_NO_MATCH",
            "The action did not match an allowed policy rule.",
        )
