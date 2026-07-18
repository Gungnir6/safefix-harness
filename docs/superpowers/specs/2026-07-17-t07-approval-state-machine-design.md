# T07 持久化人工审批状态机设计

## 1. 目标与范围

T07 实现一个 SQLite 持久化的 `ApprovalStateMachine`，用于冻结需要人工审批的原始 `Action`，签发单次明文能力令牌，并保证批准、拒绝、过期和取消都是不可逆的终态转换。

本任务只新增：

- `src/safefix/governance/approvals.py`
- `tests/unit/test_approvals.py`

现有 `ApprovalRequest`、`ApprovalStatus`、`Action`、`RiskLevel`、`action_digest()` 和 `AuditStore` 保持不变。T07 不执行工具、不生成 AgentLoop 反馈、不实现 HTTP Cookie/CSRF，也不修改 T06 的普通 SHA-256 审计链算法。

## 2. 已批准的核心决策

审批表与审计表必须使用同一个 `sqlite3.Connection`。`ApprovalStateMachine` 只接收该连接，并在内部用同一连接构造 `AuditStore`；公共 API 不接受另一个审计连接或外部 `AuditStore`，从结构上避免跨连接的伪原子操作。

每个请求或状态转换都在一个唯一外层 SQLite SAVEPOINT 中完成：

1. 严格读取并验证当前审批记录；
2. 插入或条件更新审批状态；
3. 通过内部 `AuditStore` 追加对应审计事件；
4. 重新读取审批记录并验证后置条件；
5. 成功后释放 SAVEPOINT，否则回滚审批与审计两侧。

如果调用方已经开启外层事务，T07 只管理自己的 SAVEPOINT，不主动提交、回滚或关闭调用方连接。调用方随后回滚外层事务时，审批与审计会一起回滚。

## 3. 公共接口

### 3.1 值对象

`ApprovalChallenge` 是冻结、带 slots 的 dataclass：

- `id: str`
- `token: str`，字段 `repr=False`
- `request: ApprovalRequest`

明文令牌只在 `request()` 返回的 challenge 中出现一次。SQLite、审计事件、异常和默认 `repr` 均不得包含明文令牌。

### 3.2 状态机

```python
ApprovalStateMachine(
    connection: sqlite3.Connection,
    *,
    configured_secret_values: Iterable[str] = (),
    clock: Callable[[], datetime] = utc_now,
)

request(
    run_id: str,
    action: Action,
    risk_level: RiskLevel,
    rule_ids: tuple[str, ...],
    ttl_seconds: int,
) -> ApprovalChallenge

get(approval_id: str) -> ApprovalRequest
approve(approval_id: str, plaintext_token: str, action: Action) -> ApprovalRequest
reject(approval_id: str, plaintext_token: str) -> ApprovalRequest
cancel(approval_id: str) -> ApprovalRequest
expire_pending(now: datetime) -> tuple[ApprovalRequest, ...]
```

`request()` 的参数形式与 `PLAN.md` 的锁定示例一致。风险等级和规则编号来自 `PolicyDecision(REQUIRE_APPROVAL)`；动作原因和动作类型从冻结 Action 中提取。T12/T16 可在调用前验证完整 `PolicyDecision` 的 outcome。

`get()` 只返回内部领域模型，后续 API 层仍必须显式排除 `one_time_token_hash` 和 `frozen_action_json`。

### 3.3 稳定错误

`approvals.py` 暴露以下无数据错误类型，公开消息固定，不包含 ID、令牌、动作 JSON、SQL 或底层异常：

- `ApprovalUnavailable`
- `ApprovalNotFound`
- `InvalidApprovalToken`
- `ActionMismatch`
- `ApprovalAlreadyUsed`
- `ApprovalExpired`
- `InvalidApprovalTransition`

普通异常在离开携带敏感局部变量的内部帧后转换为稳定错误，`__cause__` 和 `__context__` 为空。`KeyboardInterrupt` 与 `SystemExit` 必须先清理 SAVEPOINT 和重入状态，再原样传播。

## 4. SQLite 数据模型

表 `approval_requests` 至少包含：

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL`
- `action_hash TEXT NOT NULL`
- `status TEXT NOT NULL`
- `one_time_token_hash TEXT NOT NULL UNIQUE`
- `frozen_action_json TEXT NOT NULL`
- `action_type TEXT NOT NULL`
- `risk_level TEXT NOT NULL`
- `rule_ids TEXT NOT NULL`，保存排序后的紧凑 JSON 字符串数组
- `created_at TEXT NOT NULL`
- `expires_at TEXT NOT NULL`
- `decided_at TEXT`

增加 `(status, expires_at)` 索引支持 `expire_pending()`。构造器像 T06 一样验证同名对象确实是表、列签名符合预期；坏 schema 稳定 fail closed。

SQLite 读取不使用 `str()`、`int()` 等修复坏数据。所有文本、时间、状态、枚举、64 位小写十六进制哈希、规范 JSON 和 NULL 约束都按原始类型严格验证。数据库被直接注入或 trigger 改写后，不得构造看似正常的 `ApprovalRequest`。

## 5. 令牌与冻结动作

- 审批 ID 使用随机 UUID。
- 明文令牌使用 `secrets.token_urlsafe(32)`。
- SQLite 只保存 `sha256(token.encode("utf-8")).hexdigest()`。
- 令牌验证使用 `hmac.compare_digest()`。
- 冻结动作 JSON使用 `action.model_dump_json(exclude_none=True)`，并要求其 SHA-256 与 `action_digest(action)` 完全一致。
- `approve()` 先验证令牌，再比较传入动作的 digest 和规范 JSON；摘要或内容任一不一致都抛出 `ActionMismatch`。
- 错误令牌、动作不匹配和普通存储失败都不消耗 PENDING 请求。
- configured secret 的 exact 或 substring 若出现在 run ID、冻结动作 JSON 或即将持久化的自由字符串中，操作稳定失败；不会尝试修改冻结动作，因为批准必须绑定原始动作。

## 6. 状态转换

允许的转换只有：

```text
PENDING -> APPROVED
PENDING -> REJECTED
PENDING -> EXPIRED
PENDING -> CANCELLED
```

所有终态都没有出边。

### 6.1 request

验证 TTL 为正整数、run ID 非空、规则编号是非空字符串元组、风险等级精确为 `RiskLevel.MEDIUM`、冻结动作不含 configured secret。LOW 动作不应进入审批，HIGH 动作必须由策略永久拒绝。生成 ID 与令牌后插入 PENDING 记录，追加 `APPROVAL_REQUESTED` 审计事件，并在返回 challenge 前重读记录。

### 6.2 approve

严格读取记录。终态请求抛 `ApprovalAlreadyUsed`；已过期的 PENDING 请求在同一操作内转换为 EXPIRED、写入审计后抛 `ApprovalExpired`。未过期时验证令牌和冻结动作，再执行 `UPDATE ... WHERE status = 'PENDING'`。更新行数必须恰为 1，随后追加 `APPROVAL_APPROVED`，重读确认终态和 `decided_at`。

### 6.3 reject

拒绝也需要一次性令牌。验证成功后条件更新为 REJECTED，追加 `APPROVAL_REJECTED` 并返回请求。T12 根据 REJECTED 状态生成固定人工拒绝反馈，T07 不接受或持久化自由文本反馈。

### 6.4 cancel

取消由受信任的运行取消流程调用，不要求能力令牌。只有 PENDING 可转为 CANCELLED；追加 `APPROVAL_CANCELLED`。终态再次取消抛 `InvalidApprovalTransition`。

### 6.5 expire_pending

要求传入 aware datetime，并规范化为 UTC。一次读取所有 `expires_at <= now` 的 PENDING 请求，按 ID 稳定排序，在同一外层 SAVEPOINT 中逐条条件更新并追加 `APPROVAL_EXPIRED`。任何一条审计失败则整批回滚，避免部分过期。

## 7. 审计契约

审计事件 payload 只包含以下安全、结构化字段：

- `approval_id`
- `status`
- `action_hash`
- `risk_level`
- `rule_ids`

不写入明文令牌、令牌摘要、冻结动作 JSON、动作原因或自由文本。

事件类型固定为：

- `APPROVAL_REQUESTED`
- `APPROVAL_APPROVED`
- `APPROVAL_REJECTED`
- `APPROVAL_EXPIRED`
- `APPROVAL_CANCELLED`

内部 AuditStore 与审批表共用连接，因此其嵌套 SAVEPOINT 位于审批操作的外层 SAVEPOINT 中。审计 append 失败、写入后被 trigger 删除/改写、链已损坏或中断时，审批状态不得成功转换。

## 8. 并发与重入

`approvals.py` 使用按 connection identity 选择的固定数量全局 striped `RLock`，不持有连接强引用。同一连接上的多个状态机实例串行化完整操作；不同连接访问同一 SQLite 文件时依靠条件更新、SQLite 写锁和后置条件保证最多一个批准成功。

增加 thread-local connection-id 活跃集合，拒绝同线程通过自定义 Connection、trigger/UDF 回调重入任何写操作，避免内层返回成功后被外层 SAVEPOINT 回滚。只读 `get()` 也在同一连接锁内执行并返回单次严格 snapshot。

## 9. 测试策略

全部测试使用真实 SQLite，不用 mock 代替状态转换或事务行为。

1. 首个 RED：动作替换与令牌重放测试因模块不存在失败。
2. 请求：只存 token digest、challenge repr 隐藏 token、冻结 JSON/digest 精确、秘密不落库。
3. 状态：批准、拒绝、取消、自动过期、批量过期、所有终态不可转换。
4. 令牌：错误、exact replay、跨请求 token、动作 digest/JSON 不一致。
5. 持久化：关闭并重开连接后仍能用原 challenge 批准。
6. 并发：同连接同/多实例、不同连接同文件两次批准恰有一次成功，成功结果实际持久且审计链有效。
7. 审计原子性：通过真实 SQLite trigger 让 audit INSERT 失败、删除或改写，审批状态保持 PENDING；审批后置条件被 trigger 改写时整笔回滚。
8. 外层事务：失败只回滚内部 SAVEPOINT并保留调用方 sentinel；成功审批与审计随外层事务一起提交或回滚。
9. 恶意存储：坏 schema、SQLite 动态类型、非法状态/时间/JSON、篡改冻结动作和哈希均 fail closed。
10. 异常安全：固定 message、无 cause/context、traceback frame 不保留明文 token/动作 JSON；`KeyboardInterrupt/SystemExit` 清理后原样传播并可恢复。
11. 门禁：T07 聚焦、完整 pytest、Ruff check/format、mypy 和 `git diff --check`。

## 10. 非目标与后续集成

- T07 不执行批准后的 Action；T12 只在 `approve()` 返回已验证的 APPROVED 请求后恢复冻结动作。
- T07 不把拒绝理由作为自由文本持久化；T12 生成固定拒绝反馈，避免秘密进入 SQLite。
- T07 不返回适合 Web API 的 DTO；T16 必须排除 token hash 和 frozen JSON，并使用 HttpOnly、SameSite=Strict Cookie 与同源 CSRF。
- T07 不负责提交调用方外层事务。服务层必须在向外暴露 challenge 或批准成功前完成其拥有的事务提交。
- 普通 SHA-256 token digest 满足计划要求；令牌具有至少 256 位随机熵，数据库泄漏后的离线猜测不在现实可行范围内。
