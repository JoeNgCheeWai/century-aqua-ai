/**
 * Tests for who sits at a Boardroom session (src/lib/mc-boardroom.ts), no network and no browser:
 *     node --test scripts/mission_control_boardroom.test.mjs
 */
import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_SEATS, MIN_SEATS, SEATS, assignRoles, canConvene, roleNote, seatProblem, toggleSeat } from "../src/lib/mc-boardroom.ts";

function combos(items, size) {
  if (size === 0) return [[]];
  if (items.length < size) return [];
  const [first, ...rest] = items;
  return [...combos(rest, size - 1).map((c) => [first, ...c]), ...combos(rest, size)];
}

test("all four seats are independent: any one can be ticked or unticked, in any order", () => {
  for (const seat of SEATS) {
    const on = toggleSeat([], seat);
    assert.deepEqual(on, [seat]);
    assert.deepEqual(toggleSeat(on, seat), []);
  }
  let seats = [];
  for (const seat of ["cmo", "cto", "coo", "cao"]) seats = toggleSeat(seats, seat);
  assert.deepEqual(seats, ["cao", "coo", "cto", "cmo"]); // fixed order whatever the click order
});

test("unticking the CAO or the COO works (they are no longer forced to sit)", () => {
  assert.deepEqual(toggleSeat(["cao", "coo", "cto", "cmo"], "cao"), ["coo", "cto", "cmo"]);
  assert.deepEqual(toggleSeat(["cao", "coo", "cto", "cmo"], "coo"), ["cao", "cto", "cmo"]);
  assert.deepEqual(toggleSeat(["cao", "coo"], "cao").concat(), ["coo"]);
});

test("the table opens with the CAO and COO and is a valid table", () => {
  assert.deepEqual(DEFAULT_SEATS, ["cao", "coo"]);
  assert.ok(canConvene(DEFAULT_SEATS));
});

test("at least two seats are needed, and exactly the 11 combinations of 2 to 4 are allowed", () => {
  assert.equal(MIN_SEATS, 2);
  assert.equal(canConvene([]), false);
  for (const seat of SEATS) assert.equal(canConvene([seat]), false, seat);
  let allowed = 0;
  for (const size of [2, 3, 4]) {
    for (const combo of combos(SEATS, size)) {
      assert.ok(canConvene(combo), combo.join());
      allowed++;
    }
  }
  assert.equal(allowed, 11);
});

test("the problem line says how many are chosen and disappears once the table is valid", () => {
  assert.match(seatProblem(["cto"]), /at least 2 .*\(1 chosen\)/);
  assert.match(seatProblem([]), /\(0 chosen\)/);
  assert.equal(seatProblem(["cto", "cmo"]), null);
});

test("duplicates and unknown ids never count as extra seats", () => {
  assert.equal(canConvene(["cto", "cto"]), false);
  assert.equal(canConvene(["cto", "nobody"]), false);
});

test("roles match the backend: the COO chairs and the CAO writes the options when they sit", () => {
  assert.deepEqual(assignRoles(["cao", "coo", "cto", "cmo"]), { chair: "coo", synthesizer: "cao" });
  assert.deepEqual(assignRoles(["cao", "coo"]), { chair: "coo", synthesizer: "cao" });
});

test("roles fall back when the COO or the CAO is absent, and the chair never writes the options", () => {
  assert.deepEqual(assignRoles(["cto", "cmo"]), { chair: "cto", synthesizer: "cmo" });
  assert.deepEqual(assignRoles(["cao", "cmo"]), { chair: "cmo", synthesizer: "cao" });
  assert.deepEqual(assignRoles(["coo", "cto"]), { chair: "coo", synthesizer: "cto" });
  for (const size of [2, 3, 4]) {
    for (const combo of combos(SEATS, size)) {
      const { chair, synthesizer } = assignRoles(combo);
      assert.ok(combo.includes(chair) && combo.includes(synthesizer), combo.join());
      assert.notEqual(chair, synthesizer, combo.join());
    }
  }
});

test("the role note is empty for the usual table and names the stand-ins otherwise", () => {
  assert.equal(roleNote(["cao", "coo"]), "");
  assert.equal(roleNote(["cao", "coo", "cto", "cmo"]), "");
  assert.match(roleNote(["cto", "cmo"]), /CTO will chair .*COO is not at the table.*CMO will write the options .*CAO is not at the table/);
  assert.equal(roleNote(["cto"]), "");
});
