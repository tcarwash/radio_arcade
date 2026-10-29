# Radio Arcade wire protocol, version 4

This describes the current implementation in `radio_arcade/wire.py` and `service.py`. All radio traffic is public. Multi-byte envelope integers use network (big-endian) byte order. One complete packet is at most 240 bytes. The first byte identifies the game type; it is not the identity of an individual game.

## Envelope

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | Game type ID: 1 tic-tac-toe, 2 Nim, 3 Open Word Tiles; ID 4 retired |
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
| HOST_ANNOUNCE | 14 | roster; public hosted lobby advertisement |
| JOIN_REQUEST | 15 | empty; directed request for a seat in hosted lobby |
| HOST_ROSTER | 16 | roster; dealer's authoritative order and seated stations |
| HOST_CHAT | 17 | actor and UTF-8 text; dealer broadcast of participant chat |

Roster encoding is `[count:u8][length:u8, ASCII callsign]...`. START is `[roster byte length:u8][roster][state byte length:u8][state]`. SNAPSHOT appends `[last-action length:u8][last-action]`, with zero length if absent. ACTION and COMMIT use `[actor length:u8][actor ASCII][action length:u8][action bytes]`; the snapshot last action uses the same encoding.

The creator coordinates the locked roster. ACTION specifies the next sequence and expected revision; COMMIT advances both and carries the actor and action. Important messages are acknowledged and retried; a snapshot repairs a missed commit. Sequence numbers for chat and opaque state are independent of game revisions. Delivery acknowledgements indicate peer processing, not privacy or authentication. Opaque state remains plaintext, is recorded in debug logs, and is hidden only by the player interfaces.

## Passive broadcast observers (version 4 compatible)

An optional observer client listens to all packets on the configured KISS broadcast group, including packets addressed to other callsigns. It does not register as a game participant and does not send ACKs, moves, invitations or chat. START or SNAPSHOT supplies the roster and current board. A COMMIT for an unknown game reveals its type, instance and coordinator but no initial board; the observer displays **Awaiting snapshot**. Subsequent contiguous COMMIT packets advance a known board. Missing revisions trigger a visible stale-state warning, never an automatic transmission.

An observer may explicitly transmit one existing `SYNC_REQUEST` (kind 6, empty payload) addressed to the game's coordinator. The coordinator accepts this request from a non-rostered callsign for an active/finished game and sends one `SNAPSHOT` (kind 7) to that callsign without requiring an ACK; it does not add the observer to the roster. There is no retry guarantee for this optional exchange. Observer mode is broadcast-only, and receive-only except for this deliberate request. No new packet kind or version change is required. An observer cannot discover entirely silent games.

## Hosted dealer tables (version 4 extension)

A hosted table uses the existing broadcast KISS group, never a connected Data2G session. The creating station is the **dealer** and owns the authoritative state. This is separate from the original directed-invitation mode; old tables keep their existing ACK-and-retry behavior. All participants need this extension to join a hosted table.

1. The dealer creates a hosted lobby and sends **one** `HOST_ANNOUNCE` to destination `*`, carrying `[count:u8][callsign lengths + bytes]`. The first roster element is the dealer. Other stations discover the lobby without being invited.
2. A prospective player explicitly transmits `JOIN_REQUEST` to the dealer's callsign, with no payload. The dealer accepts if the lobby is still open and the game has capacity, then sends **one** `HOST_ROSTER` broadcast of the complete roster in authoritative order. Duplicate requests prompt another full roster, not another seat. The dealer can also manually repeat `HOST_ANNOUNCE`.
3. The dealer can rearrange the roster with `set_reply_order` while in the lobby; the dealer must remain first. The resulting `HOST_ROSTER` broadcasts the new turn/reply order. Once play starts, the roster is locked.
4. The dealer starts a game with **one** `START` broadcast to `*`, including roster and deterministic initial state. The next permitted transmitter is the game’s `current_player(state, roster)`; for Nim, this follows the dealer-chosen roster order. The dealer's own turns update state locally.
5. For a remote turn, only that active station transmits `ACTION` to the dealer, with expected revision and next sequence. The dealer validates it and sends **one** `COMMIT` to `*` carrying actor and action. Every station applies that same action deterministically to obtain the next state and the next authorized reply station. A rejected turn is returned only to its sender via `REJECT`.
6. A side-chat message sent to the dealer as `CHAT` is rebroadcast once as `HOST_CHAT` to `*`. The payload is the usual actor/action wrapper, with the original author as actor and UTF-8 text as action. Observers can read this relay; chat does not alter the game turn.

`JOIN_REQUEST`, hosted `START`, `COMMIT`, `CHAT`, `HOST_CHAT`, and host announcements are **not individually ACKed or automatically retried**. This avoids an ACK storm and repeated per-recipient airtime. The guest may manually repeat Join or Retry pending move. A participant who misses a commit automatically requests one untracked, *directed* `SNAPSHOT`; an observer still does **not** request anything automatically. Anyone may explicitly request a snapshot after a dropped start or commit. Hosted tables remain open-information games; packet content is neither encrypted nor authenticated.

The dealer controls *logical reply order*, not the RF physical layer. Only the active turn holder should key up with an action. Simultaneous join requests or unrelated radio traffic may still collide; operators should stagger requests and retry when no roster update appears. There is no collision-free TDMA clock, timed join window, automatic beacon, dealer failover, or mid-game seat change in this initial implementation. Chat and sync requests are outside the ordered game-turn slot. Because packets are broadcast without per-player acknowledgments, the UI's local receipt is not proof everyone heard it.

## Built-in game payloads

Tic-tac-toe action is one byte: zero-based cell 0–8, row-major. Its five-byte state stores nine two-bit cells in a three-byte little-endian integer (0 empty, 1 X, 2 O), then the turn index and result byte (0 ongoing, 1 draw, 2 player 0 wins, 3 player 1 wins).

Nim action is two bytes: zero-based pile index and number removed. Its seven-byte state is four pile counts, turn index, winner index (255 if ongoing), and player count. The initial piles are 1, 3, 5 and 7. The last player to take an item wins.

Open Word Tiles uses the bundled ENABLE1 lexicon (SHA-256 `3f16130220645692ed49c7134e24a18504c2ca55b3c012f7290e3e77c63b1a89`) and a public-rack ruleset. Placement actions are 7–11 bytes, exchanges are two bytes, and pass is one byte. The placement encodes its starting square, direction and span, a mask of newly occupied squares, packed letters, and blank-tile markers; receivers reconstruct intersections from the shared board. Its snapshot stores turn/score/rack/random state, a 225-square occupancy bitmap, and six-bit tile values for occupied squares. The deterministic RNG and fixed tile distribution reproduce draws on every station. All player racks are visible by design.

Game ID 4 was previously assigned to Island Settlers and is now **retired**. Current clients do not create, decode, or replay that game's messages. Saved local sessions are retained but not displayed. The ID must never be reused.

Game IDs and their rules are permanent; see `GAME-CATALOG.md`. Game-specific codecs must fit the complete 240-byte envelope, including callsigns and any roster or action wrapper. There is currently no independent game-payload version negotiation; incompatible rule or codec changes require a new game ID or an explicit future protocol mechanism.

Opaque state uses the same game ID and the `OPAQUE_STATE` kind/flag. A game may provide optional `encode_opaque_state(value) -> str` and `decode_opaque_state(text)` methods; the engine routes decoded values to `game_opaque_state` events while the bundled UIs show only a synchronization notice. Each game's own format is readable UTF-8, at most 200 bytes per message, and remains public on air.

A cancelled invitation leaves a tombstone even when cancellation arrives before the invitation. To avoid ambiguity from reordered packets, a cancelled station cannot be invited again to the same game instance; create another game instead. Version 4 adds `CANCEL_INVITE`; version 3 and version 4 clients reject each other's packets, so all participants must upgrade together. Hosted kinds 14–17 extend version 4 without changing legacy packet layouts; older version 4 clients do not understand the new kinds. Locally retained game state is not erased, and retained version 3 outbox envelopes are upgraded on startup because existing message layouts are unchanged.
