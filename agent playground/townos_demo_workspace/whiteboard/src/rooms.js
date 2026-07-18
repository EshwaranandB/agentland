function getStrokesForRoom(strokes, roomId) {
  // Deliberate P0 defect: Builder must scope results to the requested room.
  return strokes;
}

module.exports = { getStrokesForRoom };
