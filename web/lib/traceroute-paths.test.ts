import assert from "node:assert/strict";
import test from "node:test";

import { traceroutePaths } from "./traceroute-paths.ts";

test("partial gateway observations stop at the last evidenced relay", () => {
  const common = {
    from_node: 0xa0352874,
    to_node: 0x8d85affd,
    request_id: 0,
    want_response: true,
  };

  assert.deepEqual(traceroutePaths({
    ...common,
    traceroute: { route: [0xe0fc04d3, 0x2a607fb2], route_back: [] },
  }), [{ nodes: [0xa0352874, 0xe0fc04d3, 0x2a607fb2], complete: false }]);
  assert.deepEqual(traceroutePaths({
    ...common,
    traceroute: { route: [0xe0fc04d3, 0x04084b74], route_back: [] },
  }), [{ nodes: [0xa0352874, 0xe0fc04d3, 0x04084b74], complete: false }]);
});

test("completed reply joins only its recorded forward and return routes", () => {
  assert.deepEqual(traceroutePaths({
    from_node: 0x8d85affd,
    to_node: 0xa0352874,
    request_id: 417183275,
    traceroute: {
      route: [0xe0fc04d3],
      route_back: [0x734ac380, 0x0d2b80a5, 0xad18b24e, 0x2a607fb2],
    },
  }), [
    { nodes: [0xa0352874, 0xe0fc04d3, 0x8d85affd], complete: true },
    { nodes: [0x8d85affd, 0x734ac380, 0x0d2b80a5, 0xad18b24e, 0x2a607fb2, 0xa0352874], complete: true },
  ]);
});

test("an unrelayed request does not claim a path", () => {
  assert.deepEqual(traceroutePaths({
    from_node: 1,
    to_node: 2,
    request_id: 0,
    traceroute: { route: [], route_back: [] },
  }), []);
});
