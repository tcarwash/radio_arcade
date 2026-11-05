# Radio Arcade wire protocol, version 6

This describes the current implementation in `radio_arcade/wire.py` and `service.py`. All radio traffic is public. Multi-byte envelope integers use network (big-endian) byte order. One complete packet is at most 240 bytes. The first byte identifies the game type; it is not the identity of an individual game.

## Envelope

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | Game type ID: 1 tic-tac-toe, 2 Nim, 3 Open Word Tiles; ID 4 retired |
| 1 | 1 | Envelope version: 6 |
| 2 | 1 | Message kind |
| 3 | 1 | Flags |
| 4 | 8 | Random game instance ID |
| 12 | 2 | Sequence |
| 14 | 2 | State revision; for ACK, the acknowledged message kind |
| 16 | 1 | Source length (high nibble) + destination length (low nibble) |
| 17 | variable | Source ASCII, destination ASCII, then payload |

Callsign fields are 1–15 ASCII bytes; the full header uses **one byte total for both callsign lengths**, not two bytes per callsign. The one-byte length encoding is used only for the initial/session-recovery envelope, not established compact turns. Flag `0x01` is required for opaque state and forbidden for other kinds. Unknown flags and versions are rejected. Broadcast uses the Data2G named KISS group and application destination; connected mode prefixes each packet with its two-byte big-endian length on the Data2G byte stream.

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
| RESIGN | 9 | empty; participant requests a table forfeit from the coordinator |
| CHAT | 10 | 1–150 UTF-8 bytes |
| ACK | 11 | empty; sequence matches acknowledged packet, revision contains its kind |
| OPAQUE_STATE | 12 | 1–200 readable UTF-8 bytes; UI-hidden flag required |
| CANCEL_INVITE | 13 | empty; cancels a pending invitation |
| HOST_ANNOUNCE | 14 | roster; public hosted lobby advertisement |
| JOIN_REQUEST | 15 | empty; directed request for a seat in hosted lobby |
| HOST_ROSTER | 16 | roster; dealer's authoritative order and seated stations |
| HOST_CHAT | 17 | actor and UTF-8 text; dealer broadcast of participant chat |
| STATION_ID | 18 | ASCII `ID ` followed by the station's full callsign; always full envelope |

## Compact established-session turns (v6)

After the full `START` frame establishes the 64-bit session ID and player
roster, ordinary game moves and their acknowledgements omit repeated game ID,
callsigns, destinations, revisions, actor wrapper, and action-length byte. The
first byte `00` distinguishes a compact frame from the full envelope (game ID
zero is reserved). A compact frame is:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | Compact marker `00` |
| 1 | 1 | Control: high four bits zero; next two bits kind; low two bits player seat (0–3) |
| 2 | 4 | Low 32 bits of previously established session ID, big endian |
| 6 | 2 | Sequence, big endian, 1–65535 |
| 8 | variable | Game action bytes for ACTION/COMMIT; empty for ACK |

Kind codes are `00` ACTION (station at seat is actor), `01` COMMIT (dealer is
sender; seat names original actor), `10` ACK of ACTION, `11` ACK of COMMIT.
For ACK the seat names the **sending** station. An ordinary one-byte move is
**9 bytes total**, a two-byte move **10 bytes**, and its ACK **8 bytes**.

The 32-bit value is a reference, **not** a newly generated session ID. The
receiver resolves it against an existing full 64-bit ID and roster. If two
local sessions share those low 32 bits, the sender falls back to the full
envelope and the receiver rejects ambiguous compact frames. Unknown sessions
cannot be decoded from a compact frame alone; a full START or SNAPSHOT is
required. Sequence and authoritative actor/rules validation still apply.

Compact mode is used automatically by the KISS and connected Data2G
transports for active directed two-player and hosted (up-to-four-player) games.
Other messages, including setup, chat, snapshots, resign, rejection, and
station IDs, remain full-size. The reliable outbox retains full canonical
frames for durable retries and compresses them only when actually sending.
Older peers **cannot** interpret v6 traffic; every station must upgrade.

## Station identification (v6)

**Default for Data2G transports: modem-managed Morse CWID.** Radio Arcade
configures the Data2G command socket using `MYCALL <call>`, `DATA2G ON`,
`CWID ONLY 20` (or the configured 15–40 WPM). Data2G keys `DE <call>` as a
1,500 Hz Morse audio tone. This is supported in both native C++ and Python
Data2G engines. The exact setting is `station_id_mode="auto"`; a successful
`CWID` command response suppresses Radio Arcade's additional application ID
packets. A rejected CWID command leaves the original app IDs enabled and
exposes a fallback warning. Connection loss or command timeout instead fails
the setup safely. An operator may select `"application"` explicitly to avoid
changing modem settings; this does not disable any CWID independently enabled
in the modem. Observer-mode and initially TX-disabled clients do not configure
autonomous CWID; any later explicitly permitted transmission uses the app-ID
fallback.

For connected ARQ, Data2G identifies at its scheduled interval and at the end
of a session. For broadcast/KISS, it plans a CWID nine minutes after activity
and transmits it when the channel permits, including when no game action is
queued. Data2G may delay an ID when the channel is busy. A broadcast KISS game
ending is not the same as a modem ARQ session ending, so operators should
verify that an appropriate ending ID is actually transmitted (especially
before stopping the modem).

**Application fallback:** The service sends a full, explicit `STATION_ID`
packet with the complete ASCII callsign before its first game transmission
and at no more than nine-minute intervals while transmitting. An ID failure
blocks game traffic until it succeeds. An idle game doesn't beacon
indefinitely, and final ID frames are scheduled when games end or the service
closes. All these ID packets are standalone full frames, not repeated callsign
bytes in every nine-byte turn.

These features support identification under
[47 CFR §97.119](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-D/part-97/subpart-B/section-97.119).
**Operators must still ensure the transmitted waveform satisfies the permitted
identification methods**; an ASCII payload inside a binary game packet does not by itself
prove every modem/emission combination is compliant. The configured callsign
must be the station's actual assigned call sign for legal identification.

Roster encoding is `[count:u8][length:u8, ASCII callsign]...`. START is `[roster byte length:u8][roster][state byte length:u8][state]`. SNAPSHOT appends `[last-action length:u8][last-action]`, with zero length if absent. ACTION and COMMIT use `[actor length:u8][actor ASCII][action length:u8][action bytes]`; the snapshot last action uses the same encoding.

The creator coordinates the locked roster. ACTION specifies the next sequence and expected revision; COMMIT advances both and carries the actor and action. Important messages are acknowledged and retried; a snapshot repairs a missed commit. Sequence numbers for chat and opaque state are independent of game revisions. Delivery acknowledgements indicate peer processing, not privacy or authentication. Opaque state remains plaintext, is recorded in debug logs, and is hidden only by the player interfaces.

## Passive broadcast observers

An optional observer client listens to all packets on the configured KISS broadcast group, including packets addressed to other callsigns. It does not register as a game participant and does not send ACKs, moves, invitations or chat. START or SNAPSHOT supplies the roster and current board. A COMMIT for an unknown game reveals its type, instance and coordinator but no initial board; the observer displays **Awaiting snapshot**. Subsequent contiguous COMMIT packets advance a known board. Missing revisions trigger a visible stale-state warning, never an automatic transmission.

An observer may explicitly transmit one existing `SYNC_REQUEST` (kind 6, empty payload) addressed to the game's coordinator. The coordinator accepts this request from a non-rostered callsign for an active/finished game and sends one `SNAPSHOT` (kind 7) to that callsign without requiring an ACK; it does not add the observer to the roster. There is no retry guarantee for this optional exchange. Observer mode is broadcast-only, and receive-only except for this deliberate request. No new packet kind or version change is required. An observer cannot discover entirely silent games.

## Hosted dealer tables

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

Open Word Tiles uses the bundled ENABLE1 lexicon (SHA-256 `3f16130220645692ed49c7134e24a18504c2ca55b3c012f7290e3e77c63b1a89`) and a public-rack ruleset. Placement actions are 3–6 bytes, exchanges and pass are one byte. Placements encode direction/span in tag `0x40 | horizontal<<4 | (span-1)`, followed by the starting square and a little-endian bitstream of three-bit rack indices in placement order. A blank rack slot additionally carries its five-bit assigned letter (1–26). Empty squares are inferred from the shared board; rack slots must be distinct, and unused padding bits must be zero. Exchanges use `0x80 | rack-slot mask` (nonzero seven-bit mask); pass remains `0x01`. Legacy tag-0 placements and tag-2 exchanges remain decodable. Snapshot version 2 uses a packed turn/pass/finished/winner flag byte after its version byte, then the existing RNG/scores and nine-byte racks. A count byte uses bit 7 to select a sorted list of occupied square indices, otherwise a 29-byte occupancy bitmap; six-bit tile values follow. Encoders choose the smaller position representation. Empty snapshots are 20 bytes (formerly 52), and dense snapshots save three bytes. Version-1 snapshots remain readable. Upgrade both peers for new actions and snapshots. The deterministic RNG and fixed tile distribution reproduce draws on every station. All player racks are visible by design.

Game ID 4 was previously assigned to Island Settlers and is now **retired**. Current clients do not create, decode, or replay that game's messages. Saved local sessions are retained but not displayed. The ID must never be reused.

Game IDs and their rules are permanent; see `GAME-CATALOG.md`. Game-specific codecs must fit the complete 240-byte envelope, including callsigns and any roster or action wrapper. There is currently no independent game-payload version negotiation; incompatible rule or codec changes require a new game ID or an explicit future protocol mechanism.

Signal Siege (game 11) uses a one-byte action for both card plays and draft
choices. Low bits 0–1 select hand slot 0–2, bits 2–3 select support target
seat 0–3; `0x40..0x42` draft an offered card. The deterministic rotating
deck means card IDs need not be included in normal moves. Recovery state is
versioned and includes per-station deck order and HP/shield/focus. Full
details: [docs/SIGNAL-SIEGE.md](docs/SIGNAL-SIEGE.md).

Game ID **10** is a generic external-engine table, separate from PICO-8's ID 9.
The compact configuration/event/finish action grammar and shared-state codec
are deliberately identical for portability, but separate IDs prevent a native
Godot/LÖVE game from accidentally joining a PICO-8 cartridge session. External
engine IDs and versions are checked by each adapter; the core cannot validate
an arbitrary game's move rules. See [adapters/README.md](adapters/README.md).

Opaque state uses the same game ID and the `OPAQUE_STATE` kind/flag. A game may provide optional `encode_opaque_state(value) -> str` and `decode_opaque_state(text)` methods; the engine routes decoded values to `game_opaque_state` events while the bundled UIs show only a synchronization notice. Each game's own format is readable UTF-8, at most 200 bytes per message, and remains public on air.

A cancelled invitation leaves a tombstone even when cancellation arrives before the invitation. To avoid ambiguity from reordered packets, a cancelled station cannot be invited again to the same game instance; create another game instead. Version 4 adds `CANCEL_INVITE`; version 3 and version 4 clients reject each other's packets, so all participants must upgrade together. Hosted kinds 14–17 extend version 4 without changing legacy packet layouts; older version 4 clients do not understand the new kinds. Locally retained game state is not erased. Queued version 3/4/5 outbox full envelopes are rewritten to the packed-length version 6 header on startup; live older-version packets are rejected.

## Forfeit (version 5)

A participant may forfeit an active table even when it is not their turn.
A non-coordinator sends a reliable, directed `RESIGN` with empty payload, next
sequence, and current revision. The coordinator validates the participant and
commits the result with a normal `COMMIT` envelope whose action bytes are the
reserved core marker `FF 52 41`. This marker cannot be submitted as an ordinary
game action. It is processed by the shared service, never by a game plugin.
The table ends; all other participants share the win. A two-player table has
one winner. Local practice forfeits the seat whose turn it is.

Plugin state stays unchanged so all game codecs remain usable. The final
session status, last actor/action, and shared presentation override carry the
forfeit outcome. A snapshot represents that outcome with a one-byte trailer
containing the forfeiting player's zero-based roster index; a zero-length
trailer still means no last move. Trailers of two or more bytes retain the
normal actor/action format. Duplicate requests cannot change a final result.
Lost commits can be recovered using `SYNC_REQUEST` / `SNAPSHOT`, including by
observers. Forfeiting does not delete chat or saved state.

Version 5 also removes Scorched Earth's artificial upper sky collision limit;
shots can rise above the original viewport and return under gravity. Both
stations must run version 5. Version 4 peers reject these packets; this avoids
mixed physics and mismatched forfeit interpretation. Local saves are retained.

## Game payload size policy

Minimal turn payloads are a primary goal for fluent gameplay on slow radio links.
Encode only the decision in normal ACTION/COMMIT game bytes; reconstruct results
from shared deterministic rules. Do not send a board or rendered animation each
turn. Measure action bytes separately from envelope, ACK/retry, and transport
costs. Reserve snapshots for initialization and recovery, and keep them compact.

Connect Four (game 6) uses a one-byte column index, 0–6, for each move. START and
SNAPSHOT contain a seven-byte little-endian board: seven consecutive seven-bit
column stacks, left to right. In each stack the highest set bit is a sentinel
whose index is the height (0–6); bits below it encode bottom-to-top pieces
(X=0, O=1). Empty columns are 1; zero columns are invalid. Bits 49–55 must be zero.
Turn, winner, and draw are derived and validated from the board. Decoders also
accept the older 13-byte board/turn/result snapshots for saved-game migration.
Upgrade both peers before exchanging the new snapshots. Normal moves retain
their existing one-byte format and shared ACTION/COMMIT envelope.

## Hexxagon (game 8)

Board indices 0–57 enumerate axial `(q,r)` coordinates in row-major `r`, then
`q` order, each from -4 through 4, keeping `max(abs(q),abs(r),abs(q+r)) <= 4`
and omitting `(0,-1)`, `(1,0)`, `(-1,1)`. Alternating players occupy the six
corners starting at `(0,-4)` clockwise. Hex distance is
`max(abs(dq),abs(dr),abs(dq+dr))`. Cloning uses one byte: the empty destination
index, which must border a friendly piece. Jumping uses two bytes, big-endian:
`0x8000 | source<<6 | destination`; unused bits 12–14 must be zero. Source must
be friendly and destination empty at distance two. The jump vacates its source;
either move converts adjacent opposing stones. If the next player has no legal
move, the mover receives all empty cells and counts determine the winner.
There is no automatic repetition draw; players may forfeit a looping game.

Snapshots use 15 little-endian bytes. Bits 0–115 contain 58 two-bit cell values
(0 empty, 1 first player, 2 second player; 3 invalid), bit 116 holds the turn,
bit 117 is finished, and bits 118–119 must be zero. Scores and winner are derived.
No geometry, legal-move list, capture list, or scores travel in normal turns.
