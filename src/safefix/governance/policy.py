from __future__ import annotations

import ntpath
import os
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
    {"cat", "type", "more", "less", "head", "tail", "get-content", "gc"}
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
_FILE_TRANSFER_PROGRAMS = frozenset(
    {"cp", "mv", "copy", "xcopy", "robocopy", "tar", "zip", "7z"}
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
_GIT_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "ls-remote", "pull"})
_GIT_SAFE_READ_SUBCOMMANDS = frozenset(
    {
        "blame",
        "cat-file",
        "describe",
        "diff",
        "diff-tree",
        "for-each-ref",
        "grep",
        "log",
        "ls-files",
        "ls-tree",
        "merge-base",
        "name-rev",
        "rev-list",
        "rev-parse",
        "show",
        "show-ref",
        "status",
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
    r"getattr\s*\([\s\S]{0,512}?['\"]environ['\"]|"
    r"open\s*\([^)]*(?:\.env|\.ssh|\.pem|credentials?|id_(?:rsa|ed25519)))",
    re.IGNORECASE,
)
_NODE_CREDENTIAL_READ = re.compile(
    r"\bprocess(?:\.env\b|\s*\[\s*['\"]env['\"]\s*\])", re.IGNORECASE
)
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
_SHELL_ROOT_TARGET = re.compile(r"(?:^|[\s;&|'\"])/{1,2}\*?(?:$|[\s;&|'\"),])")
_COMMAND_TOKEN = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s]+)""")
_SUBCOMMAND_START = re.compile(
    r"(?:\$\(|`)\s*(?:command\s+|exec\s+)?([^\s;&|()`]+)", re.IGNORECASE
)
_QUOTED_VALUE = re.compile(r"(['\"])(.*?)\1")
_COMMAND_WRAPPERS = frozenset({"command", "exec", "builtin", "nohup"})
_WINDOWS_SHORT_COMPONENT = re.compile(r"[^\\/:]*~\d+(?:\.[^\\/:]+)?$")
_POSIX_SYSTEM_ROOTS = frozenset(
    {
        "bin",
        "boot",
        "dev",
        "etc",
        "lib",
        "lib64",
        "proc",
        "root",
        "sbin",
        "sys",
        "usr",
        "var",
    }
)
_WINDOWS_SYSTEM_ROOTS = ("windows", "program files", "programdata")


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


def _split_executable_suffix(program: str) -> tuple[str, str | None]:
    identity = program.casefold()
    for suffix in _EXECUTABLE_SUFFIXES:
        if identity.endswith(suffix) and len(identity) > len(suffix):
            return identity[: -len(suffix)], suffix
    return identity, None


def _program_matches_configuration(
    configured: str,
    actual: str,
    *,
    platform_name: str | None = None,
) -> bool:
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
    if (platform_name or os.name) != "nt":
        return actual == configured
    configured_stem, configured_suffix = _split_executable_suffix(configured)
    actual_stem, actual_suffix = _split_executable_suffix(actual)
    if configured_suffix is not None:
        return actual_stem == configured_stem and actual_suffix == configured_suffix
    return actual_stem == configured_stem and not any(
        actual_stem.endswith(suffix) for suffix in _EXECUTABLE_SUFFIXES
    )


def _argument_text(args: Sequence[str]) -> str:
    return " ".join(args)


def _inline_code(identity: str, args: Sequence[str]) -> str | None:
    flags: tuple[str, ...]
    if _PYTHON_PROGRAM.fullmatch(identity):
        flags = ("-c",)
    elif identity in {"node", "nodejs"}:
        flags = ("-e", "--eval", "-p", "--print")
    else:
        return None
    for index, arg in enumerate(args):
        if arg in flags:
            return args[index + 1] if index + 1 < len(args) else None
        if identity in {"node", "nodejs"}:
            if arg.startswith(("--eval=", "--print=")):
                return arg.partition("=")[2]
            if arg.startswith(("-e", "-p")) and len(arg) > 2:
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
            if identity in {"cmd", "powershell", "pwsh"}:
                return " ".join(args[index + 1 :])
            return args[index + 1] if index + 1 < len(args) else ""
    return None


def _split_shell_segments(command_text: str) -> tuple[str, ...]:
    segments: list[str] = []
    start = 0
    quote: str | None = None
    index = 0
    while index < len(command_text):
        character = command_text[index]
        if character == "\\" and quote != "'":
            index += 2
            continue
        if quote is not None:
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue
        if character in {";", "&", "|", "\r", "\n"}:
            segment = command_text[start:index].strip()
            if segment:
                segments.append(segment)
            if index + 1 < len(command_text):
                pair = command_text[index : index + 2]
                if pair in {"&&", "||", "\r\n"}:
                    index += 1
            start = index + 1
        index += 1
    final = command_text[start:].strip()
    if final:
        segments.append(final)
    return tuple(segments)


def _mask_single_quoted_text(command_text: str) -> str:
    masked: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command_text):
        character = command_text[index]
        if character == "\\" and quote != "'":
            masked.append(" ")
            if index + 1 < len(command_text):
                masked.append(" ")
                index += 2
                continue
        if character == "'" and quote != '"':
            quote = None if quote == "'" else "'"
            masked.append(" ")
        elif character == '"' and quote != "'":
            quote = None if quote == '"' else '"'
            masked.append(character)
        elif quote == "'":
            masked.append(" ")
        else:
            masked.append(character)
        index += 1
    return "".join(masked)


def _shell_commands(command_text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    commands: list[tuple[str, tuple[str, ...]]] = []
    for segment in _split_shell_segments(command_text):
        tokens = [
            token.strip("'\"") for token in _COMMAND_TOKEN.findall(segment.strip())
        ]
        while tokens and "=" in tokens[0] and not tokens[0].startswith(("/", "\\")):
            tokens.pop(0)
        while tokens and _risk_program_identity(tokens[0]) in _COMMAND_WRAPPERS:
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
        if not tokens:
            continue
        commands.append((_risk_program_identity(tokens[0]), tuple(tokens[1:])))
    commands.extend(
        (_risk_program_identity(program), ())
        for program in _SUBCOMMAND_START.findall(_mask_single_quoted_text(command_text))
    )
    return tuple(commands)


def _git_invocation(args: Sequence[str]) -> tuple[str | None, tuple[str, ...]]:
    index = 0
    configuration: list[str] = []
    value_options = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
    while index < len(args):
        arg = args[index]
        folded = arg.casefold()
        if arg in value_options:
            if index + 1 < len(args) and arg == "-c":
                configuration.append(args[index + 1])
            index += 2
            continue
        matched_option = next(
            (
                option
                for option in value_options
                if folded.startswith(f"{option.casefold()}=")
            ),
            None,
        )
        if matched_option is not None:
            if matched_option == "-c":
                configuration.append(arg.partition("=")[2])
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return folded, tuple(configuration)
    return None, tuple(configuration)


def _git_config_commands(args: Sequence[str]) -> tuple[str, ...]:
    _, configuration = _git_invocation(args)
    commands: list[str] = []
    for item in configuration:
        key, separator, value = item.partition("=")
        if not separator:
            continue
        folded_key = key.casefold()
        if folded_key.startswith("alias.") and value.startswith("!"):
            commands.append(value[1:])
            continue
        command_keys = (
            folded_key == "core.pager"
            or folded_key.startswith("pager.")
            or folded_key
            in {
                "core.editor",
                "sequence.editor",
                "diff.external",
                "core.sshcommand",
                "gpg.program",
                "credential.helper",
            }
            or (folded_key.startswith("diff.") and folded_key.endswith(".command"))
            or (folded_key.startswith("difftool.") and folded_key.endswith(".cmd"))
            or (folded_key.startswith("mergetool.") and folded_key.endswith(".cmd"))
        )
        if command_keys and value:
            commands.append(value)
    return tuple(commands)


def _git_pager_commands(args: Sequence[str]) -> tuple[str, ...]:
    commands: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        value: str | None = None
        if arg.startswith("--open-files-in-pager="):
            value = arg.partition("=")[2]
        elif arg.startswith("-O") and len(arg) > 2:
            value = arg[2:]
        if value:
            commands.append(value.strip().strip("'\""))
        index += 1
    return tuple(commands)


def _git_options(args: Sequence[str]) -> tuple[str, ...]:
    options: list[str] = []
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("-"):
            options.append(arg)
    return tuple(options)


def _expanded_commands(
    identity: str, args: Sequence[str], *, depth: int = 0
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    current = (identity, tuple(args))
    if depth >= 4:
        return (current,)
    expanded: list[tuple[str, tuple[str, ...]]] = [current]
    shell_text = _shell_command_text(identity, args)
    if shell_text is not None:
        for nested_identity, nested_args in _shell_commands(shell_text):
            expanded.extend(
                _expanded_commands(nested_identity, nested_args, depth=depth + 1)
            )
    if identity == "git":
        for configured_command in _git_config_commands(args):
            for nested_identity, nested_args in _shell_commands(configured_command):
                expanded.extend(
                    _expanded_commands(nested_identity, nested_args, depth=depth + 1)
                )
        for pager_command in _git_pager_commands(args):
            for nested_identity, nested_args in _shell_commands(pager_command):
                expanded.extend(
                    _expanded_commands(nested_identity, nested_args, depth=depth + 1)
                )
    return tuple(expanded)


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


def _is_system_or_escape_path(candidate: str) -> bool:
    raw = candidate.strip().strip("'\"")
    if not raw or raw.startswith("-"):
        return False
    windows_raw = raw.replace("/", "\\")
    if windows_raw.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return True
    windows_normalized = ntpath.normpath(windows_raw).casefold()
    if windows_normalized.startswith("\\\\"):
        return True
    drive, tail = ntpath.splitdrive(windows_normalized)
    if drive:
        tail_key = tail.lstrip("\\")
        if not tail_key or any(
            tail_key == root or tail_key.startswith(f"{root}\\")
            for root in _WINDOWS_SYSTEM_ROOTS
        ):
            return True

    posix_normalized = posixpath.normpath(raw.replace("\\", "/")).casefold()
    if posix_normalized == ".." or posix_normalized.startswith("../"):
        return True
    if posix_normalized.startswith("/"):
        first = posix_normalized.lstrip("/").partition("/")[0]
        return not first or first in _POSIX_SYSTEM_ROOTS
    return False


def _targets_root_or_system(args: Sequence[str]) -> bool:
    return any(_is_system_or_escape_path(arg) for arg in args)


def _has_ambiguous_windows_short_path(identity: str, args: Sequence[str]) -> bool:
    if identity not in _SYSTEM_MUTATION_PROGRAMS | _DELETE_PROGRAMS:
        return False
    for candidate in args:
        raw = candidate.strip().strip("'\"")
        if not _is_windows_absolute_path(raw):
            continue
        if any(
            _WINDOWS_SHORT_COMPONENT.fullmatch(component)
            for component in re.split(r"[\\/]", raw)
        ):
            return True
    return False


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
        and (
            _SHELL_ROOT_TARGET.search(code.replace("\\", "/"))
            or any(
                _is_system_or_escape_path(value)
                for _, value in _QUOTED_VALUE.findall(code)
            )
        )
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
    if identity in {"get-content", "gc", "get-childitem", "gci"} and any(
        arg.casefold().startswith("env:") for arg in args
    ):
        return True
    git_subcommand, _ = _git_invocation(args) if identity == "git" else (None, ())
    if git_subcommand is not None and git_subcommand.startswith("credential"):
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
                return True
    return identity in _INSTALL_PROGRAMS


def _git_approval_rule(identity: str, args: Sequence[str]) -> str | None:
    if identity.startswith("git-"):
        return "CMD_GIT_COMMAND"
    if identity != "git":
        return None
    subcommand, _ = _git_invocation(args)
    options = _git_options(args)
    if any(arg == "--output" or arg.startswith("--output=") for arg in options):
        return "CMD_GIT_WRITE"
    if subcommand in _GIT_NETWORK_SUBCOMMANDS:
        return "CMD_NETWORK"
    if subcommand in _GIT_WRITE_SUBCOMMANDS:
        return "CMD_GIT_WRITE"
    if subcommand in _GIT_SAFE_READ_SUBCOMMANDS:
        return "CMD_GIT_COMMAND"
    return "CMD_GIT_COMMAND"


def _is_interpreter_execution(identity: str, args: Sequence[str]) -> bool:
    if identity in _SHELL_PROGRAMS:
        return True
    if _PYTHON_PROGRAM.fullmatch(identity):
        return True
    if identity in {"node", "nodejs"}:
        return True
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

    def _is_sensitive_file_transfer(self, identity: str, args: Sequence[str]) -> bool:
        if identity not in _FILE_TRANSFER_PROGRAMS:
            return False
        for candidate in args:
            if not candidate or candidate.startswith("-"):
                continue
            try:
                self._boundary.resolve(candidate, AccessKind.READ)
            except SensitivePathDenied:
                return True
            except PathOutsideWorkspace:
                continue
        return False

    def _decide_process(self, action: RunProcessAction) -> PolicyDecision:
        identity = _risk_program_identity(action.program)
        args = action.args
        commands = _expanded_commands(identity, args)

        permanent_rules = (
            (
                "CMD_DENIED_PROGRAM",
                "The command invokes a program denied by policy configuration.",
                lambda program, command_args: program in self._denied_programs,
            ),
            (
                "CMD_PRIVILEGE_ESCALATION",
                "The command requests privilege escalation.",
                lambda program, command_args: program in _PRIVILEGE_PROGRAMS,
            ),
            (
                "CMD_CREDENTIAL_ACCESS",
                "The command attempts to read credentials or secrets.",
                lambda program, command_args: (
                    program not in _SHELL_PROGRAMS
                    and (
                        _is_credential_access(program, command_args)
                        or self._is_sensitive_file_transfer(program, command_args)
                    )
                ),
            ),
            (
                "CMD_SYSTEM_DESTRUCTION",
                "The command targets a root or system location.",
                lambda program, command_args: (
                    program not in _SHELL_PROGRAMS
                    and _is_system_destruction(program, command_args)
                ),
            ),
        )
        for rule, explanation, matches in permanent_rules:
            if any(
                matches(program, command_args) for program, command_args in commands
            ):
                return self._decision(
                    action.id, DecisionOutcome.DENY, rule, explanation
                )

        is_validator = any(
            args == validator_args
            and _program_matches_configuration(validator_program, action.program)
            for validator_program, validator_args in self._validators
        )
        if is_validator:
            return self._decision(
                action.id,
                DecisionOutcome.ALLOW,
                "CMD_CONFIGURED_VALIDATOR",
                "The command exactly matches a configured validator.",
            )

        for program, command_args in commands:
            if _has_ambiguous_windows_short_path(program, command_args):
                rule = "CMD_AMBIGUOUS_SYSTEM_PATH"
                explanation = (
                    "An ambiguous Windows short path in a mutation requires approval."
                )
            elif _is_delete_command(program, command_args):
                rule = "CMD_DELETE"
                explanation = "Deletion commands require explicit approval."
            elif _is_install(program, command_args):
                rule = "CMD_INSTALL"
                explanation = "Package-manager commands require explicit approval."
            elif (git_rule := _git_approval_rule(program, command_args)) is not None:
                rule = git_rule
                explanation = "The Git command is not an allowlisted local read."
            elif program in _NETWORK_PROGRAMS:
                rule = "CMD_NETWORK"
                explanation = "Network client programs require explicit approval."
            elif program in _FILE_TRANSFER_PROGRAMS:
                rule = "CMD_FILE_TRANSFER"
                explanation = "File-transfer commands require explicit approval."
            else:
                continue
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                rule,
                explanation,
            )

        for program, command_args in commands:
            if not _is_interpreter_execution(program, command_args):
                continue
            rule = (
                "CMD_SHELL_COMMAND" if program in _SHELL_PROGRAMS else "CMD_INLINE_CODE"
            )
            return self._decision(
                action.id,
                DecisionOutcome.REQUIRE_APPROVAL,
                rule,
                "Shell and interpreter execution requires explicit approval.",
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
