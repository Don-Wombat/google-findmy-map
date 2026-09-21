(function () {
  const startBtn = document.getElementById("start-btn");
  const uploadBtn = document.getElementById("upload-btn");
  const uploadFile = document.getElementById("upload-file");
  const phaseEl = document.getElementById("phase");
  const logEl = document.getElementById("log");
  const stepsEl = document.getElementById("steps");
  const waitingHintEl = document.getElementById("waiting-hint");
  const errorBoxEl = document.getElementById("error-box");
  const browserFrameEl = document.getElementById("browser-frame");

  const STEP_ORDER = ["aas_token", "owner_key", "verify"];

  let polling = null;
  let revealed = false;

  function reveal() {
    // Stays hidden until a run has actually started -- an empty, silent
    // black rectangle (or nothing at all) is confusing before there is
    // anything to look at.
    if (revealed) return;
    revealed = true;
    stepsEl.hidden = false;
    waitingHintEl.hidden = false;
    browserFrameEl.hidden = false;
  }

  function renderPhase(phase) {
    phaseEl.textContent = phase;
    phaseEl.className = "phase phase-" + phase;
    const running = phase === "running";
    startBtn.disabled = running;
    uploadBtn.disabled = running;
    waitingHintEl.hidden = !running;
  }

  function renderSteps(status) {
    const log = status.log || [];
    const lastStage = log.length ? log[log.length - 1].stage : null;
    const seen = new Set(log.map((e) => e.stage));

    stepsEl.querySelectorAll(".step").forEach((li) => {
      const stage = li.dataset.stage;
      let state = "pending";
      if (status.phase !== "idle") {
        if (stage === lastStage) {
          if (status.phase === "running") state = "active";
          else if (status.phase === "success") state = "done";
          else state = "failed"; // failed / timed_out
        } else if (seen.has(stage)) {
          state = "done";
        } else if (STEP_ORDER.indexOf(stage) < STEP_ORDER.indexOf(lastStage)) {
          state = "done";
        }
      }
      li.className = "step step-" + state;
    });
  }

  function renderLog(events) {
    logEl.textContent = events
      .map((e) => "[" + e.stage + "] " + e.msg)
      .join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }

  function renderError(status) {
    if (status.error) {
      errorBoxEl.hidden = false;
      errorBoxEl.textContent = "Something went wrong: " + status.error;
    } else {
      errorBoxEl.hidden = true;
    }
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
    if (data.phase !== "idle") reveal();
    renderPhase(data.phase);
    renderSteps(data);
    renderLog(data.log || []);
    renderError(data);
    if (data.phase !== "running" && polling) {
      clearInterval(polling);
      polling = null;
    }
  }

  function watchRun() {
    reveal();
    errorBoxEl.hidden = true;
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
        errorBoxEl.hidden = false;
        errorBoxEl.textContent = "Upload rejected: " + (err.detail || ("HTTP " + res.status));
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
