"""Public, presentation-only location of the latest opposing move.

Never inspect or return a player's private deployment data. Read committed
actions and deterministic public snapshots. The cue is intentionally not a
reconstructed private-board result.
"""


def cue_for(game, row, moves, snapshots, viewer):
    if row["state"] is None:
        return None
    players=tuple(row["players"])
    latest=game.decode_state(row["state"])
    from .wire import FORFEIT_ACTION
    for move in reversed(moves[-20:]):
        actor=move["actor"]
        action=move["body"]
        if not action or actor == viewer:
            continue
        if action in (b"\x7f", b"\xff"):  # setup / forfeit are not board moves
            continue
        before_bytes=snapshots.get(move["revision"] - 1)
        after_bytes=snapshots.get(move["revision"])
        if not before_bytes or not after_bytes:
            continue
        try:
            before=game.decode_state(before_bytes)
            after=game.decode_state(after_bytes)
            kind=game.key
            indices=[]
            board="main"
            outcome="move"
            if kind in ("battleship-classic","sea-warfare"):
                if actor not in players or action[0]>=190:
                    continue
                # A CALL is the move we animate, not the subsequent report.
                # Some Sea Warfare abilities affect multiple squares.
                if action[0]<100:
                    indices=[action[0]]
                elif kind=="sea-warfare" and 100<=action[0]<=189:
                    pair=action[0]-100
                    indices=[(pair//9)*10+pair%9,(pair//9)*10+pair%9+1]
                else:
                    continue
                side=players.index(actor)
                values=[latest["shots"][side][i] for i in indices]
                outcome=("sunk" if any(v>=3 for v in values) else
                         "hit" if any(v==2 for v in values) else
                         "miss" if values and all(v==1 for v in values) else "incoming")
                board="incoming" if actor != viewer and viewer in players else "enemy"
            elif kind in ("tic-tac-toe","connect-four","open-word-tiles"):
                b=before["board"]
                a=after["board"]
                indices=[i for i,(x,y) in enumerate(zip(b,a)) if x!=y]
                outcome="placed"
            elif kind=="hexxagon":
                indices=[i for i,(x,y) in enumerate(zip(before["board"],after["board"])) if x!=y]
                outcome="captured"
            elif kind=="nim":
                indices=[i for i,(x,y) in enumerate(zip(before["piles"],after["piles"])) if x!=y]
                outcome="taken"
            elif kind=="scorched-earth":
                # The existing trajectory animation handles this game.
                continue
            else:
                outcome="played"
            if not indices and kind not in ("signal-siege",):
                continue
            return {"revision":move["revision"],"actor":actor,
                    "description":game.describe_action(action),
                    "indices":indices[:16],"board":board,"outcome":outcome,
                    "kind":kind}
        except (ValueError,KeyError,IndexError,TypeError):
            continue
    return None
