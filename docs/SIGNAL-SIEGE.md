# Signal Siege · micro deck-builder prototype

Signal Siege is a deterministic, cooperative card battler in the existing
Radio Arcade game/session system (game ID **11**, engine API 1). It supports
one solo player or 2–4 stations over KISS broadcast / two in connected mode.
It's playable in the desktop, Textual, and browser clients. For solo play,
select **Local practice** and **Signal Siege**; no Data2G service is needed.

## Core loop

1. Every station starts with 18 HP and a six-card deck: three **Ping**, two
   **Firewall**, one **Tune**. The first three cards are that station's hand.
2. Stations take turns playing exactly **one** of their three hand cards.
   The played card moves to the back of its deck, exposing the next card.
   The two unplayed cards remain available. There is no separate discard pile
   or RNG/shuffle state in this initial prototype.
3. Each core starts with `(7 + wave × 3) × team_scale` HP, where
   `team_scale=1` in solo or `floor(1.5 × player_count)` in a team. At the end of
   every second complete team round, the core attacks each station for
   `3 + wave` damage, minus any team-wide Scramble jam. **Solo gets a stronger
   `5 + wave` pulse** because only one card is played in a solo round. Shields
   absorb damage first.
4. Reduce core HP to zero before any station drops to zero HP. All stations
   win or lose together. On victory over wave 1 or 2, each player takes one
   draft turn: choose one of three upgrade cards, appended to their deck.
   The team repairs 6 HP each before the next wave starts.
   Win wave 3 to finish the campaign. Cards earned in the run are retained
   in the local SQLite game save and shared recovery snapshot.

| Card | Effect | Obtained |
| --- | --- | --- |
| Ping | Deal 3 | Starter |
| Firewall | Gain 4 shield | Starter |
| Tune | Add 2 damage to your next attack | Starter |
| Beam | Deal 5 | Wave 1 draft |
| Repair | Heal any station for 4, up to 18 | Wave 1 draft |
| Fortify | Gain 6 shield | Wave 1 draft |
| Burst | Deal 3 and gain 2 shield | Wave 2 draft |
| Scramble | Deal 2, reduce next core pulse by 2 | Wave 2 draft |
| Overdrive | Deal 7 and take 2 self-damage | Wave 2 draft |
| Relay | Give any station 3 shield and 1 focus | Reserved for next expansion |

Shield is capped at 25, Focus at 10, and Scramble jam at 6. Enemy pulses
expend shields and reset the jam. Target selection matters to Repair and Relay;
other cards ignore the target. All game decisions are deterministic based on
the shared snapshot and action sequence.

## Wire format

Each normal move is exactly **one game-action byte**:

| Byte | Meaning |
| --- | --- |
| `00TT00HH` (`0x00–0x0E`, excluding codes with `HH=3`) | Play visible hand slot `HH=0–2`, support target `TT=0–3` |
| `0x40–0x42` | Draft reward slot 0–2 during an upgrade phase |

Example: play hand slot 2 and target the fourth seated station: `0x0E`.
Everything else is reserved/rejected. Card identity is **not** transmitted
per play: all clients know which card occupies the chosen slot and update the
rotating deck. The actor and session are supplied by the existing Radio Arcade
envelope. All normal protocol overhead, ACK/commit/retry, and Data2G modem
framing still apply; **one action byte does not mean one byte on air**.
For reference, `estimate_action(1, source="AG7SU", destination="K7ABC")`
reports a **36-byte Radio Arcade ACTION frame** (without external modem
framing); at a hypothetical 600-baud 8-FSK, rate-1/2 FEC, that's **320 ms**
for one such frame before modem preambles, turnaround, and retry overhead.
Hosted broadcast commits are additional transmissions.

Recovery snapshot version 1 begins with 9 bytes:

```
version:u8=1, station_count:u8, phase:u8, wave:u8, turn:u8,
core_hp:u8, round:u8, jam:u8, result:u8
```

Then each station appends `hp:u8, shield:u8, focus:u8, deck_len:u8,
deck_card_ids[deck_len]`. With four players, the initial game snapshot is
49 bytes and the maximum two-reward snapshot is 57 bytes. Roster and packet
headers are additional. Every decoder checks exact length, numeric bounds,
phase consistency, and permanent card IDs. The game `view_model` includes the
whole state for deterministic clients; desktop/TUI/web only *display* the
local player's hand, with other hands face down. A developer or modified client
could inspect that public data, so this is not suitable for wagering or
adversarially secure hidden-hand mechanics.

## Prototype limitations and next experiments

- Cards are drafted **during** a run and persisted in its saved session. There
  is no persistent account-level inventory, constructed pre-match deck, market,
  drop rarity, or earned collection between separate campaigns yet.
- This is cooperative PvE, not PvP. A future 2–4-player versus ruleset could
  reuse this codec and the card catalog with a different game ID.
- One-byte actions are testable locally with
  `radio_arcade.airtime.estimate_action(1, ...)`, but actual RF performance
  depends on Data2G framing, rate, acknowledgements, retries and turnaround.
- Presentation privacy is deliberate: the complete deck state is available to
  all synchronized clients, while UI components reveal only the local hand.
