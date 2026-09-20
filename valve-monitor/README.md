# ashfury Valve-only Dota monitor

This service implements the agreed polling policy without OpenDota, replay
downloads, or replay parsing:

1. Check the target Steam account every 10 minutes.
2. Treat the account as online only when Steam reports game AppID `570`.
3. While online, ask Valve GC for the latest completed Match ID every 10 seconds.
4. On a new Match ID, fetch Valve GC match details once and write one cache file.
5. Stop result polling at the next successful offline presence check.

The first observed Match ID seeds the baseline and is not emitted as a new match.
State survives restarts. If fetching new match details fails (for example, Valve
has not published them yet), the Match ID is not marked processed and the next
10-second pass retries it.

## Data and security boundaries

- Presence source: Valve Steam Web API `GetPlayerSummaries`.
- Match source: Valve Dota 2 Game Coordinator.
- No OpenDota request exists in this package.
- Use a dedicated Steam service account for GC. Do not place the player's main
  Steam refresh token on the server.
- `.env`, state, Steam login material, and match cache must be readable only by
  the service account.
- Renewed GC refresh tokens are atomically retained in the protected Steam state
  directory so a service restart does not fall back to an expired token.
- Logs contain state changes and Match IDs, never API keys or refresh tokens.

Steam privacy/invisible mode can hide `gameid`; in that case Valve cannot prove
that the target is in Dota and the service correctly treats the target as
offline. This is a data-source limitation, not a scheduling error.

## Required configuration

Copy `.env.example` to a server-owned environment file and fill:

- `STEAM_WEB_API_KEY`: key used only for the 10-minute presence check.
- `STEAM_GC_REFRESH_TOKEN`: refresh token for a dedicated Steam service account.

The checked player's defaults are already set to account ID `212121467` and
SteamID64 `76561198172387195`.

## Verification

Run `npm test`. The tests cover the exact 600-second/10-second schedule, offline
shutdown, duplicate suppression, large Match IDs, AppID 570 detection, and the
Ranked Roles lane-selection flag audit.

## Cached output

Each new match is atomically stored as `cache/matches/<match_id>.json` with:

- a Valve-only source marker and fetch time;
- the history summary returned for the target player;
- Valve GC basic match details;
- an audit of whether both teams contain the exact role flags
  `1, 2, 4, 8, 16`.

The cache is the handoff point for the future historical-profile builder. The
current service deliberately does not score players or analyze current-match
performance.
