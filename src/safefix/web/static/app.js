const terminalStates = new Set([
  "SUCCESS", "BLOCKED", "NO_PROGRESS", "BUDGET_EXCEEDED", "FAILED", "CANCELLED"
]);

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
    status.textContent = "Starting governed run…";
    const data = new FormData(runForm);
    const payload = { task: data.get("task") };
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
      status.textContent = `Could not start: ${error.message}`;
      submit.disabled = false;
    }
  });
}

const runHeader = document.querySelector("[data-run-id]");
if (runHeader) {
  const runId = runHeader.dataset.runId;
  const status = document.querySelector("#run-status");

  async function pollRun() {
    try {
      const run = await requestJson(`/api/runs/${encodeURIComponent(runId)}`);
      status.textContent = run.status;
      status.dataset.status = run.status;
      if (!terminalStates.has(run.status) && run.status !== "AWAITING_APPROVAL") {
        window.setTimeout(pollRun, 1500);
      }
    } catch (error) {
      showError(`Polling stopped: ${error.message}`);
    }
  }

  function appendEvents(events) {
    const timeline = document.querySelector("#timeline");
    const seen = new Set([...timeline.querySelectorAll("[data-sequence]")].map((item) => item.dataset.sequence));
    for (const event of events) {
      if (seen.has(String(event.sequence))) continue;
      const item = document.createElement("li");
      item.className = "event";
      item.dataset.sequence = String(event.sequence);
      item.dataset.eventType = event.type;
      const line = document.createElement("pre");
      line.textContent = JSON.stringify(event.payload, null, 2);
      item.appendChild(line);
      timeline.appendChild(item);
    }
  }

  async function refreshEvents() {
    try {
      appendEvents(await requestJson(`/api/runs/${encodeURIComponent(runId)}/events`));
    } catch (error) {
      showError(`Timeline unavailable: ${error.message}`);
    }
  }

  if (runHeader.dataset.terminal !== "true") {
    window.setTimeout(pollRun, 1000);
    window.setTimeout(refreshEvents, 1100);
  }

  document.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", async () => {
      document.querySelectorAll("[data-decision]").forEach((item) => { item.disabled = true; });
      const panel = document.querySelector(".approval-panel");
      try {
        await requestJson(`/api/runs/${encodeURIComponent(runId)}/approval/${button.dataset.decision}`, {
          method: "POST",
          body: JSON.stringify({ csrf_token: panel.dataset.csrf })
        });
        window.location.reload();
      } catch (error) {
        showError(`Decision failed: ${error.message}`);
        document.querySelectorAll("[data-decision]").forEach((item) => { item.disabled = false; });
      }
    });
  });

  document.querySelector("#cancel-run")?.addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try {
      await requestJson(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
      window.location.reload();
    } catch (error) {
      showError(`Cancel failed: ${error.message}`);
      event.currentTarget.disabled = false;
    }
  });
}

document.querySelector("#clear-memory")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await requestJson(`/api/projects/${encodeURIComponent(button.dataset.project)}/memory`, { method: "DELETE" });
    window.location.reload();
  } catch (error) {
    button.textContent = `Clear failed: ${error.message}`;
    button.disabled = false;
  }
});
