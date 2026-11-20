"""Optional, plain-text sunk-vessel disclosure for the naval games.

Messages are only generated when a ship is fully destroyed. They use the
existing reliable, human-readable opaque side channel; no new binary protocol
version or extra information about unsunk ships is required.
"""
from .games.battleship import FLEET as CLASSIC_FLEET
from .games.sea_warfare import FLEET as SEA_FLEET

PREFIX = "NAVSINK1:"


def _fleet_lengths(game_id):
    return CLASSIC_FLEET if game_id == 12 else SEA_FLEET if game_id == 13 else ()


def validate_ship(game_id, ship_id, cells):
    lengths = _fleet_lengths(game_id)
    if (type(ship_id) is not int or ship_id < 0 or ship_id >= len(lengths)
            or type(cells) is not list or len(cells) != lengths[ship_id]
            or any(type(c) is not int or not 0 <= c < 100 for c in cells)
            or len(set(cells)) != len(cells)):
        raise ValueError("invalid sunk-vessel reveal")
    indices = sorted(cells)
    rows = {c // 10 for c in indices}
    cols = {c % 10 for c in indices}
    if len(rows) == 1:
        if [c % 10 for c in indices] != list(range(indices[0] % 10, indices[0] % 10 + len(indices))):
            raise ValueError("ship does not occupy consecutive columns")
    elif len(cols) == 1:
        if indices != list(range(indices[0], indices[0] + 10 * len(indices), 10)):
            raise ValueError("ship does not occupy consecutive rows")
    else:
        raise ValueError("ship must be straight")
    return indices


def encode_reveal(game_id, ship_id, cells, revision):
    indices = validate_ship(game_id, ship_id, cells)
    if type(revision) is not int or revision < 0:
        raise ValueError("invalid reveal revision")
    return PREFIX + f"{revision}:{ship_id}:" + ",".join(map(str, indices))


def decode_reveal(game_id, text):
    if not isinstance(text, str) or not text.startswith(PREFIX) or len(text) > 90:
        raise ValueError("not a naval reveal")
    try:
        revision, ship_id, numbers = text[len(PREFIX):].split(":", 2)
        if not revision.isdecimal() or not ship_id.isdecimal():
            raise ValueError("not a naval reveal")
        if not numbers or not all(n.isdecimal() for n in numbers.split(",")):
            raise ValueError("invalid reveal cells")
        cells = validate_ship(game_id, int(ship_id), [int(n) for n in numbers.split(",")])
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid naval reveal") from exc
    return {"revision": int(revision), "id": int(ship_id), "cells": cells}


def revealed_enemy_ships(game_id, records, players, viewer, revision, shots):
    """Only verified disclosures from the *opposing* station are displayed.

    Classic additionally requires the public sunk-ID marker; Sea Warfare has
    no ship IDs in its original shot format, so it checks a confirmed hit and
    relies on the owner's explicit post-sinking disclosure.
    """
    if game_id not in (12, 13) or viewer not in players:
        return []
    opponent = next((p for p in players if p != viewer), None)
    result = {}
    for record in records:
        if record["sender"] != opponent:
            continue
        try:
            decoded = decode_reveal(game_id, record["body"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if decoded["revision"] > revision:
            continue
        cells = decoded["cells"]
        if game_id == 12:
            if not any(shots[c] == decoded["id"] + 3 for c in cells):
                continue
        elif not any(shots[c] == 2 for c in cells):
            continue
        if (game_id == 12 and not all(shots[c] >= 2 for c in cells)):
            continue
        result[decoded["id"]] = {"id": decoded["id"], "cells": cells}
    return [result[i] for i in sorted(result)]
