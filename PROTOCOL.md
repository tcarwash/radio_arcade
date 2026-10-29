# Radio Arcade wire protocol, version 4

This describes the current implementation in `radio_arcade/wire.py` and `service.py`. All radio traffic is public. Multi-byte envelope integers use network (big-endian) byte order. One complete packet is at most 240 bytes. The first byte identifies the game type; it is not the identity of an individual game.

## Envelope

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | Game type ID: 1 tic-tac-toe, 2 Nim, 3 Open Word Tiles |
| 1 | 1 | Envelope version: 4 |
| 2 | 1 | Message kind |
| 3 | 1 | Flags |
| 4 | 8 | Random game instance ID |
| 12 | 2 | Sequence |
| 14 | 2 | State revision; for ACK, the acknowledged message kind |
| 16 | 1 | Source callsign length |
| 17 | 1 | Destination callsign length |
| 18 | variable | Source ASCII, destination ASCII, then payload |

Callsign fields are 1–10 ASCII bytes. Flag `0x01` is required for opaque state and forbidden for other kinds. Unknown flags and versions are rejected. Broadcast uses the Data2G named KISS group and application destination; connected mode prefixes each packet with its two-byte big-endian length on the Data2G byte stream.

| Kind | Value | Payload |
| --- | ---: | --- |
| INVITE | 1 | empty |
| ACCEPT | 2 | empty |
| START | 3 | roster and initial state |
| ACTION | 4 | actor and game action |
| COMMIT | 5 | actor and game action |
| SYNC_REQUEST | 6 | empty |
| SNAPSHOT | 7 | roster, state and optional last action |
| REJECT | 8 | UTF-8 reason |
| RESIGN | 9 | reserved; not implemented |
| CHAT | 10 | 1–150 UTF-8 bytes |
| ACK | 11 | empty; sequence matches acknowledged packet, revision contains its kind |
| OPAQUE_STATE | 12 | 1–200 readable UTF-8 bytes; UI-hidden flag required |
| CANCEL_INVITE | 13 | empty; cancels a pending invitation |

Roster encoding is `[count:u8][length:u8, ASCII callsign]...`. START is `[roster byte length:u8][roster][state byte length:u8][state]`. SNAPSHOT appends `[last-action length:u8][last-action]`, with zero length if absent. ACTION and COMMIT use `[actor length:u8][actor ASCII][action length:u8][action bytes]`; the snapshot last action uses the same encoding.

The creator coordinates the locked roster. ACTION specifies the next sequence and expected revision; COMMIT advances both and carries the actor and action. Important messages are acknowledged and retried; a snapshot repairs a missed commit. Sequence numbers for chat and opaque state are independent of game revisions. Delivery acknowledgements indicate peer processing, not privacy or authentication. Opaque state remains plaintext, is recorded in debug logs, and is hidden only by the player interfaces.

## Built-in game payloads

Tic-tac-toe action is one byte: zero-based cell 0–8, row-major. Its five-byte state stores nine two-bit cells in a three-byte little-endian integer (0 empty, 1 X, 2 O), then the turn index and result byte (0 ongoing, 1 draw, 2 player 0 wins, 3 player 1 wins).

Nim action is two bytes: zero-based pile index and number removed. Its seven-byte state is four pile counts, turn index, winner index (255 if ongoing), and player count. The initial piles are 1, 3, 5 and 7. The last player to take an item wins.

Open Word Tiles uses the bundled ENABLE1 lexicon (SHA-256 `3f16130220645692ed49c7134e24a18504c2ca55b3c012f7290e3e77c63b1a89`) and a public-rack ruleset. Placement actions are 7–11 bytes, exchanges are two bytes, and pass is one byte. The placement encodes its starting square, direction and span, a mask of newly occupied squares, packed letters, and blank-tile markers; receivers reconstruct intersections from the shared board. Its snapshot stores turn/score/rack/random state, a 225-square occupancy bitmap, and six-bit tile values for occupied squares. The deterministic RNG and fixed tile distribution reproduce draws on every station. All player racks are visible by design.

Game IDs and their rules are permanent; see `GAME-CATALOG.md`. Game-specific codecs must fit the complete 240-byte envelope, including callsigns and any roster or action wrapper. There is currently no independent game-payload version negotiation; incompatible rule or codec changes require a new game ID or an explicit future protocol mechanism.

Opaque state uses the same game ID and the `OPAQUE_STATE` kind/flag. A game may provide optional `encode_opaque_state(value) -> str` and `decode_opaque_state(text)` methods; the engine routes decoded values to `game_opaque_state` events while the bundled UIs show only a synchronization notice. Each game's own format is readable UTF-8, at most 200 bytes per message, and remains public on air.

A cancelled invitation leaves a tombstone even when cancellation arrives before the invitation. To avoid ambiguity from reordered packets, a cancelled station cannot be invited again to the same game instance; create another game instead. Version 4 adds `CANCEL_INVITE`; version 3 and version 4 clients reject each other's packets, so all participants must upgrade together. Locally retained game state is not erased, and retained version 3 outbox envelopes are upgraded on startup because existing message layouts are unchanged.
