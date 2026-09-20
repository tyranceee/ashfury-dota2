"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { AdaptiveMonitor, isNewerMatch } = require("../src/adaptive-monitor");

function harness({ online = true, matchIds = ["100"] } = {}) {
  let onlineValue = online;
  let latestIndex = 0;
  const handled = [];
  const saved = [];
  const timers = new Map();
  let nextTimerId = 1;

  const monitor = new AdaptiveMonitor({
    presenceClient: {
      async getDotaPresence() {
        return {
          isDotaOnline: onlineValue,
          checkedAt: "2026-09-17T00:00:00.000Z",
        };
      },
    },
    resultClient: {
      async getLatestMatch() {
        const matchId = matchIds[Math.min(latestIndex, matchIds.length - 1)];
        latestIndex += 1;
        return matchId ? { matchId } : null;
      },
    },
    stateStore: {
      async load() {
        return {
          dotaOnline: false,
          lastPresenceCheckAt: null,
          lastResultCheckAt: null,
          lastProcessedMatchId: null,
          lastDiscoveredMatchId: null,
        };
      },
      async save(state) {
        saved.push(structuredClone(state));
      },
    },
    onNewMatch: async ({ matchId }) => handled.push(matchId),
    presencePollMs: 600_000,
    resultPollMs: 10_000,
    logger: { info() {}, error() {} },
    setTimer(fn, delay) {
      const id = nextTimerId++;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimer(id) {
      timers.delete(id);
    },
  });

  return {
    monitor,
    handled,
    saved,
    timers,
    setOnline(value) { onlineValue = value; },
  };
}

test("uses a 10-minute presence timer and a 10-second result timer when online", async () => {
  const h = harness({ online: true, matchIds: ["100"] });
  await h.monitor.start();

  const delays = [...h.timers.values()].map((timer) => timer.delay).sort((a, b) => a - b);
  assert.deepEqual(delays, [10_000, 600_000]);
  assert.equal(h.monitor.state.lastProcessedMatchId, "100");
  assert.deepEqual(h.handled, []);
});

test("does not query or schedule results while Dota is offline", async () => {
  const h = harness({ online: false, matchIds: ["100"] });
  await h.monitor.start();

  assert.deepEqual([...h.timers.values()].map((timer) => timer.delay), [600_000]);
  assert.equal(h.monitor.state.lastResultCheckAt, null);
});

test("stops the 10-second loop at the next offline presence check", async () => {
  const h = harness({ online: true, matchIds: ["100"] });
  await h.monitor.start();
  h.setOnline(false);
  await h.monitor.pollPresenceNow();

  const delays = [...h.timers.values()].map((timer) => timer.delay);
  assert.deepEqual(delays, [600_000]);
  assert.equal(h.monitor.state.dotaOnline, false);
});

test("processes a newly observed match exactly once", async () => {
  const h = harness({ online: true, matchIds: ["100", "101", "101"] });
  await h.monitor.start();
  await h.monitor.pollResultNow();
  await h.monitor.pollResultNow();

  assert.deepEqual(h.handled, ["101"]);
  assert.equal(h.monitor.state.lastProcessedMatchId, "101");
});

test("compares large match IDs without floating-point loss", () => {
  assert.equal(isNewerMatch("9007199254740993", "9007199254740992"), true);
  assert.equal(isNewerMatch("9007199254740992", "9007199254740993"), false);
});
