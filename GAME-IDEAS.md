# More games for Radio Arcade

An idea backlog complementing [GAME-GOALS.md](GAME-GOALS.md). These are proposals, not implemented games or reserved wire IDs. Favor explicit turns, public information, compact messages, and sessions that can pause while the link is unavailable.

Player counts describe the proposed edition. More than two stations should initially use broadcast mode and the planned multiplayer coordinator; connected mode supports one peer per station at a time.

## Small games worth building next

| Game | Players | How it plays | Fit and implementation notes |
| --- | --- | --- | --- |
| **Hex** | 2 | Place stones to connect opposite sides of a hexagonal board. | One cell per move. Start with 7×7 and define a swap rule to address first-player advantage. |
| **Gomoku** | 2 | Place stones and try to make five in a row. | Simple grid and tiny actions. Specify whether longer lines count; start with freestyle rules on a bounded board. |
| **Breakthrough** | 2 | Advance pawns to the opponent's back rank. | Familiar movement with a small ruleset. Send source and destination squares. |
| **Quarto** | 2 | Choose the piece your opponent must place; make a line sharing an attribute. | Just 16 public pieces on a 4×4 board. Explicit choose/place phases; show attributes with symbols as well as colors. |
| **Domineering** | 2 | Place dominoes vertically or horizontally, according to your side; trap the opponent. | One anchor cell per move. Small state and straightforward terminal rendering. |
| **Chomp** | 2 | Remove a square and the remaining rectangle extending from it; avoid the poisoned corner. | Tiny actions. Agree on board size and orientation at setup. |
| **Isolation** | 2 | Move your pawn, then remove an empty tile to trap your opponent. | Commit both parts together so a dropout cannot leave half a turn applied. |
| **Kalah** | 2 | Sow stones from pits, capture stones, and fill your store. | One pit index per move. Pick an exact capture/endgame variant; earned extra turns exercise turn handling. |

Suggested first picks: **Hex** for clean strategy, **Breakthrough** for familiar movement, and **Quarto** for a different interaction pattern. None requires dictionaries, transmitted questions, or random draws.

## Deeper strategy games

| Game | Players | How it plays | Fit and implementation notes |
| --- | --- | --- | --- |
| **Tak** | 2 | Place pieces and move stacks to form a road across a square board. | Start on 5×5. Stack moves need direction and drop counts; measure snapshot size carefully. |
| **Amazons** | 2 | Move a piece like a chess queen, then fire an arrow that blocks a square. | Three coordinates describe a turn. A clearly labeled smaller-board variant could shorten sessions. |
| **Quoridor** | 2–4 | Race a pawn across a board while placing walls to slow opponents. | Small actions. Every wall must preserve a path for every player. |
| **Abalone** | 2 | Shift lines of marbles and push opposing marbles off a hexagonal board. | Public state. Group selection, pushing rules, and readable terminal geometry need care. |
| **Onitama** | 2 | Move pieces using public movement cards that change hands. | Compact 5×5 board. Agree on the opening card set; send a card choice and source/destination squares. |
| **Pentago** | 2 | Place a marble and rotate a quadrant to make five in a row. | A cell, quadrant, and direction describe a turn. Define win timing and simultaneous-win handling explicitly. |

## Word and group games

| Game | Players | How it plays | Fit and implementation notes |
| --- | --- | --- | --- |
| **Ghost** | 2–6 | Add a letter to a shared fragment while avoiding completing a word. | Usually one character per turn. Agree on dictionary, minimum word length, and challenge rules. |
| **Geography chain** | 2–8 | Name a place beginning with the final letter of the previous place. | A short phrase per turn. Normalize names, forbid repeats, and use a shared place list or host adjudication. |
| **Word-grid claim** (original proposal) | 2–6 | Take turns claiming unused words from a public letter grid. | No speed scoring. Send a bounded cell path and agree on a dictionary version. |
| **Number target** (cooperative variant) | 2–6 | Combine public numbers with arithmetic to reach a target. | Propose one expression step per turn. Fix allowed operations and exact arithmetic rules. |
| **Shared crossword** (cooperative) | 2–8 | Fill a common crossword one entry at a time. | Entry IDs and short answers fit well. Preinstall puzzle packs and define how conflicting edits and answer checking work. |
| **Open-hand dominoes** (variant) | 2–4 | Place matching tiles from publicly visible hands. | Publish the deal and draw order. Explicitly a public-information variant of the usual concealed-hand game. |

Identify the exact dictionary or puzzle pack version at setup. Install larger packs locally rather than sending them during a match. Bound text lengths and provide a challenge or host-ruling mechanism when automatic validation is impractical.

## Radio-themed original concepts

These are starting points for rules design, not established games.

| Idea | Players | Core loop | What needs prototyping |
| --- | --- | --- | --- |
| **Relay Race** | 2–4 | Build routes between stations on a public map and deliver message tokens with limited hops. | Map size, route contention, and scoring. Transmit node/edge choices rather than the map each turn. |
| **Band Plan** | 2–4 | Claim frequency/time slots on a fictional band grid while avoiding interference. | Balanced placement and scoring. Keep it an abstract puzzle rather than operating guidance. |
| **Signal Rescue** | 2–6, cooperative | Allocate shared power, antennas, and relay resources to reconnect isolated stations. | Deterministic scenarios and a public event schedule. One resource assignment per turn. |
| **Foxhunt Board** | 2–6, cooperative | Combine public bearing clues to locate a transmitter on a shared map. | Prewritten clues and a reveal procedure. Do not transmit a supposedly secret solution during setup; host adjudication is an option. |
| **Packet Paths** | 2–4 | Rotate or place network tiles to connect terminals and deliver tokens before a turn limit. | Small layouts and route-resolution rules. Keep tile choices public. |
| **Station Builder** | 2–4 | Draft public equipment tiles to build a station meeting scored capability goals. | A small fictional equipment catalog and balanced costs. Begin with a fixed public market. |

## Ideas that can use opaque state synchronization

Opaque state is plain text carried on air and hidden only by the bundled player interfaces. Treat every field as public and recoverable by a listener. It is useful when a plugin needs to synchronize internal checkpoints or evaluator metadata while presenting a cleaner, game-specific view; ordinary board games do not need it just because the feature exists.

| Game | Players | Player-facing game | Useful opaque payload | Why it fits |
| --- | --- | --- | --- | --- |
| **Signal Rescue campaign** | 2–6 cooperative | Stations allocate shared resources to restore a fictional network across linked scenarios. | Scenario checkpoint, resolved event IDs, and campaign progress. | Extends the existing Signal Rescue concept into a persistent, resumable campaign without making the raw state string part of the UI. |
| **Radio Escape Room** | 2–6 cooperative | Solve a locally installed sequence of puzzles and unlock rooms together. | Normalized evaluator progress, accepted clue IDs, and current puzzle checkpoint. | The plugin can keep validation bookkeeping out of generic chat and board rendering; scenario packs are identified by version and installed at every station. |
| **Packet Paths: expedition mode** | 2–4 cooperative | Route message tokens through a public network before a turn limit. | Pending delivery queue and checkpoint markers between stages. | Adds multi-stage objectives to the existing Packet Paths idea; the visible board remains a compact view of the underlying progress. |
| **Shared mystery investigation** | 2–6 cooperative | Compare public evidence and submit theories against a locally installed case. | Evidence-processing and case-progress markers. | Supports long asynchronous sessions and reconnects. The case solution and evidence remain public; this is not a secret-answer mechanic. |
| **Radio Arcade campaign journal** | 1–6 cooperative | Continue a sequence of short games with persistent team goals and unlocks. | Campaign flags, milestone IDs, and the next scenario pointer. | Lets a game backend preserve progression across rounds while each individual game keeps its own compact actions and snapshots. |

Best first prototype: **Signal Rescue campaign**. It builds on an existing radio-themed idea, is naturally cooperative and asynchronous, and can demonstrate that a plugin consumes synchronized metadata while the desktop and TUI show only a clear “campaign state synchronized” notice plus the rendered mission state. Keep scenario packs fixed and versioned so every station can validate and recover consistently.

## Implementation guardrails

- Keep rules independent of transports and UI toolkits, with a neutral board representation for desktop and TUI clients.
- Define exact variants before assigning permanent game IDs; game names alone do not establish compatible rules.
- Make actions deterministic and atomic. Extra turns, passes, selection phases, and challenges must be explicit state.
- Keep the full packet within the existing 240-byte on-air limit. Measure snapshots as well as moves, particularly for stacks, walls, word histories, and larger boards.
- Start with fixed setups. If randomness is added, commit the resulting public setup/events once and replay them during recovery rather than rerolling locally.
- Avoid reaction timers and mandatory clocks initially; link latency and reconnect time should not decide the winner.
- Treat transmitted state as public. Addressing does not conceal hands, answers, or simultaneous choices.
- Use puzzle text, artwork, and other distributed assets with suitable reuse permission.
