"""Local durable state for games, chat, and the radio outbox."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


def default_data_dir() -> Path:
    override = os.environ.get("RADIO_ARCADE_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RadioArcade"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "radio-arcade"


class SessionStore:
    @staticmethod
    def default_observer_path() -> Path:
        return default_data_dir() / "observer.sqlite3"

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_data_dir() / "radio-arcade.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, game_id INTEGER NOT NULL, coordinator TEXT NOT NULL,
                    players TEXT NOT NULL, invited TEXT NOT NULL, status TEXT NOT NULL,
                    revision INTEGER NOT NULL, sequence INTEGER NOT NULL, state BLOB,
                    pending_action BLOB, chat_sequence INTEGER NOT NULL DEFAULT 0,
                    opaque_sequence INTEGER NOT NULL DEFAULT 0,
                    last_actor TEXT, last_action BLOB,
                    updated REAL NOT NULL, error TEXT NOT NULL DEFAULT '',
                    hosted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS outbox(
                    session TEXT NOT NULL, target TEXT NOT NULL, kind INTEGER NOT NULL,
                    sequence INTEGER NOT NULL, frame BLOB NOT NULL, attempts INTEGER NOT NULL,
                    next_at REAL NOT NULL, error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(session,target,kind,sequence)
                );
                CREATE TABLE IF NOT EXISTS chat(
                    session TEXT NOT NULL, sender TEXT NOT NULL, sequence INTEGER NOT NULL,
                    body TEXT NOT NULL, delivery TEXT NOT NULL,
                    created REAL NOT NULL,
                    PRIMARY KEY(session,sender,sequence)
                );
                CREATE TABLE IF NOT EXISTS opaque_states(
                    session TEXT NOT NULL, sender TEXT NOT NULL, sequence INTEGER NOT NULL,
                    body BLOB NOT NULL, delivery TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(session,sender,sequence)
                );
                CREATE TABLE IF NOT EXISTS cancelled_invites(
                    session TEXT NOT NULL, target TEXT NOT NULL,
                    PRIMARY KEY(session, target)
                );
            """)
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(sessions)")}
            if "opaque_sequence" not in columns:
                self.db.execute("ALTER TABLE sessions ADD COLUMN opaque_sequence INTEGER NOT NULL DEFAULT 0")
            if "last_actor" not in columns:
                self.db.execute("ALTER TABLE sessions ADD COLUMN last_actor TEXT")
            if "last_action" not in columns:
                self.db.execute("ALTER TABLE sessions ADD COLUMN last_action BLOB")
            if "hosted" not in columns:
                self.db.execute("ALTER TABLE sessions ADD COLUMN hosted INTEGER NOT NULL DEFAULT 0")

    def save_session(self, session: dict[str, Any]) -> None:
        with self._lock, self.db:
            self.db.execute("""INSERT INTO sessions
                (id,game_id,coordinator,players,invited,status,revision,sequence,state,pending_action,chat_sequence,opaque_sequence,last_actor,last_action,updated,error,hosted)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                game_id=excluded.game_id,coordinator=excluded.coordinator,players=excluded.players,
                invited=excluded.invited,status=excluded.status,revision=excluded.revision,
                sequence=excluded.sequence,state=excluded.state,pending_action=excluded.pending_action,
                chat_sequence=excluded.chat_sequence,opaque_sequence=excluded.opaque_sequence,
                last_actor=excluded.last_actor,last_action=excluded.last_action,
                updated=excluded.updated,error=excluded.error,hosted=excluded.hosted""",
                (session["id"], session["game_id"], session["coordinator"], json.dumps(session["players"]),
                 json.dumps(session["invited"]), session["status"], session["revision"], session["sequence"],
                 session["state"], session["pending_action"], session.get("chat_sequence", 0),
                 session.get("opaque_sequence", 0),
                 session.get("last_actor"), session.get("last_action"),
                 time.time(), session.get("error", ""), int(session.get("hosted", False))))

    @staticmethod
    def _session(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "game_id": row["game_id"], "coordinator": row["coordinator"],
                "players": json.loads(row["players"]), "invited": json.loads(row["invited"]),
                "status": row["status"], "revision": row["revision"], "sequence": row["sequence"],
                "state": bytes(row["state"]) if row["state"] is not None else None,
                "pending_action": bytes(row["pending_action"]) if row["pending_action"] is not None else None,
                "chat_sequence": row["chat_sequence"], "opaque_sequence": row["opaque_sequence"],
                "last_actor": row["last_actor"],
                "last_action": bytes(row["last_action"]) if row["last_action"] is not None else None,
                "updated": row["updated"], "error": row["error"], "hosted": bool(row["hosted"])}

    def load_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM sessions ORDER BY updated DESC").fetchall()
        return [self._session(row) for row in rows]

    def save_outgoing(self, session: str, target: str, kind: int, sequence: int, frame: bytes) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO outbox VALUES(?,?,?,?,?,?,?,?)",
                            (session, target, kind, sequence, frame, 1, time.time() + 3.0, ""))

    def due_outgoing(self, now: float | None = None, target: str | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        with self._lock, self.db:
            if target is None:
                rows = self.db.execute("SELECT * FROM outbox WHERE next_at<=? AND attempts<12", (now,)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM outbox WHERE next_at<=? AND attempts<12 AND target=?",
                                       (now, target.upper())).fetchall()
            for row in rows:
                attempts = row["attempts"] + 1
                delay = min(300, 2 ** min(attempts, 8))
                self.db.execute("UPDATE outbox SET attempts=?,next_at=? WHERE session=? AND target=? AND kind=? AND sequence=?",
                                (attempts, now + delay, row["session"], row["target"], row["kind"], row["sequence"]))
        return [{"session": row["session"], "target": row["target"], "kind": row["kind"],
                 "sequence": row["sequence"], "frame": bytes(row["frame"]), "attempts": row["attempts"] + 1}
                for row in rows]

    def failed_outgoing(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT session,target,kind,sequence FROM outbox WHERE attempts>=12").fetchall()
        return [dict(row) for row in rows]

    def resume_outgoing(self, target: str | None = None) -> None:
        """Give retained, unconfirmed packets a fresh retry window."""
        with self._lock, self.db:
            if target is None:
                self.db.execute("UPDATE outbox SET attempts=0,next_at=?", (time.time(),))
            else:
                self.db.execute("UPDATE outbox SET attempts=0,next_at=? WHERE target=?",
                                (time.time(), target))

    def upgrade_v3_outbox(self) -> None:
        """Upgrade retained version 3 envelopes, whose existing kinds are unchanged."""
        from .wire import decode, PROTOCOL_VERSION
        with self._lock, self.db:
            for row in self.db.execute("SELECT session,target,kind,sequence,frame FROM outbox").fetchall():
                frame = bytearray(row["frame"])
                if len(frame) < 2 or frame[1] != 3:
                    continue
                frame[1] = PROTOCOL_VERSION
                decode(bytes(frame))
                self.db.execute("UPDATE outbox SET frame=? WHERE session=? AND target=? AND kind=? AND sequence=?",
                                (bytes(frame), row["session"], row["target"], row["kind"], row["sequence"]))

    def count_outgoing(self, session: str, kind: int, sequence: int) -> int:
        with self._lock:
            row = self.db.execute("SELECT COUNT(*) AS n FROM outbox WHERE session=? AND kind=? AND sequence=?",
                                  (session, kind, sequence)).fetchone()
        return int(row["n"])

    def acknowledge(self, session: str, target: str, kind: int, sequence: int) -> bool:
        with self._lock, self.db:
            cursor = self.db.execute("DELETE FROM outbox WHERE session=? AND target=? AND kind=? AND sequence=?",
                                     (session, target, kind, sequence))
            return cursor.rowcount == 1

    def cancel_outgoing(self, session: str, target: str, kind: int) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM outbox WHERE session=? AND target=? AND kind=?",
                            (session, target, kind))

    def remember_cancelled_invite(self, session: str, target: str) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO cancelled_invites VALUES (?,?)", (session, target))

    def was_invite_cancelled(self, session: str, target: str) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM cancelled_invites WHERE session=? AND target=?",
                                   (session, target)).fetchone() is not None

    def add_chat(self, session: str, sender: str, sequence: int, body: str, delivery: str) -> bool:
        with self._lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO chat VALUES(?,?,?,?,?,?)",
                                     (session, sender, sequence, body, delivery, time.time()))
            return cursor.rowcount == 1

    def set_chat_delivery(self, session: str, sender: str, sequence: int, delivery: str) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE chat SET delivery=? WHERE session=? AND sender=? AND sequence=?",
                            (delivery, session, sender, sequence))

    def load_chat(self, session: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM (SELECT * FROM chat WHERE session=? ORDER BY created DESC LIMIT ?) ORDER BY created",
                                   (session, limit)).fetchall()
        return [dict(row) for row in rows]

    def add_opaque_state(self, session: str, sender: str, sequence: int, body: bytes,
                         delivery: str) -> bool:
        with self._lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO opaque_states VALUES(?,?,?,?,?,?)",
                                     (session, sender, sequence, body, delivery, time.time()))
            return cursor.rowcount == 1

    def set_opaque_delivery(self, session: str, sender: str, sequence: int, delivery: str) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE opaque_states SET delivery=? WHERE session=? AND sender=? AND sequence=?",
                            (delivery, session, sender, sequence))

    def load_opaque_states(self, session: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM (SELECT * FROM opaque_states WHERE session=? ORDER BY created DESC LIMIT ?) ORDER BY created",
                                   (session, limit)).fetchall()
        return [{**dict(row), "body": bytes(row["body"])} for row in rows]

    def close(self) -> None:
        with self._lock:
            self.db.close()
