# Radio Arcade

Radio Arcade is a multiplayer game engine with desktop, terminal, and browser clients. Games use Data2G KISS broadcast or its connected endpoint; local hot-seat practice works without Data2G. Built-in games: tic-tac-toe, Nim, Connect Four, and Open Word Tiles.

## Install and run

```sh
python -m pip install -e '.[all]'
radio-arcade
radio-arcade-tui
radio-arcade-web
```

Set your callsign and Data2G connection in station settings. The browser client opens at `http://127.0.0.1:8765`. Its API has no authentication; keep it on localhost or a trusted network. Use `--callsign N0CALL --transport practice` with desktop or TUI for local play.

## Radio use

The browser client has a **TX ENABLED / TX DISABLED** control in the top bar. Disabled mode blocks radio game commands; it does not interrupt an in-flight transmission. Observer mode is receive-only except for an explicit snapshot request. Game traffic is public radio traffic and is not encrypted.

All stations must use the same protocol version. See [PROTOCOL.md](PROTOCOL.md) for wire details.

## Development

The UI-independent engine API is documented in [ENGINE-API.md](ENGINE-API.md). Games register through the `radio_arcade.games` entry-point group; assigned IDs are in [GAME-CATALOG.md](GAME-CATALOG.md). Radio Civ is a separate desktop game: [apps/civ/README.md](apps/civ/README.md).
