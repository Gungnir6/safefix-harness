# SafeFix Conversational CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-run setup wizard and a lightweight conversational CLI that reuses the existing production runner.

**Architecture:** `cli.py` remains the command router. A focused `cli_setup.py` owns valid configuration and credential onboarding; `cli_chat.py` owns the REPL and slash commands. Every natural-language turn constructs `CliRunOptions` and calls the existing `run_cli`, which gains an optional summary observer instead of duplicating `TaskService` or `AgentLoop`.

**Tech Stack:** Python 3.12, argparse, pathlib, subprocess, PyYAML/Pydantic configuration, keyring credentials, pytest, Ruff, mypy.

## Global Constraints

- No-argument `safefix` starts chat in the current directory.
- `safefix chat [project]` is the explicit equivalent.
- Existing `safefix run`, WebUI, isolation, governance, approval, validation, and audit behavior remain compatible.
- The wizard never prints API keys and never silently enables `--in-place`.
- No full-screen TUI, streaming tokens, shell shortcut, file completion, or cross-process chat recovery.
- Tests inject terminal input, output, credentials, runner, and Git execution; they do not require network or a system keyring.

---

### Task 1: Expose completed run summaries

**Files:**
- Modify: `src/safefix/cli_runner.py`
- Test: `tests/unit/test_cli_runner.py`

**Interfaces:**
- Produces: `run_cli(..., summary_observer: Callable[[RunSummary], None] | None = None) -> int`
- The observer receives exactly the final rendered `RunSummary`, including safe error summaries.

- [ ] **Step 1: Write failing observer tests**

Add tests that pass `captured.append` as `summary_observer`, run one successful fake runtime and one known configuration error, and assert the observer receives one summary with the literal status, exit code, and workspace expected from that path.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli_runner.py -q
```

Expected: failure because `run_cli` does not accept `summary_observer`.

- [ ] **Step 3: Implement the narrow observer**

Thread the optional callback through `run_cli` and `_run_cli_async`. Invoke it once immediately before `_render_summary(summary, ...)`; do not expose the callback to the runtime or agent loop.

- [ ] **Step 4: Verify GREEN**

Run the same focused test command and expect all tests in the file to pass.

### Task 2: Add first-run setup

**Files:**
- Create: `src/safefix/cli_setup.py`
- Create: `tests/unit/test_cli_setup.py`
- Modify: `src/safefix/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `SetupOptions(project: Path, config: Path | None, provider: str)`
- Produces: `ensure_setup(options, *, credential_service, input_fn, secret_input_fn, stdout) -> Path`
- Produces: `run_setup(options, ...) -> int`

- [ ] **Step 1: Write failing setup tests**

Cover these observable behaviors:

```python
def test_setup_creates_loadable_config_with_selected_endpoint_and_model(...):
    ...
    assert load_settings(path).llm.model == "test-model"

def test_setup_preserves_existing_config_and_skips_config_questions(...):
    ...
    assert path.read_text(encoding="utf-8") == original

def test_setup_stores_missing_credential_without_printing_it(...):
    ...
    assert secret not in output

def test_setup_returns_actionable_error_without_traceback(...):
    ...
    assert result == 2
```

Add parser coverage for `safefix setup [project] --config PATH --provider NAME`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli_setup.py tests/unit/test_cli.py -q
```

Expected: collection/import or parser failures because setup APIs do not exist.

- [ ] **Step 3: Implement minimal setup**

Use `default_settings_yaml()` as the base, replace only `llm.endpoint` and `llm.model` through parsed YAML, validate the written file with `load_settings`, and use `CredentialService.status/set`. Defaults are `https://api.openai.com/v1` and `gpt-4.1-mini`. If the provider is `mock`, skip credential collection.

- [ ] **Step 4: Verify GREEN**

Run the focused command again and expect all tests to pass.

### Task 3: Add the lightweight conversational REPL

**Files:**
- Create: `src/safefix/cli_chat.py`
- Create: `tests/unit/test_cli_chat.py`
- Modify: `src/safefix/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `ChatOptions(project: Path, config: Path | None, data_dir: Path | None, provider: str)`
- Produces: `run_chat(options, *, credential_service, input_fn, secret_input_fn, stdout, stderr, task_runner, diff_runner) -> int`
- Consumes: `ensure_setup(...) -> Path`
- Consumes: `run_cli(..., summary_observer=...) -> int`

- [ ] **Step 1: Write failing REPL tests**

Use iterator-backed input and real `StringIO` output. Cover:

```python
def test_chat_runs_two_natural_language_tasks_with_existing_runner(...):
    ...
    assert [call.task for call in calls] == ["修复测试", "检查类型错误"]

def test_chat_help_status_new_and_exit(...):
    ...
    assert "最近任务" in output

def test_chat_diff_uses_fixed_read_only_git_arguments(...):
    ...
    assert command == ("git", "-C", workspace, "diff", "--no-ext-diff", "--")

def test_chat_recovers_after_one_task_error(...):
    ...
    assert len(calls) == 2
```

Add parser tests proving `build_parser().parse_args([])` selects chat in `"."`, and explicit `chat` accepts project/config/provider/data-dir.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli_chat.py tests/unit/test_cli.py -q
```

Expected: failures because the module, default entry, and commands do not exist.

- [ ] **Step 3: Implement minimal REPL**

Print a short banner, call `ensure_setup`, then loop over `input_fn("SafeFix > ")`. Empty input is ignored. `/help`, `/status`, `/diff`, `/new`, `/exit`, and `/quit` are handled locally; unknown slash commands show help. Natural-language input calls `task_runner(CliRunOptions(...), summary_observer=remember)` with `in_place=False`, `mock_script=None`, `non_interactive=False`, and `json_output=False`.

The default diff runner executes only:

```python
("git", "-C", workspace, "diff", "--no-ext-diff", "--")
```

with captured text, a finite timeout, and no shell.

- [ ] **Step 4: Verify GREEN**

Run the focused test command and expect all tests to pass.

### Task 4: Document and verify the install-to-chat journey

**Files:**
- Modify: `README.md`
- Modify: `PLAN.md`
- Modify: `AGENT_LOG.md`
- Modify: `tests/unit/test_distribution.py` only if the existing wheel smoke needs a no-argument entry assertion.

**Interfaces:**
- Documents: `safefix`, `safefix setup`, `safefix chat`, slash commands, and retained `safefix run`.

- [ ] **Step 1: Update user-facing documentation**

Lead the README tutorial with:

```powershell
cd C:\path\to\project
safefix
```

Explain the endpoint/model/API-key questions, isolated result workspace, `/diff`, and why `safefix run` remains useful for CI. Record implementation and fresh verification evidence in PLAN and AGENT_LOG.

- [ ] **Step 2: Run focused feature tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py tests/unit/test_cli_setup.py tests/unit/test_cli_chat.py tests/unit/test_cli_runner.py -q
```

- [ ] **Step 3: Run project gates**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m safefix.demo all
git diff --check
```

Expected: zero test failures, Ruff success, mypy success, three demo PASS lines, and no whitespace errors.

- [ ] **Step 4: Commit implementation**

```powershell
git add src/safefix/cli.py src/safefix/cli_setup.py src/safefix/cli_chat.py src/safefix/cli_runner.py tests/unit/test_cli.py tests/unit/test_cli_setup.py tests/unit/test_cli_chat.py tests/unit/test_cli_runner.py README.md PLAN.md AGENT_LOG.md docs/superpowers/plans/2026-07-29-conversational-cli.md
git commit -m "feat(cli): add conversational workflow"
```
