"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
require("../radio_arcade/web_assets/pico8_bridge.js");
const Pico8Bridge = globalThis.RadioArcadePico8Bridge;
const sid = "0123456789ABCDEF";
const fnv = arr => arr.reduce((h, c) => Math.imul(h ^ c, 16777619) >>> 0, 2166136261);
const toHex = arr => arr.map(x => x.toString(16).padStart(2, "0")).join("");

function mockBackend() {
  const state = { events: [], callsign: "AG7SU", session: {
    id: sid, game_id: 9, coordinator: "AG7SU", players: ["AG7SU", "K7ABC"],
    status: "active", revision: 0, current_player: "AG7SU", has_pending_action: false,
    view: { cart_hash: 0, cart_version: 0, mode: "turn" }
  }};
  const api = async (path, request = null) => {
    const data = request ? JSON.parse(request.body) : {};
    let result;
    if (path.startsWith("/api/pico8/events")) {
      const after = Number(new URL(path, "http://local/").searchParams.get("after"));
      result = { session: { ...state.session, view: { ...state.session.view } },
        revision: state.session.revision, events: state.events.filter(e => e.revision > after) };
    } else if (path === "/api/pico8/sessions") {
      result = { callsign: state.callsign, transport: "broadcast", sessions: [state.session] };
    } else if (path.startsWith("/api/pico8/opaque")) {
      result = { messages: [] };
    } else if (path === "/api/pico8/action") {
      const action = Array.from(Buffer.from(data.hex, "hex"));
      if (action[0] === 0) {
        state.session.view.cart_hash = ((action[1] * 0x1000000) + (action[2] << 16) +
          (action[3] << 8) + action[4]) >>> 0;
        state.session.view.cart_version = action[5] * 256 + action[6];
        state.session.view.mode = action[7] ? "event" : "turn";
      } else if (action[0] === 1) state.session.current_player = "K7ABC";
      state.session.revision += 1;
      state.events.push({ revision: state.session.revision, actor: "AG7SU", hex: data.hex });
      result = { ok: true };
    } else throw Error(`unexpected endpoint ${path}`);
    return { ok: true, json: async () => result };
  };
  return { state, api };
}

test("GPIO hello configures an existing table and relays a compact event", async () => {
  const { api, state } = mockBackend();
  const gpio = new Array(128).fill(0);
  const bridge = new Pico8Bridge({ sessionId: sid, api, gpio });
  const hello = [...Buffer.from("test-cart"), 0, 1, 0, 0];
  gpio[0] = 1; gpio[2] = 1; gpio[3] = hello.length;
  hello.forEach((b, i) => { gpio[4 + i] = b; });
  bridge.tick();
  assert.equal(gpio[1], 1, "the transmit mailbox is acknowledged");
  await new Promise(resolve => setImmediate(resolve));
  await bridge.refresh();
  await bridge.refresh();
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].hex.startsWith("00"), true);

  gpio[0] = 2; gpio[2] = 3; gpio[3] = 1; gpio[4] = 42;
  bridge.tick();
  await bridge.refresh();
  await bridge.refresh();
  assert.equal(state.events.length, 2);
  assert.equal(state.events[1].hex, "012a");
  assert.ok(bridge.out.some(([kind, data]) => kind === 18 && data.at(-1) === 42));

  // The browser never overwrites an unread receive-mailbox message.
  bridge.tick();
  const prior = gpio[64];
  bridge.tick();
  assert.equal(gpio[64], prior);
  gpio[65] = prior;
  bridge.tick();
  assert.notEqual(gpio[64], prior);
});

test("snapshot fragments are integrity checked and rebuild cart state", () => {
  const { api } = mockBackend();
  const bridge = new Pico8Bridge({ sessionId: sid, api, gpio: new Array(128).fill(0) });
  bridge.session = { coordinator: "AG7SU", players: ["AG7SU", "K7ABC"] };
  bridge.callsign = "K7ABC";
  bridge.expectedSnapshot = "b123";
  bridge.recoveryRequested = true;
  const data = [0, 1, 2, 255];
  const checksum = fnv(data).toString(16);
  const encoded = Buffer.from(data).toString("base64");
  bridge.acceptSnapshot(["P8", "S", "b123", "7", "0", "1", checksum, encoded], "AG7SU");
  assert.equal(bridge.lastRevision, 7);
  assert.equal(bridge.recoveryRequested, false);
  assert.deepEqual(bridge.out.at(-1), [21, data]);
});

test("wrong cartridge version prevents a playable status and action delivery", async () => {
  const { api, state } = mockBackend();
  const bridge = new Pico8Bridge({ sessionId: sid, api, gpio: new Array(128).fill(0) });
  bridge.callsign = "AG7SU";
  bridge.hello = { hash: 1234, version: 1, mode: 0 };
  bridge.session = { status: "active", revision: 1, players: ["AG7SU", "K7ABC"],
    current_player: "AG7SU", view: { cart_hash: 1234, cart_version: 2, mode: "turn" } };
  bridge.status();
  assert.equal(bridge.statusCode, 4);
  assert.match(bridge.error, /Different cartridge/);
  state.session.view = { cart_hash: 1234, cart_version: 2, mode: "turn" };
  state.session.revision = 1;
  state.events.push({ revision: 1, actor: "AG7SU", hex: "0105" });
  await bridge.refresh();
  assert.equal(bridge.lastRevision, 0);
  assert.equal(bridge.out.some(([type]) => type === 18), false);
});

test("dealer can finish a turn-based cartridge out of turn", async () => {
  const { api, state } = mockBackend();
  state.session.view = { cart_hash: 1234, cart_version: 1, mode: "turn" };
  state.session.current_player = "K7ABC";
  const bridge = new Pico8Bridge({ sessionId: sid, api, gpio: new Array(128).fill(0) });
  bridge.hello = { hash: 1234, version: 1, mode: 0 };
  bridge.callsign = "AG7SU";
  bridge.session = state.session;
  bridge.actions.push([2]);
  await bridge.pumpActions();
  assert.equal(state.events[0].hex, "02");
});
