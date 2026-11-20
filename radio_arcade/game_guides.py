"""Short, shared guides for the built-in games. No game logic lives here."""
GUIDES = {
    "tic-tac-toe": ["Choose an empty square to place your mark.", "Make three in a row horizontally, vertically or diagonally to win. A full board without a winner is a draw."],
    "nim": ["Choose one pile and remove one or more stones from it.", "Take the last remaining stone to win. Plan ahead so your opponent must leave you a winning move."],
    "connect-four": ["Choose a column to drop a disc into the lowest empty space.", "Connect four discs horizontally, vertically or diagonally. A full board without a winning line is a draw."],
    "open-word-tiles": ["Choose a starting square and Across or Down. Click or drag rack tiles, or type a word. The first word must cover H8.", "Connect later words to the existing board. All formed words must be in the game's dictionary. Assign a blank with {A}, for example.", "Teal dashed tiles are a local draft, not a completed move. Only Submit word commits a legal word; invalid drafts can be edited freely. Tan solid tiles are already on the board.", "Check the live score before submitting. Premium squares multiply the appropriate letter or word. Exchange tiles or pass instead of placing; racks are open information."],
    "battleship-classic": ["Arrange all five ships on your private board. Rotate with R, undo mistakes or randomize, then deploy.", "Choose an uncalled square in enemy waters. White circles are misses, red crosses hits and amber S markers sunk ships.", "When a vessel sinks, its owner reveals that wreck and the full ship sprite appears on your firing board. Remaining ships stay private. Sink every ship to win."],
    "sea-warfare": ["Deploy six ships, optionally a mine and a decoy. The fleet stays private.", "Fire at enemy waters or use the available tactical abilities. A salvo covers two adjacent squares, sonar scans a column, and movement uses limited supplies.", "Your fleet and enemy waters appear side by side on wide screens. Sunk ships reveal their hulls; afloat ships remain hidden. Destroy the enemy fleet to win."],
    "scorched-earth": ["Center on either tank, zoom or pan the battlefield; toggle projectile following as desired.", "Choose ammunition, aim angle and power. The dotted path predicts the shot. Shift + arrows adjust aim precisely.", "Fire to attack. Buying a round in the armory uses your turn; check cost and remaining ammunition. Destroy the opposing tank to win."],
    "hexxagon": ["Select a friendly piece, then an empty neighboring hex to clone it or a hex two steps away to jump.", "A move converts adjacent opposing pieces. Grow your territory and finish with more pieces than your opponent."],
    "signal-siege": ["Work together to survive interference and defeat the boss.", "Read card effects and choose actions during battle, then draft cards when prompted. Watch station health and team progress."],
}


def guide_for(key):
    return GUIDES.get(key, ["Use the game's available controls and follow its turn indicator. This game has no built-in guide yet."])
