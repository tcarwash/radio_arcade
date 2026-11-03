# Radio Arcade engine adapters

These adapters run ordinary local games over **the existing Radio Arcade
transport**. A game sends deterministic, compact binary actions; Radio Arcade
handles session rosters, RF delivery, revision ordering and durable logging.
PICO-8 continues to use game ID 9 and `/api/pico8/*`. New external engines
share **game ID 10**, the `external-game` backend, and `/api/engine/*`.

| Engine | Language | Location | Sample |
| --- | --- | --- | --- |
| LÖVE 11.x | LuaJIT | [`love/radio_arcade/`](love/radio_arcade/) | [`artillery/main.lua`](love/examples/artillery/main.lua) |
| Godot 4.x | GDScript | [`godot/addons/radio_arcade/`](godot/addons/radio_arcade/) | [`strategy`](godot/examples/strategy/) |

## Shared application contract

All actions have the same on-air representation, regardless of game engine:

1. The host selects a game ID (printable ASCII, max 48 bytes), version (1–32767),
   and mode (`turn` or `event`). A 32-bit FNV-1a hash of the ID identifies the
   game at every station. The first committed action is the eight-byte config
   header `00 + hash:u32BE + version:u16BE + mode:u8`.
2. Each application action is `01 + 0..189 binary bytes`.
3. Only the coordinator may end a session with `02` (including out-of-turn).
4. The dealer commits actions in revision order. A client only applies actions
   **after** reading the committed log, not when the user presses a key.
5. Clients refuse actions if the session's game hash, version, or mode differs.

The backend stores up to 65,535 committed actions per session. The API streams
up to 256 at a time and adapters request subsequent pages in revision order.
Actions are plaintext RF traffic; don't transmit secrets or copyrighted game
assets. Models/graphics are installed locally and never sent over RF.

### Session setup

Run `radio-arcade-web` (default `http://127.0.0.1:8765`) on **each** station,
configured for practice, connected, or broadcast transport. Create a game via
the API:

```sh
curl -s http://127.0.0.1:8765/api/engine/create \
  -H 'Content-Type: application/json' \
  -d '{"peer":"K7ABC"}'
```

For a broadcast hosted game instead use `{"hosted":true}`. Your peer joins
with `POST /api/engine/control` and `{"session_id":"...","command":"join_game"}`;
the host starts it using `{"session_id":"...","command":"start_game"}`.
For connected mode, the invited station uses `accept_invite` instead of
`join_game`. Both stations launch the same engine game with the **same session
ID**. The adapter sends the game identity automatically when the coordinator's
game is active. For local single-process debugging, practice mode auto-creates
an active hot-seat session without actual RF transmissions.

### Local API (JSON)

| Method | Path | Meaning |
| --- | --- | --- |
| `GET` | `/api/engine/sessions` | External tables, callsign, transport |
| `POST` | `/api/engine/create` | Create a new external game session |
| `POST` | `/api/engine/control` | Join, accept, invite, start, request core sync, resend |
| `GET` | `/api/engine/events?session_id=...&after=N` | Ordered committed binary actions as hex |
| `POST` | `/api/engine/action` | Submit an action as `{session_id,hex}` |
| `GET` | `/api/engine/opaque?session_id=...&after=N` | Read generic `RA:` side messages |
| `POST` | `/api/engine/opaque` | Send 200-byte max UTF-8 side message |
| `GET` | `/api/engine/delivery?session_id=...&sequence=N` | Inspect side-message ACK state |
| `POST` | `/api/engine/estimate` | Exact RA frame size, hypothetical RF airtime |

`estimate` accepts `payload_bytes` **before** the one-byte `EVENT` tag, plus
`baud`, `bits_per_symbol`, `code_rate`, or an effective `rate_bps`. Estimates
exclude modem scheduling, ACKs, retries, station ID packets, and other changing
RF conditions. The endpoint assumes the 8-byte v6 compact header for active
on-air sessions (or accepts `"compact":true`/`false` explicitly); the one-byte
`EVENT` tag is counted in the action payload. The full-envelope fallback is
used for setup, unknown session references, and session ID collisions.

Only localhost should reach this unauthenticated HTTP API. Each station runs
its own bridge; pointing the game at the other station's HTTP API would skip
the radio link. If you deliberately bind the bridge beyond loopback, protect
it separately and understand that the existing browser/game APIs have no auth.

### Recovery boundaries

Normal reconnection replays persisted committed actions from revision zero;
Radio Arcade's transport separately repairs its own session state. If a station
was offline during an unrecoverable RF gap, **session sync alone cannot
reconstruct arbitrary application state**. Both adapters now support
application-level checkpoint recovery when the developer registers a binary
save/load callback on *both* stations:

1. The joining/lagging game detects a missing committed action and sends
   `RA:REQ:<8-digit nonce>` to the session coordinator.
2. The coordinator serializes its fully applied game state and splits up to
   16 KiB into 90-byte chunks. Each `RA:S` chunk carries the nonce, revision,
   chunk index/count, 32-bit FNV-1a checksum, and Base64 application bytes.
3. One chunk is sent at a time and the sender waits for Radio Arcade's
   existing *delivery acknowledgment* before transmitting the next. A silent
   peer times out; requests/chunks are not transmitted indefinitely.
4. The receiver only trusts the coordinator for the matching nonce, validates
   each chunk and the entire checksum, calls the game's load callback, then
   resumes event replay after the restored revision.

Checkpoints are public plaintext radio traffic. Serialization is game-owned:
send essential state or compact action history, not visual assets. Recovery
requires the coordinator's game process to be running and serving requests.
The requester times out after five minutes, and each side-message ACK after
two minutes. For a busy HF channel, checkpointing 16 KiB may still take a very
long time; smaller states are strongly preferred. These clients are not
continuous real-time netcode.

The reference codec is [`radio_arcade/engine_checkpoint.py`](../radio_arcade/engine_checkpoint.py),
with corruption, ordering and simulated two-station RF tests. The LÖVE and
Godot client implementations use the same `RA:` wire grammar.

## Tests and current limitations

Run `python -m unittest tests.test_external_engine tests.test_engine_checkpoint -v`.
These tests exercise the real Python engine, the generic local API, checksum
validation, and acknowledged checkpoint chunks over simulated two-station RF.
LÖVE and Godot runtime validation still requires their respective engines;
Python tests alone cannot execute the bundled Lua/GDScript clients.

The current clients poll a loopback HTTP service and do not stream gameplay
frames. LÖVE uses a dedicated thread; Godot uses nonblocking `HTTPRequest`.
Both adapters are a **working first pass** at tiny turn-based or event-driven
games; real-engine play testing and UX polish are still to come.
