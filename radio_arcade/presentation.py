"""Shared score and result presentation; independent of every UI toolkit."""


def scoreboard(session: dict) -> dict:
    view = session.get("view") or {}
    finished = session.get("status") == "finished"
    winner = view.get("winner")
    winners = view.get("winners") or ([winner] if winner else [])
    scores = view.get("scores") or {}
    tanks = {tank["player"]: tank for tank in view.get("tanks", [])}
    if view.get("kind") == "battleship" and not finished:
        shots = view.get("shots_by_player", {})
        return {"entries": [{"player": player, "value": str(len({v for v in shots.get(player, []) if v >= 3})),
                             "unit": "SHIPS SUNK", "active": player == session.get("current_player"), "winner": False}
                            for player in session.get("players", [])],
                "finished": False, "winner": None, "label": "LIVE"}
    unit = "HP" if tanks or view.get("kind") == "signal-siege" else "POINTS" if scores else "RESULT"
    entries = []
    for player in session.get("players", []):
        result = "WIN" if player in winners else "LOSS" if winners or (view.get("kind") == "signal-siege" and finished) else "DRAW"
        value = tanks[player]["hp"] if player in tanks else scores.get(player, result if finished else "—")
        entries.append({"player": player, "value": str(value), "unit": unit,
                        "active": not finished and player == session.get("current_player"),
                        "winner": finished and player in winners})
    return {"entries": entries, "finished": finished, "winner": winner,
            "label": "FINAL" if finished else "LIVE"}


def result_title(session: dict, callsign: str, *, practice=False, observer=False) -> str:
    view = session.get("view") or {}
    if view.get("kind") == "signal-siege":
        return "VICTORY" if view.get("result") == "victory" else "DEFEAT"
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


def table_feedback(session: dict, callsign: str, *, practice=False, observer=False,
                   tx_enabled=True, submitting=False, delivery=(), outstanding=0) -> tuple[str, str]:
    """Return a concise status and its semantic tone; never infer RF completion."""
    view = session.get("view") or {}
    status = session.get("status")
    current = session.get("current_player")
    own_turn = current == callsign
    coordinator = session.get("coordinator") == callsign
    sequence = session.get("sequence", 0)
    waiting_sync = session.get("sync_requested_at", 0) > session.get("sync_at", 0)
    error = session.get("error") or ""
    if waiting_sync:
        return "Recovery requested · waiting for table state", "alert"
    if any(word in error.lower() for word in ("missed moves", "state conflict", "awaiting snapshot")):
        return "Table may be out of date · recover before playing", "alert"
    if error:
        return f"Table issue · {error}", "alert"
    if status == "finished":
        return (f"{session['forfeited_by']} forfeited" if session.get("forfeited_by")
                else result_title(session, callsign, practice=practice, observer=observer).title()), "finished"
    if observer:
        return ("Watching · receive-only" if view else "Watching · receive-only · waiting for table state"), "neutral"
    if status == "invited":
        return "Invitation received · accept to join", "waiting"
    if status == "lobby":
        if session.get("hosted"):
            return ("Open table · waiting for players" if coordinator else "Open table · join to play"), "waiting"
        return ("Invite players, then start the game" if coordinator else "Waiting for the host"), "waiting"
    if status == "joining":
        return "Join request sent · waiting for host", "waiting"
    if status == "cancelled":
        return "Invitation cancelled", "alert"
    if status != "active":
        return "Waiting for table", "neutral"
    if submitting:
        return ("Making your move…" if practice else "Sending move to Data2G…"), "waiting"
    pending = session.get("pending_action") is not None
    if pending:
        states = [event.get("state") for event in delivery
                  if event.get("sequence") == sequence + 1 and event.get("kind") in ("ACTION", "RESIGN")]
        if "unconfirmed" in states:
            return "No acknowledgement yet · move not confirmed", "alert"
        if not tx_enabled:
            return "TX paused · move awaiting confirmation", "alert"
        if "delivered" in states:
            return "Request acknowledged · waiting for host to confirm move", "waiting"
        if "retry" in states:
            return "Retrying move · waiting for host confirmation", "waiting"
        return "Move queued · waiting for host confirmation", "waiting"
    if not practice and not tx_enabled:
        return "TX paused · enable TX to play", "alert"
    if not practice and coordinator and not session.get("hosted") and outstanding:
        states = [event.get("state") for event in delivery
                  if event.get("sequence") == sequence and event.get("kind") == "COMMIT"]
        if "unconfirmed" in states:
            return f"Move applied · {outstanding} station(s) unconfirmed", "alert"
        return (f"Your turn · {outstanding} acknowledgement(s) outstanding" if own_turn else f"Move applied · awaiting {outstanding} acknowledgement(s)"), "waiting"
    last_own = (session.get("last_move") or {}).get("actor") == callsign
    if own_turn:
        if view.get("phase") == "setup":
            return "Your turn · deploy your fleet", "mine"
        if view.get("phase") == "reply":
            return "Resolving incoming fire", "waiting"
        return "Your turn · choose a move", "mine"
    if practice:
        return "Computer is playing…", "waiting"
    if last_own:
        prefix = "Move applied locally · broadcast table" if coordinator and session.get("hosted") else "Move confirmed"
        return f"{prefix} · waiting for {current or 'the next station'}", "waiting"
    return f"Waiting for {current or 'the next station'}", "waiting"
