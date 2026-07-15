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
- 合并结果：MR !1 合并到 `main`，合并提交 `22067fd`；在主工作区重新创建 Python 3.12.3 `.venv` 后复跑为 82 passed，Ruff check/format、mypy、pip check 全部 exit 0。
- 人工裁决：批准 Hatchling src-layout 映射 `packages = ["src/safefix"]`；确认 `StopDecision.code: RunStatus`、`RunSnapshot.stop_reason: str | None`；仅对 SPEC 明确要求 UTC 的 3 个字段增加约束，避免扩大范围。学生随后将仓库目录改名为 `safefix-harness`，editable `.pth` 变为纯 ASCII，原 Windows GBK/中文路径冲突消失。
- 经验：构建清单必须显式声明发行名到 src 包的映射；“UTC 时间戳”和精确默认值/边界必须进入可执行测试，单靠字段列表不足以防止冷启动代理产生可运行但不兼容后续任务的模型。

## T02 — Strict YAML Configuration

- 时间：2026-07-15 15:45–17:44 +08:00
- 分支 / MR：`codex/t02-configuration` / [GitLab !2](https://git.nju.edu.cn/Gungnir/safefix-harness/-/merge_requests/2)
- Superpowers：`using-superpowers`、`using-git-worktrees`、`subagent-driven-development`、`test-driven-development`、`dispatching-parallel-agents`、`requesting-code-review`、`receiving-code-review`、`verification-before-completion`。
- 关键 prompt/context：实现代理 Wegener 仅获得 T02 task brief、工作树、基线 `95104ab`、三个允许文件、严格 RED→GREEN、中文 Conventional Commit 与报告契约；规格评审 Volta 和代码质量评审 Planck 分别获得同一 brief、实现报告和冻结的完整 `BASE..HEAD` 差异包，并保持只读。
- 恢复与实现：续接时三个目标文件已有未提交 WIP，且原代理和原始 RED 输出不可恢复；实现代理没有伪造历史，只证明基线不存在 `safefix.config`，并对恢复后发现的 `args` 精确类型、非法 UTF-8 转换和静态类型问题记录真实 RED→GREEN。实现提交为 `d238023 feat(config): 添加严格声明式配置`，初次全套验证为 128 passed。
- 第一轮独立评审：代码质量评审为 0/0/0；规格评审发现 Critical 1：`raise ConfigError(...) from exc` 会让完整 traceback 通过 Pydantic/YAML 原始异常链泄露秘密或源文本。
- 人工裁决与第一轮修复：学生确认全局“秘密不进入日志”约束优先于 brief 的 `raise ... from exc` 示例。修复先得到两项 traceback 测试失败，再于异常上下文外抛出纯净 `ConfigError`，提交 `f6998f5 fix(config): 阻断配置异常链泄密`；复审确认 cause/context 问题关闭，但代码质量评审进一步发现 Important 1：启用 `capture_locals` 时 traceback frame 的 `raw` 仍持有完整配置。
- 第二轮修复：先新增 capture-locals 回归测试并得到 1 failed，再在 non-mapping 与 Pydantic 两个错误出口抛错前删除 `raw`，提交 `c335360 fix(config): 清理异常帧敏感配置`；测试同时验证 sentinel 不泄露、唯一 loader frame 不含 `raw`，并排除测试自身局部变量假阳性。
- 最终独立复审：规格与代码质量评审均 `APPROVED`，Critical 0、Important 0、Minor 0；确认精确 schema、错误安全边界、示例配置、三文件范围以及 message/cause/context/frame-locals 四层非披露契约均满足。
- 根代理新鲜验证：异常安全聚焦测试 3 passed；完整测试 131 passed；Ruff check/format、mypy、pip check、`git diff --check` 全部 exit 0；工作树在过程文档修改前干净。
- 过程证据边界：T02 原始 WIP 前是否实际运行 pytest RED 无法证明，`PLAN.md` 对应 Gate checkbox 保持未勾选；该缺口不影响最终代码评审结论，但保留为课程过程可追溯性风险。
- 经验：安全错误不能只净化 `str(error)`；还必须审计异常 cause/context、默认 traceback 和 capture-locals frame。恢复任务时应先落盘 RED/GREEN 证据与实现报告，避免正确工作因会话中断失去过程证明。
