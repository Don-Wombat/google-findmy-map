(function () {
  const startBtn = document.getElementById("start-btn");
  const uploadBtn = document.getElementById("upload-btn");
  const uploadFile = document.getElementById("upload-file");
  const phaseEl = document.getElementById("phase");
  const logEl = document.getElementById("log");

  let polling = null;

  function renderPhase(phase) {
    phaseEl.textContent = phase;
    phaseEl.className = "phase phase-" + phase;
    const running = phase === "running";
    startBtn.disabled = running;
    uploadBtn.disabled = running;
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

  function watchRun() {
    if (!polling) {
      polling = setInterval(poll, 1500);
    }
    poll();
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
    watchRun();
  });

  uploadBtn.addEventListener("click", async () => {
    const file = uploadFile.files[0];
    if (!file) return;
    uploadBtn.disabled = true;
    try {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch("/api/upload", { method: "POST", body });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        logEl.textContent = "[upload] " + (err.detail || ("HTTP " + res.status));
        uploadBtn.disabled = false;
        return;
      }
    } catch {
      uploadBtn.disabled = false;
      return;
    }
    watchRun();
  });

  poll();
  polling = setInterval(poll, 1500);
})();
