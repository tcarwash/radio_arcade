"""Optional loopback web UI for the exact same Radio Civ / ArcadeService backend.

No Flask/Node dependency: the Python standard library serves a small same-
origin JSON API and static assets. Bind localhost unless you have put it behind
an authenticated reverse proxy on a trusted network.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import re
from urllib.parse import urlsplit

from radio_arcade.service import ArcadeService, station_name
from radio_arcade.transport_factory import group_name

from .engine import HotseatService, broadcast_service
from .rules import TECH_GUIDE


def public_session(session: dict) -> dict:
    """Never JSON-encode mutable/raw engine internals or binary payloads."""
    result = {key: value for key, value in session.items()
              if key not in ("state", "pending_action", "last_action")}
    result["pending_move"] = session.get("pending_action") is not None
    if result.get("view", {}).get("kind") == "civilization":
        # JavaScript Numbers lose precision above 2**53. Preserve all 64
        # exploration bits in a fixed-width hexadecimal string.
        result["view"]["players"] = [dict(player, seen=f"{player['seen']:016X}")
                                       for player in result["view"]["players"]]
    return result


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine: ArcadeService):
        self.engine = engine
        super().__init__(address, WebHandler)


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer

    def log_message(self, format, *args):
        # No persistent logs of game chat/callsigns by default.
        return None

    def _write(self, status: int, body: bytes, mime: str):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, result, status=200):
        self._write(status, json.dumps(result, separators=(",", ":")).encode("utf-8"),
                    "application/json; charset=utf-8")

    def _error(self, status: int, message: str):
        self._json({"error": message}, status)

    @staticmethod
    def _sid(path: str) -> tuple[str, str] | None:
        match = re.fullmatch(r"/api/sessions/([a-fA-F0-9]{16})(?:/([a-z]+))?", path)
        return (match.group(1).upper(), match.group(2) or "") if match else None

    def do_GET(self):
        path = urlsplit(self.path).path
        engine = self.server.engine
        try:
            if path == "/api/config":
                self._json({"call": engine.callsign, "observer": engine.observer_mode,
                            "local": isinstance(engine, HotseatService), "technologies": TECH_GUIDE})
            elif path == "/api/sessions":
                self._json([public_session(row) for row in engine.sessions() if row["game_id"] == 5])
            elif (session := self._sid(path)) is not None:
                sid, action = session
                row = engine.session(sid)
                if row["game_id"] != 5:
                    raise KeyError(sid)
                if action == "":
                    self._json(public_session(row))
                elif action == "chat":
                    self._json(engine.chat_history(sid))
                else:
                    self._error(404, "unknown endpoint")
            else:
                names = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/style.css": ("style.css", "text/css; charset=utf-8")}
                if path not in names:
                    self._error(404, "not found")
                    return
                filename, mime = names[path]
                self._write(200, files("radio_civ").joinpath("static", filename).read_bytes(), mime)
        except KeyError:
            self._error(404, "campaign not found")
        except (OSError, RuntimeError, ValueError) as exc:
            self._error(400, str(exc))

    def do_POST(self):
        # Do not accept form posts or cross-site JavaScript requests: this
        # server can key a physical transmitter on the user's behalf.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and urlsplit(origin).netloc != host:
            self._error(403, "cross-origin requests are forbidden")
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._error(415, "requests must use application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= 2048:
                raise ValueError("request body too large or missing")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            engine = self.server.engine
            path = urlsplit(self.path).path
            if path == "/api/campaigns":
                sid = engine.create_game("radio-civ", hosted=not isinstance(engine, HotseatService))
            elif (match := self._sid(path)) is not None:
                sid, action = match
                row = engine.session(sid)
                if row["game_id"] != 5:
                    raise KeyError(sid)
                if action == "action":
                    raw = body.get("bytes")
                    if not isinstance(raw, list) or not 1 <= len(raw) <= 3 or any(
                            type(n) is not int or not 0 <= n <= 255 for n in raw):
                        raise ValueError("action bytes must be an array of 1–3 unsigned integers")
                    engine.legal_action(sid, bytes(raw))
                elif action == "order":
                    if not isinstance(body.get("callsigns"), str):
                        raise ValueError("expected comma-separated callsigns")
                    engine.set_reply_order(sid, body["callsigns"])
                elif action == "chat":
                    if not isinstance(body.get("text"), str):
                        raise ValueError("expected chat text")
                    engine.send_chat(sid, body["text"])
                else:
                    commands = {"join": "join_game", "start": "start_game",
                                "sync": "request_sync", "retry": "resend_pending_action",
                                "announce": "announce_game"}
                    if action not in commands:
                        self._error(404, "unknown command")
                        return
                    getattr(engine, commands[action])(sid)
            else:
                self._error(404, "unknown endpoint")
                return
            self._json({"ok": True, "session": public_session(engine.session(sid))})
        except KeyError:
            self._error(404, "campaign not found")
        except (ValueError, TypeError, OverflowError, OSError, RuntimeError) as exc:
            self._error(400, str(exc))


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Optional Radio Civ web client")
    parser.add_argument("--mode", choices=("practice", "radio", "observer"), default="practice")
    parser.add_argument("--callsign", default="AG7SU")
    parser.add_argument("--host", default="127.0.0.1", help="Data2G server host")
    parser.add_argument("--group", default="RAGAMES")
    parser.add_argument("--kiss-port", type=int, default=8100)
    parser.add_argument("--command-port", type=int, default=8300)
    parser.add_argument("--listen", default="127.0.0.1", help="Web bind address (default: loopback only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    call = station_name(args.callsign)
    engine = (HotseatService(call) if args.mode == "practice" else
              broadcast_service(call, args.host, args.kiss_port, args.command_port,
                                group_name(args.group), args.mode == "observer"))
    try:
        with WebServer((args.listen, args.port), engine) as server:
            print(f"Radio Civ web interface: http://{args.listen}:{server.server_port}/", flush=True)
            server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()


if __name__ == "__main__":
    main()
