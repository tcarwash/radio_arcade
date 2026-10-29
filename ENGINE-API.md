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

## Add a game plugin

Implement the game contract in `radio_arcade.games.base.Game`: API version, stable one-byte `game_id`, unique ASCII `key`, name, player limits, deterministic `new_state`, `current_player`, `apply_action`, state encode/decode, action description, and a UI-neutral `view_model`. Rules receive bytes for actions and never import a UI toolkit or transport. Register an installed factory under the `radio_arcade.games` Python entry-point group:

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
