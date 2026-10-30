# Radio Arcade

Radio Arcade is a multiplayer game engine with desktop, terminal, and browser clients. Games use Data2G KISS broadcast or its connected endpoint; local hot-seat practice works without Data2G. Built-in games: tic-tac-toe, Nim, Connect Four, Open Word Tiles, and Scorched Earth (desktop and web).

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

The UI-independent engine API is documented in [ENGINE-API.md](ENGINE-API.md). Games register through the `radio_arcade.games` entry-point group; assigned IDs are in [GAME-CATALOG.md](GAME-CATALOG.md). Radio Civ is a separate desktop game: [apps/civ/README.md](apps/civ/README.md).

To open a two-window Scorched Earth match over a local simulated radio link, run `uv run python scripts/scorched_earth_two_window.py`. It uses no Data2G or RF hardware; press Ctrl+C to stop it.

Scorched Earth uses 64-column panoramic terrain with horizontal panning on the desktop and web clients. Its wider snapshots are approximately 32 bytes larger than the original 32-column format; old local snapshots are migrated on load, but stations playing together must upgrade to the same version before starting a new match.
