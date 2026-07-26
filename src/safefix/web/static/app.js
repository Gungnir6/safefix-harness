const terminalStates = new Set([
  "SUCCESS", "BLOCKED", "NO_PROGRESS", "BUDGET_EXCEEDED", "FAILED", "CANCELLED"
]);

const demoStates = new Set([
  "blocked", "failed", "pending", "changed", "passed", "info"
]);

const errorMessages = Object.freeze({
  INVALID_STATE: "当前状态不能执行这个操作",
  RUN_NOT_FOUND: "找不到这次运行",
  PUBLIC_INPUT_FORBIDDEN: "公开演示不接受项目路径或真实模型",
  RATE_LIMITED: "操作太频繁，请稍后再试",
  ACTIVE_RUN_LIMIT: "已有演示正在运行，请稍后再试",
  CSRF_INVALID: "安全校验失败，请刷新页面重试"
});

const statusLabels = Object.freeze({
  CREATED: "已创建",
  RUNNING: "运行中",
  AWAITING_APPROVAL: "等待人工批准",
  SUCCESS: "运行成功",
  BLOCKED: "已被安全策略拦截",
  NO_PROGRESS: "没有取得进展",
  BUDGET_EXCEEDED: "已达到执行预算",
  FAILED: "运行失败",
  CANCELLED: "已取消"
});

const eventLabels = Object.freeze({
  MODEL_REQUEST: "模型请求",
  ACTION: "模型动作",
  POLICY_DECISION: "策略判断",
  TOOL_RESULT: "工具结果",
  APPROVAL_REQUESTED: "已请求审批",
  APPROVAL_APPROVED: "审批已通过",
  APPROVAL_EXPIRED: "审批已过期",
  APPROVAL_REJECTED: "审批已拒绝",
  APPROVAL_CANCELLED: "审批已取消",
  DEMO_EVENT: "演示步骤"
});

const eventSummaries = Object.freeze({
  MODEL_REQUEST: "模型正在请求下一步受治理的动作。",
  ACTION: "模型提出了一个结构化动作，等待策略检查。",
  POLICY_DECISION: "安全策略已完成对动作的判定。",
  TOOL_RESULT: "工具执行结果已返回并进入审计记录。",
  APPROVAL_REQUESTED: "高风险动作已暂停，等待人工审批。",
  APPROVAL_APPROVED: "人工审批已通过，冻结动作可以继续。",
  APPROVAL_EXPIRED: "审批请求已过期，动作不会执行。",
  APPROVAL_REJECTED: "人工审批已拒绝，动作不会执行。",
  APPROVAL_CANCELLED: "审批请求已取消，动作不会执行。",
  DEMO_EVENT: "演示已记录一个确定性步骤。"
});

function explainError(code) {
  const explanation = Object.hasOwn(errorMessages, code) ? errorMessages[code] : null;
  return explanation ? `${explanation} (${code})` : code;
}

function statusLabel(code, publicDemo = false) {
  if (code === "SUCCESS" && publicDemo) return "机制验证通过";
  return statusLabels[code] || code;
}

function eventLabel(code) {
  return eventLabels[code] || code;
}

function eventSummary(type, payload) {
  if (payload?.summary) return payload.summary;
  return Object.hasOwn(eventSummaries, type) ? eventSummaries[type] : null;
}

function demoState(payload) {
  return demoStates.has(payload?.state) ? payload.state : "info";
}

function demoStateLabel(payload) {
  return demoStates.has(payload?.state) && payload?.state_label
    ? payload.state_label
    : "信息";
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error?.code || "REQUEST_FAILED");
  }
  return body;
}

function showError(message) {
  const target = document.querySelector("#action-error");
  if (!target) return;
  target.textContent = message;
  target.hidden = false;
}

const runForm = document.querySelector("#run-form");
if (runForm) {
  runForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = runForm.querySelector("button[type='submit']");
    const status = document.querySelector("#form-status");
    submit.disabled = true;
    status.textContent = "正在启动受治理运行…";
    const data = new FormData(runForm);
    const payload = {
      task: runForm.dataset.public === "true" ? data.get("scenario") : data.get("task")
    };
    if (runForm.dataset.public !== "true") {
      payload.project_path = data.get("project_path");
      payload.provider = data.get("provider");
    }
    try {
      const run = await requestJson("/api/runs", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      window.location.assign(`/runs/${encodeURIComponent(run.run_id)}`);
    } catch (error) {
      status.textContent = `无法启动运行：${explainError(error.message)}`;
      submit.disabled = false;
    }
  });
}

const runHeader = document.querySelector("[data-run-id]");
if (runHeader) {
  const runId = runHeader.dataset.runId;
  const status = document.querySelector("#run-status");
  const statusCode = document.querySelector("#run-status-code");

  function updateRunStatus(code) {
    status.textContent = statusLabel(code, runHeader.dataset.public === "true");
    status.dataset.status = code;
    if (statusCode) {
      statusCode.textContent = `机器码 · ${code}`;
    }
  }

  async function pollRun() {
    try {
      const run = await requestJson(`/api/runs/${encodeURIComponent(runId)}`);
      updateRunStatus(run.status);
      await refreshEvents();
      if (!terminalStates.has(run.status) && run.status !== "AWAITING_APPROVAL") {
        window.setTimeout(pollRun, 1500);
      }
    } catch (error) {
      showError(`状态轮询已停止：${explainError(error.message)}`);
    }
  }

  function appendEvents(events) {
    const timeline = document.querySelector("#timeline");
    const seen = new Set([...timeline.querySelectorAll("[data-sequence]")].map((item) => item.dataset.sequence));
    for (const event of events) {
      if (seen.has(String(event.sequence))) continue;
      timeline.querySelector(".empty-state")?.remove();
      const item = document.createElement("li");
      item.className = "event";
      item.dataset.sequence = String(event.sequence);
      item.dataset.eventType = event.type;
      item.dataset.state = demoState(event.payload);
      seen.add(String(event.sequence));

      const index = document.createElement("div");
      index.className = "event-index";
      index.textContent = String(event.sequence).padStart(2, "0");

      const body = document.createElement("div");
      body.className = "event-body";

      const meta = document.createElement("div");
      meta.className = "event-meta";
      const label = document.createElement("strong");
      label.textContent = eventLabel(event.type);
      const state = document.createElement("span");
      state.className = "event-state";
      state.textContent = demoStateLabel(event.payload);
      const timestamp = document.createElement("time");
      timestamp.textContent = event.created_at || "";
      meta.appendChild(label);
      meta.appendChild(state);
      meta.appendChild(timestamp);
      body.appendChild(meta);

      const summaryText = eventSummary(event.type, event.payload);
      if (summaryText) {
        const summary = document.createElement("p");
        summary.className = "event-summary";
        summary.textContent = summaryText;
        body.appendChild(summary);
      }

      const details = document.createElement("details");
      const detailsLabel = document.createElement("summary");
      detailsLabel.textContent = "查看技术细节";
      const line = document.createElement("pre");
      line.textContent = JSON.stringify(event.payload, null, 2);
      details.appendChild(detailsLabel);
      details.appendChild(line);
      body.appendChild(details);

      item.appendChild(index);
      item.appendChild(body);
      timeline.appendChild(item);
    }
  }

  async function refreshEvents() {
    try {
      appendEvents(await requestJson(`/api/runs/${encodeURIComponent(runId)}/events`));
    } catch (error) {
      showError(`暂时无法刷新时间线：${explainError(error.message)}`);
    }
  }

  if (runHeader.dataset.terminal !== "true") {
    window.setTimeout(pollRun, 1000);
  }

  document.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", async () => {
      const originalLabel = button.textContent;
      document.querySelectorAll("[data-decision]").forEach((item) => { item.disabled = true; });
      button.textContent = "正在提交决定…";
      const panel = document.querySelector(".approval-panel");
      try {
        await requestJson(`/api/runs/${encodeURIComponent(runId)}/approval/${button.dataset.decision}`, {
          method: "POST",
          body: JSON.stringify({ csrf_token: panel.dataset.csrf })
        });
        window.location.reload();
      } catch (error) {
        showError(`提交决定失败：${explainError(error.message)}`);
        button.textContent = originalLabel;
        document.querySelectorAll("[data-decision]").forEach((item) => { item.disabled = false; });
      }
    });
  });

  document.querySelector("#cancel-run")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "正在取消…";
    try {
      await requestJson(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
      window.location.reload();
    } catch (error) {
      showError(`取消运行失败：${explainError(error.message)}`);
      button.textContent = originalLabel;
      button.disabled = false;
    }
  });
}

document.querySelector("#clear-memory")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "正在清除…";
  try {
    await requestJson(`/api/projects/${encodeURIComponent(button.dataset.project)}/memory`, { method: "DELETE" });
    window.location.reload();
  } catch (error) {
    button.textContent = `清除失败：${explainError(error.message)}`;
    button.disabled = false;
    window.setTimeout(() => {
      button.textContent = originalLabel;
    }, 3000);
  }
});
