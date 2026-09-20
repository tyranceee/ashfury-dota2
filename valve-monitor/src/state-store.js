"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");

const DEFAULT_STATE = Object.freeze({
  dotaOnline: false,
  lastPresenceCheckAt: null,
  lastResultCheckAt: null,
  lastProcessedMatchId: null,
  lastDiscoveredMatchId: null,
});

class JsonStateStore {
  constructor(filePath) {
    this.filePath = filePath;
  }

  async load() {
    try {
      const raw = await fs.readFile(this.filePath, "utf8");
      return { ...DEFAULT_STATE, ...JSON.parse(raw) };
    } catch (error) {
      if (error.code === "ENOENT") return { ...DEFAULT_STATE };
      throw error;
    }
  }

  async save(state) {
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    const temporary = `${this.filePath}.tmp`;
    await fs.writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, {
      mode: 0o600,
    });
    await fs.rename(temporary, this.filePath);
  }
}

module.exports = { DEFAULT_STATE, JsonStateStore };
