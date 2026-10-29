# Radio Arcade: Product Goal and Roadmap

## Vision

Build a polished, friendly game client for amateur-radio operators. Players can discover or invite other stations, start a game, take turns, and recover sensibly when the radio link pauses or drops. The same game implementations should work through either Data2G KISS broadcast or Data2G's VARA-like connected endpoint.

The application will offer two complete interfaces over the same game and transport services:

- A modern desktop application with clear game boards, station/link status, invitations, active games, and accessible controls.
- A beautiful keyboard-first TUI with the same capabilities, suitable for SSH, small Linux systems, and terminal use.

The game/session engine is also a standalone, headless library. Other applications must be able to register games, open sessions, submit actions, and subscribe to events without importing either UI toolkit or launching Radio Arcade.

The current tic-tac-toe CLI is a prototype and protocol experiment, not the finished product.

## Product goals

1. Support several distinct games through a stable, pluggable game interface.
2. Let a station keep several game instances open, including more than one game with the same station.
3. Run games over either broadcast or connected Data2G transport without putting transport code into individual game rules.
4. Support two-player games first and provide explicit, fair turn coordination for games with three or more players.
5. Make packet loss, reconnects, invitations, whose turn it is, and whether a move was accepted visible to the players.
6. Deliver desktop and TUI interfaces that feel like finished applications rather than debug panels.
7. Keep radio messages compact, versioned, bounded, and understandable from a packet capture.
8. Publish a stable Python engine API that other applications can embed without using the bundled desktop or TUI.
9. Provide session-scoped side chat so players can coordinate without leaving the game board.
10. Support explicitly flagged opaque state synchronization: plaintext that game code can consume, player UIs hide, and debug logs retain.

## Data2G operating model and constraints

### Broadcast mode

- Opens an application broadcast group over KISS. A group can carry games for many participants and many game instances.
- Each frame contains an application destination so clients can ignore messages intended for other stations. This is routing metadata, not privacy: Data2G broadcast traffic is readable by other listeners.
- Broadcast does not retransmit a missed game frame. KISS ACKMODE means the local modem sent a frame; it does not prove that the other players received it. The game protocol must retry important control messages and request state repair when versions disagree.
- This is the natural first transport for games with three or more players.

### Connected mode

- Uses Data2G's VARA-like command connection and connected byte stream. The current endpoint supports one connected peer per station at a time.
- The application adds a length prefix around each compact game packet because the connected endpoint is a byte stream rather than a message API.
- Data2G provides reliable delivery within the active connection. The game still needs to retain state and recover if the link disconnects between turns.
- Two-player games work directly. Three-or-more-player connected games require a later relay or scheduled store-and-forward design; the current one-peer endpoint cannot provide a simultaneous star connection by itself.

Both transports carry the same versioned game packets. The transport layer reports delivery semantics and link state; games consume game events and emit game actions.

## Game and protocol architecture

### Layers

1. **User interface** — desktop or TUI; presents games and collects player actions.
2. **Game catalog and plugin API** — describes games, player counts, rules, state, legal actions, rendering data, and game-specific packet payloads.
3. **Session coordinator** — owns invitations, participant roster, turn policy, event ordering, acknowledgements, retries, snapshots, and recovery.
4. **Transport interface** — broadcast KISS or connected Data2G; handles packet boundaries and connection/link state.
5. **Data2G** — modem, FEC/ARQ, and radio operation.

Game rules must be deterministic and independent of sockets, Data2G, Qt, Textual, and terminal rendering. The UI should receive a neutral game view model; each UI may render it differently.

### Pluggable games

- Every game declares a stable one-byte game type ID, human-readable name, minimum/maximum player count, payload schema version, and whether it needs private or simultaneous information.
- A game supplies initialization, legal-action validation, state transition, end condition, and a compact action/snapshot codec. It may also supply UI metadata but cannot depend on a specific UI toolkit.
- Start with built-in games, then load installed games through the Python entry-point plugin mechanism. Validate plugin IDs and API versions before loading. A plugin must not be able to redefine an assigned wire ID.
- Keep the common binary envelope small. It should identify game type, envelope version, game-instance ID, sender, recipient or broadcast scope, event sequence, and packet kind. Each game then defines its own payload bytes.
- Use a 64-bit session identity for persistent, resumable sessions. The envelope carries an explicit protocol version; incompatible stations reject unsupported versions rather than reinterpreting fields.
- Assign game IDs from a maintained registry. Never reuse a retired ID for different rules.

### Headless engine API

- Keep game rules, session coordination, persistence, and transport adapters in importable modules that do not import PySide6 or Textual.
- Expose a small public API for listing games, creating/joining sessions, inviting players, starting a lobby, submitting a legal action, requesting a snapshot, disconnecting/reconnecting, and subscribing/unsubscribing to typed events.
- Allow an embedding application to supply its own transport adapter or consume the built-in KISS and connected Data2G adapters.
- Define callback/threading behavior, event ordering, exception types, lifecycle/close semantics, and API versioning. Provide a runnable headless example and stable imports from `radio_arcade`.
- Verify headless use in an environment where GUI packages are unavailable; importing or using the engine must not require a display server.

### Multi-player turn coordination

Use a session coordinator with an explicit, agreed participant roster and an explicit turn policy. For turn-based games:

- Invite and accept players, then lock the roster before the game starts.
- Assign stable participant IDs and a deterministic turn order shown in both interfaces.
- Grant one turn at a time. A move includes the game-instance ID, participant, turn number, and expected state revision.
- Accept a move only from the player holding the current turn token and only if it matches the current revision. Duplicate or stale moves are harmlessly ignored.
- Publish a committed event and new revision so every player can confirm the same state.
- Retry invitations, turn grants, commits, and state requests on broadcast. Let late or recovering players request a snapshot and replay newer events.
- If the coordinator disappears, pause the game. A later recovery policy may elect a replacement with a roster/epoch handshake; never allow two coordinators to commit different turns.

For the first multi-player release, the game creator is coordinator. Broadcast sessions can include several stations. Connected mode is two-player until a relay design is implemented and exercised.

### Public-radio information policy

Treat all on-air payloads as public, including addressed messages and connected traffic that other receivers may monitor. The opaque-state flag is a UI redaction hint only; it is not encryption, secrecy, or a way around amateur-radio rules. Opaque state must remain readable plaintext and is intentionally written to a debug log. Normal player UIs hide its content while showing that state synchronization occurred. Do not call it private or secure. Prefer open-information games; hidden state is a display/gameplay choice, never a confidentiality guarantee.

## User experience goals

### Desktop application

- Modern, polished visual design with a compact station/navigation rail, active game workspace, and clear connection/status area.
- One-click choices for Broadcast and Connected modes with visible host, ports, callsign, group/peer, and link state.
- Game lobby for invitations, open sessions, player roster, turn order, and recent games.
- Game boards that resize cleanly and make legal actions, current turn, last move, win/draw state, and recovery state obvious.
- A marked opaque-state synchronization message for game backends: readable, unencrypted text on air, hidden from normal player views, and recorded in debug logs.
- Side chat in every open session, with sender, delivery state, and a compact-message limit suited to radio frames.
- No hidden auto-transmit behavior: actions that send a move are explicit and show queued/sent/committed status appropriate to the transport.
- Preferences for callsign, Data2G host/ports, default transport, color scheme, sound, and accessibility.

### TUI

- Keyboard-first navigation with a clear screen hierarchy, not a stream of debug prints.
- Lobby, game list, board, roster/turn order, and connection status remain visible or quickly reachable.
- Side chat is available from the active game without losing board or turn context.
- Support mouse where the terminal supports it, while keeping all actions available by keyboard.
- Provide color and monochrome-friendly themes, readable focus states, resize behavior, and concise link/recovery messages.

Both interfaces must expose the same product behavior and persist open-game state locally.

## Proposed games

Order favors compact messages, clear turns, and open information over games that need hidden state.

### First wave

1. **Tic-tac-toe** — finish the current prototype with invitations, acknowledgements, recovery, and the same rules in both UIs.
2. **Nim** — excellent protocol exercise: a few piles, one move per turn, two or more players, tiny actions, and easy validation. Implemented.
3. **Connect Four** — familiar two-player game with a slightly richer board and clear turn-taking.
4. **Dots and Boxes** — compact actions and natural support for two or more players.
5. **Open Word Tiles** — two-player Scrabble-style play with a fixed shared lexicon, deterministic draws, and public racks. Implemented as an open-information variant.

### Second wave

6. **Reversi/Othello** — two-player strategy with useful state-snapshot and legal-move tests.
7. **Checkers** — familiar strategy game with more involved legal-action validation.
8. **Public trivia** — host-led questions and rotating answers; all question and answer traffic is public on radio.
9. **Word ladder** — cooperative or competitive, low-bandwidth, and suitable for several stations.

### Later, after synchronization and recovery are solid

10. **Chess** — standard two-player game, algebraic move notation, clocks as an optional rule.
11. **Nine men's morris** — compact board state and good multi-round play.
12. **Go on a small board (9x9)** — more complex rules and larger snapshots; a good stress test for recovery and long sessions.
13. **Cooperative radio scavenger/puzzle game** — asynchronous contributions and team progress, useful for store-and-forward play.

Do not begin with Poker, Battleship, or other hidden-information games. Public radio exposes addressed game payloads, so their normal rules would imply privacy the transport does not provide.

## Delivery roadmap

### Current implementation status

The desktop client, TUI, headless engine API, versioned compact protocol, local practice mode, tic-tac-toe, Nim, Open Word Tiles, plugin loading, side chat, opaque plaintext synchronization, and broadcast/connected Data2G adapters are implemented. Automated scenarios cover simultaneous open games, broadcast multi-player turns, dropped-frame recovery, reconnect synchronization, and local play in both interfaces. The native Data2G audio-pipe smoke runner has exercised broadcast and connected games, including reconnect; this is simulator evidence, not live-radio validation.

Remaining project work includes live-link checks with matched versions, continued UI/accessibility refinement, more built-in games, and a connected-mode relay/store-and-forward design for more than two players. Do not describe the product as RF-validated until matched clients have been exercised over a live link.

### Phase 1 — Reliable game sessions

- **Implemented:** stable binary envelope, permanent game-ID registry, 64-bit session identity, compatibility rejection, transport adapters, session coordinator, retries, duplicate suppression, snapshots, and local persistence.
- **Simulator verified:** two-player games on broadcast and connected Data2G endpoints, including reconnect recovery.
- **Still required:** matched-version live-link validation.

### Phase 2 — Multi-player core and TUI

- **Implemented:** roster agreement, coordinator-owned deterministic turns for broadcast games with three or more players, and the shared keyboard-first TUI.
- **Implemented:** Nim as the first additional game.
- **Remaining:** define player departure behavior; add Connect Four and Dots and Boxes.

### Phase 3 — Desktop application

- **Implemented:** native desktop client with lobby, game boards, link settings, and shared engine behavior.
- **Remaining:** continue visual/accessibility refinement and add Reversi or another game based on user feedback.

### Phase 4 — Plugin and connected multiplayer research

- **Implemented:** documented plugin API, entry-point loading, explicit IDs, and API compatibility checks.
- **Remaining:** design and test a connected-mode relay/store-and-forward topology for more than two stations, respecting Data2G's one-peer-per-station connected-session limit.
- Add further games only when their state, payload size, public-information assumptions, and recovery behavior fit the platform.

## Acceptance criteria

- A game can be started, played to completion, and recovered after a dropped frame in broadcast mode.
- The same game rules run over connected mode without transport-specific game code.
- Two or more game instances can remain open at once; actions route to the selected game instance.
- Players can exchange bounded side-chat messages in an active session from either UI.
- A game backend can exchange bounded opaque plaintext state, recover it after retransmission, and observe a sync event while the bundled player UIs hide its body.
- A broadcast game with at least three participants has a visible roster, deterministic turn order, one valid move per turn, duplicate suppression, and agreed state revisions.
- A disconnected player can rejoin a retained game and obtain the current snapshot without restarting the game.
- Desktop and TUI both support setup, invitations, active games, turn-taking, reconnects, and game completion.
- A separate non-GUI program can import the engine API, register or select a game, join and play a session, and consume state events.
- Radio-visible status distinguishes queued/sent from peer-committed state; KISS ACKMODE is never presented as proof of peer receipt.
- Game payloads remain compact and bounded, and unsupported protocol/game versions fail clearly without corrupting state.
- Live RF readiness is claimed only after matched-version, multi-station verification; simulator results and physical-link results are reported separately.
