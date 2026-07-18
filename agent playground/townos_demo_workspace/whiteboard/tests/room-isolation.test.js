const test = require("node:test");
const assert = require("node:assert/strict");
const { getStrokesForRoom } = require("../src/rooms");

test("room strokes are isolated", () => {
  const strokes = [
    { id: "a", roomId: "alpha", points: [[1, 1]] },
    { id: "b", roomId: "beta", points: [[2, 2]] },
  ];
  assert.deepEqual(getStrokesForRoom(strokes, "alpha"), [strokes[0]]);
});
