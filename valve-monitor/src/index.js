"use strict";

const { AdaptiveMonitor } = require("./adaptive-monitor");
const { loadConfig, validateRuntimeConfig } = require("./config");
const { createLogger } = require("./logger");
const { MatchCache } = require("./match-cache");
const { SteamPresenceClient } = require("./presence-client");
const { auditAssignedRoles } = require("./role-flags");
const { JsonStateStore } = require("./state-store");
const { ValveGcClient } = require("./valve-gc-client");

async function main() {
  const config = loadConfig();
  validateRuntimeConfig(config);
  const logger = createLogger();
  const stateStore = new JsonStateStore(config.stateFile);
  const cache = new MatchCache(config.matchCacheDir);
  const presenceClient = new SteamPresenceClient({
    apiKey: config.steamWebApiKey,
    steamId64: config.steamId64,
    timeoutMs: config.requestTimeoutMs,
  });
  const gcClient = new ValveGcClient({
    refreshToken: config.steamGcRefreshToken,
    dataDirectory: config.steamDataDir,
    timeoutMs: config.requestTimeoutMs,
  });

  const monitor = new AdaptiveMonitor({
    presenceClient,
    resultClient: {
      getLatestMatch: () => gcClient.getLatestMatch(config.accountId),
    },
    stateStore,
    presencePollMs: config.presencePollMs,
    resultPollMs: config.resultPollMs,
    logger,
    onPresenceChange: async (online) => {
      if (!online) gcClient.close();
    },
    onNewMatch: async ({ matchId, summary }) => {
      if (await cache.has(matchId)) {
        logger.info("match_cache_hit", { matchId });
        return;
      }

      const match = await gcClient.getMatchDetails(matchId);
      const roleAudit = auditAssignedRoles(match.players);
      const cachePath = await cache.write(matchId, {
        source: "valve_gc",
        fetchedAt: new Date().toISOString(),
        summary,
        match,
        roleAudit,
      });
      logger.info("match_cached", {
        matchId,
        cachePath,
        exactRankedRoles: roleAudit.exactRankedRoles,
      });
    },
  });

  const shutdown = async (signal) => {
    logger.info("shutdown", { signal });
    await monitor.stop();
    gcClient.close();
    process.exitCode = 0;
  };
  process.once("SIGINT", () => void shutdown("SIGINT"));
  process.once("SIGTERM", () => void shutdown("SIGTERM"));

  logger.info("monitor_starting", {
    accountId: config.accountId,
    presencePollMs: config.presencePollMs,
    resultPollMs: config.resultPollMs,
  });
  await monitor.start();
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({
      timestamp: new Date().toISOString(),
      level: "fatal",
      event: "monitor_failed",
      message: error.message,
    }));
    process.exitCode = 1;
  });
}

module.exports = { main };
