"use strict";

const POSITION_BY_FLAG = Object.freeze({
  1: 1,
  4: 2,
  2: 3,
  8: 4,
  16: 5,
});

const EXPECTED_FLAGS = Object.freeze([1, 2, 4, 8, 16]);

function assignedPosition(flag) {
  return POSITION_BY_FLAG[Number(flag)] ?? null;
}

function auditAssignedRoles(players) {
  const teams = new Map();

  for (const player of players || []) {
    const team = Number(player.teamNumber);
    if (!teams.has(team)) teams.set(team, []);
    teams.get(team).push(Number(player.laneSelectionFlags || 0));
  }

  const teamAudits = [...teams.entries()].map(([teamNumber, flags]) => {
    const sorted = [...flags].sort((a, b) => a - b);
    const isExact = sorted.length === 5
      && EXPECTED_FLAGS.every((flag, index) => flag === sorted[index]);

    return { teamNumber, flags, isExact };
  });

  return {
    exactRankedRoles: teamAudits.length === 2
      && teamAudits.every((team) => team.isExact),
    teams: teamAudits,
  };
}

module.exports = { EXPECTED_FLAGS, assignedPosition, auditAssignedRoles };
