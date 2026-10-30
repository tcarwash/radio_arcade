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
| 7 | `scorched-earth` | Scorched Earth | 2 | Built in (desktop and web) |

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

The armory adds Mega Nuke, Needle, and Earth Mover to the original five weapons.
Each player starts with 70 credits and one round of every limited weapon.
The basic cannon is always free and unlimited. A shot earns 8 credits plus a
bonus for damage dealt to the opponent (one credit per three HP). Buying one
round of ammunition costs credits and consumes a turn; ammunition and balances
are included in the snapshot. The expanded snapshot is 68 bytes; old 50-byte
snapshots can still be loaded with default armory balances, but **both connected
stations must upgrade together** because the new rules and snapshot are not
compatible with older clients using game ID 7. Avoid mixed-version radio matches.

Each browser presents one local pilot against an opponent seat. In a connected
match, the other station controls that seat; both browsers render the synced
turn, shot, and result locally.

Desktop and browser battlefields use two transparent, bundled tank sprites
(`tank-teal.png` and `tank-amber.png`), with independently aimed barrels and
local-only recoil, flight, and damage animations. The artwork does not change
the game rules or add anything to radio packets.
