"use strict";

/* Job-status polling, as a small state machine with every side effect injected
   (fetch, timers, tab visibility) so tests/js can drive it without a browser.

   What it guarantees, and why each exists:
   - one request in flight at a time: a slow server never accumulates overlapping polls
   - a 404 means the job is gone: report it once and stop (the old loop froze silently)
   - a network error or 5xx is retried with exponential backoff, and given up on after
     `maxFailures` in a row, so a dead server is reported instead of a bar that never moves
   - polling slows down while the tab is hidden, and `nudge()` polls at once when it returns
   - an answer for a job we have since stopped following is ignored
*/
(function (root) {
  const TERMINAL = ["done", "error", "cancelled"];

  function createPoller(options) {
    const o = Object.assign({
      intervalMs: 1000,
      hiddenMs: 5000,
      maxFailures: 8,
      baseBackoffMs: 1000,
      maxBackoffMs: 15000,
      isHidden: () => false,
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id),
    }, options);

    let jobId = null;
    let running = false;
    let inFlight = false;
    let failures = 0;
    let timer = null;
    let generation = 0;   // bumped on start/stop so stale answers can be recognised

    function cancelTimer() {
      if (timer !== null) {
        o.clearTimer(timer);
        timer = null;
      }
    }

    function schedule(ms) {
      cancelTimer();
      timer = o.setTimer(tick, ms);
    }

    function stop() {
      running = false;
      inFlight = false;
      generation += 1;
      cancelTimer();
    }

    function fail() {
      failures += 1;
      if (failures >= o.maxFailures) {
        stop();
        o.onGiveUp();
        return;
      }
      const delay = Math.min(o.maxBackoffMs, o.baseBackoffMs * 2 ** (failures - 1));
      o.onLost(failures, delay);
      schedule(delay);
    }

    async function tick() {
      timer = null;
      if (!running || inFlight) return;
      const mine = generation;
      inFlight = true;

      let response;
      let body = null;
      try {
        response = await o.fetchStatus(jobId);
        if (response.ok) body = await response.json();
      } catch (error) {
        if (mine === generation) {
          inFlight = false;
          fail();
        }
        return;
      }
      if (mine !== generation) return;   // we moved on while this was in flight
      inFlight = false;

      if (response.status === 404) {
        stop();
        o.onGone();
        return;
      }
      if (!response.ok) {
        fail();
        return;
      }

      failures = 0;
      o.onStatus(body);
      if (mine !== generation) return;   // onStatus may have stopped or restarted us
      if (TERMINAL.includes(body.state)) {
        stop();
        return;
      }
      schedule(o.isHidden() ? o.hiddenMs : o.intervalMs);
    }

    return {
      start(id) {
        stop();
        jobId = id;
        running = true;
        failures = 0;
        schedule(0);
      },
      stop,
      // Poll now rather than waiting out a (possibly slow) timer, e.g. the tab became visible.
      nudge() {
        if (running && !inFlight) schedule(0);
      },
    };
  }

  const api = { createPoller, TERMINAL };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClefPoll = api;
})(typeof window !== "undefined" ? window : globalThis);
