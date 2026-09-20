"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");

function jsonSafe(value) {
  return JSON.parse(JSON.stringify(value, (_, item) => (
    typeof item === "bigint" ? item.toString() : item
  )));
}

class MatchCache {
  constructor(directory) {
    this.directory = directory;
  }

  filePath(matchId) {
    const safeId = String(matchId);
    if (!/^\d+$/.test(safeId)) throw new Error("Invalid match ID");
    return path.join(this.directory, `${safeId}.json`);
  }

  async has(matchId) {
    try {
      await fs.access(this.filePath(matchId));
      return true;
    } catch (error) {
      if (error.code === "ENOENT") return false;
      throw error;
    }
  }

  async read(matchId) {
    return JSON.parse(await fs.readFile(this.filePath(matchId), "utf8"));
  }

  async write(matchId, value) {
    await fs.mkdir(this.directory, { recursive: true });
    const destination = this.filePath(matchId);
    const temporary = `${destination}.${process.pid}.tmp`;
    await fs.writeFile(temporary, `${JSON.stringify(jsonSafe(value), null, 2)}\n`, {
      mode: 0o600,
    });
    await fs.rename(temporary, destination);
    return destination;
  }
}

module.exports = { MatchCache, jsonSafe };
