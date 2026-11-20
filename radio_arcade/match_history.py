"""History presentation, scoped to the active local database and callsign."""
from collections import defaultdict


def match_history(sessions, callsign, *, observer=False):
    records, totals = [], dict(played=0, wins=0, losses=0, draws=0)
    by_game = defaultdict(lambda: dict(played=0, wins=0, losses=0, draws=0))
    for s in sessions:
        if s["status"] != "finished" or not s.get("view"):
            continue
        v = s["view"]
        participated = callsign in s["players"] and not observer
        winners = v.get("winners") or ([v["winner"]] if v.get("winner") else [])
        if v.get("kind") == "signal-siege" and not s.get("forfeited_by"):
            outcome = "Win" if v.get("result") == "victory" else "Loss"
        else:
            outcome = "Win" if callsign in winners else "Loss" if winners else "Draw"
        if not participated:
            outcome = "Observed"
        else:
            for group in (totals, by_game[s["game"]]):
                group["played"] += 1
                group[{"Win":"wins","Loss":"losses","Draw":"draws"}[outcome]] += 1
        records.append(dict(id=s["id"], game=s["game"], players=s["players"],
                            outcome=outcome, winners=winners, updated=s.get("updated",0),
                            revision=s["revision"], forfeited_by=s.get("forfeited_by"),
                            archived=bool(s.get("archived"))))
    return dict(totals=totals, by_game=dict(sorted(by_game.items())),
                matches=sorted(records,key=lambda r:r["updated"],reverse=True))
