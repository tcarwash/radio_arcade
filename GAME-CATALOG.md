# Radio Arcade game ID registry

Game IDs identify rules and payload codecs. IDs are permanent and must never be reassigned to a different game. A plugin must choose an unused value from 1–255. The engine rejects duplicate IDs and duplicate game keys at startup.

| ID | Key | Display name | Players | Status |
| ---: | --- | --- | ---: | --- |
| 1 | `tic-tac-toe` | Tic-tac-toe | 2 | Built in |
| 2 | `nim` | Nim | 2–4 | Built in |
| 3 | `open-word-tiles` | Open Word Tiles | 2 | Built in |
| 4 | `island-settlers` | Island Settlers | 2–4 | Built in |

Open Word Tiles is a Scrabble-style, open-rack variant using the bundled ENABLE1 lexicon. ENABLE1 is a public-domain alternative, not a current official tournament lexicon. Its racks and tile distribution are public; it is not the standard hidden-rack game. The tile seed and deterministic draw state travel in the ordinary game snapshot, not the opaque side channel.

Island Settlers is an original compact settlement-and-resource game: build roads, outposts and cities, roll regional production, trade at the bank, and race to six victory points. It deliberately uses a six-region/eighteen-site topology with a fixed 72-byte open state; it is not a digital adaptation of Catan's official board, deck or rules.

Do not infer protocol compatibility from a display name alone. A game that changes incompatible rules or payload shapes needs a new registered ID or an explicit game-specific schema negotiation before deployment.
