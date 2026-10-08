"use strict";

const $ = (id) => document.getElementById(id);
const PART_NAMES = { sax: "Alto Sax", keys: "Keyboard", drums: "Drumset" };
const KEY_NAMES = {
  "-7": "7 flats", "-6": "6 flats", "-5": "5 flats", "-4": "4 flats",
  "-3": "3 flats", "-2": "2 flats", "-1": "1 flat", "0": "No sharps or flats",
  "1": "1 sharp", "2": "2 sharps", "3": "3 sharps", "4": "4 sharps",
  "5": "5 sharps", "6": "6 sharps", "7": "7 sharps",
};
const ACTIVE_JOB_KEY = "clefline.activeJob";

let chosen = null;       // the YouTube candidate we will transcribe
let activeJob = null;    // the job whose progress card is showing
let failedJob = null;    // the failed/cancelled job the "Try again" button re-submits

// Remembering the running job lets a reload pick it back up. Storage can be
// blocked or throw (private windows, site data off), and nothing depends on it.
const store = {
  get() { try { return localStorage.getItem(ACTIVE_JOB_KEY); } catch { return null; } },
  set(id) { try { localStorage.setItem(ACTIVE_JOB_KEY, id); } catch { /* optional */ } },
  clear() { try { localStorage.removeItem(ACTIVE_JOB_KEY); } catch { /* optional */ } },
};

function show(id, visible) { $(id).hidden = !visible; }

function setError(id, message) {
  const node = $(id);
  node.textContent = message || "";
  node.hidden = !message;
}

// Errors from a job live in their own card, so they are visible wherever the user is
// (the confirm card can be hidden after a reload).
function showJobError(message, { retry = null } = {}) {
  failedJob = retry;
  $("job-error").textContent = message;
  show("retry-job", Boolean(retry));
  show("alert-card", true);
}

function clearJobError() {
  failedJob = null;
  $("job-error").textContent = "";
  show("retry-job", false);
  show("alert-card", false);
}

// Cards appear and disappear; move focus to the new one's heading so keyboard and
// screen-reader users land on it instead of being left on a control that vanished.
function focusHeading(id) { $(id).focus(); }

function seconds(value) {
  if (!value) return "";
  const total = Math.round(value);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// Built from DOM nodes, never innerHTML: titles, uploaders and thumbnail URLs all
// come from yt-dlp / Spotify metadata, which is untrusted text.
function rowNode(thumbnail, title, sub) {
  const fragment = document.createDocumentFragment();
  const img = document.createElement("img");
  img.alt = "";
  if (typeof thumbnail === "string" && thumbnail.startsWith("https://")) img.src = thumbnail;
  const text = document.createElement("div");
  const titleNode = document.createElement("div");
  titleNode.className = "meta-title";
  titleNode.textContent = title || "";
  const subNode = document.createElement("div");
  subNode.className = "meta-sub";
  subNode.textContent = sub;
  text.append(titleNode, subNode);
  fragment.append(img, text);
  return fragment;
}

function candidateNode(candidate) {
  const sub = [
    candidate.uploader || "",
    candidate.duration ? seconds(candidate.duration) : "",
    candidate.note || "",
  ].filter(Boolean).join(" · ");
  return rowNode(candidate.thumbnail, candidate.title, sub);
}

// ---------------------------------------------------------------- resolve

async function find() {
  const url = $("url").value.trim();
  if (!url) return;
  setError("input-error", "");
  const button = $("find");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "Looking…";
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
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = "Find";
  }
}

function renderResolved(data) {
  chosen = data.chosen;
  $("match").replaceChildren(candidateNode(data.chosen));

  if (data.requested) {
    const { thumbnail, title, artist, duration } = data.requested;
    $("requested").replaceChildren(
      rowNode(thumbnail, title, `From Spotify · ${artist || ""} · ${seconds(duration)}`)
    );
    show("requested", true);
  } else {
    show("requested", false);
  }

  const alternatives = data.alternatives || [];
  $("alternatives").replaceChildren();
  alternatives.forEach((candidate) => {
    const button = document.createElement("button");
    button.className = "alt";
    button.type = "button";
    button.replaceChildren(candidateNode(candidate));
    button.onclick = () => {
      chosen = candidate;
      $("match").replaceChildren(candidateNode(candidate));
      $("alternatives-wrap").open = false;
    };
    $("alternatives").appendChild(button);
  });
  show("alternatives-wrap", alternatives.length > 0);

  const sharps = $("sharps");
  sharps.replaceChildren(new Option("Detected", ""));
  for (let value = -7; value <= 7; value += 1) {
    sharps.appendChild(new Option(KEY_NAMES[String(value)], String(value)));
  }

  show("confirm-card", true);
  show("result-card", false);
  clearJobError();
  focusHeading("confirm-title");
}

// ---------------------------------------------------------------- jobs

function transcribe(part) {
  if (!chosen) return;
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

  submit({ source_id: chosen.source_id, part, meta: chosen, options });
}

async function submit(request) {
  clearJobError();
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not start");
    attach(data.job_id);
  } catch (error) {
    showJobError(error.message, { retry: request });
  }
}

// Follow a job: show the progress card and poll until it finishes. Called for a
// new job, for one still running after a reload, and when Recent re-opens one.
function attach(jobId, { focus = true } = {}) {
  activeJob = jobId;
  store.set(jobId);
  clearJobError();
  show("result-card", false);
  show("progress-card", true);
  $("cancel-job").disabled = false;
  setProgress(0, "Queued");
  if (focus) focusHeading("progress-title");
  poller.start(jobId);
}

function setProgress(percent, text) {
  $("bar-fill").style.width = `${percent}%`;
  $("progress-bar").setAttribute("aria-valuenow", String(percent));
  $("stage").textContent = text;
}

function finish() {
  activeJob = null;
  store.clear();
  show("progress-card", false);
}

function handleStatus(status) {
  const percent = Math.round((status.progress || 0) * 100);

  if (status.state === "queued") {
    const ahead = typeof status.ahead === "number" ? status.ahead : null;
    const waiting = ahead === null ? "" : ahead === 0 ? "Next in line" : `${ahead} ahead of you`;
    setProgress(percent, [status.message, waiting].filter(Boolean).join(" — "));
  } else if (status.state === "done") {
    finish();
    renderResult(status.job_id, status);
    loadRecent();
  } else if (status.state === "error" || status.state === "cancelled") {
    finish();
    const cancelled = status.state === "cancelled";
    showJobError(cancelled ? "Cancelled." : status.message || "Transcription failed", {
      retry: { source_id: status.source_id, part: status.part, meta: status.meta || {},
               options: status.options || {} },
    });
    loadRecent();
  } else {
    setProgress(percent, status.message || status.stage || "");
  }
}

const poller = ClefPoll.createPoller({
  fetchStatus: (id) => fetch(`/api/jobs/${id}`),
  onStatus: handleStatus,
  onGone: () => {
    finish();
    showJobError("That job no longer exists — the server may have been reset.");
  },
  onLost: (attempt) => {
    $("stage").textContent = `Connection lost — retrying (attempt ${attempt})…`;
  },
  onGiveUp: () => {
    $("cancel-job").disabled = true;
    $("stage").textContent =
      "Can't reach clefline. Make sure it is running, then reload this page.";
  },
  isHidden: () => document.hidden,
});

async function cancel() {
  if (!activeJob) return;
  $("cancel-job").disabled = true;
  $("stage").textContent = "Cancelling…";
  try {
    await fetch(`/api/jobs/${activeJob}/cancel`, { method: "POST" });
  } catch {
    $("cancel-job").disabled = false;   // the poller will report if the server is gone
  }
  poller.nudge();
}

// A job that was running when the page was closed or reloaded.
async function resume() {
  const id = store.get();
  if (!id) return;
  try {
    const response = await fetch(`/api/jobs/${id}`);
    if (!response.ok) { store.clear(); return; }
    const status = await response.json();
    if (status.state === "queued" || status.state === "running") attach(id, { focus: false });
    else store.clear();   // finished while away: it is in Recent
  } catch {
    /* server unreachable: keep the id; a reload once it is back will pick it up */
  }
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
  pages.replaceChildren();
  const count = (status.artifacts && status.artifacts.page_count) || 0;
  for (let number = 1; number <= count; number += 1) {
    const img = document.createElement("img");
    img.src = `/api/jobs/${jobId}/page/${number}`;
    img.alt = `Page ${number}`;
    pages.appendChild(img);
  }
  show("result-card", true);
  focusHeading("result-title");
}

// ---------------------------------------------------------------- history

const STATE_LABELS = {
  queued: "Queued", running: "In progress", error: "Failed", cancelled: "Cancelled",
};

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
  list.replaceChildren();
  if (!jobs.length) {
    const none = document.createElement("p");
    none.className = "hint";
    none.textContent = "Nothing transcribed yet.";
    list.appendChild(none);
    return;
  }

  for (const job of jobs) {
    const part = PART_NAMES[job.part] || job.part;
    const failed = job.state === "error" || job.state === "cancelled";
    const metaText = job.state === "done"
      ? part
      : `${part} — ${STATE_LABELS[job.state] || job.state}`;

    const row = document.createElement("div");
    row.className = "history-row";
    const button = document.createElement("button");
    button.className = "history-main";
    button.type = "button";
    const titleNode = document.createElement("span");
    titleNode.className = "history-title";
    titleNode.textContent = (job.meta && job.meta.title) || "Untitled";
    const metaNode = document.createElement("span");
    metaNode.className = `history-meta${job.state === "error" ? " is-error" : ""}`;
    metaNode.textContent = metaText;
    button.append(titleNode, metaNode);

    button.addEventListener("click", () => {
      if (job.state === "done") {
        finish();
        renderResult(job.job_id, job);
      } else if (failed) {
        showJobError(job.state === "cancelled" ? "Cancelled." : job.message || "Transcription failed", {
          retry: { source_id: job.source_id, part: job.part, meta: job.meta || {},
                   options: job.options || {} },
        });
      } else {
        attach(job.job_id);   // queued or running: follow it
      }
    });
    row.appendChild(button);
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
$("cancel-job").addEventListener("click", cancel);
$("retry-job").addEventListener("click", () => {
  if (failedJob) submit(failedJob);
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poller.nudge();
});

loadRecent();
resume();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
