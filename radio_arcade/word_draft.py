"""UI-only word composition helpers shared by both clients through the preview API."""
import re
from .games.scrabble import make_placement

def preview_word(game, state, players, actor, start, direction, word):
    """Validate with the real game rules, without committing a move."""
    action = make_placement(state, actor, start, direction, word)
    result = game.apply_action(state, players, actor, action)
    seat = players.index(actor)
    return {"score": result["scores"][seat] - state["scores"][seat], "action": action.hex()}

def positions(start, direction, word):
    match = re.fullmatch(r"([A-O])(1[0-5]|[1-9])", start.strip().upper())
    if not match:
        return {}
    tokens = re.findall(r"\{[A-Z]\}|[A-Z]", word.strip().upper())
    x, y = ord(match[1])-65, int(match[2])-1
    result = {}
    for token in tokens:
        if x>=15 or y>=15:
            break
        result[y*15+x] = token
        x += direction=="H"
        y += direction=="V"
    return result

def append_rack_tile(start, direction, word, letter, cells):
    """Include any contiguous committed tiles before appending a rack tile."""
    match = re.fullmatch(r"([A-O])(1[0-5]|[1-9])", start.strip().upper())
    if not match:
        return word
    tokens = re.findall(r"\{[A-Z]\}|[A-Z]",word.strip().upper())
    if "".join(tokens)!=word.strip().upper():
        return word
    x,y=ord(match[1])-65,int(match[2])-1
    x+=len(tokens)*(direction=="H")
    y+=len(tokens)*(direction=="V")
    while x<15 and y<15 and cells[y*15+x]:
        tokens.append(cells[y*15+x].upper())
        x+=direction=="H";y+=direction=="V"
    if x>=15 or y>=15:
        return word
    tokens.append(letter)
    x+=direction=="H";y+=direction=="V"
    while x<15 and y<15 and cells[y*15+x]:
        tokens.append(cells[y*15+x].upper())
        x+=direction=="H";y+=direction=="V"
    return "".join(tokens)

def used_tiles(start,direction,word,cells):
    return ["?" if token.startswith("{") else token for i, token in positions(start,direction,word).items() if not cells[i]]

def drop_rack_tile(start, direction, word, letter, cells, target):
    """First drop sets the origin; subsequent drops extend a contiguous word.

    Return (origin,word). Existing board letters are included automatically.
    A dropped tile never replaces a committed tile or moves the existing draft.
    """
    if not 0 <= target < 225 or cells[target]:
        raise ValueError("Drop onto an empty square")
    if direction not in ("H","V"):
        raise ValueError("Choose Across or Down")
    step = 1 if direction == "H" else 15
    coord = lambda i: f"{chr(65+i%15)}{i//15+1}"
    if not word.strip():
        first = target
        while (first%15 if direction=="H" else first//15)>0 and cells[first-step]:
            first -= step
        start = coord(first)
        return start, append_rack_tile(start,direction,"",letter,cells)
    draft = positions(start,direction,word)
    if not draft:
        raise ValueError("Choose a starting square")
    last = next(reversed(draft))
    if (last%15 if direction=="H" else last//15)==14:
        raise ValueError("Word already reaches the board edge")
    next_cell = last+step
    while cells[next_cell]:
        if (next_cell%15 if direction=="H" else next_cell//15)==14:
            raise ValueError("Word already reaches the board edge")
        next_cell+=step
    if target!=next_cell:
        raise ValueError(f"Extend your word at {coord(next_cell)}, or clear the draft to start elsewhere")
    return start,append_rack_tile(start,direction,word,letter,cells)
