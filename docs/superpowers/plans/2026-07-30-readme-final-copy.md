# README Final Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the repository README as an accurate, concise final course-delivery entry point for the CLI Release and deployed WebUI.

**Architecture:** This is a documentation-only change. `README.md` remains the single user-facing entry point; the approved design document defines structure and factual boundaries, while existing integration tests and targeted text checks verify required sections, version references, and removal of stale claims.

**Tech Stack:** Markdown, PowerShell, ripgrep, pytest.

## Global Constraints

- Do not change source code, tests, packaging, deployment configuration, or runtime behavior.
- Keep the required headings: Installation, Usage, Distribution, Project Structure, Security Boundaries, Known Limitations.
- Use `v0.1.1`, wheel name `safefix_harness-0.1.1-py3-none-any.whl`, and Python requirement `>=3.12,<3.13`.
- Keep the public repository, Release, and Render WebUI links.
- Describe the public WebUI as a deterministic Mock presentation backed by `PublicDemoService`; do not claim it runs the complete AgentLoop.
- Describe the local production path separately as `TaskService` + AgentLoop + governance + tools + feedback.
- Do not claim the repository lacks `REFLECTION.md` or depends on a final GitLab pipeline.
- Do not imply the Mock scripts understand arbitrary natural-language tasks.

---

### Task 1: Rewrite and verify the final README

**Files:**
- Modify: `README.md`
- Reference: `docs/superpowers/specs/2026-07-30-readme-final-copy-design.md`
- Test: `tests/integration/test_distribution_metadata.py`

**Interfaces:**
- Consumes: the published `v0.1.1` wheel, `safefix-demo all`, `safefix serve --public-demo`, the Render URL, and the existing local CLI commands.
- Produces: a single final-delivery README with accurate links, commands, mechanism descriptions, architecture boundaries, and limitations.

- [ ] **Step 1: Record the stale claims that must disappear**

Run:

```powershell
rg -n "缺少.*REFLECTION|GitLab CI|三个场景均.*AgentLoop|CLI 与 WebUI 复用同一个" README.md
```

Expected: the current README reports all four stale or inaccurate areas.

- [ ] **Step 2: Rewrite `README.md` in the approved order**

Use these sections and exact responsibilities:

1. Title, one-paragraph project summary, and a three-row delivery-entry table for GitHub, v0.1.1 Release, and Render WebUI.
2. `Core Mechanisms`: guardrail blocks before tool execution; feedback changes the next action after objective failure; approval binds one-time capability to a frozen action.
3. `Quick Start`: online WebUI first, then `safefix-demo all`.
4. `Installation`: Python 3.12 Conda and venv commands, followed by source-development installation.
5. `Usage`: local public-demo server and the packaged full Mock repair command.
6. `Credentials`: optional OpenAI-compatible provider and keyring commands.
7. `Architecture`: distinguish public `PublicDemoService` presentation from the local `TaskService`/AgentLoop path.
8. Required Distribution, Project Structure, Security Boundaries, Known Limitations, and Third-Party Licenses sections.

Keep the following exact links:

```text
https://github.com/Gungnir6/safefix-harness
https://github.com/Gungnir6/safefix-harness/releases/tag/v0.1.1
https://safefix-public-demo.onrender.com
```

- [ ] **Step 3: Verify required headings, version, links, and removed claims**

Run:

```powershell
rg -n "^## (Installation|Usage|Distribution|Project Structure|Security Boundaries|Known Limitations)$" README.md
rg -n "v0\\.1\\.1|safefix_harness-0\\.1\\.1-py3-none-any\\.whl|safefix-public-demo\\.onrender\\.com" README.md
rg -n "缺少.*REFLECTION|最终.*GitLab|三个场景均.*AgentLoop|CLI 与 WebUI 复用同一个" README.md
```

Expected: the first command finds all six required headings; the second finds current version and delivery links; the third returns no matches with exit code 1.

- [ ] **Step 4: Run the existing delivery metadata tests**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path "src").Path
.\.venv\Scripts\python.exe -m pytest tests/integration/test_distribution_metadata.py -q
```

Expected: 8 tests pass; the existing third-party Starlette deprecation warning may remain.

- [ ] **Step 5: Check the final Markdown diff**

Run:

```powershell
git diff --check
git diff -- README.md
```

Expected: `git diff --check` exits 0; the README diff contains only the approved wording and organization changes.

- [ ] **Step 6: Commit the README**

```powershell
git add -- README.md docs/superpowers/plans/2026-07-30-readme-final-copy.md
git commit -m "docs: polish final README"
```
