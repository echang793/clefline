"use strict";

const $ = (id) => document.getElementById(id);
const PART_NAMES = { sax: "Alto Sax", keys: "Keyboard", drums: "Drumset" };
const KEY_NAMES = {
  "-7": "7 flats", "-6": "6 flats", "-5": "5 flats", "-4": "4 flats",
  "-3": "3 flats", "-2": "2 flats", "-1": "1 flat", "0": "No sharps or flats",
  "1": "1 sharp", "2": "2 sharps", "3": "3 sharps", "4": "4 sharps",
  "5": "5 sharps", "6": "6 sharps", "7": "7 sharps",
};

let chosen = null;      // the YouTube candidate we will transcribe
let poller = null;

function show(id, visible) { $(id).hidden = !visible; }

function setError(id, message) {
  const node = $(id);
  node.textContent = message || "";
  node.hidden = !message;
}

function seconds(value) {
  if (!value) return "";
  const total = Math.round(value);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function candidateMarkup(candidate) {
  const thumb = candidate.thumbnail
    ? `<img src="${candidate.thumbnail}" alt="">`
    : `<img alt="">`;
  return `${thumb}
    <div>
      <div class="meta-title">${escapeHtml(candidate.title)}</div>
      <div class="meta-sub">${escapeHtml(candidate.uploader || "")}${
        candidate.duration ? " · " + seconds(candidate.duration) : ""
      }${candidate.note ? " · " + escapeHtml(candidate.note) : ""}</div>
    </div>`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// ---------------------------------------------------------------- resolve

async function find() {
  const url = $("url").value.trim();
  if (!url) return;
  setError("input-error", "");
  $("find").disabled = true;
  $("find").textContent = "Looking…";
  try {
    const response = await fetch("/api/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not read that link");
    renderResolved(data);
  } catch (error) {
    setError("input-error", error.message);
  } finally {
    $("find").disabled = false;
    $("find").textContent = "Find";
  }
}

function renderResolved(data) {
  chosen = data.chosen;
  $("match").innerHTML = candidateMarkup(data.chosen);

  if (data.requested) {
    $("requested").innerHTML = `
      <img src="${data.requested.thumbnail || ""}" alt="">
      <div>
        <div class="meta-title">${escapeHtml(data.requested.title)}</div>
        <div class="meta-sub">From Spotify · ${escapeHtml(data.requested.artist)} · ${seconds(
      data.requested.duration
    )}</div>
      </div>`;
    show("requested", true);
  } else {
    show("requested", false);
  }

  const alternatives = data.alternatives || [];
  $("alternatives").innerHTML = "";
  alternatives.forEach((candidate) => {
    const button = document.createElement("button");
    button.className = "alt";
    button.innerHTML = candidateMarkup(candidate);
    button.onclick = () => {
      chosen = candidate;
      $("match").innerHTML = candidateMarkup(candidate);
      $("alternatives-wrap").open = false;
    };
    $("alternatives").appendChild(button);
  });
  show("alternatives-wrap", alternatives.length > 0);

  const sharps = $("sharps");
  sharps.innerHTML = '<option value="">Detected</option>';
  for (let value = -7; value <= 7; value += 1) {
    sharps.insertAdjacentHTML(
      "beforeend",
      `<option value="${value}">${KEY_NAMES[String(value)]}</option>`
    );
  }

  show("confirm-card", true);
  show("result-card", false);
  setError("job-error", "");
}

// ---------------------------------------------------------------- jobs

async function transcribe(part) {
  if (!chosen) return;
  setError("job-error", "");
  document.querySelectorAll(".part").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.part === part))
  );

  const options = {
    subdivision: Number($("subdivision").value),
    page_size: $("page-size").value,
  };
  if ($("sharps").value !== "") options.sharps = Number($("sharps").value);
  if ($("bpm").value !== "") options.bpm = Number($("bpm").value);
  if ($("meter").value !== "") options.time_signature = $("meter").value;

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_id: chosen.source_id, part, meta: chosen, options,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not start");
    show("progress-card", true);
    show("result-card", false);
    $("bar-fill").style.width = "0%";
    watch(data.job_id);
  } catch (error) {
    setError("job-error", error.message);
  }
}

function watch(jobId) {
  clearInterval(poller);
  poller = setInterval(async () => {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) return;
    const status = await response.json();

    $("bar-fill").style.width = `${Math.round((status.progress || 0) * 100)}%`;
    $("stage").textContent = status.message || status.stage || "";

    if (status.state === "done") {
      clearInterval(poller);
      show("progress-card", false);
      renderResult(jobId, status);
      loadRecent();
    } else if (status.state === "error") {
      clearInterval(poller);
      show("progress-card", false);
      setError("job-error", status.message || "Transcription failed");
      loadRecent();
    }
  }, 1000);
}

function renderResult(jobId, status) {
  const detected = status.detected || {};
  $("result-title").textContent = `${PART_NAMES[status.part] || status.part} — ${
    (status.meta && status.meta.title) || "Score"
  }`;
  $("detected").textContent = [
    detected.key,
    detected.tempo ? `${Math.round(detected.tempo)} BPM` : null,
    detected.time_signature,
    detected.swing ? "swing feel" : null,
    detected.beat_source ? `beats via ${detected.beat_source}` : null,
  ].filter(Boolean).join(" · ");
  show("sparse-warning", Boolean(detected.sparse_melody));

  $("dl-pdf").href = `/api/jobs/${jobId}/file/pdf`;
  $("dl-musicxml").href = `/api/jobs/${jobId}/file/musicxml`;
  $("dl-midi").href = `/api/jobs/${jobId}/file/midi`;

  const pages = $("pages");
  pages.innerHTML = "";
  const count = (status.artifacts && status.artifacts.page_count) || 0;
  for (let number = 1; number <= count; number += 1) {
    const img = document.createElement("img");
    img.src = `/api/jobs/${jobId}/page/${number}`;
    img.alt = `Page ${number}`;
    pages.appendChild(img);
  }
  show("result-card", true);
}

// ---------------------------------------------------------------- history

const STATE_LABELS = { queued: "Queued", running: "In progress", error: "Failed" };

async function loadRecent() {
  let jobs;
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) return;
    ({ jobs } = await response.json());
  } catch {
    return; // history is a convenience; a network hiccup here should not be loud
  }

  const list = $("history-list");
  if (!jobs.length) {
    list.innerHTML = '<p class="hint">Nothing transcribed yet.</p>';
    return;
  }

  list.innerHTML = "";
  for (const job of jobs) {
    const title = (job.meta && job.meta.title) || "Untitled";
    const part = PART_NAMES[job.part] || job.part;
    const isError = job.state === "error";
    const metaText = job.state === "done"
      ? part
      : `${part} — ${STATE_LABELS[job.state] || job.state}`;

    const row = document.createElement("div");
    row.className = "history-row";
    row.innerHTML = `
      <button class="history-main" type="button">
        <span class="history-title"></span>
        <span class="history-meta${isError ? " is-error" : ""}"></span>
      </button>`;
    row.querySelector(".history-title").textContent = title;
    row.querySelector(".history-meta").textContent = metaText;
    row.querySelector(".history-main").addEventListener("click", () => {
      if (job.state === "done") {
        show("progress-card", false);
        renderResult(job.job_id, job);
        $("result-card").scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (job.state === "error") {
        setError("job-error", job.message || "Transcription failed");
      }
    });
    list.appendChild(row);
  }
}

// ---------------------------------------------------------------- wiring

$("find").addEventListener("click", find);
$("url").addEventListener("keydown", (event) => {
  if (event.key === "Enter") find();
});
document.querySelectorAll(".part").forEach((button) => {
  button.addEventListener("click", () => transcribe(button.dataset.part));
});
loadRecent();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
