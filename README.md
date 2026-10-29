# Radio Arcade

Radio Arcade is a reusable, headless game engine with a native desktop client and a keyboard-first TUI. The same session and game logic runs over Data2G KISS broadcast or its VARA-like connected endpoint. Built-in games include tic-tac-toe, Nim, and Open Word Tiles, a two-player open-rack Scrabble-style game.

To exercise a game against two local native Data2G hosts joined by audio pipes, run `python scripts/data2g_smoke.py --mode broadcast` or `python scripts/data2g_smoke.py --mode connected --reconnect`. The script needs the Data2G native host at `../Data2G/native/build/data2g-host` (or specify `--host-binary`), takes several minutes, and prints the directory containing its logs. This is a simulated audio link.

Run the fast session regression checks with `python -m unittest discover -s tests -v`.

For local hot-seat practice without Data2G, run `python -m radio_arcade.desktop --callsign N0CALL --transport practice` or `python -m radio_arcade.tui --callsign N0CALL --transport practice`. Create a game and take both sides; practice games use a separate local database. You can also choose Local practice in settings.

## Observe broadcast games

Use **Observer mode** in desktop/TUI station settings (Broadcast · KISS only), or run:

```sh
radio-arcade --callsign AG7SU --transport broadcast --observer
radio-arcade-tui --callsign AG7SU --transport broadcast --observer
```

An observer overhears START, SNAPSHOT and COMMIT packets addressed to other players in the selected Data2G broadcast group. Live tables appear automatically, with boards, rosters, turns and overheard side chat. Observers do not join the roster, ACK, retry, play, or chat. Observed games use a separate `observer.sqlite3` database.

If tuning in after START, the first overheard COMMIT creates a table marked **Awaiting snapshot**. If a move is missed, the last known board remains visible with a recovery warning. Select **Request snapshot (TRANSMITS)** to send a single explicit sync request to the coordinator; there are no automatic on-air requests or retries from the observer. Both clients clearly label this control as transmitting (desktop also asks for confirmation). The coordinator responds with a single unacknowledged snapshot addressed to the observer. A game with no overheard frames cannot yet be discovered. Connected Data2G observation is not supported.

In the TUI on a narrow terminal, press `1`, `2`, or `3` to switch between the lobby, board, and chat. Use Tab to reach controls and scroll them into view.

## Install and launch

The core library has no GUI dependency. Install both clients from this checkout with:

```sh
python -m pip install -e '.[all]'
radio-arcade
radio-arcade-tui
```

On first launch, enter your station callsign and Data2G host in the setup panel. Settings persist under the platform's user data directory (override it with `RADIO_ARCADE_HOME`). You can open the settings panel later to change the link.

For an explicit configuration:

```sh
radio-arcade --callsign N0CALL --host 127.0.0.1 --transport broadcast
radio-arcade-tui --callsign N0CALL --host 127.0.0.1 --transport connected
```

Broadcast mode opens the `RAGAMES` group using Data2G's command TCP port (8300), then connects to KISS (8100). Connected mode registers the callsign, listens for incoming calls, and uses command TCP 8300 plus its byte-stream data port 8301. Both clients show link, invite, turn, acknowledgement, and recovery status.

All participants must run clients using the same Radio Arcade protocol version. The current version is 4; it does not interoperate on air with version 3. Update both stations together when continuing an older game. Settings also offer midnight, daylight, and monochrome themes and optional invitation/turn sounds.

## Play

- Create Tic-tac-toe, Nim, or Open Word Tiles from the game selector and invite a station.
- In Open Word Tiles, racks are public. Enter a start square such as `H8`, choose across/down, and type the word; use `{A}` to mark a blank tile assigned as A. ENABLE1 is the shared lexicon. This is an open-rack variant because seed-derived racks are visible to all stations.
- In the TUI's Open Word Tiles board, click a square to choose the start coordinate. Its cursor blinks, and typed letters preview along the selected Horizontal/Vertical direction. Conflicting letters and board-edge overflow are highlighted/warned locally; nothing transmits until you press Place.
- For Nim, invite one to three other stations into the lobby and start when the roster is ready.
- Select a received invitation and accept it.
- Make a move only when the turn indicator says it is yours.
- Use side chat in the selected game to coordinate.
- Use `radio-arcade-cli --callsign N0CALL` for the headless reference client and scripting examples.

Connected mode supports one connected peer per station at a time. Several game instances can share one connection to that peer. Broadcast mode supports multiple peers. Game traffic is public radio traffic: destination callsigns route application packets but do not provide privacy.

## Engine library

The public, UI-independent API is in [ENGINE-API.md](ENGINE-API.md). Import `ArcadeService`, register/select a game, subscribe to typed events, and call methods such as `create_game`, `accept_invite`, `legal_action`, `send_chat`, and `request_sync`. The service owns the transport and local SQLite session store. It persists game state and chat, validates turns at the coordinator, retries unacknowledged messages, and exchanges snapshots to repair state after reconnects.

Game rules implement the plugin contract in `radio_arcade.games.base.Game`. Installed games register through the `radio_arcade.games` Python entry-point group with a permanent one-byte game ID. Game code does not import Qt, Textual, sockets, or Data2G. The desktop and TUI render neutral view models from the same engine.

Assigned IDs are listed in [GAME-CATALOG.md](GAME-CATALOG.md); don't reuse retired IDs.

The current versioned packet starts with a one-byte **game type ID**, followed by a protocol version, message kind, flags, a 64-bit instance ID, sequence/revision fields, and compact callsign routing. Packets are bounded to 240 bytes. Connected mode adds a two-byte big-endian length because its Data2G endpoint is a byte stream. Opaque game state uses an explicit flag and stays readable plaintext; the bundled player interfaces hide its content and the rotating `opaque-debug.log` records it for debugging. The flag is not privacy or encryption. See [PROTOCOL.md](PROTOCOL.md) for the packet layout.

See [GAME-GOALS.md](GAME-GOALS.md) for architecture, product goals, turn coordination, constraints, and the game roadmap.
For a broader candidate list, see [GAME-IDEAS.md](GAME-IDEAS.md). These ideas are not implemented or assigned wire IDs until their rules are specified.

