(function () {
  const startBtn = document.getElementById("start-btn");
  const phaseEl = document.getElementById("phase");
  const logEl = document.getElementById("log");

  let polling = null;

  function renderPhase(phase) {
    phaseEl.textContent = phase;
    phaseEl.className = "phase phase-" + phase;
    startBtn.disabled = phase === "running";
  }

  function renderLog(events) {
    logEl.textContent = events
      .map((e) => "[" + e.stage + "] " + e.msg)
      .join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function poll() {
    let data;
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      data = await res.json();
    } catch {
      return;
    }
    renderPhase(data.phase);
    renderLog(data.log || []);
    if (data.error) {
      logEl.textContent += "\n[error] " + data.error;
    }
    if (data.phase !== "running" && polling) {
      clearInterval(polling);
      polling = null;
    }
  }

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    try {
      const res = await fetch("/api/start", { method: "POST" });
      if (res.status === 409) {
        startBtn.disabled = false;
        return;
      }
    } catch {
      startBtn.disabled = false;
      return;
    }
    if (!polling) {
      polling = setInterval(poll, 1500);
    }
    poll();
  });

  poll();
  polling = setInterval(poll, 1500);
})();
