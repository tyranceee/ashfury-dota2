"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { MatchCache } = require("../src/match-cache");

test("stores one JSON cache file and preserves large integer values as strings", async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "valve-match-cache-"));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const cache = new MatchCache(directory);

  await cache.write("9007199254740993", {
    matchId: 9007199254740993n,
    source: "valve_gc",
  });

  assert.equal(await cache.has("9007199254740993"), true);
  assert.deepEqual(await cache.read("9007199254740993"), {
    matchId: "9007199254740993",
    source: "valve_gc",
  });
});
