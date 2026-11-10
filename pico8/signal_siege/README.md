# Signal Siege: The Silent Vault

An endless offline PICO-8 dungeon crawler with 60 collectible cards.

## Run

From a terminal:

```sh
pico8 -run /storage5/dev/tak/pico8/signal_siege/signal_siege.p8
```

If the cartridge is already open in the PICO-8 editor, press Esc and type `run`.
Press Z on the title screen to read the lore, then Z again to enter the dungeon. Enter opens PICO-8's pause menu.

- **Arrows:** move through rooms; select a hand card or reward in combat.
- **Z:** use a nearby object, play the selected card, or accept salvage.
- **X:** open/close the archive. Left/right browses all 60 cards.

Each floor has nine connected rooms. Walk through doors in the middle of the
walls. The minimap shows visited rooms and your current position in yellow.
Iron brutes alternate a light probe with a heavy slam. Null jammers drain two
focus after attacking. Hollow leeches heal from unshielded damage (up to three).
The vault warden drains one focus. Combat shows the next attack before you play.

Touch an enemy, or stand beside it and press Z, to enter a card battle.
Defeating it opens three card rewards plus a fourth option to leave salvage.
You return to the cleared room afterward. Other rooms contain healing springs,
caches, or elite enemies. Defeat the guardian in the southeast room, then stand
by its stairs and press Z to descend to the next floor. Enemies scale with depth.
Guardians guarantee a legendary reward choice, heal you, and add attack power.

Cards cycle through your deck. Your hand is the first three cards. Collection
and deck each allow at most two copies of any card. At twelve cards, a new card
replaces the oldest deck card. Finds beyond two owned copies become salvage.
At the entry repair station, spend three salvage for a full heal.

Drones attack each turn. Corrosion damages the enemy each turn and decays.
Counters retaliate after enemy attacks. Disruption reduces incoming attacks by
two. These effects reset at each encounter. Scrap persists during the run and
powers scrap cannon. Shield caps at 24; focus at 12. Focus and guardian power
add damage to direct attacks. Death ends the run; collection, salvage and best
completed floor survive. Progress saves after movement and every combat action.

Native PICO-8 saves are independent of the earlier HTML prototype. This offline
cartridge does not yet exchange collections or trades with Radio Arcade.

## Edit sprites

Press Esc to stop, then Esc again for the editor. Use the sprite tab:

| Sprite | Purpose |
| --- | --- |
| 1–60 | Card artwork, matching catalog order |
| 64 | Sentinel |
| 65 | Elite |
| 66 | Guardian |
| 67 | Cache |
| 68 | Healing spring |
| 69 | Stairs |
| 70 | Repair station |
| 71 | Player |
| 72 | Null jammer |
| 73 | Hollow leech |

These are editable 8×8 placeholders. Card portraits are enlarged to 32×32 on
screen. Higher resolution master art can be maintained separately; a multi-cart
portrait pipeline is still future work. Dungeon walls are currently drawn in code.
Save the cartridge with Ctrl+S after editing. The main `.p8` is the source of truth.

## Export and checks

```sh
cd /storage5/dev/tak/pico8/signal_siege
pico8 signal_siege.p8 -export exports/index.html
python tools/check.py
```

Distribute both exported HTML and JS files together. Players need no PICO-8
purchase. The check script exercises movement, collisions, combat, guardian
stairs, collection effects and save recovery using the installed PICO-8 runtime.
It isolates its save data from your actual collection.
