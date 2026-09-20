"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { assignedPosition, auditAssignedRoles } = require("../src/role-flags");

test("maps Valve lane-selection flags to positions 1 through 5", () => {
  assert.equal(assignedPosition(1), 1);
  assert.equal(assignedPosition(4), 2);
  assert.equal(assignedPosition(2), 3);
  assert.equal(assignedPosition(8), 4);
  assert.equal(assignedPosition(16), 5);
  assert.equal(assignedPosition(0), null);
});

test("accepts an exact Ranked Roles allocation for both teams", () => {
  const players = [2, 3].flatMap((teamNumber) => [1, 2, 4, 8, 16].map(
    (laneSelectionFlags) => ({ teamNumber, laneSelectionFlags }),
  ));
  assert.equal(auditAssignedRoles(players).exactRankedRoles, true);
});

test("rejects duplicate or missing role flags", () => {
  const players = [
    ...[1, 2, 4, 8, 8].map((laneSelectionFlags) => ({ teamNumber: 2, laneSelectionFlags })),
    ...[1, 2, 4, 8, 16].map((laneSelectionFlags) => ({ teamNumber: 3, laneSelectionFlags })),
  ];
  assert.equal(auditAssignedRoles(players).exactRankedRoles, false);
});
