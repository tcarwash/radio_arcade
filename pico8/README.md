# PICO-8 Multiplayer via Radio Arcade

This is the **generic PICO-8 cartridge library**, not a Python port of each
game. PICO-8 owns cartridge rules, drawing, and compact application state.
Radio Arcade owns callsign identity, lobbies, the dealer, event ordering,
transport retries, and persistence. The existing on-air protocol stays at v5.

## Runtime setup

1. Install Radio Arcade (`pip install -e '.[all]'`) and configure Data2G,
   callsign, KISS broadcast or connected mode via `radio-arcade-web`.
2. Develop a cartridge with `#include radio.lua` (copy that library next to
   the cart or use an appropriate PICO-8 include path). You can also include
   `radio_debug.lua` for offline `radio.estimate(...)` calculations.
3. Export the cartridge from PICO-8 to **HTML**, including its generated JS.
   Native PICO-8 instances do not have this browser GPIO bridge.
4. Put the exported `.html` and `.js` files in a directory and start the web
   client with `RADIO_ARCADE_PICO8_CARTS=/absolute/path/to/exports`.
5. Visit `http://127.0.0.1:8765/pico8/` on each station. Create or join a
   PICO-8 table, then open the **same** exported cartridge with that session.

For local development, select Radio Arcade's **practice** transport before
creating the table. It uses local hot-seat turns and no RF transmissions.
For two independent stations, use Data2G, or the simulated in-process link in
the tests. Hosted radio tables require broadcast KISS; connected sessions
support two participants. Don't expose the unauthenticated local web API to the
internet. Only load trusted exported HTML/JavaScript.

The bundled [`examples/radio_dots.p8`](examples/radio_dots.p8) is a simple
two-seat cartridge. Its `#include ../radio.lua` path assumes the supplied
directory layout.

## Cartridge API

```lua
#include radio.lua

function _init()
 radio.init("my-game",1,{mode="turn"})  -- or mode="event"
 radio.snapshot(
  function() return encode_my_state() end,
  function(bytes) decode_my_state(bytes) end)
end

function _update()
 radio.poll()
 if radio.my_turn() and btnp(4) then
  radio.send(chr(3)..chr(7)) -- exactly two game-action bytes
 end
 while true do
  local event=radio.receive()
  if not event then break end
  apply_my_action(event.player,event.data)
 end
end
```

| Function | Meaning |
| --- | --- |
| `radio.init(id, version, opts)` | Identify cartridge and choose turn/event mode |
| `radio.poll()` | Process GPIO once per PICO-8 frame (30/60 FPS) |
| `radio.send(binary_string)` | Queue 0–189 application bytes; never transmits immediately |
| `radio.receive()` | Pop `{player, revision, data}` for a committed action |
| `radio.my_turn()` | True when this player may send (or during event mode) |
| `radio.connected()` | True when the cartridge has a compatible active table |
| `radio.player()` | Zero-based station index in the roster |
| `radio.revision()` | Last server revision announced to the cartridge |
| `radio.error()` | Most recent bridge or validation error |
| `radio.snapshot(save_fn, load_fn)` | Register binary-string state serialization callbacks |
| `radio.request_sync()` | Ask the dealer for a fresh application checkpoint |
| `radio.finish()` | Ask the dealer to finish the session (host only) |
| `radio.estimate(...)` | Optional standalone debug helper in `radio_debug.lua` |

**Use deterministic events.** For example send `chr(column)` for Connect Four,
not the entire board. All clients run the same cartridge and process identical
committed events in the same order. Use a deterministic RNG seed if actions
depend on randomness. Do not include large scenery, animation frames, or
graphics in event payloads. Cart identity is the FNV-1a 32-bit hash of the
ASCII id plus a 16-bit version. A mismatched cart/version is refused locally.

### Recovery and snapshots

The backend permanently logs committed PICO-8 actions in local SQLite. On a
fresh HTML launch, the bridge replays them from revision zero. If an RF commit
is missed, Radio Arcade first repairs its own control state using the normal
v5 `SNAPSHOT`; if the cartridge's event history has a gap, the bridge asks
the dealer's running cartridge for a **full application checkpoint**.

`save_fn` must return a binary PICO-8 string containing the complete game
state; `load_fn(bytes)` restores it. The library fragments it across the
128-byte GPIO mailbox, then the bridge sends acknowledged, plaintext PICO-8
checkpoint chunks through Radio Arcade's existing opaque side messages.
Chunks carry a nonce, revision, order, and integrity checksum. The maximum
checkpoint in this release is **16 KiB**; larger game state must be compressed
or represented by a seed plus compact event history. Checkpoint recovery
requires the dealer's browser/cartridge to remain available. Checkpoint
messages consume substantial RF airtime, so keep snapshots compact.

Passive Radio Arcade observers can follow a cartridge session if they heard
the start and a contiguous event history. As with the core observer feature,
they do not transmit automatically; a listener joining after missing cart
events cannot currently request the cartridge's application checkpoint as a
non-participant. Participating stations have full checkpoint recovery.

No checkpoint, action, or side message is encrypted or authenticated beyond
Radio Arcade's existing callsign/session handling. All radio traffic is public.

## Wire and GPIO details

- Two 64-byte mailboxes in GPIO `0x5f80`–`0x5fff`; each has a sequence byte,
  acknowledgement byte, command byte, length byte, and 60 data bytes.
- Lua and JavaScript separately acknowledge consumed mailbox frames; no
  overwrite while a frame is unread.
- PICO-8 binary actions are wrapped as `01 + game bytes`, with a reserved
  `00 + hash:u32 + version:u16 + mode:u8` configuration action, and `02` finish.
- Radio Arcade game ID **9**, v5 frame envelope. Its 16-byte compact state
  tracks cartridge identity/version, event count, digest, turn and completion.
- Normal ACTION/COMMIT frames stay small. Larger checkpoints travel only via
  explicitly requested, acknowledged `OPAQUE_STATE` chunks.
- The bridge's local HTTP routes are `/api/pico8/*`; exported cartridges are
  served only from `RADIO_ARCADE_PICO8_CARTS` and get the bridge script injected.

## Tests

Run `python -m unittest tests.test_pico8 -v` and
`node --test tests/pico8_bridge.test.js`. These cover the deterministic game
codec, hosted simulated radio link, replay persistence, opaque recovery
messages, and GPIO mailbox integration. An actual PICO-8 executable is not
bundled here, so render/play testing an exported cart is a separate hardware /
license-dependent validation step.
