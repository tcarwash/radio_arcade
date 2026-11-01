"""Shared score and result presentation; independent of every UI toolkit."""


def scoreboard(session: dict) -> dict:
    view = session.get("view") or {}
    finished = session.get("status") == "finished"
    winner = view.get("winner")
    winners = view.get("winners") or ([winner] if winner else [])
    scores = view.get("scores") or {}
    tanks = {tank["player"]: tank for tank in view.get("tanks", [])}
    unit = "HP" if tanks else "POINTS" if scores else "RESULT"
    entries = []
    for player in session.get("players", []):
        result = "WIN" if player in winners else "LOSS" if winners else "DRAW"
        value = tanks[player]["hp"] if player in tanks else scores.get(player, result if finished else "—")
        entries.append({"player": player, "value": str(value), "unit": unit,
                        "active": not finished and player == session.get("current_player"),
                        "winner": finished and player in winners})
    return {"entries": entries, "finished": finished, "winner": winner,
            "label": "FINAL" if finished else "LIVE"}


def result_title(session: dict, callsign: str, *, practice=False, observer=False) -> str:
    view = session.get("view") or {}
    winners = view.get("winners") or ([view["winner"]] if view.get("winner") else [])
    if not winners:
        return "DRAW"
    if practice or observer:
        return f"{' / '.join(winners)} WINS" if len(winners) == 1 else f"{' / '.join(winners)} WIN"
    return "VICTORY" if callsign in winners else "DEFEAT"


class CompletionTracker:
    """Announce only a live transition, once per game, never on loading a save."""
    def __init__(self):
        self.statuses = {}
        self.announced = set()

    def observe(self, session: dict) -> bool:
        sid, status = session["id"], session["status"]
        previous = self.statuses.get(sid)
        self.statuses[sid] = status
        if previous == "active" and status == "finished" and sid not in self.announced:
            self.announced.add(sid)
            return True
        return False
