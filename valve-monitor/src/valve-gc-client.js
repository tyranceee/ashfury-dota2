"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const SteamUser = require("steam-user");
const { Dota2User } = require("dota2-user");
const protobufs = require("dota2-user/protobufs");

const DOTA_APP_ID = 570;

function withTimeout(promise, timeoutMs, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

class ValveGcClient {
  constructor({ refreshToken, dataDirectory, timeoutMs = 15_000 }) {
    this.refreshToken = refreshToken;
    this.dataDirectory = dataDirectory;
    this.tokenFile = path.join(dataDirectory, "refresh-token");
    this.timeoutMs = timeoutMs;
    this.steam = null;
    this.dota = null;
    this.connectPromise = null;
    this.requestId = 0;
    this.tokenPersistError = null;
  }

  async connect() {
    if (this.dota?.haveGCSession) return;
    if (this.connectPromise) return this.connectPromise;

    this.connectPromise = this.#connectOnce().finally(() => {
      this.connectPromise = null;
    });
    return this.connectPromise;
  }

  async #connectOnce() {
    await fs.mkdir(this.dataDirectory, { recursive: true, mode: 0o700 });
    const refreshToken = await this.#loadRefreshToken();
    this.steam = new SteamUser({
      dataDirectory: this.dataDirectory,
      autoRelogin: true,
      renewRefreshTokens: true,
    });
    this.dota = new Dota2User(this.steam);

    const ready = new Promise((resolve, reject) => {
      const cleanup = () => {
        this.dota?.off("connectedToGC", onConnected);
        this.steam?.off("error", onError);
      };
      const onConnected = () => {
        cleanup();
        resolve();
      };
      const onError = (error) => {
        cleanup();
        reject(error);
      };
      this.dota.once("connectedToGC", onConnected);
      this.steam.once("error", onError);
    });

    this.steam.once("loggedOn", () => {
      this.steam.setPersona(SteamUser.EPersonaState.Online);
      this.steam.gamesPlayed(DOTA_APP_ID);
    });
    this.steam.on("refreshToken", (token) => {
      this.refreshToken = token;
      void this.#persistRefreshToken(token).catch((error) => {
        this.tokenPersistError = error;
      });
    });
    this.steam.logOn({
      refreshToken,
      machineName: "ashfury-valve-monitor",
    });

    try {
      await withTimeout(ready, this.timeoutMs, "Timed out connecting to Dota GC");
    } catch (error) {
      this.close();
      throw error;
    }
  }

  async #loadRefreshToken() {
    try {
      const persisted = (await fs.readFile(this.tokenFile, "utf8")).trim();
      return persisted || this.refreshToken;
    } catch (error) {
      if (error.code === "ENOENT") return this.refreshToken;
      throw error;
    }
  }

  async #persistRefreshToken(token) {
    const temporary = `${this.tokenFile}.${process.pid}.tmp`;
    await fs.writeFile(temporary, `${token}\n`, { mode: 0o600 });
    await fs.rename(temporary, this.tokenFile);
  }

  async getLatestMatch(accountId) {
    await this.connect();
    this.requestId = (this.requestId + 1) >>> 0;

    const response = await this.dota.sendJob(
      protobufs.EDOTAGCMsg.k_EMsgDOTAGetPlayerMatchHistory,
      {
        accountId: Number(accountId),
        startAtMatchId: "0",
        matchesRequested: 1,
        heroId: 0,
        requestId: this.requestId,
        includePracticeMatches: false,
        includeCustomGames: false,
        includeEventGames: false,
      },
    );

    const matches = response?.matches || [];
    if (!matches.length) return null;
    return matches.reduce((latest, match) => (
      BigInt(match.matchId) > BigInt(latest.matchId) ? match : latest
    ));
  }

  async getMatchDetails(matchId) {
    await this.connect();
    const response = await this.dota.sendJob(
      protobufs.EDOTAGCMsg.k_EMsgGCMatchDetailsRequest,
      { matchId: String(matchId) },
    );
    if (Number(response?.result) !== 1 || !response?.match) {
      throw new Error(`Valve GC did not return match details for ${matchId}`);
    }
    return response.match;
  }

  close() {
    if (this.steam) {
      try {
        this.steam.gamesPlayed([]);
        this.steam.logOff();
      } catch {
        // The Steam connection may already be closed after an error.
      }
    }
    this.steam = null;
    this.dota = null;
    this.connectPromise = null;
  }
}

module.exports = { DOTA_APP_ID, ValveGcClient, withTimeout };
