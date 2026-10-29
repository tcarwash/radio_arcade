"""Optional internet spot publishing; never transmits over the radio transport."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


GAME_NAMES = {"tic-tac-toe": "Tic-Tac-Toe", "nim": "Nim", "open-word-tiles": "Open Word Tiles", "scrabble": "Open Word Tiles"}


def build_spot(session: dict, callsign: str, frequency_khz: str, mode: str, note: str = "") -> dict:
    if not session.get("hosted") or session.get("coordinator") != callsign:
        raise ValueError("Only the dealer of a hosted table can spot that game")
    if session.get("status") not in ("lobby", "active"):
        raise ValueError("Only open or active hosted tables can be spotted")
    try:
        frequency = int(frequency_khz.strip())
    except (ValueError, AttributeError):
        raise ValueError("Enter a dial frequency in kHz") from None
    if not 1 <= frequency <= 3000000:
        raise ValueError("Frequency must be between 1 and 3,000,000 kHz")
    mode = mode.strip()
    note = note.strip()
    if len(mode) > 40 or len(note) > 240:
        raise ValueError("Mode or note is too long")
    return {"callsign": callsign, "frequency_khz": frequency,
            "game": GAME_NAMES.get(session["game"], "Other"),
            "mode": mode, "session": str(session["id"]),
            "note": note, "ttl_minutes": 60}


def post_spot(cluster_url: str, spot: dict) -> dict:
    parsed = urlsplit(cluster_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Configure a valid HTTP(S) DX Cluster URL in Settings")
    endpoint = cluster_url.rstrip("/") + "/api/spots"
    req = Request(endpoint, data=json.dumps(spot).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=8) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.load(exc).get("error", str(exc))
        except (ValueError, OSError):
            message = str(exc)
        raise ValueError(f"DX Cluster rejected spot: {message}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"Cannot reach DX Cluster: {exc}") from exc
