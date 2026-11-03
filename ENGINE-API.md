# Headless engine API

`radio_arcade` is the reusable game engine package. Importing it does not import PySide6 or Textual and does not require a graphical display. The desktop application and TUI are clients of the same `ArcadeService` API.

The current binary envelope and built-in game payloads are described in [PROTOCOL.md](PROTOCOL.md).

## Create a service

```python
from radio_arcade import ArcadeService
from radio_arcade.connected import ConnectedConnection

transport = ConnectedConnection("127.0.0.1", 8300, "N0CALL")
engine = ArcadeService("N0CALL", transport)
unsubscribe = engine.subscribe(on_engine_event)
```

For broadcast KISS, open a Data2G broadcast group and pass the resulting `KissConnection` instead:

```python
from radio_arcade.transport_factory import BroadcastConnection

transport = BroadcastConnection("127.0.0.1", 8100, 8300, "RAGAMES")
engine = ArcadeService("N0CALL", transport)
```

## Start and play

```python
session_id = engine.create_game("tic-tac-toe", peer="W1ABC")
# On the invited station:
engine.accept_invite(session_id)
# Submit zero-based cell 0 through 8 when it is your turn:
engine.legal_action(session_id, bytes([4]))
engine.send_chat(session_id, "Nice move")
```

## Broadcast dealer / hosted multiplayer

Hosted sessions require a broadcast transport, and use one dealer broadcast per state change rather than per-recipient ACKs:

```python
# On the dealer station:
sid = engine.create_game("nim", hosted=True)  # broadcasts HOST_ANNOUNCE
# On a station that discovered that lobby via engine.sessions():
engine.join_game(sid)                  # sends JOIN_REQUEST; roster broadcast confirms seat
# On the dealer, before starting:
engine.set_reply_order(sid, "AG7SU,K7ABC,N7XYZ")  # dealer always first
engine.announce_game(sid)             # optional manual re-announcement
engine.start_game(sid)                # one START broadcast
# The game view's current_player designates the only station permitted to act.
engine.legal_action(sid, b"\x00\x01")  # Nim: take one item from pile zero
```

If a remote hosted `ACTION` is lost, `engine.resend_pending_action(sid)` repeats that exact action. `engine.request_sync(sid)` asks for one snapshot when a player notices missing state; automatic catch-up also occurs if a gap in broadcast revisions is detected. The observer API remains strictly receive-only unless the user explicitly requests a snapshot. No application-level per-recipient ACK/retry exists for hosted messages. `set_reply_order` takes a comma-separated string or a list/tuple, contains all seated callsigns exactly once, and is only allowed in the open lobby.

Subscribe to typed `EngineEvent` values such as `invitation`, `session_updated`, `chat_message`, `chat_delivery`, `delivery`, and `transport_status`. Callbacks run on the receive or retry thread; GUI callers must marshal them onto their own UI thread. `engine.session(id)` and `engine.sessions()` return session metadata and the game's UI-neutral view model. The local SQLite store retains sessions, chat, and messages awaiting acknowledgement. Side chat is available after the game starts, including after its result. Call `unsubscribe()` and `engine.close()` during shutdown.

The Python game contract is API version 1; `api_version` is separate from the on-air envelope version. Calls that operate on a session may raise `ValueError` for an invalid request, `KeyError` for an unknown session, or a transport/socket error. A transport send failure can also be reported asynchronously as `transport_send_error`, with the message retained for retry. Applications should surface these errors and retain user input when appropriate.

Callbacks may run on the thread calling an engine method as well as receive and retry threads. They can overlap, and no global ordering across callback threads or sessions is guaranteed. Use a session's revision and sequence to order committed game changes; marshal events through your own queue when a single consumer needs serialized notifications. Callback exceptions are isolated from the engine. `subscribe()` returns an unsubscribe function. Stop initiating operations, unsubscribe, then call `close()` once the application is finished; it stops the retry worker and closes the supplied transport and store. Do not reuse the engine or its store after closing it.

`engine.send_opaque_state(session_id, target, text)` sends up to 200 bytes of readable UTF-8 as a reliable, acknowledged game-state side message. Receivers store it and emit `opaque_state` with `hidden_from_ui=True`; bundled UIs show a synchronization notice but never render its body. `engine.opaque_state_history(session_id)` is available to game backends. The payload remains plain text on air and is written in readable form to the rotating `opaque-debug.log`; the flag provides no privacy or encryption.

A game plugin may own its opaque text format by implementing `encode_opaque_state(value) -> str` and `decode_opaque_state(text)`. `engine.send_game_opaque_state(session_id, target, value)` invokes the selected game's encoder. Receivers emit the raw hidden `opaque_state` notice and a decoded `game_opaque_state` event; the UI must continue to hide both payload forms from players. Opaque schemas are game-specific, readable UTF-8, and limited to 200 encoded bytes.

The service owns the transport and store passed to it. It schedules bounded retries for important packets, validates turn order at the coordinator, commits actions by revision, suppresses duplicate chat, and serves/request snapshots to repair state after reconnects.

## Native game-engine adapters

Native game-engine adapters for **LÖVE2D and Godot** now share an
engine-independent session type (game ID 10) and local JSON API. See
[`adapters/README.md`](adapters/README.md) for setup, engine SDKs, demo games,
wire actions and exact recovery limitations. Existing PICO-8 sessions remain
on game ID 9; the PICO-8 GPIO endpoints are unchanged.

## Developer payload/airtime estimates

Use `radio_arcade.airtime.estimate_action()` to preview the **exact** Radio
Arcade application frame size for a proposed binary action, including callsigns
and the actor/action wrapper, without transmitting anything:

```python
from radio_arcade.airtime import estimate_action

size = estimate_action(2, source="AG7SU", destination="K7ABC")
print(size.payload_bytes, size.radio_arcade_frame_bytes)  # 2 37

# Hypothetical 600-baud 8-FSK, rate-1/2 FEC (3 * 0.5 bits/symbol):
estimate = estimate_action(2, source="AG7SU", destination="K7ABC",
                           baud=600, bits_per_symbol=3, code_rate=0.5)
print(estimate.effective_rate_bps)       # 900.0
print(estimate.estimated_airtime_ms)     # 330.0, not a measurement
```

Alternatively, pass `rate_bps` as the assumed effective bitrate, without
`baud` or modulation parameters. `modem_overhead_bytes`, `preamble_ms`, and
`transmissions` permit explicit per-transmission overhead and repeats.
Using `baud` requires `bits_per_symbol` (e.g. 1 for 2-FSK, 2 for 4-FSK, 3 for
8-FSK); the FEC `code_rate` defaults to 1 for an uncoded estimate. The helper
reports `estimated_symbols` for the baud-based calculation, rounded up to
complete symbols. With no rate, airtime is `None` but frame bytes are exact.

**These are planning estimates, not Data2G airtime readings.** Actual time
also depends on modem preamble/pilots, FEC block padding, interleaving,
transmission scheduling, adaptive mode changes, turnarounds, ACKs and retries.
KISS/TCP delimiters are not included as on-air bytes. In hosted play, estimate
the participant's directed ACTION and the dealer's broadcast COMMIT as two
separate frames. This utility is intended to back the proposed PICO-8
`radio.estimate()` debug API when that bridge is implemented.

For cartridge developers, [`pico8/radio_debug.lua`](pico8/radio_debug.lua)
already provides the matching **offline** `radio.estimate(payload_bytes, opts)`
function. Its duration is in seconds to avoid PICO-8's limited fixed-point
integer range. It does not depend on the networking bridge:

```lua
-- #include pico8/radio_debug.lua
local info=radio.estimate(2,{source="AG7SU",destination="K7ABC",
 baud=600,bits_per_symbol=3,code_rate=0.5})
-- info.radio_arcade_frame_bytes == 37
-- info.estimated_airtime_s is about 0.33 (estimate, not measurement)
```

## Add a game plugin

Implement the game contract in `radio_arcade.games.base.Game`: API version, stable one-byte `game_id`, unique ASCII `key`, name, player limits, deterministic `new_state`, `current_player`, `apply_action`, state encode/decode, action description, and a UI-neutral `view_model`. Rules receive bytes for actions and never import a UI toolkit or transport. Register an installed factory under the `radio_arcade.games` Python entry-point group:

Set optional `ui_clients` on a game to a tuple drawn from `"desktop"`, `"tui"`, and `"web"` to limit which game pickers show it. The default is all three clients. This is for games whose complete playable frontend is only available on selected interfaces; shared transport, session, and rule behavior remains in the engine.

Game state is a dictionary with a boolean `finished` key. Return it from `new_state`, `apply_action`, and `decode_state`; the engine uses it to mark the session complete and restore its status from snapshots. `current_player` returns `None` once the game is finished. The encoded state and actions must decode consistently on every participant's installed copy of the game.

Games whose state depends on the roster can optionally implement `validate_roster(state, players)` and raise `ValueError` for a mismatch. The engine calls it before accepting a start or recovery snapshot.

```toml
[project.entry-points."radio_arcade.games"]
my-game = "my_radio_game:MyGame"
```

IDs 1–3 are assigned to tic-tac-toe, Nim, and Open Word Tiles. ID 4 is retired and cannot be reassigned. IDs are permanent; a plugin using a duplicate or retired ID or the wrong API version is rejected. Plugin payloads must keep a full packet within the 240-byte on-air limit. All game messages are public radio traffic, including addressed messages.

Open Word Tiles (ID 3) uses a fixed, bundled ENABLE1 lexicon and open racks so every station can validate words and deterministically reproduce tile draws.

Third-party plugins are executable Python code. Install only plugins you trust.

For a complete small rules implementation, see [examples/countdown_game.py](examples/countdown_game.py). To register it directly in an embedding program:

```python
from radio_arcade import ArcadeService, builtin_registry
from examples.countdown_game import CountdownGame

registry = builtin_registry()
registry.register(CountdownGame())
engine = ArcadeService("N0CALL", transport, registry=registry)
```

Every participating station needs the same rules and ID. The example ID is for local experiments; coordinate an ID before transmitting with a new game.

### Forfeiting a table

`engine.forfeit_game(session_id)` ends an active game and awards the result to
all remaining players. It is available off-turn, but requires a participant
and enabled TX (practice uses the current hot-seat player). The coordinator
commits the result; callers on another station wait for that commit. Results
include `forfeited_by` and `view.winners`, with `view.winner` also populated for
a single winner. `current_player` becomes `None`. The outcome survives save
reload and synchronization, while the plugin's original state remains intact.
Use `session.status` and `session.view` for the authoritative final result.
The game remains in the list and chat remains available. Wire version 5 is
required on all stations.
