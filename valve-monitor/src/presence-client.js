"use strict";

const DOTA_APP_ID = "570";

class SteamPresenceClient {
  constructor({ apiKey, steamId64, timeoutMs = 15_000, fetchImpl = fetch }) {
    this.apiKey = apiKey;
    this.steamId64 = steamId64;
    this.timeoutMs = timeoutMs;
    this.fetchImpl = fetchImpl;
  }

  async getDotaPresence() {
    const endpoint = new URL(
      "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
    );
    endpoint.searchParams.set("key", this.apiKey);
    endpoint.searchParams.set("steamids", this.steamId64);

    const response = await this.fetchImpl(endpoint, {
      signal: AbortSignal.timeout(this.timeoutMs),
      headers: { "user-agent": "ashfury-valve-monitor/0.1" },
    });
    if (!response.ok) {
      throw new Error(`Steam presence request failed with HTTP ${response.status}`);
    }

    const body = await response.json();
    const player = body?.response?.players?.[0];
    if (!player) throw new Error("Steam presence response did not contain a player");

    return {
      checkedAt: new Date().toISOString(),
      isDotaOnline: String(player.gameid || "") === DOTA_APP_ID,
      personaState: Number(player.personastate || 0),
      visibilityState: Number(player.communityvisibilitystate || 0),
    };
  }
}

module.exports = { DOTA_APP_ID, SteamPresenceClient };
