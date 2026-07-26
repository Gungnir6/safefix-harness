# Task 2 实现报告

## 提交

- 功能提交：`9d2390166458e7965978b0f3b7c47137684d0950`
- 提交信息：`feat(web): 突出演示失败与机制结论`

## RED

先修改 `tests/web/test_pages.py`，加入真实 `PublicDemoService` 的 `guardrail`、`feedback`、`approval` 页面测试，并扩展可执行 Node 行为测试。旧实现运行结果为 **5 failed、18 passed**：

- 三个公开场景均缺少机制结论卡、证据网格和服务端状态徽标。
- 动态事件缺少 `data-state` 与 `.event-state`，未知状态未回退。
- 公开模式动态 `SUCCESS` 仍显示旧文案。

失败均由 brief 要求的展示行为缺失引起，不是测试语法或环境错误。

## GREEN 与回归

- 最小实现后 `tests/web/test_pages.py`：**23 passed**。
- `pytest tests/web -q`：**33 passed**，另有 1 条既有第三方 `StarletteDeprecationWarning`。
- `ruff check src/safefix/web tests/web`：`All checks passed!`
- `rg -n "innerHTML|insertAdjacentHTML|document\.write" src/safefix/web`：无匹配。
- `git diff --check`：退出码 0。

以上 Python 命令均使用 `C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe`，未调用系统 Python。

## 修改摘要

- 仅有公开 `presentation` 时渲染高对比机制结论卡和紧凑证据网格。
- 服务端演示事件增加固定六态 `data-state` 和中文文字徽标；缺失或未知状态回退为 `info` / “信息”。
- 客户端轮询事件使用 `createElement`、`textContent` 与 `dataset` 生成同构结构；公开 `SUCCESS` 显示“机制验证通过”，本地仍显示“运行成功”。
- CSS 延续深色工业控制台，红、橙、黄、青蓝、酸性绿、灰分别承担拦截、失败、等待、修正、通过、信息语义；760px 以下结论卡与证据为单列。
- README 和 PLAN 记录结果页能力、RED/GREEN、Web 回归与待验收项。

## 自审

- 未修改核心 demo、策略、审批、工具或路由 API。
- 本地页面不会显示演示结论；恶意 payload 仍由模板转义，动态内容不使用 HTML 字符串插入。
- 事件标题、摘要、时间、机器码与折叠 JSON 详情均保留。
- 未引入框架、外部字体、图标库或其他外部资源；未增加自动播放或绕过既有 reduced-motion 规则。

## 待主代理浏览器验收

- 分别打开三个公开场景，确认中间失败/拦截在未展开技术详情时足够醒目。
- 检查桌面布局和 760px 以下单列布局，确认结论、证据与时间线无横向溢出。
- 确认最终通过只作为机制结论和 `passed` 状态使用酸性绿，其他状态的文字和边框语义可区分。

## 审查修复

### 提交

- 修复提交：`8aa74b24c6b71783f2d87c5eef5c63c5b0c72610`
- 提交信息：`fix(web): 限定公开演示状态样式`

### RED / GREEN

审查指出本地运行也被无条件添加演示 `data-state` / `.event-state`，且工具结果标题的旧酸性绿色会覆盖失败和修正语义。先扩展真实本地页面断言和可执行 Node DOM 行为测试：

- RED：`pytest tests/web/test_pages.py -q` 为 **2 failed、21 passed**。服务端本地页仍有 `data-state`，动态本地工具事件仍有状态属性和“验证失败”徽标。
- GREEN：仅公开模式生成状态属性与徽标后，同一命令为 **23 passed**。

CSS 同步移除工具结果标题的无条件酸性绿；公开事件标题现在跟随固定 state 着色，仅 `passed` 使用酸性绿，本地事件保持既有无状态结构和中性/原有事件类型样式。

### 新鲜验证

- `C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe -m pytest tests/web -q`：**33 passed**，另有 1 条既有第三方 `StarletteDeprecationWarning`。
- `C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe -m ruff check src/safefix/web tests/web`：`All checks passed!`
- `rg -n "innerHTML|insertAdjacentHTML|document\.write" src/safefix/web`：无匹配。
- `git diff --check`：退出码 0。

修复未修改核心 demo、策略、审批、工具或路由 API；未合并、推送或删除工作树。

## 审查修复第二轮

### 提交

- 修复提交：`5a9ae86ae1c1b6fe4a0ff771ed7f6752c193d920`
- 提交信息：`fix(web): 保留本地工具结果样式`

### RED / GREEN

第二轮审查确认第一轮修复删除了本地真实 `TOOL_RESULT` 原有的酸性绿标题样式。先加入页面/CSS 契约测试，验证本地 `TOOL_RESULT` 无 `data-state`、公开 `TOOL_RESULT` 有 `data-state`，并要求本地专用选择器显式存在：

- RED：`pytest tests/web/test_pages.py -q` 为 **1 failed、23 passed**；真实页面结构断言通过，失败精确指向缺少 `.event-tool_result:not([data-state]) .event-meta strong` 规则。
- GREEN：新增这一条本地专用规则后，同一命令为 **24 passed**。

该选择器不会命中公开带 `data-state` 的事件；公开标题继续由显式 state 规则控制，不依赖规则的偶然先后顺序。

### 新鲜验证

- `C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe -m pytest tests/web -q`：**34 passed**，另有 1 条既有第三方 `StarletteDeprecationWarning`。
- `C:\Users\Gungnir\Desktop\safefix-harness\.venv\Scripts\python.exe -m ruff check src/safefix/web tests/web`：`All checks passed!`
- `rg -n "innerHTML|insertAdjacentHTML|document\.write" src/safefix/web`：无匹配。
- `git diff --check`：退出码 0。

本轮只修改 `app.css`、对应页面契约测试和本报告；未合并、推送或删除工作树。
