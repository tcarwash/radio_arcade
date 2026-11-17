"""Small legal-move opponent used by local practice tables."""
import random


def fleet(lengths, rng=random):
    ships, occupied = [], set()
    for length in lengths:
        for _ in range(500):
            horizontal = bool(rng.randrange(2))
            row, col = rng.randrange(10), rng.randrange(11 - length if horizontal else 10)
            cells = [row * 10 + col + i if horizontal else (row + i) * 10 + col
                     for i in range(length)]
            if not occupied.intersection(cells):
                ships.append(cells); occupied.update(cells); break
        else:
            return fleet(lengths, rng)
    return ships


def choose(game, state, players, actor, rng=random):
    """Choose a legal action, preferring simple tactical moves where practical."""
    key = game.key
    if key == "tic-tac-toe":
        board = state["board"]
        moves = [bytes((i,)) for i, cell in enumerate(board) if cell == " "]
    elif key == "nim":
        moves = [bytes((i, n)) for i, pile in enumerate(state["piles"]) for n in range(1, pile + 1)]
    elif key == "connect-four":
        moves = [bytes((c,)) for c in range(7) if state["board"][c] == " "]
    elif key == "hexxagon":
        from .hexxagon import moves as hexx_moves
        possible = hexx_moves(state["board"], state["turn"] + 1)
        moves = []
        from .hexxagon import distance
        for source, target in possible:
            moves.append(bytes((target,)) if distance(source, target) == 1 else
                         (0x8000 | (source << 6) | target).to_bytes(2, "big"))
    elif key == "scorched-earth":
        moves = (bytes((code, param)) for code in range(256) for param in range(256))
    elif key == "open-word-tiles":
        player = players.index(actor)
        rack = state["racks"][player]
        mask = sum(1 << i for i, tile in enumerate(rack) if tile != 0xFF)
        moves = [b"\x01"]
        if mask and sum(state["board"]) < 220:
            moves.append(bytes((2, mask)))
    elif key == "signal-siege":
        if state["phase"] == "draft":
            moves = [bytes((x,)) for x in (0x40, 0x41, 0x42)]
        else:
            moves = [bytes((x,)) for x in range(64)]
    elif key in ("battleship-classic", "sea-warfare"):
        if state["phase"] == "setup":
            moves = [b"\x7f"]
        elif state["phase"] == "call":
            used = state["shots"][state["turn"]]
            moves = [bytes((i,)) for i, value in enumerate(used) if value == 0]
            if key == "sea-warfare":
                moves += [bytes((190 + row,)) for row in range(10) if not state["used"][state["turn"]] & 2]
        else:
            return None
    elif key in ("pico8-cartridge", "external-game"):
        from .pico8 import EVENT
        moves = [bytes((EVENT,))] if state["cart_hash"] else []
    else:
        moves = []
    if isinstance(moves, list):
        rng.shuffle(moves)
    for action in moves:
        try:
            game.apply_action(state, players, actor, action)
            return action
        except (ValueError, IndexError, KeyError):
            continue
    return None
