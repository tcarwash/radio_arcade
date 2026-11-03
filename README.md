# Radio Arcade

Radio Arcade is a multiplayer game engine with desktop, terminal, and browser clients. Games use Data2G KISS broadcast or its connected endpoint; local hot-seat practice works without Data2G. Built-in games: tic-tac-toe, Nim, Connect Four, Hexxagon, Open Word Tiles, and Scorched Earth (desktop and web).

**PICO-8 cartridges:** A reusable [`pico8/radio.lua`](pico8/radio.lua) SDK and
browser GPIO bridge now let cartridges own their game rules while Radio Arcade
handles multiplayer transport, sessions and committed events. Set
`RADIO_ARCADE_PICO8_CARTS` to a directory of trusted PICO-8 HTML exports, run
`radio-arcade-web`, and open `/pico8/` to launch or join a table. See the
[PICO-8 developer guide](pico8/README.md) for examples, checkpoint callbacks,
and restrictions. Native standalone PICO-8 isn't networked by this bridge.

## Install and run

```sh
python -m pip install -e '.[all]'
radio-arcade
radio-arcade-tui
radio-arcade-web
```

Set your callsign and Data2G connection in station settings. The browser client opens at `http://127.0.0.1:8765`. Its API has no authentication; keep it on localhost or a trusted network. Use `--callsign N0CALL --transport practice` with desktop or TUI for local play.

All three clients provide TX enable/disable, a transmission reason indicator, and local game deletion. For Broadcast · KISS, **Broadcast modem mode** applies a Data2G mode to this station's port; leave it blank for Data2G's default. Games may optionally declare `ui_clients = ("web",)` when their frontend is only available in the browser; by default, games appear in all three clients.

## Radio use

Disabled TX blocks radio sends from the client, including automatic acknowledgements and retries; it does not interrupt an in-flight transmission. Observer mode is receive-only except for an explicit snapshot request while TX is enabled. Game traffic is public radio traffic and is not encrypted.

All stations must use the same protocol version. See [PROTOCOL.md](PROTOCOL.md) for wire details.

## Development

**Minimal turn payloads are a primary design goal for fluent radio gameplay.**
Send only the player's compact binary action and deterministically reconstruct
its effects on every station. Keep full state, scenery, and presentation data
out of normal turns; use compact snapshots for starting and recovering games.
Connect Four moves use **one byte** (column 0–6); its recovery board uses **7 bytes**
(previously 13). Scorched Earth moves use **two bytes**. These are game payload
sizes; the shared protocol envelope and radio framing add overhead. Word Tiles
placements use **3–6 bytes**, exchange/pass **1 byte**, and snapshots use sparse
positions or a bitmap according to which is smaller (20 bytes for an empty board).
Older saved moves and snapshots remain readable; upgrade both peers for new encodings.


The UI-independent engine API is documented in [ENGINE-API.md](ENGINE-API.md). Games register through the `radio_arcade.games` entry-point group; assigned IDs are in [GAME-CATALOG.md](GAME-CATALOG.md). Radio Civ is a separate desktop game: [apps/civ/README.md](apps/civ/README.md).

To open a two-window Scorched Earth match over a local simulated radio link, run `uv run python scripts/scorched_earth_two_window.py`. It uses no Data2G or RF hardware; press Ctrl+C to stop it.

Scorched Earth uses five distinct, deterministic **128-column panoramic maps**. The desktop battlefield pans horizontally; the web game adds a draggable minimap and zoom controls. Base terrain is regenerated from the shared map ID on both stations. Each two-byte weapon move deterministically traces its own shot and terrain deformation. Recovery snapshots replay compact **crater events** (column and weapon) for typical matches, without sending map geometry. Very long matches and migrated saves can fall back to sparse terrain differences when the event log grows too large. All stations in a match must upgrade to the same version before playing together.

Desktop, browser, and terminal clients share a match scoreboard with turn indicators, real points or HP, and final results. Completing a live match opens an animated result panel once; loading a finished save does not reopen it. Use **View match result** on desktop/web or **V** in the terminal to view the result again. Local practice names the winner; radio participants see Victory or Defeat.

Scorched Earth now has independent shot power (25–100%) and 0–90° aiming in desktop and web play. All stations must upgrade together. The DOS 1.5 comparison and next parity priorities are tracked in [SCORCHED-PARITY.md](SCORCHED-PARITY.md).

Scorched Earth camera controls (desktop/web): focus the map or minimap and use **WASD / arrow keys**, drag the main map, click to center, or click/drag the minimap. Camera panning does not consume a turn or fuel; **Drive** moves the tank.

Use **Forfeit game** in desktop/web/terminal, or `forfeit ID` in the CLI, to end the table and award the other player(s) the win. Chat and saved state remain available. This update uses protocol **5**; upgrade every radio station together. High Scorched Earth shots now travel above the visible sky and fall back under gravity, with the camera following the full arc.

Hexxagon is available in desktop, terminal, and web clients, including local
practice and radio matches. Select a friendly stone, then a highlighted empty hex:
clone to an adjacent hex or jump two hexes, converting all adjacent enemy stones.
The 58-cell board starts with three stones per player and three central holes.
When the next player cannot move, remaining empty hexes go to the mover; the
larger army wins (equal counts draw). Rules follow [Yucata's Hexxagon rules](https://www.yucata.de/en/Rules/Hexxagon).
Clone actions are **one byte**, jumps **two bytes**, and snapshots **15 bytes**;
board geometry and captures are reconstructed locally. Game ID 8 is reserved.
All participating stations need an updated client to play this new game.
