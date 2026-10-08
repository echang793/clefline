"use strict";
// Run with: node --test tests/js/*.test.js   (also run from pytest via tests/test_js.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const { createPoller } = require("../../static/poll.js");

function harness(overrides = {}) {
  const timers = [];
  const events = [];
  const responses = [];
  const hidden = { value: false };
  let nextId = 1;
  const poller = createPoller({
    fetchStatus: (id) => { events.push(["fetch", id]); return responses.shift()(); },
    onStatus: (body) => events.push(["status", body.state]),
    onGone: () => events.push(["gone"]),
    onLost: (attempt, delay) => events.push(["lost", attempt, delay]),
    onGiveUp: () => events.push(["giveup"]),
    isHidden: () => hidden.value,
    setTimer: (fn, ms) => { const id = nextId++; timers.push({ id, fn, ms }); return id; },
    clearTimer: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    intervalMs: 1000, hiddenMs: 5000, maxFailures: 3, baseBackoffMs: 1000, maxBackoffMs: 4000,
    ...overrides,
  });
  const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
  const fire = async () => { const timer = timers.shift(); await timer.fn(); await flush(); return timer; };
  return { poller, events, responses, timers, hidden, fire, flush };
}

const ok = (state) => () => Promise.resolve({ ok: true, status: 200, json: async () => ({ state }) });
const missing = () => Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
const serverError = () => Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
const offline = () => Promise.reject(new Error("offline"));

test("start polls at once, then again after the interval", async () => {
  const h = harness();
  h.responses.push(ok("running"), ok("running"));
  h.poller.start("job1");
  assert.equal(h.timers[0].ms, 0);

  await h.fire();
  assert.deepEqual(h.events, [["fetch", "job1"], ["status", "running"]]);
  assert.equal(h.timers[0].ms, 1000);

  await h.fire();
  assert.equal(h.events.filter((e) => e[0] === "fetch").length, 2);
});

for (const state of ["done", "error", "cancelled"]) {
  test(`a ${state} job stops the polling`, async () => {
    const h = harness();
    h.responses.push(ok(state));
    h.poller.start("job1");
    await h.fire();
    assert.deepEqual(h.events.at(-1), ["status", state]);
    assert.equal(h.timers.length, 0);
  });
}

test("a 404 means the job is gone: report it once and stop", async () => {
  const h = harness();
  h.responses.push(missing);
  h.poller.start("job1");
  await h.fire();
  assert.deepEqual(h.events.filter((e) => e[0] !== "fetch"), [["gone"]]);
  assert.equal(h.timers.length, 0);
});

test("network failures back off exponentially, then give up", async () => {
  const h = harness();
  h.responses.push(offline, offline, offline);
  h.poller.start("job1");

  await h.fire();
  assert.deepEqual(h.events.at(-1), ["lost", 1, 1000]);
  assert.equal(h.timers[0].ms, 1000);

  await h.fire();
  assert.deepEqual(h.events.at(-1), ["lost", 2, 2000]);
  assert.equal(h.timers[0].ms, 2000);

  await h.fire();
  assert.deepEqual(h.events.at(-1), ["giveup"]);
  assert.equal(h.timers.length, 0);
});

test("the backoff is capped", async () => {
  const h = harness({ maxFailures: 10 });
  for (let i = 0; i < 6; i += 1) h.responses.push(offline);
  h.poller.start("job1");
  const delays = [];
  for (let i = 0; i < 5; i += 1) { await h.fire(); delays.push(h.timers[0].ms); }
  assert.deepEqual(delays, [1000, 2000, 4000, 4000, 4000]);
});

test("one success resets the failure count", async () => {
  const h = harness();
  h.responses.push(offline, offline, ok("running"), offline);
  h.poller.start("job1");
  await h.fire(); await h.fire(); await h.fire();
  await h.fire();
  assert.deepEqual(h.events.at(-1), ["lost", 1, 1000]);
});

test("a 5xx is a failure to retry, not a verdict on the job", async () => {
  const h = harness();
  h.responses.push(serverError, ok("running"));
  h.poller.start("job1");
  await h.fire();
  assert.deepEqual(h.events.at(-1), ["lost", 1, 1000]);
  await h.fire();
  assert.deepEqual(h.events.at(-1), ["status", "running"]);
});

test("a hidden tab polls slowly", async () => {
  const h = harness();
  h.responses.push(ok("running"), ok("running"));
  h.poller.start("job1");
  h.hidden.value = true;
  await h.fire();
  assert.equal(h.timers[0].ms, 5000);
  h.hidden.value = false;
  await h.fire();
  assert.equal(h.timers[0].ms, 1000);
});

test("nudge polls now instead of waiting out a slow timer", async () => {
  const h = harness();
  h.responses.push(ok("running"), ok("running"));
  h.poller.start("job1");
  h.hidden.value = true;
  await h.fire();
  assert.equal(h.timers[0].ms, 5000);

  h.poller.nudge();
  assert.equal(h.timers.length, 1);
  assert.equal(h.timers[0].ms, 0);
});

test("overlapping polls cannot pile up: one request in flight at a time", async () => {
  const h = harness();
  let release;
  h.responses.push(() => new Promise((resolve) => { release = resolve; }), ok("running"));
  h.poller.start("job1");
  const first = h.timers.shift().fn();          // request now pending
  h.poller.nudge();                              // visibility change while it is pending
  while (h.timers.length) await h.timers.shift().fn();
  assert.equal(h.events.filter((e) => e[0] === "fetch").length, 1);

  release({ ok: true, status: 200, json: async () => ({ state: "running" }) });
  await first;
  await h.flush();
  assert.deepEqual(h.events.at(-1), ["status", "running"]);
});

test("a late answer for a job we stopped following is ignored", async () => {
  const h = harness();
  let releaseA;
  h.responses.push(() => new Promise((resolve) => { releaseA = resolve; }), ok("running"));
  h.poller.start("a");
  const pendingA = h.timers.shift().fn();
  h.poller.start("b");                           // user moved on to another job
  releaseA({ ok: true, status: 200, json: async () => ({ state: "done" }) });
  await pendingA;
  await h.flush();
  assert.equal(h.events.some((e) => e[0] === "status" && e[1] === "done"), false);
});

test("stop cancels the timer and drops a late answer", async () => {
  const h = harness();
  let release;
  h.responses.push(() => new Promise((resolve) => { release = resolve; }));
  h.poller.start("job1");
  const pending = h.timers.shift().fn();
  h.poller.stop();
  release({ ok: true, status: 200, json: async () => ({ state: "running" }) });
  await pending;
  await h.flush();
  assert.equal(h.events.some((e) => e[0] === "status"), false);
  assert.equal(h.timers.length, 0);
});
