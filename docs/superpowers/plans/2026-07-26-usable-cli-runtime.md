# SafeFix Usable CLI Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the installed `safefix run` command assemble and run the real SafeFix harness against a persistent isolated project copy by default, with real OpenAI-compatible model support, deterministic Mock support, interactive one-time approvals, understandable results, and fresh-install distribution verification.

**Architecture:** Add a workspace preparation boundary and an application composition root instead of building dependencies inside `cli.py`. A CLI runner will drive the existing `TaskService` and `AgentLoop`, render redacted audit evidence, and handle approval pauses until a terminal state. Existing WebUI and mechanism demos remain compatible and can reuse the runtime later.

**Tech Stack:** Python 3.12, argparse, asyncio, sqlite3, pathlib/shutil, httpx, keyring, Pydantic, PyYAML, pytest, pytest-asyncio, Ruff, mypy, Hatchling.

## Global Constraints

- Default `safefix run` mode is a persistent isolated copy; only `--in-place` may target the original project.
- Isolation copies must exclude `.git`, `.venv`, caches, build outputs, SafeFix data directories, and configured sensitive patterns.
- `--non-interactive` must reject approval-required actions; there is no auto-approve flag.
- API keys must come from `CredentialService`; never place keys or approval capabilities in CLI arguments, YAML, output, audit payloads, or tracebacks.
- Real runs use the existing `AgentLoop`, `ToolRegistry`, `PolicyEngine`, `FeedbackEngine`, stores, and approval state machine; do not add a second agent loop.
- Mock runs require an explicit JSONL action script unless the user invokes the existing built-in mechanism demos.
- Default human output is concise and redacted; `--json` emits a stable final summary.
- Preserve existing dependency version ranges and Python requirement `>=3.12,<3.13`.
- Preserve existing public-demo behavior and all existing tests.
- Do not automatically commit, push, deploy, or delete a user project.

---

## File and Responsibility Map

- Create `src/safefix/execution_workspace.py`: validate a source project and prepare either a persistent isolated copy or an explicit in-place workspace.
- Create `src/safefix/runtime.py`: construct and close the real application graph for one CLI execution.
- Create `src/safefix/cli_runner.py`: drive `TaskService`, approval prompts, audit rendering, JSON summaries, and exit-code mapping.
- Modify `src/safefix/config.py`: expose a complete conservative default configuration template.
- Modify `src/safefix/task_service.py`: separate stable project identity from the effective workspace and persist redacted successful-repair memory.
- Modify `src/safefix/agent_loop.py`: persist redacted tool results and feedback so CLI/Web timelines represent the real loop.
- Modify `src/safefix/cli.py`: parse the production CLI options and delegate to the runner/runtime.
- Modify `src/safefix/context.py`: strengthen the system contract so a real model uses validators before finishing and consumes feedback.
- Create `examples/mock_repair.jsonl`: deterministic complete-loop actions for the packaged Python bug fixture.
- Modify `pyproject.toml`: include the Mock script in built artifacts if it is loaded as package data.
- Create `tests/unit/test_execution_workspace.py`: isolation and in-place safety tests.
- Create `tests/unit/test_runtime.py`: composition-root and resource lifecycle tests.
- Create `tests/unit/test_task_service.py`: stable project identity and terminal memory tests.
- Modify `tests/integration/test_agent_loop.py`: audit evidence for tool and validator outcomes.
- Extend `tests/unit/test_cli.py`: parser, config, approval, output, and exit-code tests.
- Create `tests/integration/test_cli_run.py`: real end-to-end CLI run with scripted Mock and an isolated project.
- Extend `tests/integration/test_distribution_metadata.py`: wheel/entry-point/package-data expectations.
- Modify `README.md`, `PLAN.md`, and `AGENT_LOG.md`: real CLI tutorial, limitations, verification evidence, and process record.

---

### Task 1: Valid Configuration Template and Persistent Execution Workspace

**Files:**
- Create: `src/safefix/execution_workspace.py`
- Modify: `src/safefix/config.py`
- Create: `tests/unit/test_execution_workspace.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `SafeFixSettings`, `PolicySettings.sensitive_patterns`.
- Produces:
  - `default_settings_yaml() -> str`
  - `WorkspacePreparationError(RuntimeError)`
  - `WorkspaceMode = Literal["isolated", "in_place"]`
  - `PreparedWorkspace(execution_id: str, source: Path, path: Path, mode: WorkspaceMode, metadata_path: Path | None)`
  - `default_data_dir(*, environment: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None) -> Path`
  - `prepare_workspace(project: Path, data_dir: Path, *, in_place: bool, sensitive_patterns: tuple[str, ...]) -> PreparedWorkspace`
  - `record_run_id(prepared: PreparedWorkspace, run_id: str) -> None`

- [ ] **Step 1: Write failing configuration-template tests**

Add tests that parse the generated YAML through the real loader and refuse accidental overwrite in the CLI task later:

```python
def test_default_settings_yaml_loads_as_complete_conservative_config(tmp_path: Path) -> None:
    path = tmp_path / "safefix.yaml"
    path.write_text(default_settings_yaml(), encoding="utf-8")

    settings = load_settings(path)

    assert settings.llm.model == "gpt-4.1-mini"
    assert str(settings.llm.endpoint).rstrip("/") == "https://api.openai.com/v1"
    assert settings.validators[0].id == "pytest"
    assert settings.validators[0].program == sys.executable
    assert settings.policy.allowed_programs == (
        sys.executable,
        "git",
    )
```

- [ ] **Step 2: Run the configuration test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -q
```

Expected: FAIL because `default_settings_yaml` does not exist.

- [ ] **Step 3: Implement the complete template**

Add `default_settings_yaml()` in `config.py`. Generate YAML with `yaml.safe_dump` from an explicit mapping so the current interpreter path is valid on Windows and Linux:

```python
def default_settings_yaml() -> str:
    raw = {
        "llm": {
            "endpoint": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini",
        },
        "validators": [
            {
                "id": "pytest",
                "kind": "test",
                "program": sys.executable,
                "args": ["-m", "pytest", "-q"],
                "timeout_seconds": 120,
                "success_exit_codes": [0],
                "output_limit_bytes": 65536,
            }
        ],
        "policy": {
            "sensitive_patterns": [".env", ".env.*", "**/*.pem", "**/.ssh/**"],
            "allowed_programs": [sys.executable, "git"],
            "denied_programs": ["sudo", "su", "powershell", "pwsh", "cmd", "sh", "bash"],
        },
        "budget": {
            "repair_rounds": 3,
            "no_progress_rounds": 2,
            "total_steps": 20,
            "wall_time_seconds": 900,
        },
        "memory": {"retrieval_limit": 5, "character_budget": 4000},
    }
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
```

Import `sys`; do not duplicate validation logic.

- [ ] **Step 4: Write failing workspace tests**

Cover:

```python
def test_isolated_workspace_is_persistent_and_excludes_sensitive_content(tmp_path: Path) -> None:
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
    assert (prepared.path / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (prepared.path / ".env").exists()
    assert not (prepared.path / ".git").exists()
    assert not (prepared.path / ".venv").exists()
    assert prepared.metadata_path is not None


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
```

Also test missing path, file instead of directory, data directory nested inside source, and copying into an existing execution directory.

Test data-directory precedence: `SAFEFIX_DATA_DIR`, Windows `LOCALAPPDATA`, Unix `XDG_DATA_HOME`, and Unix home fallback. The helper must not read `$HOME` directly when a `home` argument is supplied.

- [ ] **Step 5: Run workspace tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_execution_workspace.py -q
```

Expected: collection FAIL because `safefix.execution_workspace` does not exist.

- [ ] **Step 6: Implement workspace preparation**

Use a UUID execution ID, `Path.resolve(strict=True)`, `shutil.copytree`, and a fail-closed ignore callback. Always ignore:

```python
_IGNORED_NAMES = frozenset({
    ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "build", "dist", ".safefix",
})
```

Compile configured sensitive patterns with `pathspec.PathSpec.from_lines("gitwildmatch", patterns)`. Match relative POSIX paths, including directory candidates with a trailing slash. Write `execution.json` atomically beside the workspace with execution ID, source path, mode, created-at UTC, and `run_id: null`. `record_run_id` rewrites the metadata atomically for isolated runs and is a no-op for in-place runs.

- [ ] **Step 7: Run Task 1 tests and quality checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py tests/unit/test_execution_workspace.py -q
.\.venv\Scripts\python.exe -m ruff check src/safefix/config.py src/safefix/execution_workspace.py tests/unit/test_config.py tests/unit/test_execution_workspace.py
.\.venv\Scripts\python.exe -m mypy src/safefix/config.py src/safefix/execution_workspace.py
```

Expected: PASS with no Ruff or mypy errors.

- [ ] **Step 8: Commit Task 1**

```powershell
git add src/safefix/config.py src/safefix/execution_workspace.py tests/unit/test_config.py tests/unit/test_execution_workspace.py
git commit -m "feat(cli): 添加安全执行工作区"
```

---

### Task 2: Production Runtime Composition Root

**Files:**
- Create: `src/safefix/runtime.py`
- Create: `tests/unit/test_runtime.py`
- Modify: `src/safefix/task_service.py`
- Create: `tests/unit/test_task_service.py`
- Modify: `src/safefix/agent_loop.py`
- Modify: `tests/integration/test_agent_loop.py`
- Modify: `src/safefix/context.py`
- Modify: `tests/unit/test_context.py`

**Interfaces:**
- Consumes:
  - `PreparedWorkspace.path`
  - `SafeFixSettings`
  - `CredentialService`
  - optional Mock action strings from Task 4
- Produces:
  - `RuntimeConfigurationError(RuntimeError)`
  - `RuntimeSession(service: TaskService, audit: AuditStore, database_path: Path, model_name: str, provider: str)`
  - `RuntimeSession.list_events(run_id: str, *, after_sequence: int = 0) -> list[AuditEvent]`
  - `async RuntimeSession.aclose() -> None`
  - `create_runtime(settings: SafeFixSettings, prepared: PreparedWorkspace, data_dir: Path, *, provider: str, credential_service: CredentialService, mock_actions: Sequence[str] | None = None, http_transport: httpx.AsyncBaseTransport | None = None) -> RuntimeSession`
  - `TaskService.create(*, task: str, project_path: str, provider: str, project_id: str | None = None, mode: TaskMode = TaskMode.LOCAL) -> RunSnapshot`

- [ ] **Step 1: Strengthen the real-model system contract with RED tests**

Assert the system message tells the model to inspect before editing, run configured validators, consume feedback, and finish only after validation:

```python
def test_context_system_message_requires_inspection_feedback_and_validation() -> None:
    messages = ContextBuilder(None, MemorySettings()).build(_snapshot())
    system = messages[0].content

    assert "Inspect relevant files before editing" in system
    assert "Use run_validation" in system
    assert "Use the latest tool result and feedback" in system
    assert "finish only after validation succeeds" in system
```

- [ ] **Step 2: Run the context test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_context.py -q
```

Expected: FAIL because the current system message only lists allowed actions.

- [ ] **Step 3: Update the system message**

Keep the one-action JSON contract and add explicit operational rules. Do not add provider-specific syntax or free-form shell instructions.

- [ ] **Step 4: Write failing runtime-composition tests**

Use a temporary project, valid settings, a `CredentialService` fake, and scripted actions:

```python
@pytest.mark.asyncio
async def test_runtime_uses_real_agent_loop_and_persists_audit(tmp_path: Path) -> None:
    prepared = prepare_workspace(
        _fixture_project(tmp_path),
        tmp_path / "data",
        in_place=False,
        sensitive_patterns=(".env",),
    )
    actions = (
        '{"type":"list_files","id":"1","reason":"inspect","path":"."}',
        '{"type":"finish","id":"2","reason":"done","summary":"inspection complete"}',
    )
    runtime = create_runtime(
        _settings(),
        prepared,
        tmp_path / "data",
        provider="mock",
        credential_service=FakeCredentials(),
        mock_actions=actions,
    )

    snapshot = await runtime.service.create(
        task="inspect the project",
        project_path=str(prepared.path),
        provider="mock",
    )

    assert snapshot.status is RunStatus.SUCCESS
    assert runtime.database_path.is_file()
    assert [event.event_type for event in runtime.list_events(snapshot.run_id)]
    await runtime.aclose()
```

Also assert:

- Mock without `mock_actions` raises `RuntimeConfigurationError`;
- unsupported provider fails before opening a model call;
- openai-compatible without a credential fails with the existing safe credential error;
- openai-compatible with `httpx.MockTransport` and a fake key completes a two-action inspect/finish run through `OpenAICompatibleClient`;
- registered tools cover list/read/search/patch/process/validation;
- `aclose` can be called once and subsequent store use fails safely rather than leaking a connection;
- secret values are passed to audit/memory/approval redactors but never appear in events.

- [ ] **Step 5: Write stable project-memory RED tests**

Create `tests/unit/test_task_service.py` with a recording loop and memory store:

```python
@pytest.mark.asyncio
async def test_task_service_separates_project_identity_and_workspace() -> None:
    loop = SuccessfulLoop(changed_files=("calculator.py",))
    memory = RecordingMemory()
    service = TaskService(
        lambda project_path, provider: loop,
        RecordingRuns(loop.snapshot),
        memory_store=memory,
    )

    snapshot = await service.create(
        task="fix addition",
        project_path="C:/SafeFix/runs/execution/workspace",
        project_id="C:/source/project",
        provider="mock",
    )

    assert loop.task.workspace_root == "C:/SafeFix/runs/execution/workspace"
    assert loop.task.project_id == "C:/source/project"
    assert snapshot.status is RunStatus.SUCCESS
    assert memory.added == [
        (
            "C:/source/project",
            "repair_summary",
            "Task: fix addition\nResult: SUCCESS\nChanged files: calculator.py",
            ("calculator.py",),
        )
    ]
```

Also verify awaiting-approval does not write memory, successful `approve` writes once, failure does not write, and raw stdout/stderr/model text never enters the summary.

- [ ] **Step 6: Run runtime and task-service tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_runtime.py tests/unit/test_task_service.py -q
```

Expected: runtime collection FAIL and task-service assertions FAIL because the new interfaces do not exist.

- [ ] **Step 7: Implement stable project identity and terminal memory**

In `TaskService.create`, set `Task.project_id` to `project_id or project_path` while preserving `workspace_root=project_path`. Add a private `_remember_terminal(snapshot)` used after `start`, `approve`, and `reject`. It writes only on `RunStatus.SUCCESS`:

```python
content = (
    f"Task: {snapshot.description}\n"
    f"Result: {snapshot.status.value}\n"
    f"Changed files: {', '.join(snapshot.changed_files) or 'none'}"
)
self._memory.add(
    snapshot.project_id,
    "repair_summary",
    content,
    snapshot.changed_files,
)
```

Track remembered `run_id` values in process memory to prevent duplicate writes. Convert storage failure to `TaskServiceError("memory storage is unavailable")` without exposing the original exception.

- [ ] **Step 8: Implement `RuntimeSession` and `create_runtime`**

Create one SQLite file at `data_dir / "safefix.sqlite3"` and one `httpx.AsyncClient`. Build:

```python
boundary = WorkspaceBoundary(prepared.path, settings.policy.sensitive_patterns)
process = ProcessTool(boundary)
validators = ValidatorRunner(process, settings.validators)
tools = ToolRegistry((
    ListFilesTool(boundary, ignored_directories=(".git", ".venv", ".safefix")),
    ReadFileTool(boundary),
    SearchTextTool(boundary, ignored_directories=(".git", ".venv", ".safefix")),
    ApplyPatchTool(boundary),
    process,
    validators,
))
```

Construct `RunStore`, `AuditStore`, `MemoryStore`, `ApprovalStateMachine`, `PolicyEngine`, `FeedbackEngine`, `ContextBuilder`, `ActionParser`, and `AgentLoop` using the same connection. Build `TaskService` with a loop factory that rejects a workspace path different from `prepared.path` and returns the constructed loop once.

For `openai-compatible`, validate the credential once for startup diagnostics and then create `OpenAICompatibleClient`; for `mock`, create `ScriptedMockLLM(tuple(mock_actions))`. Ensure constructor failures close every resource already opened.

- [ ] **Step 9: Persist real tool and feedback audit evidence**

Add integration assertions using a recording audit:

```python
event_types = [event_type for event_type, payload in fixture.audit.events]
assert event_types == [
    "ACTION",
    "POLICY_DECISION",
    "TOOL_RESULT",
    "FEEDBACK",
    "ACTION",
    "POLICY_DECISION",
    "TOOL_RESULT",
    "FEEDBACK",
]
```

In `AgentLoop`, append the direct tool result once. Append validator results and the derived feedback in `_record_feedback`. Never append raw model messages. If a result or feedback audit append fails, transition to `RunStatus.FAILED` with stop reason `audit unavailable`; do not execute another action.

- [ ] **Step 10: Run Task 2 tests and full integration-loop regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_context.py tests/unit/test_runtime.py tests/unit/test_task_service.py tests/integration/test_agent_loop.py -q
.\.venv\Scripts\python.exe -m ruff check src/safefix/runtime.py src/safefix/context.py src/safefix/task_service.py src/safefix/agent_loop.py tests/unit/test_runtime.py tests/unit/test_context.py tests/unit/test_task_service.py tests/integration/test_agent_loop.py
.\.venv\Scripts\python.exe -m mypy src/safefix/runtime.py src/safefix/context.py src/safefix/task_service.py src/safefix/agent_loop.py
```

Expected: PASS.

- [ ] **Step 11: Commit Task 2**

```powershell
git add src/safefix/runtime.py src/safefix/context.py src/safefix/task_service.py src/safefix/agent_loop.py tests/unit/test_runtime.py tests/unit/test_context.py tests/unit/test_task_service.py tests/integration/test_agent_loop.py
git commit -m "feat(runtime): 组装真实代理运行时"
```

---

### Task 3: CLI Run Driver, Approval Interaction, and Result Rendering

**Files:**
- Create: `src/safefix/cli_runner.py`
- Modify: `src/safefix/cli.py`
- Modify: `tests/unit/test_cli.py`
- Create: `tests/unit/test_cli_runner.py`

**Interfaces:**
- Consumes:
  - `prepare_workspace`
  - `create_runtime`
  - `TaskService.create/get_approval/approve/reject`
  - `RuntimeSession.list_events`
- Produces:
  - `CliRunOptions(project: Path, task: str, config: Path, data_dir: Path | None, provider: str, in_place: bool, mock_script: Path | None, non_interactive: bool, json_output: bool)`
  - `RunSummary(run_id: str | None, status: str, exit_code: int, workspace: str | None, mode: str, changed_files: tuple[str, ...], stop_reason: str | None, audit_database: str | None)`
  - `RunSummary.from_snapshot(snapshot: RunSnapshot, prepared: PreparedWorkspace, database_path: Path) -> RunSummary`
  - `_credential_service() -> CredentialService`
  - `_render_events(events: Sequence[AuditEvent], *, after_sequence: int, stdout: TextIO) -> int`
  - `_approval_prompt(request: ApprovalRequest) -> str`
  - `_render_summary(summary: RunSummary, *, json_output: bool, stdout: TextIO) -> int`
  - `run_cli(options: CliRunOptions, *, credential_service: Any | None = None, input_fn: Callable[[str], str] = input, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr, runtime_factory: Callable[..., RuntimeSession] = create_runtime, workspace_factory: Callable[..., PreparedWorkspace] = prepare_workspace) -> int`
- Exit codes: `0`, `2`, `3`, `4`, `5`, `6`, `7` exactly as specified in the design.

- [ ] **Step 1: Write parser and configuration-init RED tests**

Extend `test_cli.py`:

```python
def test_run_parser_exposes_safe_defaults() -> None:
    args = build_parser().parse_args(
        ["run", "C:/project", "--task", "fix tests"]
    )
    assert args.config == Path("safefix.yaml")
    assert args.data_dir is None
    assert args.provider == "openai-compatible"
    assert args.in_place is False
    assert args.non_interactive is False
    assert args.json is False


def test_config_init_writes_valid_template_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "safefix.yaml"
    assert main(["config", "init", str(path)]) == 0
    assert load_settings(path).validators[0].id == "pytest"
    original = path.read_text(encoding="utf-8")
    assert main(["config", "init", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == original
```

- [ ] **Step 2: Run parser tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py -q
```

Expected: FAIL because the options and valid template behavior are missing.

- [ ] **Step 3: Extend the parser and safe config commands**

Use `type=Path` for path arguments. Preserve injectable `task_service` behavior for narrow adapter unit tests, but the default path must call `run_cli`.

- [ ] **Step 4: Write CLI runner RED tests**

Use fake prepared workspaces, runtime sessions, services, snapshots, events, and approval access:

```python
def test_cli_approves_only_the_presented_action_once(capsys: pytest.CaptureFixture[str]) -> None:
    runtime = approval_runtime_then_success()

    result = run_cli(
        _options(),
        credential_service=FakeCredentials(),
        input_fn=lambda prompt: "y",
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=fake_workspace,
    )

    assert result == 0
    assert runtime.service.approved == [("run-1", "one-time-capability")]
    output = capsys.readouterr().out
    assert "需要一次性审批" in output
    assert "git commit" in output
    assert "one-time-capability" not in output


def test_non_interactive_run_rejects_approval() -> None:
    runtime = approval_runtime_then_blocked()
    result = run_cli(
        _options(non_interactive=True),
        runtime_factory=lambda *args, **kwargs: runtime,
        workspace_factory=fake_workspace,
    )
    assert result == 6
    assert runtime.service.rejected == [("run-1", "one-time-capability")]
```

Cover:

- missing project/config/key errors and their exit codes;
- isolated and in-place banner text;
- no traceback on expected failures;
- event payload redaction and bounded output;
- only new audit sequences rendered after resume;
- terminal status-to-exit-code mapping;
- JSON summary parses and contains no human banner;
- `record_run_id` is called after the first snapshot;
- runtime closes on success, rejection, configuration error after creation, and unexpected interrupt.

- [ ] **Step 5: Run CLI runner tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli_runner.py -q
```

Expected: collection FAIL because `safefix.cli_runner` does not exist.

- [ ] **Step 6: Implement the CLI driver**

`run_cli` should use one `asyncio.run` call and pass all injectable boundaries explicitly:

```python
return asyncio.run(
    _run_cli_async(
        options,
        credential_service=credential_service,
        input_fn=input_fn,
        stdout=stdout,
        stderr=stderr,
        runtime_factory=runtime_factory,
        workspace_factory=workspace_factory,
    )
)
```

The async flow is:

```python
settings = load_settings(options.config)
data_dir = options.data_dir or default_data_dir()
prepared = workspace_factory(
    options.project,
    data_dir,
    in_place=options.in_place,
    sensitive_patterns=settings.policy.sensitive_patterns,
)
mock_actions = (
    load_mock_actions(options.mock_script, prepared.path)
    if options.mock_script is not None
    else None
)
runtime = runtime_factory(
    settings,
    prepared,
    data_dir,
    provider=options.provider,
    credential_service=credential_service or _credential_service(),
    mock_actions=mock_actions,
)
snapshot = await runtime.service.create(
    task=options.task,
    project_path=str(prepared.path),
    project_id=str(prepared.source),
    provider=options.provider,
)
record_run_id(prepared, snapshot.run_id)
events = runtime.list_events(snapshot.run_id, after_sequence=last_sequence)
last_sequence = _render_events(
    events,
    after_sequence=last_sequence,
    stdout=stdout,
)
while snapshot.status is RunStatus.AWAITING_APPROVAL:
    access = runtime.service.get_approval(snapshot.run_id)
    prompt = _approval_prompt(access.request)
    approved = False if options.non_interactive else input_fn(prompt).strip().lower() == "y"
    snapshot = (
        await runtime.service.approve(snapshot.run_id, access.capability)
        if approved
        else await runtime.service.reject(snapshot.run_id, access.capability)
    )
    events = runtime.list_events(snapshot.run_id, after_sequence=last_sequence)
    last_sequence = _render_events(
        events,
        after_sequence=last_sequence,
        stdout=stdout,
    )
summary = RunSummary.from_snapshot(snapshot, prepared, runtime.database_path)
return _render_summary(summary, json_output=options.json_output, stdout=stdout)
```

Catch only known configuration, credential, provider, workspace, storage, and user-interrupt errors at the adapter boundary. Map them to safe messages and defined exit codes. Use `finally: await runtime.aclose()` when a runtime was created.

- [ ] **Step 7: Run Task 3 tests and CLI regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py tests/unit/test_cli_runner.py tests/web/test_api.py tests/web/test_pages.py -q
.\.venv\Scripts\python.exe -m ruff check src/safefix/cli.py src/safefix/cli_runner.py tests/unit/test_cli.py tests/unit/test_cli_runner.py
.\.venv\Scripts\python.exe -m mypy src/safefix/cli.py src/safefix/cli_runner.py
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```powershell
git add src/safefix/cli.py src/safefix/cli_runner.py tests/unit/test_cli.py tests/unit/test_cli_runner.py
git commit -m "feat(cli): 运行真实代理并处理审批"
```

---

### Task 4: Deterministic Full-Loop CLI Repair

**Files:**
- Create: `examples/mock_repair.jsonl`
- Create: `tests/integration/test_cli_run.py`
- Modify: `src/safefix/runtime.py`
- Modify: `pyproject.toml`
- Modify: `tests/integration/test_distribution_metadata.py`

**Interfaces:**
- Consumes: `safefix run --provider mock --mock-script PATH`.
- Produces:
  - `load_mock_actions(script_path: Path, workspace: Path) -> tuple[str, ...]`
  - a deterministic script that exercises list, read, initial validation failure, patch, validation success, and finish through the real AgentLoop.

- [ ] **Step 1: Write the full-loop CLI RED test**

Copy `examples/python_bug` to a test source project, create a valid config using `default_settings_yaml`, and invoke the installed adapter function:

```python
def test_cli_mock_run_repairs_isolated_copy_and_preserves_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    shutil.copytree(Path("examples/python_bug"), source)
    original = (source / "calculator.py").read_text(encoding="utf-8")
    config = tmp_path / "safefix.yaml"
    config.write_text(default_settings_yaml(), encoding="utf-8")
    data = tmp_path / "data"

    result = main([
        "run", str(source),
        "--task", "修复失败的加法测试",
        "--config", str(config),
        "--provider", "mock",
        "--mock-script", "examples/mock_repair.jsonl",
        "--data-dir", str(data),
    ])

    assert result == 0
    assert (source / "calculator.py").read_text(encoding="utf-8") == original
    copied = next(data.glob("runs/*/workspace/calculator.py"))
    assert "return left + right" in copied.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "验证失败" in output
    assert "验证通过" in output
    assert "SUCCESS" in output
```

Add a second test with `--in-place` against a disposable fixture and assert the source changes. Add a JSON-output test and assert `json.loads(stdout)["status"] == "SUCCESS"`.

- [ ] **Step 2: Run the integration test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_cli_run.py -q
```

Expected: FAIL because the Mock script and complete CLI path do not exist.

- [ ] **Step 3: Add the deterministic action script**

Write one JSON action object per line. Use valid SHA handling by allowing the integration test to materialize `{CALCULATOR_SHA256}` before passing a temporary rendered script, while package the template:

```json
{"type":"list_files","id":"list-1","reason":"inspect project","path":"."}
{"type":"read_file","id":"read-1","reason":"inspect failing code","path":"calculator.py"}
{"type":"run_validation","id":"validate-1","reason":"capture objective failure","validator_id":"pytest"}
{"type":"apply_patch","id":"patch-1","reason":"repair from validation feedback","path":"calculator.py","expected_sha256":"{CALCULATOR_SHA256}","old_text":"return left - right","new_text":"return left + right","expected_replacements":1}
{"type":"run_validation","id":"validate-2","reason":"verify repair","validator_id":"pytest"}
{"type":"finish","id":"finish-1","reason":"validated repair complete","summary":"pytest passes after repairing calculator.add"}
```

The CLI Mock script loader must:

- reject blank files;
- require every non-empty line to parse as a JSON object;
- replace only the documented `{CALCULATOR_SHA256}` fixture token when the targeted file exists;
- reject any other `{...}` placeholder;
- bound script size to 1 MiB and 1000 actions.

- [ ] **Step 4: Include the script in distributions**

Add a Hatch force-include entry:

```toml
"examples/mock_repair.jsonl" = "safefix/_fixtures/mock_repair.jsonl"
```

Extend distribution metadata tests to assert the source template and packaged-resource declaration are present.

- [ ] **Step 5: Run Task 4 tests and all demos**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_cli_run.py tests/integration/test_distribution_metadata.py -q
.\.venv\Scripts\safefix-demo.exe all
.\.venv\Scripts\python.exe -m ruff check examples tests/integration src/safefix
.\.venv\Scripts\python.exe -m mypy src
```

Expected: integration tests PASS; guardrail, feedback, and approval each print PASS; Ruff and mypy are clean.

- [ ] **Step 6: Commit Task 4**

```powershell
git add examples/mock_repair.jsonl pyproject.toml tests/integration/test_cli_run.py tests/integration/test_distribution_metadata.py
git commit -m "test(cli): 验证完整隔离修复流程"
```

---

### Task 5: Fresh-Install Distribution, Documentation, and Final Evidence

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `PLAN.md`
- Modify: `AGENT_LOG.md`
- Modify: `.github/workflows/ci.yml`
- Test: complete repository and built wheel in a fresh temporary virtual environment

**Interfaces:**
- Consumes: complete CLI runtime and packaged Mock fixture.
- Produces: reproducible install/use instructions and CI evidence that entry points work outside the development editable install.

- [ ] **Step 1: Add fresh-wheel smoke checks to CI**

After the normal build step, create a clean venv and install the built wheel. On Linux CI:

```yaml
- name: Smoke-test built CLI
  run: |
    python -m build --wheel
    python -m venv .smoke-venv
    .smoke-venv/bin/python -m pip install dist/*.whl
    .smoke-venv/bin/safefix --help
    .smoke-venv/bin/safefix-demo all
```

Add `build>=1.2,<2` to the dev optional dependencies so CI does not depend on an undeclared tool. Keep Docker, Gitleaks, Ruff, mypy, and pytest jobs intact.

- [ ] **Step 2: Build and smoke-test locally in a fresh Windows venv**

Use project Python explicitly:

```powershell
.\.venv\Scripts\python.exe -m build --wheel
.\.venv\Scripts\python.exe -m venv .smoke-venv
.\.smoke-venv\Scripts\python.exe -m pip install (Get-ChildItem dist\safefix_harness-*.whl | Select-Object -Last 1).FullName
.\.smoke-venv\Scripts\safefix.exe --help
.\.smoke-venv\Scripts\safefix-demo.exe all
```

Expected: all three launchers exist; help exits 0; all demos PASS. Remove `.smoke-venv` and `dist` only after resolving and verifying both paths are inside the repository.

- [ ] **Step 3: Write the real CLI README tutorial**

Document:

- install from wheel/Release and Python 3.12 requirement;
- `config init`, endpoint/model editing, and `config validate`;
- hidden key input with `credentials set/status/clear`;
- default isolated run command;
- result workspace and audit locations;
- explicit `--in-place` warning;
- approval interaction;
- deterministic Mock command using the packaged example;
- JSON output;
- provider compatibility and limitations;
- public WebUI and Docker commands;
- statement that Mock is an acceptance harness, not a general intelligent model.

- [ ] **Step 4: Record plan and agent-log evidence**

Add a “Usable CLI runtime” section to `PLAN.md` with task commits and actual RED/GREEN/full-suite evidence. Add `AGENT_LOG.md` entries with timestamp, task, skills, context, implementation/review agents, manual changes, tests, and lessons. Do not claim GitHub Release, public deployment, or CI success until each has an actual URL/result.

- [ ] **Step 5: Run complete final verification**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m safefix.demo all
git diff --check
```

Then build the Docker image and run the packaged demo:

```powershell
docker build -t safefix:cli-runtime .
docker run --rm safefix:cli-runtime python -m safefix.demo all
```

Expected: full pytest PASS with only documented third-party warnings; Ruff, mypy, demos, diff check, image build, and container demos PASS.

- [ ] **Step 6: Manually exercise the exact user journey**

In a disposable project:

```powershell
.\.venv\Scripts\safefix.exe config init .manual-safefix.yaml
.\.venv\Scripts\safefix.exe config validate .manual-safefix.yaml
.\.venv\Scripts\safefix.exe run examples\python_bug --task "修复失败的加法测试" --config .manual-safefix.yaml --provider mock --mock-script examples\mock_repair.jsonl
```

Verify:

- original fixture unchanged;
- output shows isolation path, failed validation, patch, passing validation, changed file, SUCCESS, and audit database;
- result copy contains `return left + right`;
- no key, capability, traceback, or absolute sensitive path appears.

- [ ] **Step 7: Commit Task 5**

```powershell
git add pyproject.toml .github/workflows/ci.yml README.md PLAN.md AGENT_LOG.md
git commit -m "docs(cli): 完成真实运行与分发说明"
```

---

## Final Review Gate

- [ ] Verify every design acceptance criterion maps to Tasks 1–5.
- [ ] Request a whole-branch code review from the design commit to the final implementation commit.
- [ ] Fix all Critical and Important findings and rerun scoped tests.
- [ ] Run one final full verification on the exact reviewed HEAD.
- [ ] Use `superpowers:finishing-a-development-branch` to offer merge, PR, or keep.
- [ ] After integration and successful GitHub Actions, create the GitHub Release and deploy the public WebUI as separate external-state steps requiring the repository/provider account permissions.
