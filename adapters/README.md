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
exclude modem scheduling, ACKs, retries and other changing RF conditions.

Only localhost should reach this unauthenticated HTTP API. Each station runs
its own bridge; pointing the game at the other station's HTTP API would skip
the radio link. If you deliberately bind the bridge beyond loopback, protect
it separately and understand that the existing browser/game APIs have no auth.

### Recovery boundaries

Normal reconnection replays persisted committed actions from revision zero;
Radio Arcade's transport separately repairs its own session state. If a station
was offline during an unrecoverable RF gap, **session sync alone cannot
reconstruct arbitrary application state**. These first adapters detect the gap
and report `application checkpoint required` rather than silently rendering a
divergent game. The generic `RA:` side-message channel is available for
application-owned compressed checkpoints, but automatic checkpoint chunking
and negotiated recovery are **not implemented yet**. Don't treat these sample
games as drop-in continuous real-time netcode.

## Tests and current limitations

Run `python -m unittest tests.test_external_engine -v`. These tests exercise
the real Python engine and local API, including a two-station simulated-radio
exchange. LÖVE and Godot runtime validation require their respective engines
installed; Python tests cannot execute the bundled Lua/GDScript clients.

The current clients poll a loopback HTTP service and do not stream gameplay
frames. LÖVE uses a dedicated thread; Godot uses nonblocking `HTTPRequest`.
Both adapters are a **working first pass** at tiny, turn-based or event-driven
games, with generic RF checkpoint automation and UX polish still to come.
