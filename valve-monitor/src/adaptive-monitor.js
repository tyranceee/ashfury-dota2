"use strict";

function isNewerMatch(candidate, baseline) {
  if (baseline == null) return true;
  return BigInt(candidate) > BigInt(baseline);
}

class AdaptiveMonitor {
  constructor({
    presenceClient,
    resultClient,
    stateStore,
    onNewMatch,
    onPresenceChange = async () => {},
    presencePollMs = 600_000,
    resultPollMs = 10_000,
    logger,
    clock = () => new Date(),
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    this.presenceClient = presenceClient;
    this.resultClient = resultClient;
    this.stateStore = stateStore;
    this.onNewMatch = onNewMatch;
    this.onPresenceChange = onPresenceChange;
    this.presencePollMs = presencePollMs;
    this.resultPollMs = resultPollMs;
    this.logger = logger;
    this.clock = clock;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.state = null;
    this.presenceTimer = null;
    this.resultTimer = null;
    this.running = false;
    this.presenceBusy = false;
    this.resultBusy = false;
  }

  async start() {
    if (this.running) return;
    this.running = true;
    this.state = await this.stateStore.load();
    await this.pollPresenceNow();
  }

  async stop() {
    this.running = false;
    this.#cancelPresenceTimer();
    this.#cancelResultTimer();
  }

  async pollPresenceNow() {
    if (!this.running || this.presenceBusy) return;
    this.presenceBusy = true;
    try {
      const presence = await this.presenceClient.getDotaPresence();
      const wasOnline = Boolean(this.state.dotaOnline);
      this.state.dotaOnline = Boolean(presence.isDotaOnline);
      this.state.lastPresenceCheckAt = presence.checkedAt || this.clock().toISOString();
      await this.stateStore.save(this.state);
      this.logger.info("presence_checked", {
        dotaOnline: this.state.dotaOnline,
        changed: wasOnline !== this.state.dotaOnline,
      });

      if (this.state.dotaOnline) {
        if (!this.resultTimer && !this.resultBusy) await this.pollResultNow();
      } else {
        this.#cancelResultTimer();
      }

      if (wasOnline !== this.state.dotaOnline) {
        try {
          await this.onPresenceChange(this.state.dotaOnline);
        } catch (error) {
          this.logger.error("presence_change_handler_failed", { message: error.message });
        }
      }
    } catch (error) {
      this.logger.error("presence_check_failed", { message: error.message });
    } finally {
      this.presenceBusy = false;
      if (this.running) this.#schedulePresence();
    }
  }

  async pollResultNow() {
    if (!this.running || !this.state?.dotaOnline || this.resultBusy) return;
    this.resultBusy = true;
    try {
      const latest = await this.resultClient.getLatestMatch();
      this.state.lastResultCheckAt = this.clock().toISOString();

      if (!latest?.matchId) {
        await this.stateStore.save(this.state);
        this.logger.info("result_checked", { latestMatchId: null });
        return;
      }

      const matchId = String(latest.matchId);
      if (this.state.lastProcessedMatchId == null) {
        this.state.lastProcessedMatchId = matchId;
        await this.stateStore.save(this.state);
        this.logger.info("result_baseline_seeded", { matchId });
        return;
      }

      if (!isNewerMatch(matchId, this.state.lastProcessedMatchId)) {
        await this.stateStore.save(this.state);
        this.logger.info("result_checked", { latestMatchId: matchId, isNew: false });
        return;
      }

      this.state.lastDiscoveredMatchId = matchId;
      await this.stateStore.save(this.state);
      await this.onNewMatch({ matchId, summary: latest });
      this.state.lastProcessedMatchId = matchId;
      await this.stateStore.save(this.state);
      this.logger.info("new_match_processed", { matchId });
    } catch (error) {
      this.logger.error("result_check_failed", { message: error.message });
    } finally {
      this.resultBusy = false;
      if (this.running && this.state?.dotaOnline) this.#scheduleResult();
    }
  }

  #schedulePresence() {
    this.#cancelPresenceTimer();
    this.presenceTimer = this.setTimer(() => {
      this.presenceTimer = null;
      void this.pollPresenceNow();
    }, this.presencePollMs);
  }

  #scheduleResult() {
    this.#cancelResultTimer();
    this.resultTimer = this.setTimer(() => {
      this.resultTimer = null;
      void this.pollResultNow();
    }, this.resultPollMs);
  }

  #cancelPresenceTimer() {
    if (this.presenceTimer != null) this.clearTimer(this.presenceTimer);
    this.presenceTimer = null;
  }

  #cancelResultTimer() {
    if (this.resultTimer != null) this.clearTimer(this.resultTimer);
    this.resultTimer = null;
  }
}

module.exports = { AdaptiveMonitor, isNewerMatch };
