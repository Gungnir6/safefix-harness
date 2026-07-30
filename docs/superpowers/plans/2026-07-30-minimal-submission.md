# SafeFix Minimal Submission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove nonessential conversational and container/deployment features while preserving the deterministic Mock Harness, Mock WebUI, wheel distribution, CI, and required course evidence.

**Architecture:** Roll the CLI back to explicit one-shot commands while leaving the Harness kernel untouched. Select wheel as the only promised distribution artifact, keep the public Mock WebUI as an assignment demonstration surface, and reconcile all root documents with that smaller truth. The optional OpenAI-compatible adapter and credential boundary remain isolated extensions, not an acceptance path.

**Tech Stack:** Python 3.12, argparse, FastAPI, Pydantic 2, SQLite, PyYAML, HTTPX, keyring, pytest, Hypothesis, Jinja2, vanilla JavaScript, Hatchling wheel distribution.

## Global Constraints

- Preserve AgentLoop, action parsing, tools, governance, approval, audit, feedback, memory, configuration, Mock LLM, Mock WebUI, and all deterministic demos.
- Keep `safefix-demo all`, `safefix serve --public-demo`, and Mock-driven `safefix run` operational.
- Wheel is the only promised distribution artifact; Docker and Render are removed.
- OpenAI-compatible and credential modules remain optional and are not used for assignment acceptance.
- Do not generate `REFLECTION.md`; the student must author it.
- Do not delete course requirement files, unrelated untracked files, other worktrees, or non-task branches.
- Use the repository-root `.venv` Python, never the system `python.exe`.

---

### Task 1: Remove the conversational CLI surface

**Files:**
- Delete: `src/safefix/cli_chat.py`
- Delete: `src/safefix/cli_setup.py`
- Delete: `tests/unit/test_cli_chat.py`
- Delete: `tests/unit/test_cli_setup.py`
- Modify: `src/safefix/cli.py`
- Modify: `src/safefix/cli_runner.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/unit/test_cli_runner.py`

**Interfaces:**
- Preserve: `build_parser() -> argparse.ArgumentParser`
- Preserve: `main(argv: Sequence[str] | None = None, ...) -> int`
- Preserve: `run_cli(options: CliRunOptions, ...) -> int`
- Remove: `ChatOptions`, `run_chat`, `SetupOptions`, `ensure_setup`, `run_setup`, and `summary_observer`

- [x] **Step 1: Write the failing CLI contract test**

Replace the conversational parser tests with a test that captures real parser behavior:

```python
def test_cli_requires_an_explicit_command() -> None:
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args([])
    assert caught.value.code == 2


@pytest.mark.parametrize("removed", ["chat", "setup"])
def test_removed_commands_are_rejected(removed: str) -> None:
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args([removed])
    assert caught.value.code == 2
```

This catches accidental restoration of the removed public commands.

- [x] **Step 2: Verify RED**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py -q
```

Expected: the new tests fail because no arguments still select `chat` and both removed commands still parse.

- [x] **Step 3: Implement the minimal rollback**

In `cli.py`, make subparsers required again and remove all `chat`/`setup` parsers and dispatch branches:

```python
commands = parser.add_subparsers(dest="command", required=True)
```

Delete both production modules and their focused tests. In `cli_runner.py`, remove `summary_observer` from `_run_cli_async` and `run_cli`, remove observer calls, and restore direct rendering of `_error_summary(options, exit_code)`. Delete only the two observer tests from `test_cli_runner.py`.

- [x] **Step 4: Verify GREEN**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py tests/unit/test_cli_runner.py tests/integration/test_cli_run.py -q
```

Expected: all retained CLI and Mock run tests pass.

### Task 2: Select wheel-only distribution

**Files:**
- Delete: `Dockerfile`
- Delete: `render.yaml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitlab-ci.yml`
- Modify: `tests/integration/test_distribution_metadata.py`
- Verify: `pyproject.toml`

**Interfaces:**
- Preserve console scripts: `safefix`, `safefix-demo`, `safefix-public-demo`
- Preserve wheel-packaged fixtures and Web assets
- Remove OCI image build/push and Render Blueprint contracts

- [x] **Step 1: Establish the retained distribution contract**

Update `test_distribution_metadata.py` so the required artifact set contains only files used by wheel/CI documentation:

```python
required = {
    "pyproject.toml",
    "README.md",
    ".gitlab-ci.yml",
    ".github/workflows/ci.yml",
}
assert all((ROOT / path).is_file() for path in required)
```

Delete Dockerfile-specific assertions and Render Blueprint assertions. Preserve tests that build/load wheel resources, validate console scripts, inspect runtime dependencies, and verify CI test commands.

- [x] **Step 2: Remove optional deployment artifacts**

Delete `Dockerfile` and `render.yaml`. In GitHub Actions:

- change permissions to `contents: read` only;
- keep `test-quality`, Gitleaks, wheel build, fresh venv install, CLI smoke, and demos;
- remove the complete `image` job and all GHCR login/push configuration.

In `.gitlab-ci.yml`, preserve `unit-test`, `lint-type`, and `secret-scan`, remove
the Docker `image-build` job, and reduce `stages` to `[test, quality]`. The named
`unit-test` job remains a formal checklist item.

- [x] **Step 3: Verify distribution tests**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
..\..\.venv\Scripts\python.exe -m pytest tests/integration/test_distribution_metadata.py -q
```

Expected: wheel and CI metadata tests pass without Docker or Render.

### Task 3: Reconcile submission documentation

**Files:**
- Modify: `README.md`
- Modify: `SPEC.md`
- Modify: `SPEC_PROCESS.md`
- Modify: `PLAN.md`
- Modify: `AGENT_LOG.md`

**Interfaces:**
- README primary acceptance commands are exactly the three retained entry paths.
- SPEC distribution choice is wheel; Mock WebUI remains; Docker/Render are historical removals.
- REFLECTION remains a student-owned missing gate.

- [x] **Step 1: Rewrite README around deterministic acceptance**

Order the usage sections as:

1. Installation from source or wheel.
2. `safefix-demo all` with the three expected PASS lines.
3. `safefix serve --public-demo` and localhost URL.
4. deterministic Mock repair command.
5. architecture and security boundaries.
6. optional OpenAI-compatible provider and credentials, explicitly unsupported for grading/demo.
7. tests, wheel build, directory structure, known limitations, and student-owned REFLECTION gate.

Remove all `safefix chat`, `safefix setup`, Docker, Render, GHCR, and unverified public URL instructions.

- [x] **Step 2: Make SPEC and SPEC_PROCESS truthful**

In `SPEC.md`, change distribution/deployment commitments from Docker/Render to Hatchling wheel and local Mock WebUI. Preserve historical design intent only where labeled superseded; acceptance criteria must not require Docker. In `SPEC_PROCESS.md`, retain the fact that Docker was originally selected, then append the dated decision that final scope selected wheel-only distribution after reviewing the assignment's mandatory Mock criteria.

- [x] **Step 3: Update PLAN and AGENT_LOG evidence**

Add a dated minimal-submission task recording:

- user-authorized removals;
- retained acceptance commands;
- focused RED/GREEN evidence;
- final verification numbers after Task 4;
- REFLECTION.md remains student-authored and missing until the student adds it.

Do not rewrite earlier historical entries; label the conversational feature and Docker/Render scope as subsequently removed.

- [x] **Step 4: Scan for active contradictions**

Run:

```powershell
rg -n "safefix chat|safefix setup|render\.yaml|Dockerfile|docker build|GHCR|Render deploy" README.md SPEC.md SPEC_PROCESS.md PLAN.md AGENT_LOG.md .github/workflows/ci.yml tests/integration/test_distribution_metadata.py
```

Expected: matches only in explicitly historical entries marked removed/superseded, not active instructions or acceptance criteria.

### Task 4: Clean temporary data and run final gates

**Files:**
- Remove locally only: `.try-safefix-20260729.yaml`
- Remove locally only: `.try-safefix-data-20260729/`
- Do not modify: course requirement files and all other untracked files

**Interfaces:**
- Produces a clean feature diff containing only the approved tracked-file changes.
- Produces fresh test, quality, demo, WebUI, and wheel evidence.

- [x] **Step 1: Safely remove the two approved temporary targets**

Resolve each target to an absolute path and verify its parent/ancestor is the main repository root before deletion. Delete only the two exact names; do not use a wildcard and do not touch `.worktrees/`.

- [x] **Step 2: Run focused acceptance checks**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
..\..\.venv\Scripts\python.exe -m pytest tests/integration/test_demo.py tests/web/test_api.py tests/web/test_pages.py tests/integration/test_cli_run.py -q
..\..\.venv\Scripts\python.exe -m safefix.demo all
```

- [x] **Step 3: Run full project gates**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
..\..\.venv\Scripts\python.exe -m pytest -q
..\..\.venv\Scripts\python.exe -m ruff check src tests
..\..\.venv\Scripts\python.exe -m mypy src
git diff --check
```

- [x] **Step 4: Build and smoke-test a fresh wheel**

Build with the repository `.venv`, create `.wheel-smoke-minimal`, remove inherited `PYTHONPATH`, install the produced wheel, then run:

```powershell
.\.wheel-smoke-minimal\Scripts\safefix.exe --help
.\.wheel-smoke-minimal\Scripts\safefix-demo.exe all
.\.wheel-smoke-minimal\Scripts\python.exe -c "from importlib.metadata import entry_points; assert any(ep.name == 'safefix-public-demo' for ep in entry_points(group='console_scripts'))"
```

Verify `safefix.__file__` resolves under `.wheel-smoke-minimal\Lib\site-packages`, not the worktree `src`. Remove only the verified smoke directory and `dist/` after recording evidence.

- [ ] **Step 5: Commit the minimal submission**

Stage only the approved tracked changes and commit:

```powershell
git commit -m "refactor: trim to minimal assignment scope"
```
