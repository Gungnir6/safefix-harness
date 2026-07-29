# SafeFix Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-coded coding-agent harness that safely repairs small defects in a local repository, uses deterministic validation feedback, pauses for risky-action approval, and remains fully testable with a scripted mock LLM.

**Architecture:** A FastAPI/CLI task service drives a custom `AgentLoop`. Each model action passes through strict parsing and a deterministic `PolicyEngine` before a focused tool executes; validation, memory, approvals, and audit data are stored through explicit interfaces backed by SQLite. Local real-LLM mode and public mock-demo mode inject different LLM, workspace, and policy dependencies while sharing the same core.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLite, PyYAML, httpx, keyring, pytest, Hypothesis, Jinja2, vanilla JavaScript, Docker/OCI.

## Global Constraints

- Do not use LangChain AgentExecutor, AutoGen, CrewAI, LlamaIndex Agent, a coding-agent SDK runner, or any external agent loop.
- Every production behavior begins with a failing test; capture RED, implement the minimum GREEN change, then refactor.
- Core tests use `ScriptedMockLLM`, never the network or a real API Key.
- Formal development uses a project-local Python 3.12 virtual environment. After T01 Step 0, every `python` command means the activated `.venv` interpreter; dependency installation may use the package index, but tests themselves remain offline.
- Every action is parsed into a typed model and evaluated by `PolicyEngine` before tool dispatch.
- File tools resolve real paths and may operate only inside the configured workspace; sensitive paths remain denied.
- Process execution uses `program + args`, `shell=False`, explicit timeout, output limits, and a scrubbed environment.
- Repair attempts default to 3; two no-progress rounds, repeated actions, or exhausted budget stop deterministically.
- Secrets never appear in YAML, SQLite, logs, audit events, terminal arguments, test fixtures, Git history, or the public demo.
- Public mode uses only an embedded sample repository and `ScriptedMockLLM`; it accepts no project path, source upload, real Key, arbitrary program, or network tool.
- Primary native target is Windows 10/11 with Python 3.12; Docker provides the reproducible Linux runtime.
- Each task uses a fresh subagent, a dedicated `codex/tNN-*` branch/worktree, spec-compliance review, code-quality review, and one PR.
- Every commit message follows Conventional Commits as `type(scope): 中文摘要`; allowed types are `feat`, `fix`, `test`, `refactor`, `docs`, `build`, `ci`, and `chore`.
- After each implementation commit, update this plan with its hash and append only required evidence to `AGENT_LOG.md`.

---

## File Responsibility Map

```text
pyproject.toml                         Package metadata, dependencies, entry points, test config
src/safefix/domain.py                 Typed actions, results, feedback, policy and run states
src/safefix/config.py                 Strict YAML settings schema and loader
src/safefix/llm/base.py               LLM protocol and provider-neutral message/result types
src/safefix/llm/mock.py               Scripted deterministic mock LLM
src/safefix/llm/openai_compatible.py  Single-call HTTP provider implementation
src/safefix/action_parser.py          Strict JSON-to-Action parsing
src/safefix/governance/paths.py       Workspace and sensitive-path boundary checks
src/safefix/governance/policy.py      Three-level deterministic action classification
src/safefix/governance/audit.py       Redacted, hash-chained SQLite audit events
src/safefix/governance/approvals.py   Persistent frozen-action HITL state machine
src/safefix/tools/base.py             Tool protocol and output limiting helpers
src/safefix/tools/filesystem.py       List, read, search and exact patch tools
src/safefix/tools/process.py          shell=False process and configured validator runner
src/safefix/tools/registry.py         Action-to-tool dispatch
src/safefix/feedback.py               Validation classification, fingerprint and progress logic
src/safefix/memory.py                 Project-scoped SQLite memory and bounded retrieval
src/safefix/context.py                Prompt/context construction with bounded memory and feedback
src/safefix/run_store.py              Persistent run snapshots and state transitions
src/safefix/agent_loop.py             Custom decision/action/feedback/stop loop
src/safefix/credentials.py            Keyring and secret-file credential lifecycle
src/safefix/task_service.py           Application service shared by API and CLI
src/safefix/web/app.py                FastAPI application factory and dependencies
src/safefix/web/routes.py             JSON and HTML routes
src/safefix/web/templates/*.html      Local/public user interface
src/safefix/web/static/*              Minimal CSS and JavaScript
src/safefix/cli.py                    CLI commands
src/safefix/demo.py                   Three deterministic mechanism demonstrations
examples/python_bug/*                 Embedded Python repair fixture
tests/unit/*                           Deterministic component tests
tests/integration/*                    Full-loop temporary-repository tests
tests/web/*                            API and rendered-page tests
Dockerfile                            Non-root OCI distribution
.gitlab-ci.yml                        Required `unit-test` job and image build
.github/workflows/ci.yml              GitHub test, scan and image workflow
README.md                             Required usage, distribution, security and limitations
```

## Dependency and Parallelism Map

```text
Gate 0 cold start
  → T01 domain/package foundation
      ├─ T02 configuration
      ├─ T03 LLM abstraction/parser
      └─ T04 path boundary
           ├─ T05 policy engine
           └─ T08 filesystem tools
      T01 ──→ T06 audit store ──→ T07 approvals
      T02 + T05 ───────────────→ T09 process/validators
      T08 + T09 ───────────────→ T10 feedback
      T01 + T06 ───────────────→ T11 memory/run persistence
      T02 + T03 + T05 + T07 + T08 + T10 + T11 → T12 AgentLoop
      T03 ──→ T13 credentials/real provider
      T12 + T13 → T14 API/CLI → T15 WebUI
      T12 + T14 → T16 deterministic demos
      T15 + T16 → T17 distribution/CI/docs → Gate 2 release
```

After T01 merges, T02/T03/T04/T06 may run in parallel worktrees. After their dependencies merge, T07/T08/T11/T13 may run in parallel. T14 and T16 may run in parallel after T12 if both consume stable service interfaces.

## Specification Coverage Matrix

| SPEC concern | Plan coverage |
|---|---|
| Decision loop and mockable LLM | T03, T12, T13 |
| File/process tools and dispatch | T08, T09 |
| Workspace, policy, HITL and audit | T04, T05, T06, T07 |
| Objective validation feedback and stopping | T09, T10, T12 |
| Project memory and bounded context | T11, T12 |
| Declarative configuration | T02 |
| Credential threat controls | T06, T13, T14, T17 |
| Local CLI/WebUI and public mock WebUI | T14, T15, T16 |
| Three deterministic mechanism demos | T16 |
| Distribution, CI and required README | T17 |
| SPEC process, cold start and final human evidence | Gate 0, Gate 1, Gate 2 |

---

## Gate 0: Required Cross-Agent Cold Start — Before Formal Implementation

This gate is performed by the student with a different agent type, such as OpenCode, Claude Code or Gemini CLI, in a fresh session with no Codex conversation or memory.

- [x] Create a disposable directory containing only `SPEC.md`, `PLAN.md` and a fresh local `.git`; do not copy process logs or conversation history.
- [x] Give the new agent only `SPEC.md` and `PLAN.md` and the instruction: “Attempt T01 and T04. If any requirement is uncertain, stop and ask instead of guessing.”
- [x] Restrict the cold-start agent from real LLM/API access and network-dependent tests. The first run exposed that dependency installation must be the sole permitted network exception, now stated in T01 Step 0.
- [x] Save every question, conflicting interpretation and incomplete result; do not provide oral clarification during the attempt.
- [x] Compare the output with the intended interfaces and acceptance criteria in this plan.
- [x] Record the defects and before/after SPEC or PLAN diffs in `SPEC_PROCESS.md`.
- [x] Revise `SPEC.md` and `PLAN.md`, obtain student approval again, and keep the disposable implementation isolated.
- [x] Do not begin T01 formal implementation until this gate is marked complete.

Expected evidence: named second agent type, fresh-session instruction, 1–2 attempted task identifiers, questions/incorrect interpretations, and exact document revisions.

---

### Task 01: Package Foundation and Typed Domain Model

**Branch/PR:** `codex/t01-domain-foundation`

**Files:**
- Modify: `.gitignore`
- Create: `pyproject.toml`
- Create: `src/safefix/__init__.py`
- Create: `src/safefix/domain.py`
- Create: `tests/unit/test_domain.py`

**Interfaces:**
- Consumes: only Python/Pydantic.
- Produces: `Task`; `Action` discriminated union; `action_digest(action) -> str`; `BudgetState`; `ToolResult`; `PolicyDecision`; `Feedback`; `ProgressResult`; `StopDecision`; `ApprovalRequest`; `RunSnapshot`; enums `TaskMode`, `DecisionOutcome`, `RiskLevel`, `RunStatus`, `FeedbackCategory`, `ApprovalStatus`, `AccessKind`.

- [x] **Step 0: Create the Python 3.12 environment without production behavior**

Verify the existing `.gitignore` retains `.worktrees/`, `.superpowers/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/` and `.hypothesis/`. Create an empty `src/safefix/__init__.py` and this package manifest:

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "safefix-harness"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115,<1", "httpx>=0.28,<1", "jinja2>=3.1,<4",
  "keyring>=25,<26", "pathspec>=0.12,<1", "pydantic>=2.10,<3",
  "python-multipart>=0.0.20,<1", "PyYAML>=6,<7", "uvicorn>=0.34,<1",
]

[project.optional-dependencies]
dev = ["hypothesis>=6.120,<7", "mypy>=1.14,<2", "pytest>=8.3,<9", "pytest-asyncio>=0.25,<2", "ruff>=0.9,<1"]

[tool.hatch.build.targets.wheel]
packages = ["src/safefix"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe --version
```

Expected: Python reports `3.12.x`; dependencies install successfully; no `domain.py` exists yet.

- [x] **Step 1: Write failing digest and discriminator tests**

```python
from safefix.domain import ReadFileAction, action_digest


def test_action_digest_is_stable_for_equal_actions() -> None:
    first = ReadFileAction(id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=20)
    second = ReadFileAction(id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=20)
    assert first.type == "read_file"
    assert action_digest(first) == action_digest(second)


def test_action_digest_changes_when_payload_changes() -> None:
    first = ReadFileAction(id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=20)
    second = ReadFileAction(id="a1", reason="inspect", path="src/app.py", start_line=1, end_line=21)
    assert action_digest(first) != action_digest(second)
```

- [x] **Step 2: Run RED test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_domain.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'safefix.domain'`.

- [x] **Step 3: Implement the complete immutable domain model**

Use `ConfigDict(frozen=True, extra="forbid")` on every model and `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]` for nonempty strings. Define these exact action fields:

| Class | Exact fields after common `id`, `reason` |
|---|---|
| `ListFilesAction` | `type: Literal["list_files"]="list_files"`, `path="."`, `pattern="**/*"`, `limit=Field(100, ge=1, le=1000)` |
| `ReadFileAction` | `type: Literal["read_file"]="read_file"`, nonempty `path`, `start_line=Field(1, ge=1)`, `end_line=Field(200, ge=1)`; validate `end_line >= start_line` and span `<=500` |
| `SearchTextAction` | `type: Literal["search_text"]="search_text"`, nonempty `pattern` with max length 512 and literal-match semantics, `path="."`, `file_glob="**/*"`, `max_results=Field(50, ge=1, le=200)` |
| `ApplyPatchAction` | `type: Literal["apply_patch"]="apply_patch"`, nonempty `path`, `expected_sha256` matching `^[0-9a-f]{64}$`, nonempty `old_text`, `new_text: str`, `expected_replacements=Field(1, ge=1, le=100)` |
| `RunValidationAction` | `type: Literal["run_validation"]="run_validation"`, nonempty `validator_id` |
| `RunProcessAction` | `type: Literal["run_process"]="run_process"`, nonempty `program`, `args: tuple[str, ...]=()` |
| `FinishAction` | `type: Literal["finish"]="finish"`, nonempty `summary` |

Define exact enum values: `TaskMode={LOCAL:"local", PUBLIC_DEMO:"public-demo"}`, `DecisionOutcome={ALLOW, REQUIRE_APPROVAL, DENY}`, `RiskLevel={LOW, MEDIUM, HIGH}`, `RunStatus={CREATED, RUNNING, AWAITING_APPROVAL, SUCCESS, BLOCKED, NO_PROGRESS, BUDGET_EXCEEDED, FAILED, CANCELLED}`, `FeedbackCategory={VALIDATION_SUCCESS, TEST_FAILURE, LINT_FAILURE, TYPE_ERROR, TIMEOUT, TOOL_ERROR, POLICY_REJECTION}`, `ApprovalStatus={PENDING, APPROVED, REJECTED, EXPIRED, CANCELLED}`, and `AccessKind={READ, WRITE, LIST, SEARCH}`. Except for `TaskMode`, each member's serialized value equals its uppercase name.

Define exact supporting models:

- `Task(id, project_id, workspace_root, description, mode, created_at)` with nonempty identifiers, path and description.
- `BudgetState(max_steps, remaining_steps, max_repair_rounds, remaining_repairs, deadline_at=None)` with maximums `>=1`, remaining values `>=0`, remaining values not exceeding maximums, and an optional aware deadline normalized to UTC.
- `ToolResult(action_id, success, exit_code=None, stdout_summary="", stderr_summary="", changed_files=(), duration_ms=0, error_type=None)` and classmethod `failure(action_id, error_type, message)`.
- `PolicyDecision(action_id, outcome, risk_level, rule_ids, explanation)`.
- `Feedback(category, summary, failure_count, fingerprint, remaining_steps, remaining_repairs, changed_files=())`.
- `ProgressResult(made_progress, reason)` and `StopDecision(code, reason)`.
- `ApprovalRequest(id, run_id, action_hash, status, one_time_token_hash, frozen_action_json, created_at, expires_at, decided_at=None)`.
- `RunSnapshot(run_id, task_id, project_id, workspace_root, description, status, repair_round, step_count, budget, version, pending_approval_id=None, action_digests=(), feedback_history=(), latest_tool_result=None, changed_files=(), stop_reason=None, created_at, updated_at)` with aware `created_at` and `updated_at` values normalized to UTC.

```python
Action = Annotated[
    ListFilesAction
    | ReadFileAction
    | SearchTextAction
    | ApplyPatchAction
    | RunValidationAction
    | RunProcessAction
    | FinishAction,
    Field(discriminator="type"),
]


def action_digest(action: Action) -> str:
    canonical = action.model_dump_json(exclude_none=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [x] **Step 4: Run GREEN tests and add enum/model validation cases**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_domain.py -v`

Expected: PASS. Add tests proving `start_line < 1`, `end_line < start_line`, a span over 500, whitespace-only `program`, negative remaining budget and remaining budget above maximum raise Pydantic validation errors. Prove every action rejects an unknown field, serializes the exact discriminator and preserves its exact defaults and bounds. Prove naive UTC-contract timestamps are rejected and aware timestamps are normalized to UTC; rerun and keep PASS.

- [x] **Step 5: Review and commit**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests PASS.

Commit: `feat(domain): 添加 SafeFix 类型化领域模型`

After review, record the implementation commit hash beside T01 and add required evidence to `AGENT_LOG.md` in a separate documentation commit.

---

### Task 02: Strict YAML Configuration

**Branch/PR:** `codex/t02-configuration`

**Files:**
- Create: `src/safefix/config.py`
- Create: `tests/unit/test_config.py`
- Create: `examples/safefix.yaml`

**Interfaces:**
- Consumes: domain limits and Python path types from T01.
- Produces: `SafeFixSettings`, `LLMSettings`, `ValidatorSettings`, `PolicySettings`, `BudgetSettings`, `MemorySettings`, `ConfigError`, `load_settings(path: Path) -> SafeFixSettings`.

- [x] **Step 1: Write failing strict-schema tests**

```python
from pathlib import Path
import pytest
from safefix.config import ConfigError, load_settings


def test_config_rejects_unknown_and_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "safefix.yaml"
    path.write_text("llm:\n  model: test\n  api_key: secret\nunknown: true\n", encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_settings(path)
    assert "api_key" in str(error.value)
    assert "unknown" in str(error.value)
```

- [ ] **Step 2: Run RED test**

Run: `python -m pytest tests/unit/test_config.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'safefix.config'`.

Evidence caveat (2026-07-15): execution resumed from uncommitted WIP, so the original pre-production pytest RED cannot be proven. The recorded base lacks `safefix.config`, and every defect found during recovery and review followed observed RED→GREEN. This checkbox intentionally remains open.

- [x] **Step 3: Implement strict settings and loader**

Use Pydantic models with `ConfigDict(extra="forbid", frozen=True)` and stripped nonempty strings. Implement this exact schema:

| Model | Exact fields and constraints |
|---|---|
| `LLMSettings` | required `endpoint: HttpUrl`; required nonempty `model`; no Key or secret field |
| `ValidatorSettings` | required nonempty `id`; `kind: Literal["test", "lint", "type"]`; required nonempty `program`; required `args: tuple[str, ...]`; `timeout_seconds` in 1–3600; nonempty `success_exit_codes: frozenset[int]`; `output_limit_bytes` in 1024–10485760 |
| `PolicySettings` | `sensitive_patterns=(".env", "**/*.pem", "**/.ssh/**")`; `allowed_programs=()`; `denied_programs=()`; every entry is nonempty after stripping; reject duplicates and any case-insensitive allow/deny overlap |
| `BudgetSettings` | `repair_rounds=3` in 1–10; `no_progress_rounds=2` in 1–10 and not above repair rounds; `total_steps=20` in 1–1000 and not below repair rounds; `wall_time_seconds=900` in 1–86400 |
| `MemorySettings` | `retrieval_limit=5` in 1–50; `character_budget=4000` in 256–100000 |
| `SafeFixSettings` | required `llm`; at least one `validators` entry; default `policy`, `budget`, and `memory`; reject validator IDs that collide after stripping and `casefold()` |

`load_settings` reads UTF-8 with `yaml.safe_load`, requires the YAML root to be a mapping, and never expands environment variables or reads a Key. Convert file, YAML, root-type and Pydantic errors into `ConfigError`. Pydantic error messages must contain dotted field locations such as `validators.0.timeout_seconds`, but must be constructed from `ValidationError.errors()` without including the rejected input value. YAML messages report only safe line/column information rather than echoing source text.

```python
def load_settings(path: Path) -> SafeFixSettings:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigError(f"cannot read configuration: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(safe_yaml_error(exc)) from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")
    try:
        return SafeFixSettings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(format_validation_errors(exc)) from exc
```

- [x] **Step 4: Add boundary tests and example config**

Test duplicate validator IDs including case variations, zero and excessive timeout, empty success exit codes, output bounds, contradictory allow/deny programs, missing model, invalid endpoint, non-mapping YAML, malformed YAML, budget cross-field rules, memory bounds, immutability, unknown fields and non-disclosure of rejected secret values. Create `examples/safefix.yaml` containing a valid Python `pytest` validator, three repair rounds, two no-progress rounds, bounded wall time/output/memory and default sensitive patterns. Load this example with the production loader in a test and prove its serialized data contains no Key field.

```yaml
llm:
  endpoint: https://api.openai.com/v1
  model: configured-by-user
validators:
  - id: pytest
    kind: test
    program: python
    args: [-m, pytest, -q]
    timeout_seconds: 120
    success_exit_codes: [0]
    output_limit_bytes: 65536
budget:
  repair_rounds: 3
  no_progress_rounds: 2
  total_steps: 20
  wall_time_seconds: 900
policy:
  sensitive_patterns: [.env, "**/*.pem", "**/.ssh/**"]
  allowed_programs: []
  denied_programs: []
memory:
  retrieval_limit: 5
  character_budget: 4000
```

Run: `python -m pytest tests/unit/test_config.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(config): 添加严格声明式配置`

Record the T02 hash and required evidence after both reviews.

---

### Task 03: LLM Protocol, Scripted Mock and Action Parser

**Branch/PR:** `codex/t03-llm-parser`

**Files:**
- Create: `src/safefix/llm/__init__.py`
- Create: `src/safefix/llm/base.py`
- Create: `src/safefix/llm/mock.py`
- Create: `src/safefix/action_parser.py`
- Create: `tests/unit/test_llm_mock.py`
- Create: `tests/unit/test_action_parser.py`

**Interfaces:**
- Consumes: `Action` and its `TypeAdapter` from T01.
- Produces: async `LLMClient.complete(messages, settings) -> ModelResponse`; `ScriptedMockLLM`; `ActionParser.parse(text: str) -> Action`; `ActionParseError.feedback`.

- [x] **Step 1: Write failing scripted-sequence test**

```python
import pytest
from safefix.llm.mock import ScriptedMockLLM


@pytest.mark.asyncio
async def test_mock_returns_script_in_order_and_then_fails() -> None:
    client = ScriptedMockLLM(['{"type":"finish","id":"a1","reason":"done","summary":"ok"}'])
    first = await client.complete([], {})
    assert '"type":"finish"' in first.text
    with pytest.raises(AssertionError, match="script exhausted"):
        await client.complete([], {})
```

- [x] **Step 2: Run RED tests**

Run: `python -m pytest tests/unit/test_llm_mock.py tests/unit/test_action_parser.py -v`

Expected: FAIL because the LLM package and parser do not exist.

- [x] **Step 3: Implement protocol, mock and strict parser**

`ModelMessage` has `role` and `content`; `ModelResponse` has `text`, provider request ID and usage counts. `ScriptedMockLLM` stores an immutable script and a call index. `ActionParser.parse` accepts exactly one JSON object and validates it through `TypeAdapter(Action)`.

```python
class ActionParser:
    def parse(self, text: str) -> Action:
        try:
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("action must be a JSON object")
            return ACTION_ADAPTER.validate_python(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ActionParseError.from_exception(exc) from exc
```

- [x] **Step 4: Add parser feedback tests**

Test trailing text, unknown action type, missing required payload, and valid `run_process` arrays. Assert parse errors expose redacted field-level feedback without echoing a value matching `sk-SECRET`.

Run: `python -m pytest tests/unit/test_llm_mock.py tests/unit/test_action_parser.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(llm): 添加可注入模型接口与严格动作解析`

Record the T03 hash and required evidence after both reviews.

---

### Task 04: Canonical Workspace and Sensitive-Path Boundary

**Branch/PR:** `codex/t04-path-boundary`

**Files:**
- Create: `src/safefix/governance/__init__.py`
- Create: `src/safefix/governance/paths.py`
- Create: `tests/unit/test_paths.py`

**Interfaces:**
- Consumes: a configured workspace root, T01 `AccessKind`, and sensitive GitWildMatch patterns.
- Produces: `WorkspaceBoundary.resolve(candidate: str, access: AccessKind) -> Path`; exceptions `PathOutsideWorkspace`, `SensitivePathDenied`, `SymlinkEscapeDenied`.

- [x] **Step 1: Write failing traversal and sensitive-file tests**

```python
from pathlib import Path
import pytest
from safefix.domain import AccessKind
from safefix.governance.paths import PathOutsideWorkspace, SensitivePathDenied, WorkspaceBoundary


def test_boundary_rejects_parent_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    boundary = WorkspaceBoundary(workspace, (".env", "**/*.pem"))
    with pytest.raises(PathOutsideWorkspace):
        boundary.resolve("../outside.txt", AccessKind.READ)


def test_boundary_denies_sensitive_file_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".env").write_text("TOKEN=x", encoding="utf-8")
    boundary = WorkspaceBoundary(workspace, (".env",))
    with pytest.raises(SensitivePathDenied):
        boundary.resolve(".env", AccessKind.READ)
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/unit/test_paths.py -v`

Expected: FAIL because `WorkspaceBoundary` does not exist.

- [x] **Step 3: Implement canonical boundary checks**

Resolve the workspace once. Join relative candidates to it and call `Path.resolve(strict=False)`, which resolves existing symlink parents without requiring a new write leaf to exist. Compare canonical paths with `os.path.normcase`, `os.path.abspath` and `os.path.commonpath`; a different drive or common path raises `PathOutsideWorkspace`. If the lexical path is inside but the resolved target escapes through a symlink, raise `SymlinkEscapeDenied`. Compile sensitive patterns once with `pathspec.PathSpec.from_lines("gitwildmatch", patterns)` and match the workspace-relative POSIX path.

```python
def _assert_inside(root: Path, target: Path) -> None:
    root_key = os.path.normcase(os.path.abspath(root))
    target_key = os.path.normcase(os.path.abspath(target))
    try:
        inside = os.path.commonpath((root_key, target_key)) == root_key
    except ValueError:
        inside = False
    if not inside:
        raise PathOutsideWorkspace(str(target))
```

- [x] **Step 4: Add symlink and property tests**

Use Hypothesis to generate segments containing `.`, `..`, separators and Unicode. Assert every accepted path resolves beneath the root using the same canonical comparison. Add a symlink-escape test guarded by platform capability, a Windows drive/case test, and GitWildMatch cases proving `.env`, `**/*.pem` and `**/.ssh/**` match the documented files.

Run: `python -m pytest tests/unit/test_paths.py -v`

Expected: PASS; permitted symlink-capability skip is explicitly reported.

- [x] **Step 5: Review and commit**

Commit: `feat(governance): 实现规范化工作区边界`

Record the T04 hash and required evidence after both reviews.

---

### Task 05: Three-Level Policy Engine

**Branch/PR:** `codex/t05-policy-engine`

**Files:**
- Create: `src/safefix/governance/policy.py`
- Create: `tests/unit/test_policy.py`

**Interfaces:**
- Consumes: `Action`, `PolicyDecision`, `WorkspaceBoundary`, `SafeFixSettings`.
- Produces: `PolicyEngine.decide(action: Action) -> PolicyDecision` with stable rule IDs and outcomes `ALLOW`, `REQUIRE_APPROVAL`, `DENY`.

- [x] **Step 1: Write failing table-driven classification tests**

```python
import pytest
from safefix.domain import DecisionOutcome, RunProcessAction
from safefix.governance.policy import PolicyEngine


@pytest.mark.parametrize(
    ("program", "args", "expected", "rule"),
    [
        ("pytest", ("-q",), DecisionOutcome.ALLOW, "CMD_CONFIGURED_VALIDATOR"),
        ("git", ("commit", "-m", "x"), DecisionOutcome.REQUIRE_APPROVAL, "CMD_GIT_WRITE"),
        ("pip", ("install", "requests"), DecisionOutcome.REQUIRE_APPROVAL, "CMD_INSTALL"),
        ("sudo", ("rm", "-rf", "/"), DecisionOutcome.DENY, "CMD_PRIVILEGE_ESCALATION"),
    ],
)
def test_process_risk_matrix(policy: PolicyEngine, program: str, args: tuple[str, ...], expected: DecisionOutcome, rule: str) -> None:
    action = RunProcessAction(id="a1", reason="test", program=program, args=args)
    decision = policy.decide(action)
    assert decision.outcome is expected
    assert rule in decision.rule_ids
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/unit/test_policy.py -v`

Expected: FAIL because `PolicyEngine` does not exist.

- [x] **Step 3: Implement ordered deterministic rules**

Evaluate permanent-deny rules before approval rules and approval rules before allows. Deny privilege escalation, credential readers, system paths and destructive root targets. Require approval for deletion, installers, network clients, Git writes and unconfigured programs. Allow only structured file actions that pass `WorkspaceBoundary` and exact configured validators.

```python
def decide(self, action: Action) -> PolicyDecision:
    for rule in self._rules:
        match = rule.evaluate(action)
        if match is not None:
            return PolicyDecision(action_id=action.id, outcome=match.outcome, risk_level=match.risk_level, rule_ids=(rule.id,), explanation=match.explanation)
    return self._deny_by_default(action, "POLICY_NO_MATCH")
```

- [x] **Step 4: Add bypass and explanation tests**

Test executable case variations on Windows, path-like program names, Shell metacharacters embedded in arguments, unknown programs, delete-like Python/Node inline execution, and configured validator argument mismatch. Every decision must include a nonempty explanation and stable rule ID.

Run: `python -m pytest tests/unit/test_policy.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(governance): 添加确定性动作策略分类`

Record the T05 hash and required evidence after both reviews.

---

### Task 06: Redacted Hash-Chained Audit Store

**Branch/PR:** `codex/t06-audit-store`

**Files:**
- Create: `src/safefix/governance/audit.py`
- Create: `tests/unit/test_audit.py`

**Interfaces:**
- Consumes: JSON-serializable event payloads and a SQLite connection factory.
- Produces: `AuditStore.append(run_id, event_type, payload) -> AuditEvent`; `list_events(run_id)`; `verify_chain(run_id) -> AuditVerification`.

- [x] **Step 1: Write failing tamper-detection test**

```python
import sqlite3
from safefix.governance.audit import AuditStore


def test_audit_chain_detects_modified_payload() -> None:
    connection = sqlite3.connect(":memory:")
    store = AuditStore(connection)
    store.append("run-1", "ACTION", {"token": "sk-SECRET", "value": 1})
    store.append("run-1", "DECISION", {"outcome": "DENY"})
    connection.execute("UPDATE audit_events SET payload = ? WHERE sequence = 1", ('{"value":9}',))
    result = store.verify_chain("run-1")
    assert result.valid is False
    assert result.first_invalid_sequence == 1
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/unit/test_audit.py -v`

Expected: FAIL because `AuditStore` does not exist.

- [x] **Step 3: Implement redaction and hash chaining**

Canonicalize redacted JSON with sorted keys and compact separators. Hash `run_id`, sequence, event type, redacted payload, timestamp and previous hash. Redact keys matching token/key/secret/password/authorization and values matching configured secret values before persistence.

```python
event_hash = hashlib.sha256(
    f"{run_id}|{sequence}|{event_type}|{payload_json}|{created_at}|{previous_hash}".encode("utf-8")
).hexdigest()
```

- [x] **Step 4: Add isolation and fail-closed tests**

Test separate chains per run, empty-chain validity, Key redaction, deterministic sequence, and transaction failure. Expose `AuditUnavailable`; later dangerous-action code must treat it as a deny condition.

Run: `python -m pytest tests/unit/test_audit.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(audit): 添加脱敏防篡改审计日志`

Record the T06 hash and required evidence after both reviews.

---

### Task 07: Persistent HITL Approval State Machine

**Branch/PR:** `t07-approval-state-machine`

**Files:**
- Create: `src/safefix/governance/approvals.py`
- Create: `tests/unit/test_approvals.py`

**Interfaces:**
- Consumes: `Action`, `PolicyDecision(REQUIRE_APPROVAL)`, `action_digest`, `AuditStore`.
- Produces: `ApprovalStateMachine.request(...) -> ApprovalChallenge`; `approve(id, plaintext_token, action) -> ApprovalRequest`; `reject(...)`; `expire_pending(now)`; single-use frozen-action verification.

- [x] **Step 1: Write failing replacement and replay tests**

```python
import pytest
from safefix.domain import RiskLevel, RunProcessAction
from safefix.governance.approvals import ActionMismatch, ApprovalAlreadyUsed


def test_approval_cannot_authorize_changed_action(approval_store) -> None:
    original = RunProcessAction(id="a1", reason="commit", program="git", args=("commit", "-m", "ok"))
    changed = RunProcessAction(id="a1", reason="commit", program="git", args=("push",))
    challenge = approval_store.request("run-1", original, RiskLevel.MEDIUM, ("CMD_GIT_WRITE",), 300)
    with pytest.raises(ActionMismatch):
        approval_store.approve(challenge.id, challenge.token, changed)


def test_approval_token_is_single_use(approval_store, risky_action) -> None:
    challenge = approval_store.request("run-1", risky_action, RiskLevel.MEDIUM, ("CMD_GIT_WRITE",), 300)
    approval_store.approve(challenge.id, challenge.token, risky_action)
    with pytest.raises(ApprovalAlreadyUsed):
        approval_store.approve(challenge.id, challenge.token, risky_action)
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/unit/test_approvals.py -v`

Expected: FAIL because approval storage does not exist.

- [x] **Step 3: Implement state transitions**

Persist only a SHA-256 token digest, frozen action JSON and `action_digest`. Valid transitions are `PENDING -> APPROVED|REJECTED|EXPIRED|CANCELLED`; terminal states never transition. Compare token digests with `hmac.compare_digest`. Write an audit event in the same logical operation; if audit append fails, do not approve.

```python
ALLOWED_TRANSITIONS = {
    "PENDING": frozenset({"APPROVED", "REJECTED", "EXPIRED", "CANCELLED"}),
    "APPROVED": frozenset(), "REJECTED": frozenset(),
    "EXPIRED": frozenset(), "CANCELLED": frozenset(),
}
```

- [x] **Step 4: Add persistence, expiry and concurrency tests**

Close/reopen the SQLite connection and prove pending requests survive. Test rejection transition (without storing free-text feedback; T12 owns user-facing feedback), expiry, wrong token, terminal transition rejection, and two concurrent approvals where exactly one succeeds.

Run: `python -m pytest tests/unit/test_approvals.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(approval): 实现持久化人工审批状态机`

Record the T07 hash and required evidence after both reviews.

---

### Task 08: Bounded Filesystem Tools and Registry

**Branch/PR:** `t08-filesystem-tools`

**Files:**
- Create: `src/safefix/tools/__init__.py`
- Create: `src/safefix/tools/base.py`
- Create: `src/safefix/tools/filesystem.py`
- Create: `src/safefix/tools/registry.py`
- Create: `tests/unit/test_filesystem_tools.py`
- Create: `tests/unit/test_tool_registry.py`

**Interfaces:**
- Consumes: file action models, `WorkspaceBoundary`, `ToolResult`.
- Produces: async `Tool.execute(action) -> ToolResult`; `ToolRegistry.dispatch(action) -> ToolResult`; exact list/read/search/patch tools.

- [x] **Step 1: Write failing stale-patch test**

```python
from hashlib import sha256
import pytest
from safefix.domain import ApplyPatchAction


@pytest.mark.asyncio
async def test_patch_rejects_stale_expected_hash(file_tools, workspace) -> None:
    target = workspace / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    action = ApplyPatchAction(
        id="a1", reason="fix", path="app.py", expected_sha256=sha256(b"different").hexdigest(),
        old_text="value = 1", new_text="value = 2", expected_replacements=1,
    )
    result = await file_tools.apply_patch(action)
    assert result.success is False
    assert result.error_type == "STALE_FILE"
    assert target.read_text(encoding="utf-8") == "value = 1\n"
```

- [x] **Step 2: Run RED tests**

Run: `python -m pytest tests/unit/test_filesystem_tools.py tests/unit/test_tool_registry.py -v`

Expected: FAIL because filesystem tools do not exist.

- [x] **Step 3: Implement bounded file operations**

Every operation resolves through `WorkspaceBoundary`. List skips `.git` and configured ignored directories, read uses line/byte caps, search limits files/matches/output, and patch requires exact current SHA-256 and exact replacement count. Write through a same-directory temporary file followed by atomic replace.

```python
if current_hash != action.expected_sha256:
    return ToolResult.failure(action.id, "STALE_FILE", "file changed since it was read")
if text.count(action.old_text) != action.expected_replacements:
    return ToolResult.failure(action.id, "PATCH_MISMATCH", "replacement count differs")
```

- [x] **Step 4: Implement registry and edge tests**

Dispatch by action class, reject missing tools, and never accept raw strings. Test binary files, oversized reads, Unicode, ignored `.git`, sensitive paths, output truncation, patch mismatch and atomic-write failure.

Run: `python -m pytest tests/unit/test_filesystem_tools.py tests/unit/test_tool_registry.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(tools): 添加受限工作区文件工具`

Record the T08 hash and required evidence after both reviews.

Implementation range: `789cf99..3003aa2`; design `d2e8f3c`; detailed plan `e3f9892`; formatting gate `d3a5365`. Final task and whole-branch reviews approved with 0 Critical / 0 Important; process evidence is recorded separately in `AGENT_LOG.md`.

---

### Task 09: Structured Process and Validator Runner

**Branch/PR:** `codex/t09-process-runner`

**Files:**
- Create: `src/safefix/tools/process.py`
- Create: `tests/unit/test_process_tool.py`
- Create: `tests/integration/test_validator_runner.py`

**Interfaces:**
- Consumes: approved `RunProcessAction`, `RunValidationAction`, `ValidatorSettings`, `WorkspaceBoundary`, `ToolResult`.
- Produces: `ProcessTool.execute(action) -> ToolResult`; `ValidatorRunner.run(validator_id) -> ToolResult`.

- [x] **Step 1: Write failing no-Shell and timeout tests**

```python
import sys
import pytest
from safefix.domain import RunProcessAction


@pytest.mark.asyncio
async def test_process_passes_metacharacters_as_literal_arguments(process_tool) -> None:
    action = RunProcessAction(
        id="a1", reason="literal", program=sys.executable,
        args=("-c", "import sys; print(sys.argv[1])", "; echo injected"),
    )
    result = await process_tool.execute(action)
    assert result.success is True
    assert result.stdout_summary.strip() == "; echo injected"


@pytest.mark.asyncio
async def test_process_timeout_returns_structured_error(process_tool) -> None:
    action = RunProcessAction(id="a2", reason="timeout", program=sys.executable, args=("-c", "import time; time.sleep(5)"))
    result = await process_tool.execute(action, timeout_seconds=0.05)
    assert result.error_type == "PROCESS_TIMEOUT"
```

- [x] **Step 2: Run RED tests**

Run: `python -m pytest tests/unit/test_process_tool.py tests/integration/test_validator_runner.py -v`

Expected: FAIL because process tools do not exist.

- [x] **Step 3: Implement process execution**

Use `asyncio.create_subprocess_exec`, workspace `cwd`, `shell=False` by construction, a minimal inherited environment with configured additions, concurrent stdout/stderr collection, timeout termination and byte caps. Never log the full environment.

```python
process = await asyncio.create_subprocess_exec(
    action.program, *action.args, cwd=workspace, env=safe_environment,
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
)
```

- [x] **Step 4: Implement validator lookup and tests**

`ValidatorRunner` accepts only a configured validator ID and builds an exact process action from immutable settings. Test success and failing exit codes using a temporary Python project, unknown validator ID, output truncation, process-not-found and cancellation.

Run: `python -m pytest tests/unit/test_process_tool.py tests/integration/test_validator_runner.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(tools): 添加结构化验证进程执行`

Record the T09 hash and required evidence after both reviews.

---

### Task 10: Deterministic Feedback and Progress Sensor

**Branch/PR:** `codex/t10-feedback-engine`

**Files:**
- Create: `src/safefix/feedback.py`
- Create: `tests/unit/test_feedback.py`

**Interfaces:**
- Consumes: validator `ToolResult` values and previous `Feedback`.
- Produces: `FeedbackEngine.from_results(results, changed_files, remaining_steps, remaining_repairs) -> Feedback`; `compare(previous, current) -> ProgressResult`; `should_stop(history, budget: BudgetState, action_digests=()) -> StopDecision | None`.

- [x] **Step 1: Write failing classification and progress tests**

```python
from safefix.domain import BudgetState
from safefix.feedback import FeedbackEngine


def test_fewer_failed_tests_counts_as_progress(pytest_results) -> None:
    engine = FeedbackEngine()
    previous = engine.from_results([pytest_results("2 failed, 3 passed", 1)], ("app.py",), remaining_steps=2, remaining_repairs=2)
    current = engine.from_results([pytest_results("1 failed, 4 passed", 1)], ("app.py",), remaining_steps=1, remaining_repairs=1)
    assert engine.compare(previous, current).made_progress is True


def test_two_equal_failure_fingerprints_stop_no_progress(pytest_results) -> None:
    engine = FeedbackEngine(no_progress_limit=2)
    feedback = engine.from_results([pytest_results("1 failed: test_value", 1)], ("app.py",), remaining_steps=2, remaining_repairs=1)
    budget = BudgetState(max_steps=20, remaining_steps=2, max_repair_rounds=3, remaining_repairs=1)
    decision = engine.should_stop([feedback, feedback], budget)
    assert decision.code == "NO_PROGRESS"
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/unit/test_feedback.py -v`

Expected: FAIL because `FeedbackEngine` does not exist.

- [x] **Step 3: Implement normalized feedback**

Classify results as test, lint, type, timeout, tool or policy failure using validator kind and error type. Strip volatile timestamps/paths, extract bounded failure lines, count recognizable failures and hash the normalized category/summary. Progress is fewer failures or a changed fingerprint with no new higher-severity category.

```python
fingerprint = hashlib.sha256(
    json.dumps({"category": category.value, "failures": normalized_failures}, sort_keys=True).encode("utf-8")
).hexdigest()
```

- [x] **Step 4: Add stop-condition tests**

Test repeated action digest, two no-progress rounds, repair-round exhaustion, total-step exhaustion, time budget, success, and mixed validators where lint passes but tests regress.

Run: `python -m pytest tests/unit/test_feedback.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(feedback): 添加验证反馈与停机判断`

Record the T10 hash and required evidence after both reviews.

---

### Task 11: Project Memory and Run Persistence

**Branch/PR:** `codex/t11-persistence`

**Files:**
- Create: `src/safefix/memory.py`
- Create: `src/safefix/run_store.py`
- Create: `tests/unit/test_memory.py`
- Create: `tests/unit/test_run_store.py`

**Interfaces:**
- Consumes: SQLite connection and T01 `RunSnapshot`.
- Produces: `MemoryRecord`; `MemoryStore.add/list/delete_project/search`; `RunStore.create/get/transition/save_snapshot`; compare-and-set transitions.

- [x] **Step 1: Write failing bounded retrieval test**

```python
import sqlite3
from safefix.memory import MemoryStore


def test_memory_is_project_scoped_relevant_and_bounded() -> None:
    store = MemoryStore(sqlite3.connect(":memory:"))
    store.add("project-a", "convention", "Use pytest and keep functions pure", ("pytest", "pure"))
    store.add("project-b", "convention", "Use jest", ("jest",))
    results = store.search("project-a", "pytest failure", limit=3, char_budget=80)
    assert [item.project_id for item in results] == ["project-a"]
    assert sum(len(item.content) for item in results) <= 80
```

- [x] **Step 2: Run RED tests**

Run: `python -m pytest tests/unit/test_memory.py tests/unit/test_run_store.py -v`

Expected: FAIL because memory and run stores do not exist.

- [x] **Step 3: Implement deterministic memory scoring**

Tokenize Unicode text into normalized words, compute keyword overlap, apply type weights and bounded time decay, then sort by score and stable ID. Never store full transcript, validator output or values flagged by the shared redactor.

```python
score = keyword_overlap(query_tokens, record_tokens) * TYPE_WEIGHTS[record.type] + recency_score(record.created_at, now)
```

- [x] **Step 4: Implement run transitions and persistence tests**

Allowed transitions are `CREATED -> RUNNING`, `RUNNING -> AWAITING_APPROVAL|SUCCESS|BLOCKED|NO_PROGRESS|BUDGET_EXCEEDED|FAILED|CANCELLED`, and `AWAITING_APPROVAL -> RUNNING|CANCELLED|FAILED`. Use transactions and expected-version compare-and-set. Test reopen persistence, invalid transitions, concurrent version conflict, project deletion and redaction rejection.

Run: `python -m pytest tests/unit/test_memory.py tests/unit/test_run_store.py -v`

Expected: PASS.

- [x] **Step 5: Review and commit**

Commit: `feat(storage): 持久化项目记忆与运行状态`

Record the T11 hash and required evidence after both reviews.

---

### Task 12: Context Builder and Custom AgentLoop

**Branch/PR:** `codex/t12-agent-loop`

**Files:**
- Create: `src/safefix/context.py`
- Create: `src/safefix/agent_loop.py`
- Create: `tests/unit/test_context.py`
- Create: `tests/integration/test_agent_loop.py`

**Interfaces:**
- Consumes: `LLMClient`, parser, policy, approvals, tool registry, feedback, memory, run store, audit, settings.
- Produces: `ContextBuilder.build(snapshot) -> list[ModelMessage]`; async `AgentLoop.start(task) -> RunSnapshot`; `resume_approved`; `resume_rejected`; `cancel`.

- [x] **Step 1: Write failing dangerous-action pause test**

```python
import pytest
from safefix.domain import RunStatus


@pytest.mark.asyncio
async def test_loop_pauses_before_dangerous_tool_execution(loop_factory, git_commit_json) -> None:
    loop, process_spy = loop_factory([git_commit_json])
    snapshot = await loop.start(project="fixture", description="commit changes")
    assert snapshot.status is RunStatus.AWAITING_APPROVAL
    assert process_spy.calls == []
    assert snapshot.pending_approval_id is not None
```

- [x] **Step 2: Write failing feedback-repair test**

```python
@pytest.mark.asyncio
async def test_failed_validation_changes_next_action_and_finishes(loop_factory, bad_patch_json, validate_json, good_patch_json, finish_json) -> None:
    loop, _ = loop_factory([bad_patch_json, validate_json, good_patch_json, finish_json])
    snapshot = await loop.start(project="fixture", description="fix value")
    assert snapshot.status is RunStatus.SUCCESS
    assert snapshot.repair_round == 2
    assert snapshot.action_digests[0] != snapshot.action_digests[2]
    assert any(item.category.value == "TEST_FAILURE" for item in snapshot.feedback_history)
```

- [x] **Step 3: Run RED integration tests**

Run: `python -m pytest tests/unit/test_context.py tests/integration/test_agent_loop.py -v`

Expected: FAIL because `ContextBuilder` and `AgentLoop` do not exist.

- [x] **Step 4: Implement bounded context construction**

Build a provider-neutral system message describing only the action JSON schema and current deterministic limits, then add task, relevant memory, latest tool result, feedback and remaining budget. Enforce character budgets per section and never include Key or raw audit payloads.

- [x] **Step 5: Implement the explicit loop**

The loop performs exactly: load snapshot → check cancellation/budget → build context → one LLM call → `ActionParser.parse` → audit action → policy decision → audit decision → deny feedback, approval pause, or tool dispatch → validators after mutation → feedback/progress → persist snapshot → stop or repeat. No component recursively invokes the loop.

```python
while snapshot.status is RunStatus.RUNNING:
    stop = self.feedback.should_stop(snapshot.feedback_history, snapshot.budget, snapshot.action_digests)
    if stop is not None:
        return self._stop(snapshot, stop)
    response = await self.llm.complete(self.context.build(snapshot), self.model_settings)
    action = self.action_parser.parse(response.text)
    snapshot = await self._handle_action(snapshot, action)
return snapshot
```

- [x] **Step 6: Add failure and stop tests**

Test invalid model JSON feedback, parse retry exhaustion, policy deny, audit unavailable fail-closed, approval rejection, approval resume, repeated action, two no-progress rounds, time/step/repair budgets, cancellation and final validation failure.

Run: `python -m pytest tests/unit/test_context.py tests/integration/test_agent_loop.py -v`

Expected: PASS without network or real credentials.

- [x] **Step 7: Review and commit**

Commit: `feat(agent): 实现 SafeFix 智能体主循环`

Record the T12 hash and required evidence after both reviews.

---

### Task 13: Credential Lifecycle and OpenAI-Compatible Single Call

**Branch/PR:** `codex/t13-credentials-provider`

**Files:**
- Create: `src/safefix/credentials.py`
- Create: `src/safefix/llm/openai_compatible.py`
- Create: `tests/unit/test_credentials.py`
- Create: `tests/unit/test_openai_compatible.py`

**Interfaces:**
- Consumes: LLM protocol, configured endpoint/model, injected Keyring backend or secret-file path.
- Produces: `CredentialService.set/status/clear/get_for_request`; `OpenAICompatibleClient.complete` performing one HTTP request only.

- [x] **Step 1: Write failing no-plaintext-status test**

```python
from safefix.credentials import CredentialService


def test_status_never_returns_plaintext_key(fake_keyring) -> None:
    service = CredentialService(fake_keyring, service_name="safefix")
    service.set("openai-compatible", "sk-SECRET")
    status = service.status("openai-compatible")
    assert status.configured is True
    assert "sk-SECRET" not in repr(status)
```

- [x] **Step 2: Run RED tests**

Run: `python -m pytest tests/unit/test_credentials.py tests/unit/test_openai_compatible.py -v`

Expected: FAIL because the credential service and real provider do not exist.

- [x] **Step 3: Implement credential sources**

The native backend delegates set/get/delete to `keyring`. Secret-file mode requires an explicitly configured readable regular file and strips one trailing newline. `.env` fallback is opt-in, loaded from a named file rather than shell history, and reports a warning status. Interactive prompting belongs in CLI and uses `getpass.getpass`.

```python
def set(self, provider: str, value: str) -> None:
    if not value.strip():
        raise CredentialError("credential cannot be empty")
    self._backend.set_password(self._service_name, provider, value)
```

- [x] **Step 4: Implement one-call HTTP client with mocked transport tests**

Use injected `httpx.AsyncClient`. Build a provider request from `ModelMessage`, pass the Key only in the Authorization header, parse the first assistant content and usage, and convert timeout, 401, 429 and malformed responses into typed provider errors. Tests use `httpx.MockTransport` and assert captured logs/exceptions do not contain the Key.

```python
response = await self._http.post(
    f"{self._endpoint}/chat/completions",
    headers={"Authorization": f"Bearer {credential}"},
    json={"model": self._model, "messages": [message.as_dict() for message in messages]},
)
```

Run: `python -m pytest tests/unit/test_credentials.py tests/unit/test_openai_compatible.py -v`

Expected: PASS with no network.

- [x] **Step 5: Review and commit**

Commit: `feat(credentials): 安全管理凭据与模型调用`

Record the T13 hash and required evidence after both reviews.

---

### Task 14: Task Service, FastAPI Endpoints and CLI

**Branch/PR:** `codex/t14-api-cli`

**Files:**
- Create: `src/safefix/task_service.py`
- Create: `src/safefix/web/__init__.py`
- Create: `src/safefix/web/app.py`
- Create: `src/safefix/web/routes.py`
- Create: `src/safefix/cli.py`
- Create: `tests/web/test_api.py`
- Create: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: AgentLoop factory, run/approval/memory/credential stores and settings.
- Produces: `TaskService.create/get/list_events/approve/reject/cancel`; `create_app(dependencies) -> FastAPI`; CLI commands `run`, `serve`, `config`, `credentials`, `demo`.

- [x] **Step 1: Write failing public-mode input-boundary API test**

```python
def test_public_mode_rejects_project_path_and_real_provider(public_client) -> None:
    response = public_client.post("/api/runs", json={"task": "fix value", "project_path": "C:/private", "provider": "openai"})
    assert response.status_code == 422
    assert "project_path" in response.text
    assert "provider" in response.text
```

- [x] **Step 2: Write failing approval API test**

```python
def test_approval_response_never_returns_token_or_frozen_secret(local_client, pending_run) -> None:
    response = local_client.get(f"/api/runs/{pending_run}/approval")
    assert response.status_code == 200
    body = response.json()
    assert "token" not in body
    assert body["status"] == "PENDING"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
```

- [x] **Step 3: Run RED tests**

Run: `python -m pytest tests/web/test_api.py tests/unit/test_cli.py -v`

Expected: FAIL because API and CLI modules do not exist.

- [x] **Step 4: Implement service and typed endpoints**

Expose create run, get run, list redacted events, get pending approval, approve, reject, cancel, memory list/delete and credential status endpoints. Local startup binds `127.0.0.1` by default. Public dependencies force the embedded project and mock provider regardless of request body. Map domain errors to stable JSON codes. On approval creation, send the one-time capability only as an `HttpOnly; SameSite=Strict` cookie scoped to the approval endpoint; never include it in JSON or HTML. Approval POSTs require both that cookie and a same-origin CSRF token.

```python
@router.post("/api/runs", status_code=202)
async def create_run(request: CreateRunRequest, service: TaskService = Depends(get_task_service)) -> RunView:
    return RunView.from_snapshot(await service.create(request))
```

- [x] **Step 5: Implement CLI without secret arguments**

Use `argparse`. `credentials set` accepts no Key option and calls `getpass`; `status` prints only configured/source; `clear` asks confirmation unless `--yes`. `run` accepts project and task, prints state transitions and handles approval interactively. Add entry point `safefix = safefix.cli:main`.

- [x] **Step 6: Add API/CLI error tests**

Test missing run, invalid transition, approval rejection, cancellation, local credential missing, public rate/active-run limit, local bind default, hidden input and no plaintext in captured stdout/stderr.

Run: `python -m pytest tests/web/test_api.py tests/unit/test_cli.py -v`

Expected: PASS.

- [x] **Step 7: Review and commit**

Commit: `feat(interface): 添加 API 与命令行入口`

Record the T14 hash and required evidence after both reviews.

---

### Task 15: Local and Public WebUI

**Branch/PR:** `codex/t15-web-ui`

**Required skills:** Use the available frontend-design skill and document the chosen Open Design conventions in the PR and SPEC if the implementation differs from the approved interface.

**Files:**
- Create: `src/safefix/web/templates/base.html`
- Create: `src/safefix/web/templates/index.html`
- Create: `src/safefix/web/templates/run.html`
- Create: `src/safefix/web/templates/settings.html`
- Create: `src/safefix/web/static/app.css`
- Create: `src/safefix/web/static/app.js`
- Create: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: T14 HTML/API routes and redacted view models.
- Produces: accessible task form, run timeline, feedback/diff presentation, approval panel, settings/memory pages and public-demo scenario selector.

- [x] **Step 1: Write failing rendered-page tests**

```python
def test_pending_run_page_explains_risk_and_offers_decision(client, pending_run) -> None:
    response = client.get(f"/runs/{pending_run}")
    assert response.status_code == 200
    assert "CMD_GIT_WRITE" in response.text
    assert "Approve once" in response.text
    assert "Reject" in response.text


def test_public_home_has_no_path_or_key_controls(public_client) -> None:
    response = public_client.get("/")
    assert 'name="project_path"' not in response.text
    assert "API Key" not in response.text
    assert "Dangerous action demo" in response.text
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/web/test_pages.py -v`

Expected: FAIL because templates do not exist.

- [x] **Step 3: Implement the interface shell and task pages**

Use semantic HTML, visible focus, keyboard-operable controls, status text in addition to color, and responsive server-rendered layouts. Use a restrained developer-console visual system rather than a generic chat UI. Render model request, policy reason, tool result and feedback as distinct timeline event types.

- [x] **Step 4: Implement approval and polling behavior**

JavaScript polls only the current run, stops at terminal states, submits server-issued CSRF tokens for approve/reject/cancel, disables controls after submission and shows server errors. The browser sends the HttpOnly approval capability automatically; JavaScript cannot read it. Never render HTML received from model/tool output; insert it as text.

```javascript
const line = document.createElement("pre");
line.textContent = event.redacted_payload;
timeline.appendChild(line);
```

- [x] **Step 5: Add accessibility and redaction tests**

Assert form labels, heading order, live status region, keyboard buttons, escaped model output, no hidden token, no Key values, and local/public field differences. Run a manual keyboard pass and save screenshots for README only if they improve installation or usage explanation.

Run: `python -m pytest tests/web/test_pages.py tests/web/test_api.py -v`

Expected: PASS.

- [x] **Step 6: Review and commit**

Commit: `feat(web): 添加透明可审计的 Web 界面`

Record the T15 hash and required evidence after both reviews.

#### 2026-07-26 中文引导演示完善（Task 3）

- [x] 为稳定 API 错误码补充中文解释，并在用户提示中保留机器码。
- [x] 统一服务端与 JavaScript 状态中文映射，轮询同步更新中文主状态和机器码辅助标签。
- [x] 为服务端事件补齐 `data-sequence`，动态时间线按序号去重并构建同构的中文事件、摘要和技术细节。
- [x] 完整中文化 `keyring`、`secret-file`、`env-file` 凭据来源，未知来源保留原值。
- [x] 增加真实页面响应与可执行 JavaScript 映射回归，更新 CLI/WebUI 及三个公开场景文档。
- [x] 按 Task 3 brief 完成聚焦测试、Ruff 与 DOM 注入 API 扫描；实现提交哈希记录在对应 `task-3-report.md`。

---

### Task 16: Embedded Fixture and Three Deterministic Mechanism Demos

**Branch/PR:** `codex/t16-mechanism-demos`

**Files:**
- Create: `examples/python_bug/calculator.py`
- Create: `examples/python_bug/test_calculator.py`
- Create: `src/safefix/demo.py`
- Create: `tests/integration/test_demo.py`

**Interfaces:**
- Consumes: TaskService, ScriptedMockLLM, temporary-workspace factory.
- Produces: `python -m safefix.demo guardrail|feedback|approval|all`; public-demo scenarios with deterministic event sequences.

- [x] **Step 1: Write failing demo contract tests**

```python
import subprocess
import sys


def test_all_demo_prints_three_passed_scenarios() -> None:
    result = subprocess.run([sys.executable, "-m", "safefix.demo", "all"], text=True, capture_output=True, check=False)
    assert result.returncode == 0
    assert "guardrail: PASS" in result.stdout
    assert "feedback: PASS" in result.stdout
    assert "approval: PASS" in result.stdout
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/integration/test_demo.py -v`

Expected: FAIL because demo module and fixture do not exist.

- [x] **Step 3: Implement isolated fixture creation**

Copy the embedded example into a new temporary directory per scenario. Never mutate files under `examples/python_bug`. Use a known failing implementation and test with deterministic output.

- [x] **Step 4: Implement exact scripted scenarios**

Guardrail: request a permanently denied privilege/destructive process and assert the process spy has zero calls. Feedback: apply a wrong exact replacement, run failing pytest, apply a different correct replacement, then pass final validation. Approval: request Git write, persist/reopen stores, approve frozen action against a spy, then assert changed action and token replay both fail.

```python
SCENARIOS = {
    "guardrail": run_guardrail_demo,
    "feedback": run_feedback_demo,
    "approval": run_approval_demo,
}
```

- [x] **Step 5: Run demos repeatedly**

Run: `python -m safefix.demo all` three times.

Expected each time: exit 0 and exactly three `PASS` summaries with no network or credentials.

Run: `python -m pytest tests/integration/test_demo.py -v`

Expected: PASS.

- [x] **Step 6: Review and commit**

Commit: `feat(demo): 添加确定性 Harness 机制演示`

Record the T16 hash and required evidence after both reviews.

---

### Task 17: Distribution, CI, README and Public-Demo Release

**Branch/PR:** `t17-distribution-ci-docs` / [GitLab !18](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/18)

**Files:**
- Modify: `.gitignore`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `.gitlab-ci.yml`
- Create: `.github/workflows/ci.yml`
- Create: `README.md`
- Create: `tests/integration/test_distribution_metadata.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: complete package, CLI, WebUI and demos.
- Produces: installable package, non-root image, required CI jobs, complete README and deployable public-demo command.

- [x] **Step 1: Write failing delivery-metadata tests**

```python
from pathlib import Path


def test_required_delivery_files_and_ci_job_exist() -> None:
    assert Path("Dockerfile").is_file()
    gitlab = Path(".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "unit-test:" in gitlab
    readme = Path("README.md").read_text(encoding="utf-8")
    for heading in ("Installation", "Usage", "Distribution", "Project Structure", "Security Boundaries", "Known Limitations"):
        assert f"## {heading}" in readme
```

- [x] **Step 2: Run RED test**

Run: `python -m pytest tests/integration/test_distribution_metadata.py -v`

Expected: FAIL because delivery files do not exist.

- [x] **Step 3: Add secret-safe ignore rules and package metadata**

Ignore `.env`, `.env.*` except example templates, private keys, SQLite runtime databases, logs, caches, coverage and build outputs. Include templates/static/example fixture as package data. Define CLI entry point and a public-demo launch command.

- [x] **Step 4: Build a non-root container**

Use Python 3.12 slim, install the package from the reviewed version constraints, create an unprivileged user and writable `/data`, expose the documented port, define a health check and run `safefix serve --public-demo --host 0.0.0.0`. Do not copy `.git`, local databases or environment files.

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir . && useradd --create-home safefix && mkdir /data && chown safefix:safefix /data
ENV SAFEFIX_DATA_DIR=/data
USER safefix
EXPOSE 8000
CMD ["safefix", "serve", "--public-demo", "--host", "0.0.0.0", "--port", "8000"]
```

Run: `docker build -t safefix:test .`

Expected: successful build.

Run: `docker run --rm safefix:test python -m safefix.demo all`

Expected: three PASS scenarios.

- [x] **Step 5: Add both CI systems**

`.gitlab-ci.yml` contains a top-level `unit-test` job that installs the package and runs `python -m pytest`; add lint/type/secret-scan and image-build jobs with explicit dependencies. GitHub Actions runs the same commands on push/PR and publishes a tagged image to GHCR only with protected CI credentials.

```yaml
unit-test:
  image: python:3.12-slim
  script:
    - python -m pip install -e ".[dev]"
    - python -m pytest
```

- [x] **Step 6: Write the required README**

Include project overview, installation, local Keyring setup/status/update/clear, Docker secret and `.env` risk, local WebUI/CLI commands, public demo, distribution commands, directory structure, three-level security boundary, Docker-vs-native limitations, supported platforms, tests/demos, architecture, deployment, third-party licenses and the final public URL after deployment.

- [x] **Step 7: Run full verification and secret scan**

Run: `python -m pytest`

Expected: PASS.

Run the configured linter, type checker and secret scanner over tracked files and Git history.

Expected: zero errors and zero verified secrets.

本地测试、Ruff 与 mypy 已通过。仓库迁移至 [GitHub](https://github.com/Gungnir6/safefix-harness) 后，最终 [GitHub Actions #30192558314](https://github.com/Gungnir6/safefix-harness/actions/runs/30192558314) 全部通过：Linux 801 passed、19 skipped，Ruff、mypy、Gitleaks 和 Docker 镜像构建成功。
迁移前 GitLab MR 首次运行曾暴露学校 Runner 访问 Docker Hub 的可用性问题，且管理员只允许 `always` 拉取策略；保留的 GitLab CI 已改走 Dependency Proxy。
首次人工 WebUI 体验暴露公开演示在 FastAPI 事件循环内嵌套 `asyncio.run()`；真实 API 回归测试先稳定复现 409，再将同步演示移入工作线程。修复后本地完整回归为 819 passed、2 skipped，真实 `feedback` HTTP 请求返回 `SUCCESS`。

- [ ] **Step 8: Deploy and verify public demo**

Deploy the public-demo container to Render without any LLM Key, open the health endpoint and manually run all three scenarios. Record the exact URL in README and save the passing deployment/CI URLs as submission evidence.

阻塞：本机没有 Render CLI/API 凭据，无法代替仓库所有者创建外部服务；`render.yaml` 已按官方 Blueprint 规范准备好。

- [x] **Step 9: Review and commit**

Commit: `build(distribution): 添加可复现分发与持续集成`

Record the T17 hash and required evidence after both reviews.

---

## WebUI 中文引导演示 — 最终整分支审查修复

- [x] 安全边界卡与真实 `guardrail` 提权破坏命令拒绝、零工具调用语义一致。
- [x] 公开设置页明确显示确定性 Mock 且不查询/暗示真实凭据；本地设置页保留真实凭据状态。
- [x] 反馈演示区分初次失败与错误补丁后的再次失败，并验证完整事件序列和摘要。
- [x] 服务端与 JavaScript 覆盖实际审计事件中文标签，未知类型回退原机器码。
- [x] 公开结果页隐藏真实工作区路径；技术详情使用安全 JSON；成功标签按公开/本地模式一致渲染。
- [x] RED：8 failed、23 passed；GREEN：31 passed。
- [x] 最终验证：834 passed、2 skipped；Ruff、mypy、`git diff --check` 全通过。
- [x] 实现提交：`b7c09d9dc9e610184283a82c2b0adf10c75fe108`（`fix(web): 对齐演示文案与真实审计语义`）。

---

## 演示结果清晰度 — Task 2

- [x] RED：先扩展真实 `PublicDemoService` 三场景页面测试与 Node 动态 DOM 行为测试；`tests/web/test_pages.py` 得到 5 failed、18 passed，失败精确指向缺少结论卡、状态徽标/回退和公开 SUCCESS 新文案。
- [x] GREEN：最小实现结论卡、证据网格、服务端/客户端六态时间线和模式化 SUCCESS 文案；`tests/web/test_pages.py` 为 23 passed。
- [x] Web 完整回归：仓库根 `.venv` 下 `pytest tests/web -q` 为 33 passed，伴随 1 条既有 `StarletteDeprecationWarning`；Ruff 全通过；危险 DOM API 扫描无匹配；`git diff --check` 无错误。
- [x] 浏览器验收：真实运行 `guardrail`、`feedback`、`approval` 三场景；桌面端均突出机制结论、关键证据和语义状态，390px 窄屏无横向溢出。
- [x] 最终验证：843 passed、2 skipped；Ruff、mypy、危险 DOM API 扫描和 `git diff --check` 全通过。
- [x] 最终整分支审查：Critical / Important / Minor 均为 0，结论 `Ready to merge: Yes`。

---

## Gate 1: Per-Task Evidence and Review

For every formal task T01–T17:

- [ ] A fresh implementer subagent uses the task's dedicated worktree.
- [ ] RED output is captured before production implementation.
- [ ] GREEN full-task output is captured after minimal implementation and refactor.
- [ ] A spec-compliance reviewer approves or returns concrete issues.
- [ ] A separate code-quality reviewer approves or returns concrete issues.
- [ ] Critical issues are fixed and reverified before merge.
- [ ] The implementation commit and PR identify the subagent; the PR states the student's manual changes.
- [ ] `PLAN.md` is marked with the actual commit hash and `AGENT_LOG.md` receives the required timestamp/task/skill/context/subagent/manual-change/lesson evidence.

## Gate 2: Human-Owned Final Submission

- [ ] Student writes `REFLECTION.md` in Chinese, 1500–2500 Chinese characters, covering every required reflection prompt. AI may only proofread on request, and assistance must be disclosed.
- [ ] Student verifies the NJU Git submission repository/access and, if required, the public GitHub mirror.
- [ ] Student verifies every worktree has a corresponding PR and that commit history is not a single bulk commit.
- [ ] Student verifies `SPEC.md`, `PLAN.md`, `SPEC_PROCESS.md`, `AGENT_LOG.md`, `REFLECTION.md`, `README.md`, source, tests, demos, distribution and CI files are present.
- [ ] Student verifies no real credential exists in working tree, Git history, CI variables shown in logs, screenshots or submitted archives.
- [ ] Student verifies the final CI/CD execution is PASS and the WebUI URL is publicly reachable.
- [ ] Invoke `superpowers:finishing-a-development-branch` and choose merge/PR/keep only after fresh verification output.

## Task Status Ledger

Update this table only with actual commits; do not prefill hashes.

| Task | Status | Implementation commit | PR | Reviews |
|---|---|---|---|---|
| Gate 0 | Completed | — | — | OpenCode + GLM-5.2 cold start; revisions approved |
| T01 | Completed | `755a001`, `eb8f057`; merge `22067fd` | [!1](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/1) | First independent review requested UTC/boundary fixes; second independent review APPROVED (0/0/0); merged and reverified on `main` |
| T02 | Completed (RED evidence caveat) | `d238023`, `f6998f5`, `c335360`; plan `95104ab`; merge `bf675b0` | [!2](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/2) | Spec and quality final reviews APPROVED (0/0/0); exception-chain and traceback-local disclosure findings fixed; merged and reverified on `main` |
| T03 | Completed | `762a073`, `b322870`, `fea9a51`, `1e585fe`, `2be70e4`, `c38f3bc`; plan `a822e82`, `abc0fac`; merge `8b34b10` | [!4](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/4) | Final spec and quality reviews APPROVED (0 Critical / 0 Important / 2 Minor); strict JSON, staged exception classification, bounded feedback and traceback-local disclosure findings fixed; merged and reverified on `main`; mock bare-string and ModelResponse immutability-test Minors deferred |
| T04 | Completed | `91dbd88`, `94c0edd`, `49895de`, `325aa1b`; plan `6031621`, `dad25df`; merge `10cd964` | [!3](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/3) | Final spec and quality reviews APPROVED (0 Critical / 0 Important / 1 Minor); Windows device/ADS, workspace symlink alias and exception-disclosure findings fixed; merged and reverified on `main`; Hypothesis oracle design Minor deferred |
| T05 | Completed | `44c625e`, `1a0d503`, `2204d34`, `3a640c8`, `224d696`, `0da385a`, `6a6ff31`; plan `7b12b89`, `63ac64a`; merge `ff7c17f` | [!5](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/5) | Final spec, security and whole-branch reviews APPROVED (0 Critical / 0 Important / 0 Minor); authorization identity, nested shell/interpreter, normalized system paths, Git side effects, sensitive file transfer and disclosure findings fixed; merged and reverified on `main` |
| T06 | Completed | `65f54d0`, `c1e04c4`, `9067a95`, `12ce19b`; plan `1d0a0c3`, `0ddcc8f`; merge `8699d37` | [!6](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/6) | Final spec, security and whole-branch reviews APPROVED (0 Critical / 0 Important / 2 Minor); metadata/payload disclosure, forged-chain reads, dynamic SQLite types, concurrent/reentrant writes, post-insert trigger tampering and interrupted savepoint cleanup findings fixed; merged and reverified on `main`; read-only reentry scope and long-chain O(N²) Minors deferred |
| T07 | Completed | `a953456`, `06995fd`, `4be5161`, `bb86c20`, `da73da4`, `503abb2`, `f5e45d8`, `95173fc`, `a92e61b`; plan `bbe6298`, `948c02e`; process `dc30b16`, `ae96a21`; merge `d19aa5a` | [!7](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/7) | Final spec, security and whole-branch reviews APPROVED (0 Critical / 0 Important / 1 Minor); persistent frozen-action approval, single-use token digest, expiry/cancel/reject terminal states, transactional audit coupling, concurrency and traceback-local non-disclosure verified; merged and reverified on `main`; different-stripe SQLite contention coverage Minor deferred |
| T08 | Completed | `789cf99`–`3003aa2`; merge `a1a5923` | [!8](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/8) | Independent task and whole-branch reviews approved; 748 passed, 2 platform skips before merge |
| T09 | Completed | `5991852`; merge `0ed3b1e` | [!9](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/9) | Quick-delivery mode, no independent review; fresh branch regression 755 passed, 2 skipped |
| T10 | Completed | `4dfd319`; merge `95d6efd` | [!10](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/10) | Quick-delivery mode, no independent review; fresh branch regression 769 passed, 2 skipped |
| T11 | Completed | `4b691f2`; merge `5cceb3c` | [!11](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/11) | Quick-delivery mode, no independent review; fresh branch regression 778 passed, 2 skipped |
| T12 | Completed | `d8a2f9a`; merge `4867bc3` | [!12](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/12) | Quick-delivery mode, no independent review; fresh branch regression 784 passed, 2 skipped |
| T13 | Completed | `7f19825`; merge `c0e84c7` | [!13](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/13) | Quick-delivery mode, no independent review; 792 passed, 2 skipped; Ruff passed |
| T14 | Completed | `35daee5`; merge `a72afc9` | [!14](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/14) | Quick-delivery mode, no independent review; 800 passed, 2 skipped; Ruff passed |
| T15 | Completed | `a6b9384`; merge `583c15b` | [!15](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/15) | Quick-delivery mode, no independent review; 805 passed, 2 skipped; Ruff and DOM-injection scan passed |
| T16 | Completed / Merged | `0e5afc5` | [!17](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/17) | Quick-delivery mode, no independent review; demos passed three consecutive runs; 811 passed, 2 skipped; Ruff passed |
| T17 | Completed on GitHub main (deploy pending) | `3c405c9`, `8f86cb9`, `61eb61a`, `b259161` | [GitHub main](https://github.com/Gungnir6/safefix-harness) / legacy [!18](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/18) | Quick-delivery mode; [Actions #30192558314](https://github.com/Gungnir6/safefix-harness/actions/runs/30192558314) passed 801 tests with Ruff, mypy, Gitleaks and Docker build; Render deploy remains pending |
| Gate 2 | Pending | — | — | Final submission |

## Usable CLI runtime

### 任务与提交

- Task 1（完整配置与持久化隔离工作区）：`48de56f`；符号链接失败关闭修复 `7e5e499`。RED 为缺少 `default_settings_yaml` 与 `safefix.execution_workspace`；最终定向验证为 63 passed，Ruff 与 mypy 通过。
- Task 2（生产运行时组合根）：`07b9584`；凭据异常图清理、确定性资源关闭和安全退出码修复为 `1c05d31`、`6981331`、`32d6498`。RED 覆盖缺少运行时、稳定项目身份、审计结果和构造失败清理；任务最终定向验证为 33 passed，Ruff 与 mypy 通过。
- Task 3（真实 CLI、审批交互与结果输出）：`15c66bf`；审批交互、安全完整展示修复为 `84651fc`、`05da5b5`。RED 覆盖 parser/template、缺少 runner、拒绝退出码、JSON stdout、动作边界与终端控制字符；任务最终门禁为 72 passed、1 条既有第三方 warning，Ruff 与 mypy 通过。
- Task 4（完整 Mock 修复流程）：`1489907`；动作级 repair budget 与广义占位符失败关闭修复为 `0651614`。RED 覆盖缺失模板/loader、finish 前提前成功、补丁预算和占位符绕过；最终受影响回归为 67 passed、1 条既有第三方 warning，Ruff 与 mypy 通过。
- Task 5（fresh wheel、CI、教程与最终证据）：本提交 `docs(cli): 完成真实运行与分发说明`。新增 Linux CI fresh-wheel smoke、声明 `build>=1.2,<2`、真实中文 CLI 教程、Windows fresh-wheel 与手工旅程证据。fresh wheel 首轮暴露 demo validator 只在 dev extra；新增分发依赖测试先 RED，再把既有 `pytest>=8.3,<9` 约束移到运行时依赖后 GREEN。
- Final review fixes 轮次 1/5：本提交修复连续审批 capability 刷新、原地模式与运行时 SQLite 数据目录边界、普通 CLI 动态终端输出的 Cc/Cf 安全编码，以及审批提示的安全有界 `rule_ids`。精确 RED 覆盖真实双审批/拒绝恢复、解析别名与建库前失败、事件/摘要/JSON/规则显示；受影响回归为 244 unit + 64 integration/web，Ruff 与 6 个变更源码的 mypy 通过。按审查要求未运行全套。

### Task 5 本地证据（2026-07-28 +08:00）

- wheel：仓库根 `.venv` 的 Python 3.12 执行 `-m build --wheel` 成功，生成 `safefix_harness-0.1.0-py3-none-any.whl`。
- fresh Windows venv（审查修复后证据）：所有 smoke 命令前显式移除 `PYTHONPATH`。wheel 及其声明的运行时依赖安装后，`safefix.__file__` 位于 `.smoke-venv/Lib/site-packages` 而非工作树 `src`；packaged `python_bug` 与 `mock_repair.jsonl` 均存在；三个 launcher 均存在，`safefix --help` exit 0，guardrail、feedback、approval 均 PASS，public-demo entry 可从 wheel metadata 加载。
- source-checkout exact Mock journey：`config init`、`config validate` 与真实 `safefix run` exit 0；原 fixture SHA-256 前后相同；隔离副本包含 `return left + right`；输出包含初始验证失败、patch、验证通过、`calculator.py`、`SUCCESS`、结果工作区与审计数据库；未发现 capability/CSRF/approval token、traceback 或 API key。
- packaged Mock journey（无 `PYTHONPATH`）：将 site-packages 内 fixture/script 复制到工作树临时目录后，以 `.smoke-venv` launcher 完成配置、解析与隔离修复；源副本未变，结果工作区含 `return left + right`，审计库存在，输出包含失败→patch→通过→SUCCESS 且无 traceback/capability。
- 第一轮完整门禁：pytest `942 passed, 2 skipped, 1 warning in 58.59s`；warning 是既有 FastAPI TestClient 的 `StarletteDeprecationWarning`。Ruff `All checks passed!`；mypy `Success: no issues found in 33 source files`；三个 demo PASS；`git diff --check` exit 0。
- 清理：删除 `.smoke-venv`、`dist`、`.manual-data`、`.manual-safefix.yaml` 及审查轮的 `.wheel-smoke-*` 前，均解析为当前工作树的严格后代路径；清理后 Git 状态只包含预期跟踪文件。
- Docker：本机 `docker_engine` named pipe 不存在，Docker daemon 未运行，因此没有执行 image build/container demo，也不宣称通过。
- 外部状态：当前提交尚无 GitHub Actions 结果、GitHub Release URL 或 Render 公网 URL；这些仍是集成后需要账户权限的独立步骤。

### 设计验收映射

1. fresh wheel 安装与 `safefix --help`：Task 5，本地通过。
2. `config init` 后立即 `config validate`：Task 1/3/5，本地手工旅程通过。
3. 缺少 key 时安全给出 keyring 配置命令：Task 2/3 自动测试。
4. OpenAI-compatible 测试服务完整运行：Task 2 runtime HTTP Mock 自动测试。
5. 默认不改原项目且结果/审计可定位：Task 1/4/5，自动测试与手工 SHA-256 证据。
6. 只有 `--in-place` 修改原项目：Task 1/3/4 自动测试与 README 警告。
7. 高风险动作等待审批且能力单次消费：Task 3 自动测试。
8. 动作、治理、工具、反馈和最终结果可见：Task 2/3/5，审计自动测试与手工输出。
9. Mock 端到端、provider 传输、隔离与分发入口：Task 2/4/5。
10. pytest/Ruff/mypy/demo/diff 本地通过；Gitleaks 与 Docker jobs 保留在 CI。当前 Docker daemon 不可用，当前提交外部 CI 尚未运行。
11. README 的安装、真实运行、Mock、凭据、隔离、原地和限制说明：Task 5。
12. 公网 WebUI 与 GitHub Release：映射到 Final Gate 的外部状态步骤，当前未完成且未虚构链接。
# T18 — Lightweight Conversational CLI

- [x] 无参数 `safefix` 默认在当前项目启动轻量对话模式。
- [x] 增加 `safefix chat [project]` 和 `safefix setup [project]`。
- [x] 首次配置向导生成可加载配置，并通过现有 keyring 服务隐藏保存 API Key。
- [x] 自然语言任务复用现有 `run_cli`、`TaskService` 和 `AgentLoop`，不建立第二套执行循环。
- [x] 支持 `/help`、`/status`、`/diff`、`/new`、`/exit`，默认保持隔离工作区。
- [x] `run_cli` 提供窄的最终摘要观察接口，供对话状态和只读 diff 使用。
- [x] README 将安装后的首选体验改为 `cd <project>` 后直接运行 `safefix`。
- [x] 完整 pytest、Ruff、mypy、三个机制 demo 和 diff check 最终门禁。
