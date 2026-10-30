# Radio Arcade game ID registry

Game IDs identify rules and payload codecs. IDs are permanent and must never be reassigned to a different game. A plugin must choose an unused value from 1–255. The engine rejects duplicate IDs and duplicate game keys at startup.

| ID | Key | Display name | Players | Status |
| ---: | --- | --- | ---: | --- |
| 1 | `tic-tac-toe` | Tic-tac-toe | 2 | Built in |
| 2 | `nim` | Nim | 2–4 | Built in |
| 3 | `open-word-tiles` | Open Word Tiles | 2 | Built in |
| 4 | `island-settlers` | Island Settlers | 2–4 | **Retired; unavailable** |
| 5 | `radio-civ` | Radio Civ | 2–4 | Standalone desktop/web client (external) |
| 6 | `connect-four` | Connect Four | 2 | Built in |
| 7 | `scorched-earth` | Scorched Earth | 2 | Built in (web only) |

Open Word Tiles is a Scrabble-style, open-rack variant using the bundled ENABLE1 lexicon. ENABLE1 is a public-domain alternative, not a current official tournament lexicon. Its racks and tile distribution are public; it is not the standard hidden-rack game. The tile seed and deterministic draw state travel in the ordinary game snapshot, not the opaque side channel.

Game ID 4 is retired and intentionally reserved. Existing on-disk sessions are preserved but are hidden because the game rules have been removed; the engine never retries their pending transmissions. Do not assign ID 4 to a different game.

Do not infer protocol compatibility from a display name alone. A game that changes incompatible rules or payload shapes needs a new registered ID or an explicit game-specific schema negotiation before deployment.

**Game ID 5** is allocated to [Radio Civ](apps/civ/README.md), a standalone
graphical 4X client that uses the Radio Arcade engine. Installing Radio Civ
does not add it to the built-in Radio Arcade desktop/TUI game pickers.

Scorched Earth uses five shared map profiles, so map data is never sent over the
radio. Every turn stays two bytes: weapon and firing angle, or a tactical action
and its small parameter. Wind follows a deterministic cycle, and both clients
resolve the same ballistic path, hit, damage, and terrain crater. Drive fuel,
repair kits, and one-hit shields make each turn a choice between attacking and
preparing. The compact shared snapshot carries the changed terrain and player
supplies so reconnecting clients see the same battlefield.

Each browser presents one local pilot against an opponent seat. In a connected
match, the other station controls that seat; both browsers render the synced
turn, shot, and result locally.
