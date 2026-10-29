# Radio Civ game protocol v1

Radio Civ uses Radio Arcade envelope **version 4** and its hosted broadcast
message flow unchanged. Every participating station must have the same rules.
This document defines **only** the external `CivilizationGame` payloads.

- Permanent game ID: **5** (IDs 1–3 remain built-ins; ID 4 is retired)
- Registry key: `radio-civ`
- Python game API version: **1**
- Players: **2–4**; dealer first; turn sequence is the session roster order
- Information: public; local fog-of-war is a visual convention, not privacy
- World: **8×8** odd-row-offset, pointy-top hexes (indices `y*8+x`)
- Terrain: derived from a fixed 32-bit seed, not transmitted tile-by-tile
- Seed: CRC32 of `"|".join(players).encode("ascii")`, independent of Python
  RNG/platform; the static map algorithm is part of rules v1
- Winner: score, elimination or turn limit as described in README

## Action payloads

Each individual order is a 1–3 byte binary body. At **End Turn**, the client
sends one `P` turn-bundle body inside the ordinary Radio Arcade `ACTION`
envelope; the resulting `COMMIT` carries that same bundle. It contains the
queued orders and a final `E`. All indices are unsigned, zero-based and
snapshots must apply actions in exact revision order.

| First byte | Action | Payload | Meaning |
| --- | --- | --- | --- |
| `E` | End-turn marker | `45` | Final byte of a `P` bundle; collect yields and advance the player/round |
| `M` | Move / attack | `4d unit tile` | Adjacent hex, deterministic combat |
| `F` | Found city | `46 unit` | Consume one settler; obey spacing and cap |
| `R` | Recruit | `52 city kind` | Kind 0 scout, 1 warrior, 2 settler |
| `G` | Grow city | `47 city` | Consume food/timber for population |
| `B` | Build | `42 city kind` | Kind 0 granary, 1 sawmill, 2 forge, 3 library |
| `T` | Research | `54 tech` | Tech 0 agriculture, 1 mining, 2 bronze, 3 writing, 4 navigation |
| `P` | Turn bundle | `50 count [orders] 45` | Count 0–2; each order uses the encoding above; final byte is `E` |

Each queued order consumes one of two action points. The bundle applies every
order in sequence and then ends the turn atomically; invalid bundles change no
state. A bundle is at most 9 bytes. Unit/city indices refer to snapshot order,
which is preserved deterministically across commits. No partial or ambiguous
moves.

## Packed snapshot state

Big-endian. The version byte is game-state version **1**, distinct from the
outer Radio Arcade protocol version. Total size: `12 + 13P + 4C + 3U` bytes.

| Field | Bytes | Details |
| --- | ---: | --- |
| Version | 1 | `01` |
| Seed | 4 | uint32, seeded by the ordered roster |
| Turn, round, action points | 3 | uint8 each; 0-based turn, 1-based round |
| Winner | 1 | 0-based seat; `ff` means no winner |
| Player count, city count, unit count | 3 | P 2–4, C 0–8, U 0–14 |
| Player × P | 13 each | food, timber, ore, science, technology flags; 64-bit discovery bitmap |
| City × C | 4 each | tile, `(hp << 2) | owner`, population, building flags |
| Unit × U | 3 each | tile, `(owner << 4) | kind`, hit points |

Technology/building flags are bitsets corresponding to the indices above.
Cities have HP 1–3 and population 1–5; units have HP 1–3. Numeric resources
are capped at 255. A winner value other than `ff` means finished.

The worst-case **state** is `12+13×4+4×8+3×14 = 138 bytes`.
With four ten-character callsigns, a ten-character sender and recipient,
a maximal three-byte last action, and the engine's roster+snapshot trailer,
the whole on-air snapshot is **239 bytes**, under the **240-byte** envelope
limit. Both `START` and `SYNC` snapshots fit. If a longer action bundle makes
the optional last-move trailer overflow the envelope, the snapshot omits that
trailer; the full game state still synchronizes. Ordinary commit payloads
remain well under the limit.

## Broadcast safety

Radio Arcade's dealer validates each player's complete turn bundle by revision
and turn, then sends a single broadcast `COMMIT`. A receiving station runs the
same deterministic rules. Missing revisions use standard `SYNC_REQUEST` and
`SNAPSHOT`; user-initiated retransmission of a pending turn is safe under
the engine's duplicate/sequence checks. Observers send no ACK, join, move,
or automatic sync. Player names, tile positions, fog and resources are
**not encrypted or hidden on air**.

Game ID 5 and state format v1 are permanent wire commitments. Incompatible
rules must use a new game ID or a negotiated migration—not silently modify
these bytes in a future build.
