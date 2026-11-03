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
| 8 | `hexxagon` | Hexxagon | 2 | Built in (desktop, terminal, web) |
| 9 | `pico8-cartridge` | PICO-8 Cartridge | 2–8 | Built in (web bridge; cartridge rules remain in Lua) |
| 10 | `external-game` | External Engine Game | 2–8 | Built in (LÖVE2D / Godot; rules remain in game runtime) |
| 11 | `signal-siege` | Signal Siege | 1–4 | Built in (co-op deck-builder prototype) |

Open Word Tiles is a Scrabble-style, open-rack variant using the bundled ENABLE1 lexicon. ENABLE1 is a public-domain alternative, not a current official tournament lexicon. Its racks and tile distribution are public; it is not the standard hidden-rack game. The tile seed and deterministic draw state travel in the ordinary game snapshot, not the opaque side channel.

Game ID 4 is retired and intentionally reserved. Existing on-disk sessions are preserved but are hidden because the game rules have been removed; the engine never retries their pending transmissions. Do not assign ID 4 to a different game.

Signal Siege is an open-state, presentation-masked cooperative prototype. The
three visible hand slots are selected with one byte, along with a two-bit
support target. The fixed card catalog and deterministic card rotation are
shared by all clients, with draft rewards added after the first two waves.
This version does not negotiate future card-balance changes; all participants
must run the same game implementation. See [rules and codec](docs/SIGNAL-SIEGE.md).

Do not infer protocol compatibility from a display name alone. A game that changes incompatible rules or payload shapes needs a new registered ID or an explicit game-specific schema negotiation before deployment.

ID 10 is a generic transport-neutral game table. It keeps each game's
ASCII ID/hash, version and turn/event mode in the shared state. Normal
committed actions are `01 + up to 189 application bytes`; the coordinator's
first config action is `00 + hash:u32BE + version:u16BE + mode:u8`, and it
may finish with `02`. See [adapters/README.md](adapters/README.md).

**Game ID 5** is allocated to [Radio Civ](apps/civ/README.md), a standalone
graphical 4X client that uses the Radio Arcade engine. Installing Radio Civ
does not add it to the built-in Radio Arcade desktop/TUI game pickers.

Scorched Earth uses five shared map profiles, so map data is never sent over the
radio. Every turn stays two bytes: packed weapon/power and firing angle, or a
tactical action and its small parameter. Elevation is 0–90°; shot power is
25–100% in steps of 5%. Wind follows a deterministic cycle, and both clients
resolve the same ballistic path, hit, damage, and terrain crater. Drive fuel,
repair kits, and one-hit shields make each turn a choice between attacking and
preparing. The compact shared snapshot carries the changed terrain and player
supplies so reconnecting clients see the same battlefield.

The armory adds Mega Nuke, Needle, and Earth Mover to the original five weapons.
Each player starts with 70 credits and one round of every limited weapon.
The basic cannon is always free and unlimited. A shot earns 8 credits plus a
bonus for damage dealt to the opponent (one credit per three HP). Buying one
round of ammunition costs credits and consumes a turn; ammunition and balances
are included in the snapshot. Snapshot size varies with crater history; older
50-byte snapshots can still be loaded with default armory balances. **Both
connected stations must upgrade together** because the new rules and snapshot
are not compatible with older clients using game ID 7. Avoid mixed-version radio matches.

Each browser presents one local pilot against an opponent seat. In a connected
match, the other station controls that seat; both browsers render the synced
turn, shot, and result locally.

Desktop and browser battlefields use two transparent, bundled tank sprites
(`tank-teal.png` and `tank-amber.png`), with independently aimed barrels and
local-only recoil, flight, and damage animations. The artwork does not change
the game rules or add anything to radio packets.

Powered shots encode `(weapon_id | power_slot << 3, angle)`. Weapon IDs occupy
the low three bits; slot 0 means 100%, while slots 1–15 mean 25–95% in steps
of 5%. Codes 128 and above remain tactical actions. Crater history stores the
unpacked weapon ID. New snapshots set capability bit `0x20` in the first byte:
`0xE0 | map_id` for crater-event snapshots, `0xA0 | map_id` for sparse terrain.
Older clients reject these as unknown maps. This version still reads earlier
snapshots. Upgrade all players together; the shared envelope version is unchanged.
See [SCORCHED-PARITY.md](SCORCHED-PARITY.md) for the DOS reference comparison.
