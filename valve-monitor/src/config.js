"use strict";

const path = require("node:path");

function positiveInteger(value, fallback, name) {
  const parsed = Number(value ?? fallback);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function loadConfig(env = process.env, cwd = process.cwd()) {
  const accountId = positiveInteger(
    env.TARGET_ACCOUNT_ID,
    212121467,
    "TARGET_ACCOUNT_ID",
  );

  return {
    accountId,
    steamId64: String(env.TARGET_STEAM_ID64 || "76561198172387195"),
    steamWebApiKey: String(env.STEAM_WEB_API_KEY || ""),
    steamGcRefreshToken: String(env.STEAM_GC_REFRESH_TOKEN || ""),
    steamDataDir: path.resolve(
      cwd,
      env.STEAM_DATA_DIR || "./state/steam",
    ),
    requestTimeoutMs: positiveInteger(
      env.REQUEST_TIMEOUT_MS,
      15_000,
      "REQUEST_TIMEOUT_MS",
    ),
    presencePollMs: positiveInteger(
      env.PRESENCE_POLL_MS,
      600_000,
      "PRESENCE_POLL_MS",
    ),
    resultPollMs: positiveInteger(
      env.RESULT_POLL_MS,
      10_000,
      "RESULT_POLL_MS",
    ),
    stateFile: path.resolve(
      cwd,
      env.MONITOR_STATE_FILE || "./state/monitor-state.json",
    ),
    matchCacheDir: path.resolve(
      cwd,
      env.VALVE_MATCH_CACHE_DIR || "./cache/matches",
    ),
  };
}

function validateRuntimeConfig(config) {
  const missing = [];
  if (!config.steamWebApiKey) missing.push("STEAM_WEB_API_KEY");
  if (!config.steamGcRefreshToken) missing.push("STEAM_GC_REFRESH_TOKEN");
  if (missing.length) {
    throw new Error(`Missing required secret configuration: ${missing.join(", ")}`);
  }
}

module.exports = { loadConfig, validateRuntimeConfig };
