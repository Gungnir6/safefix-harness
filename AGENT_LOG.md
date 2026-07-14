# AGENT_LOG

本文件只记录课程要求的关键过程证据：task 编号与时间戳、Superpowers 技能、关键 prompt/context、subagent 产出或 commit、人工修改及原因、经验总结。普通命令与无关环境信息不记录。

正式记录从 `PLAN.md` 任务执行阶段开始。

## T01 — Package Foundation and Typed Domain Model

- 时间：2026-07-14 14:35–17:18 +08:00
- 分支 / MR：`codex/t01-domain-foundation` / [GitLab !1](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/1)
- Superpowers：`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`systematic-debugging`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：实现代理仅获得 T01 task brief、worktree 路径、基线 `1cabadb`、允许文件、严格 RED→GREEN、报告路径，以及“全部提交使用中文 Conventional Commits”的全局约束；评审代理获得同一 brief、实现报告与完整 `BASE..HEAD` 只读差异范围。
- 实现代理产出：`755a001 feat(domain): 添加 SafeFix 类型化领域模型`。生产代码前，指定测试因 `ModuleNotFoundError: safefix.domain` 精确 RED；初始 GREEN 为 2 passed，补齐领域契约后为 51 passed。
- 第一次独立合并评审：Critical 0、Important 1、Minor 1。Important 指出 `deadline_at`、`RunSnapshot.created_at/updated_at` 接受 naive datetime，违反 UTC 契约并会阻塞后续时间比较；Minor 指出 action 默认值和主要边界缺少回归保护。
- 评审修复：先运行评审测试得到 `25 passed, 6 failed`；6 个失败精确证明 naive datetime 未拒绝且非 UTC aware 值未规范化。修复提交为 `eb8f057 fix(domain): 强化 UTC 时间与动作边界验证`；目标 GREEN 为 `31 passed, 51 deselected`，完整测试为 82 passed。
- 第二次独立合并评审：`APPROVED`，Critical 0、Important 0、Minor 0；确认上次 UTC 与动作边界问题均关闭，未发现 T02–T17 接口阻塞。
- 根代理新鲜验证：Python 3.12.3；82 passed；Ruff check/format、mypy、pip check、`git diff --check` 全部 exit 0；过程文档提交前的实现差异仅含 `pyproject.toml`、`src/safefix/__init__.py`、`src/safefix/domain.py`、`tests/unit/test_domain.py`。
- 人工裁决：批准 Hatchling src-layout 映射 `packages = ["src/safefix"]`；确认 `StopDecision.code: RunStatus`、`RunSnapshot.stop_reason: str | None`；仅对 SPEC 明确要求 UTC 的 3 个字段增加约束，避免扩大范围。学生随后将仓库目录改名为 `safefix-harness`，editable `.pth` 变为纯 ASCII，原 Windows GBK/中文路径冲突消失。
- 经验：构建清单必须显式声明发行名到 src 包的映射；“UTC 时间戳”和精确默认值/边界必须进入可执行测试，单靠字段列表不足以防止冷启动代理产生可运行但不兼容后续任务的模型。
