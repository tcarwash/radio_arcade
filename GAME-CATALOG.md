# Radio Arcade game ID registry

Game IDs identify rules and payload codecs. IDs are permanent and must never be reassigned to a different game. A plugin must choose an unused value from 1–255. The engine rejects duplicate IDs and duplicate game keys at startup.

| ID | Key | Display name | Players | Status |
| ---: | --- | --- | ---: | --- |
| 1 | `tic-tac-toe` | Tic-tac-toe | 2 | Built in |
| 2 | `nim` | Nim | 2–4 | Built in |
| 3 | `open-word-tiles` | Open Word Tiles | 2 | Built in |
| 4 | `island-settlers` | Island Settlers | 2–4 | **Retired; unavailable** |

Open Word Tiles is a Scrabble-style, open-rack variant using the bundled ENABLE1 lexicon. ENABLE1 is a public-domain alternative, not a current official tournament lexicon. Its racks and tile distribution are public; it is not the standard hidden-rack game. The tile seed and deterministic draw state travel in the ordinary game snapshot, not the opaque side channel.

Game ID 4 is retired and intentionally reserved. Existing on-disk sessions are preserved but are hidden because the game rules have been removed; the engine never retries their pending transmissions. Do not assign ID 4 to a different game.

Do not infer protocol compatibility from a display name alone. A game that changes incompatible rules or payload shapes needs a new registered ID or an explicit game-specific schema negotiation before deployment.
