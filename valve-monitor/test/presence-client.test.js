"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { SteamPresenceClient } = require("../src/presence-client");

test("only treats AppID 570 as Dota online", async () => {
  const client = new SteamPresenceClient({
    apiKey: "secret",
    steamId64: "76561198172387195",
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return { response: { players: [{ gameid: "570", personastate: 1 }] } };
      },
    }),
  });

  const result = await client.getDotaPresence();
  assert.equal(result.isDotaOnline, true);
});

test("generic Steam online is not Dota online", async () => {
  const client = new SteamPresenceClient({
    apiKey: "secret",
    steamId64: "76561198172387195",
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return { response: { players: [{ personastate: 1 }] } };
      },
    }),
  });

  const result = await client.getDotaPresence();
  assert.equal(result.isDotaOnline, false);
});
