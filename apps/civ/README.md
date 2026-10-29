# Radio Civ

**Radio Civ** is a standalone, native graphical strategy game powered by the
[Radio Arcade](../../ENGINE-API.md) Python library. It is deliberately **not a
game tab inside Radio Arcade**. Its only client is a PySide6 desktop app. The
rules have no Qt or networking dependencies and register as an explicit game
plugin on the standalone program's private engine registry.

This is a **small, complete 4X campaign**, inspired by civilization-building
strategy games, not a port of Sid Meier's *Civilization*. Expect a compact
8×8 hex world, four possible player seats, short turns, and rules designed
for noisy, low-bandwidth amateur radio, rather than a large civilization
simulation with extensive diplomacy or AI.

## Install and launch

From the Radio Arcade checkout root:

```sh
uv sync --project apps/civ --extra desktop
uv run --project apps/civ --extra desktop radio-civ
```

The standalone project has a local `uv` source override pointing at the
Radio Arcade library in the parent checkout. You can also install it in any
existing virtual environment with:

```sh
uv pip install -e .
uv pip install -e './apps/civ[desktop]'
radio-civ
```

For a different callsign: `radio-civ --callsign AG7SU`.

### Optional browser interface

The web interface uses the **same Python rules and multiplayer service**. It
does not require Qt or any additional web framework. From the checkout:

```sh
uv run --project apps/civ radio-civ-web --mode practice
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in a browser. The web
client also supports the real Data2G radio transport:

If you previously installed the desktop extra in the same environment,
append `--extra desktop` to web-client `uv run` commands to avoid syncing the
Qt packages out of that environment.

```sh
uv run --project apps/civ radio-civ-web --mode radio --callsign AG7SU \
    --host 127.0.0.1 --group RAGAMES
```

`--mode observer` listens receive-only, with a manual snapshot button. The web
server binds to `127.0.0.1` by default. **It has no login/authentication:**
do not expose its control endpoints to the public internet (especially when
attached to a transmitter). A trusted authenticated proxy would be needed for
remote access. One service instance backs one web server; do not run a native
client against the same callsign simultaneously.

Choose **Local hotseat** to try the complete game without a modem or Data2G
host. Both local nations take turns at the keyboard. The other modes use the
Radio Arcade broadcast transport and its usual KISS/command ports.

## Multiplayer in 60 seconds

1. Every station installs the same Radio Civ version and opens the same
   Data2G broadcast group. Select **Data2G · broadcast**, enter a callsign,
   host, KISS port and command port, and connect.
2. The dealer clicks **New Campaign**. The lobby is advertised once.
3. Other stations select that hosted lobby and click **Join Hosted Lobby**.
4. The dealer can optionally set the comma-separated reply order, with the
   dealer first, then clicks **Start Campaign** once 2–4 stations have joined.
5. The current station queues up to two orders locally, then presses **End
   Turn**. That submits one atomic turn bundle; the dealer validates it and
   broadcasts one commit for the complete turn.

The app also supports an **RX-only observer**. It can listen to broadcasts
and inspect the campaign without sending ordinary game traffic. A deliberately
clicked **Request Snapshot** is the only observer transmission.

If a committed turn was missed, click **Request Snapshot**. A participant
whose submitted turn went missing can click **Retry My Order**. Orders still
marked **QUEUED** have not been transmitted yet.
Avoid simultaneous multiplayer apps sharing the same station callsign.

## Playing the campaign

- **Map:** A deterministic 8×8 hex world containing water, plains, forest,
  hills and desert. Start positions have guaranteed accessible terrain.
- **Exploration:** Each nation's discovered hexes persist across turns. Scouts
  can explore; Navigation expands scout visibility. Other nations' hidden
  territory is not shown in your view, but the radio protocol is *not secret*.
- **Economy:** Cities generate food, timber, ore and science when their owner
  ends a turn. Neighboring plains/forests/hills add local yields.
- **Cities:** Grow population, found new settlements with settlers, construct
  a granary, sawmill, forge or library. There are eight city slots worldwide.
- **Research:** Agriculture, Mining, Bronze Working, Writing and Navigation.
  Bronze Working follows Mining; Navigation follows Writing. Research is
  permanent and boosts yields, construction/recruitment, or exploration.
- **Units:** Scouts explore, warriors attack, settlers found cities. Select a
  friendly unit's hex, click **Move / Attack**, then an adjacent hex. Attacks
  are deterministic and allow besieging/capturing cities.
- **Turn pacing:** Two orders per turn; orders are validated and previewed
  locally, then submitted together with **End Turn**. Unused orders are
  skipped. Production is collected when the bundled turn commits.
- **Victory:** Reach 30 victory points after round 3; eliminate opposing
  cities and settlers; or lead the scores after round 24. Score is computed
  from settlements, population, buildings and technology.

All action buttons are authoritative: the shared game rules reject illegal
or unaffordable orders. The scoreboard is public. The world is a shared
deterministic map, so there is no map file to exchange.

## Technical design

- `src/radio_civ/rules.py`: independent deterministic game plugin (game ID 5)
- `src/radio_civ/ui.py`: graphical PySide6 client, engine events marshalled
  to the GUI thread; never imported by the game rules
- `src/radio_civ/engine.py`: headless radio and local-hotseat service factory
- `src/radio_civ/web.py` and `static/`: optional same-origin HTTP/API and
  browser client, without a second game implementation
- `../../radio_arcade/service.py`: session, dealer, roster, transport,
  action commit, observer and resynchronization logic **reused**, not forked

Wire layout, capacity math and action opcodes: [PROTOCOL.md](PROTOCOL.md).
Test with `PYTHONPATH=apps/civ/src:. python -m unittest discover -s apps/civ/tests -v`
from the Radio Arcade checkout root. Desktop integration tests skip without
PySide6; game rules, browser API and radio engine tests do not need it.

## Scope / roadmap

The initial release intentionally does not include computer opponents,
trading/diplomacy, ship combat, larger worlds, procedural save editor or a
campaign browser beyond current Radio Arcade sessions. The game is playable
from founding to victory today, and can evolve independently of the main
Radio Arcade desktop/TUI interfaces.
