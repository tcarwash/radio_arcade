# Scorched Earth parity work

## Reference and evidence

The supplied `scorched.zip` identifies itself as Scorched Earth **1.5**, updated
June 3, 1995 (`scorched/README`, lines 1–3). Archive SHA-256:
`07c2bc1bd2b8e6c2b9ce6bc15b3160c00e222c944bbfdd180b70e417ef9df93c`.
REA inspected its ZIP inventory: 20 occurrences, no recorded integrity
contradictions. Inventory evidence: `ev_ce0224f8912f8522b9e026e44b4a723079c49c033fee7c013c8c2cfd681f1e78`;
inspection evidence: `ev_959daf5079461dab64c1ea01753f275a10af3e74fbe1ddb76d48b09c6bfaa986`.

The comparisons below are against the **bundled manual and our source**.
The DOS executable has not been run or decompiled. Its exact physics, timing,
and visual behavior remain unverified. Original game assets and executable
code are reference material; our implementation uses its own rules and assets.

## First slice: aiming

The manual documents independent shot power (0–1000 at full energy) and
0–90° elevation (`SCORCH.DOC`, lines 363–371). Our implementation now offers
power from **25% to 100% in steps of 5%**, plus the full **0–90°** elevation
range, in the headless engine, desktop, and web clients. Scorched Earth is
currently supported by those two graphical clients.

This is a radio-oriented adaptation, not numerical DOS physics parity:
weapon-specific speeds, gravity, terrain, and the 25% minimum remain ours.
Power scales launch velocity, not blast damage. The original's HP-dependent
maximum power (manual lines 398–401) is still deferred. Full-power actions retain
their former velocity, so existing aiming remains usable.

Browser previews now follow the same integer simulation and ties-to-even
rounding as the engine, including wind and endpoint collisions. Tests compare
180 trajectories covering both players, all five maps, multiple weapons,
0/45/90° elevation, and 25/50/100% power.

Shots remain two bytes. A snapshot capability bit makes older implementations
reject the new rules rather than silently interpret a powered shot differently.
**Both radio stations must upgrade before starting or recovering a match.**
Historical snapshots remain readable by this version. See `GAME-CATALOG.md`.

## Remaining parity priorities

| Priority | Reference feature | Current gap | Manual lines |
| --- | --- | --- | --- |
| Next | Falling tanks, fall damage, parachutes | Tanks follow crater height without fall damage; driving ignores slope | 705–725, 1354–1371, 1899–1902 |
| Next | Rounds, between-round shopping, cumulative results | One duel per session; purchases consume turns; our `round` counts actions | 789–801, 1658–1661, 1808–1820, 1848–1864 |
| High | Distinct weapon mechanics | MIRVs, rollers, diggers, dirt weapons, napalm, and tracers need separate simulations | 999–1033, 1056–1087, 1099–1103, 1140–1179 |
| High | Batteries and varied shields | Our repair heals 24 HP; shield is a free one-hit damage reduction | 416–425, 618–624, 650–675, 1347–1352, 1373–1406 |
| Later | 2–10 tanks, AI personalities, teams | Engine and radio state assume exactly two players; practice is hot-seat | 273–320, 1658–1659, 2036–2040, 2048 onward |
| Later | Offscreen continuation and configurable walls | Boundary exits are misses; wind cannot reverse horizontal travel | 1904–1914, 1921–1938 |
| Later | Sequential, synchronous, simultaneous play | Strict alternating turns only | 2023–2034 |

Before adding rounds, separate the action counter from the campaign round.
Before adding weapons, define deterministic effect records and recovery state;
keep action packets compact and ensure previews and authority agree.

## Impact presentation

Desktop and web retain the pre-shot terrain during projectile flight and settle
to the authoritative crater over 650 ms after impact. The camera follows the
shot; the browser stops following when the player pans manually. Fireballs,
shockwaves, smoke, and debris are visual effects, including on terrain misses.
Edge exits still cause neither explosions nor craters. Completion dialogs wait
for the explosion to finish. This uses the existing saved terrain history and
does not add bytes to shot packets or snapshots.

## Camera navigation

Desktop and web support WASD and arrow keys while the battlefield or minimap
has focus: A/D pan left/right, W/S pan up/down when vertical scrolling is
available. Click the main map to center that location, drag it to scout, or
click/drag the minimap. Manual navigation stops automatic shot tracking.
These controls only move the camera and send no game actions.

Drive remains a separate, fuel-consuming game action. The original manual
includes tank movement, uphill constraints, and downhill falls (lines 705–725).
Our driving behavior still needs the slope/fall parity work listed above.

Aiming previews are turn-scoped; past shot trajectories are shown only during
flight and then disappear. They do not remain as another player’s aiming guide.
