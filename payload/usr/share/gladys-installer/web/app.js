(() => {
  "use strict";

  const translations = window.GLADYS_SETUP;
  const ui = translations.ui;
  const phases = translations.phases;
  const message = document.getElementById("message");
  const detail = document.getElementById("detail");
  const percent = document.getElementById("percent");
  const track = document.getElementById("track");
  const progress = document.getElementById("progress");
  const connection = document.getElementById("connection");
  const connectionLabel = document.getElementById("connection-label");
  const stepElements = Array.from(document.querySelectorAll("[data-step]"));
  let centeredStage = null;

  const phaseOrder = [
    "PREFLIGHT",
    "WAIT_NETWORK",
    "APT_UPDATE",
    "INSTALL_PACKAGES",
    "START_SYSTEM_SERVICES",
    "DOCKER_PREFLIGHT",
    "PULL_GLADYS",
    "START_GLADYS",
    "VERIFY_GLADYS",
    "START_WATCHTOWER",
    "FINALIZE",
    "READY",
  ];

  const stages = {
    system: ["PREFLIGHT"],
    network: ["WAIT_NETWORK"],
    packages: ["APT_UPDATE", "INSTALL_PACKAGES", "START_SYSTEM_SERVICES"],
    docker: ["DOCKER_PREFLIGHT"],
    download: ["PULL_GLADYS"],
    services: ["START_GLADYS", "VERIFY_GLADYS", "START_WATCHTOWER", "FINALIZE"],
  };

  function setConnection(isLive) {
    connection.classList.toggle("is-live", isLive);
    connection.classList.toggle("is-offline", !isLive);
    connectionLabel.textContent = isLive ? ui.live : ui.connection_lost;
  }

  function setSteps(phase) {
    const current = Math.max(0, phaseOrder.indexOf(phase));
    stepElements.forEach((element) => {
      const stagePhases = stages[element.dataset.step];
      const indexes = stagePhases.map((item) => phaseOrder.indexOf(item));
      const active = stagePhases.includes(phase);
      const complete = phase === "READY" || current > Math.max(...indexes);
      const status = complete
        ? ui.status_complete
        : active
          ? ui.status_active
          : ui.status_pending;
      element.classList.toggle("is-complete", complete);
      element.classList.toggle("is-active", active);
      element.classList.toggle("is-pending", !active && !complete);
      element.setAttribute("aria-label", `${element.textContent.trim()}: ${status}`);
      if (active) element.setAttribute("aria-current", "step");
      else element.removeAttribute("aria-current");
    });

    const activeStep = stepElements.find((element) =>
      element.classList.contains("is-active"),
    );
    if (activeStep && centeredStage !== activeStep.dataset.step) {
      centeredStage = activeStep.dataset.step;
      activeStep.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "center",
      });
    }
  }

  function detailFor(phase) {
    if (phase === "PULL_GLADYS") return ui.download_note;
    if (phase === "WAIT_NETWORK") return ui.network_body;
    if (phase === "FATAL") return ui.fatal_hint;
    if (phase === "READY") return `${ui.open_gladys}: gladysassistant.local`;
    return ui.keep_connected;
  }

  function render(state) {
    const bounded = Math.max(0, Math.min(100, Number(state.progress) || 0));
    const phase = Object.prototype.hasOwnProperty.call(phases, state.phase)
      ? state.phase
      : "PREFLIGHT";
    const active = phase !== "READY" && phase !== "FATAL";

    document.body.dataset.phase = phase;
    document.body.dataset.active = String(active);
    message.textContent = phases[phase];
    detail.textContent = detailFor(phase);
    percent.textContent = `${bounded}%`;
    progress.style.width = `${bounded}%`;
    track.setAttribute("aria-valuenow", String(bounded));
    setSteps(phase);
    setConnection(true);
  }

  async function refresh() {
    try {
      const response = await fetch("/status.json", { cache: "no-store" });
      if (!response.ok) throw new Error("status endpoint unavailable");
      render(await response.json());
      window.setTimeout(refresh, 1250);
    } catch (_) {
      setConnection(false);
      window.setTimeout(() => window.location.reload(), 3000);
    }
  }

  refresh();
})();
