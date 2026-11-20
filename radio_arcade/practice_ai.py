"""Small legal-move opponent used by local practice tables."""
import random
from functools import reduce
from operator import xor


def fleet(lengths, rng=random):
    ships, occupied = [], set()
    for length in lengths:
        for _ in range(500):
            horizontal = bool(rng.randrange(2))
            row, col = rng.randrange(10 if horizontal else 11 - length), rng.randrange(11 - length if horizontal else 10)
            cells = [row * 10 + col + i if horizontal else (row + i) * 10 + col
                     for i in range(length)]
            if not occupied.intersection(cells):
                ships.append(cells); occupied.update(cells); break
        else:
            return fleet(lengths, rng)
    return ships


def _tactical_score(game,state,players,actor,action,result):
    """Score public legal outcomes, with random tie-breaking at the caller."""
    seat=players.index(actor)
    key=game.key
    if result.get("finished"):
        return 10000 if result.get("winner")==seat else -10000 if result.get("winner",-1)>=0 else 0
    if key=="nim" and len(players)==2:
        return 100 if reduce(xor,result["piles"],0)==0 else 0
    if key in ("tic-tac-toe","connect-four"):
        other=players[1-seat]
        candidates=range(9 if key=="tic-tac-toe" else 7)
        threats=0
        for i in candidates:
            try:
                reply=game.apply_action(result,players,other,bytes((i,)))
                threats+=reply.get("winner")==1-seat
            except ValueError:
                pass
        preference=(3 if action[0]==4 else 1 if action[0] in (0,2,6,8) else 0) if key=="tic-tac-toe" else 3-abs(action[0]-3)
        return preference-100*threats
    if key=="hexxagon":
        mark=seat+1
        return result["board"].count(mark)-result["board"].count(3-mark)
    if key in ("battleship-classic","sea-warfare") and state["phase"]=="call" and action[0]<100:
        shots=state["shots"][seat]
        row,col=divmod(action[0],10)
        # Use only public hits. A sunk vessel's undisclosed footprint is not consulted.
        adjacent=sum(shots[r*10+c]==2 for r,c in ((row-1,col),(row+1,col),(row,col-1),(row,col+1)) if 0<=r<10 and 0<=c<10)
        return 10*adjacent+int((row+col)%2==0)
    return 0


def choose(game, state, players, actor, rng=random, difficulty="standard"):
    """Choose a legal action, preferring simple tactical moves where practical."""
    if difficulty not in ("casual","standard","challenging"):
        raise ValueError("invalid AI difficulty")
    key = game.key
    if key == "open-word-tiles":
        from .word_ai import choose_word
        return choose_word(game,state,players,actor,difficulty=difficulty,rng=rng)
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
        # External cartridges supply their own event stream. A fabricated AI
        # event would corrupt replay/checkpoint semantics and may desync peers.
        return None
    else:
        moves = []
    if isinstance(moves, list):
        rng.shuffle(moves)
    tactical=difficulty != "casual" and key in ("tic-tac-toe","connect-four","nim","hexxagon","battleship-classic","sea-warfare")
    best,score=None,float("-inf")
    for action in moves:
        try:
            result=game.apply_action(state, players, actor, action)
            if not tactical:
                return action
            value=_tactical_score(game,state,players,actor,action,result)
            if difficulty == "challenging" and key in ("connect-four","tic-tac-toe") and not result.get("finished"):
                other=players[1-players.index(actor)]
                # Penalize giving away immediate tactical counterplay; prefer
                # moves that create multiple winning replies next turn.
                values=range(7 if key=="connect-four" else 9)
                replies=[]
                for i in values:
                    try:
                        response=game.apply_action(result,players,other,bytes((i,)))
                        replies.append(response)
                    except ValueError:pass
                if replies:
                    value-=15*max(int(r.get("winner",-1)==1-players.index(actor)) for r in replies)
                    value+=min(5,len(replies))*0.01
            if value>score:
                best,score=action,value
        except (ValueError, IndexError, KeyError):
            continue
    return best
