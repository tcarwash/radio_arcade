"""Local browser client for Radio Arcade's UI-independent game service."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .service import ArcadeService, station_name
from .settings import Settings
from .transport_factory import create_service, group_name


ASSETS = Path(__file__).with_name("web_assets")
MAX_REQUEST_BYTES = 16_384


class WebController:
    """Small JSON API over the same engine used by the desktop and TUI."""

    def __init__(self, settings: Settings | None = None,
                 service_factory: Callable[[Settings], ArcadeService] = create_service,
                 settings_path: str | Path | None = None):
        self.settings = settings or Settings.load()
        self.service_factory = service_factory
        self.settings_path = settings_path
        self.engine: ArcadeService | None = None
        self.last_error = ""
        self.last_transmission: dict[str, Any] | None = None
        self.transmitting = False
        self._lock = threading.RLock()
        if self.settings.callsign:
            self._connect()

    def _connect(self) -> None:
        self.engine = None
        try:
            self.engine = self.service_factory(self.settings)
            self.engine.set_tx_enabled(self.settings.tx_enabled if self.settings.transport != "practice" else True)
            self.engine.subscribe(self._engine_event)
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)

    def _engine_event(self, event: Any) -> None:
        if event.name == "transmitting":
            self.transmitting = True
            self.last_transmission = {**event.data, "started": time.time(), "active": True}
        elif event.name == "transmission_complete":
            self.transmitting = False
            if self.last_transmission:
                self.last_transmission["active"] = False
                self.last_transmission["completed"] = time.time()

    def close(self) -> None:
        with self._lock:
            engine, self.engine = self.engine, None
        if engine:
            engine.close()

    @staticmethod
    def _public_session(session: dict[str, Any]) -> dict[str, Any]:
        result = dict(session)
        result.pop("state", None)
        result.pop("last_action", None)
        result["has_pending_action"] = result.pop("pending_action", None) is not None
        return result

    def _state(self) -> dict[str, Any]:
        engine = self.engine
        catalog = [asdict(game) for game in engine.catalog()] if engine else []
        web_game_ids = {game["game_id"] for game in catalog if "web" in game.get("clients", ("web",))}
        return {
            "callsign": self.settings.callsign,
            "host": self.settings.host,
            "transport": self.settings.transport,
            "group": self.settings.group,
            "broadcast_mode": self.settings.broadcast_mode,
            "command_port": self.settings.command_port,
            "kiss_port": self.settings.kiss_port,
            "color_scheme": self.settings.color_scheme,
            "sound_enabled": self.settings.sound_enabled,
            "dx_cluster_url": self.settings.dx_cluster_url,
            "observer_mode": self.settings.observer_mode,
            "tx_enabled": self.settings.tx_enabled,
            "transmitting": self.transmitting,
            "last_transmission": self.last_transmission,
            "ready": engine is not None,
            "connected": bool(engine and (
                self.settings.transport == "practice"
                or not getattr(engine.transport, "closed", threading.Event()).is_set()
            )),
            "error": self.last_error,
            "catalog": catalog,
            "sessions": [self._public_session(session) for session in engine.sessions()
                         if session["game_id"] in web_game_ids] if engine else [],
        }

    def _configure(self, data: dict[str, Any]) -> dict[str, Any]:
        def integer(name: str) -> int:
            value = data.get(name, getattr(self.settings, name))
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
                raise ValueError(f"{name.replace('_', ' ')} must be a port from 1 to 65535")
            return value

        transport = data.get("transport", self.settings.transport)
        if transport not in ("broadcast", "connected", "practice"):
            raise ValueError("choose Broadcast, Connected, or Local practice transport")
        observer = data.get("observer_mode", self.settings.observer_mode)
        if not isinstance(observer, bool):
            raise ValueError("observer mode must be true or false")
        if observer and transport != "broadcast":
            raise ValueError("observer mode requires KISS broadcast transport")
        scheme = data.get("color_scheme", self.settings.color_scheme)
        if scheme not in ("midnight", "daylight", "monochrome"):
            raise ValueError("choose a valid color scheme")
        candidate = Settings(
            callsign=station_name(str(data.get("callsign", self.settings.callsign))),
            host=str(data.get("host", self.settings.host)).strip() or "127.0.0.1",
            transport=transport,
            group=group_name(str(data.get("group", self.settings.group))),
            broadcast_mode=str(data.get("broadcast_mode", self.settings.broadcast_mode)).strip().lower(),
            command_port=integer("command_port"),
            kiss_port=integer("kiss_port"),
            color_scheme=scheme,
            sound_enabled=bool(data.get("sound_enabled", self.settings.sound_enabled)),
            dx_cluster_url=str(data.get("dx_cluster_url", self.settings.dx_cluster_url)).strip(),
            observer_mode=observer,
            tx_enabled=self.settings.tx_enabled,
        )
        previous, self.engine = self.engine, None
        if previous:
            previous.close()
        self.settings = candidate
        self.settings.save(self.settings_path)
        self._connect()
        return self._state()

    def _command(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.engine is None:
            raise ValueError(self.last_error or "save station settings before using the game room")
        engine = self.engine
        name = data.get("command")
        transmit_commands = {"create_game", "invite_player", "cancel_invitation", "accept_invite", "join_game", "announce_game", "set_reply_order", "start_game", "legal_action", "place_word", "exchange_word", "send_chat", "request_sync", "resend_pending_action", "connect_peer"}
        if name in transmit_commands and self.settings.transport != "practice" and not self.settings.tx_enabled:
            raise ValueError("TX is disabled. Enable TX to send radio traffic.")
        sid = data.get("session_id")
        def place_word() -> None:
            from .games.scrabble import make_placement
            row = engine.session(sid)
            game = engine.registry.by_id(row["game_id"])
            state = game.decode_state(row["state"])
            game.validate_roster(state, tuple(row["players"]))
            action = make_placement(state, row["current_player"],
                                    str(data.get("start", "")),
                                    str(data.get("direction", "H")),
                                    str(data.get("word", "")))
            engine.legal_action(sid, action)

        def exchange_word() -> None:
            from .games.scrabble import make_exchange
            row = engine.session(sid)
            game = engine.registry.by_id(row["game_id"])
            state = game.decode_state(row["state"])
            game.validate_roster(state, tuple(row["players"]))
            action = make_exchange(state, row["current_player"], str(data.get("tiles", "")))
            engine.legal_action(sid, action)

        commands: dict[str, Callable[[], Any]] = {
            "create_game": lambda: engine.create_game(
                str(data.get("game", "")),
                str(data["peer"]) if data.get("peer") else None,
                hosted=bool(data.get("hosted", False))),
            "invite_player": lambda: engine.invite_player(sid, str(data.get("peer", ""))),
            "cancel_invitation": lambda: engine.cancel_invitation(sid, str(data.get("peer", ""))),
            "accept_invite": lambda: engine.accept_invite(sid),
            "join_game": lambda: engine.join_game(sid),
            "announce_game": lambda: engine.announce_game(sid),
            "set_reply_order": lambda: engine.set_reply_order(sid, str(data.get("order", ""))),
            "start_game": lambda: engine.start_game(sid),
            "legal_action": lambda: engine.legal_action(sid, bytes.fromhex(str(data.get("action", "")))),
            "place_word": place_word,
            "exchange_word": exchange_word,
            "send_chat": lambda: engine.send_chat(sid, str(data.get("text", ""))),
            "request_sync": lambda: engine.request_sync(sid),
            "resend_pending_action": lambda: engine.resend_pending_action(sid),
            "connect_peer": lambda: engine.connect_peer(str(data.get("peer", ""))),
            "disconnect_peer": engine.disconnect,
            "delete_game": lambda: engine.delete_game(sid),
        }
        if name not in commands:
            raise ValueError("unknown game-room command")
        result = commands[name]()
        selected_id = result if name == "create_game" else sid
        response: dict[str, Any] = {"ok": True}
        if name == "delete_game":
            response["deleted"] = True
            return response
        if selected_id is not None:
            response["session_id"] = str(selected_id)
            try:
                response["session"] = self._public_session(engine.session(selected_id))
                response["chat"] = engine.chat_history(selected_id)
            except KeyError:
                pass
        return response

    def dispatch(self, method: str, path: str, body: bytes = b"") -> tuple[int, Any]:
        """Handle one API request; separated from HTTP for focused integration tests."""
        try:
            route = urlsplit(path).path
            if len(body) > MAX_REQUEST_BYTES:
                raise ValueError("request body is too large")
            data = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(data, dict):
                raise ValueError("request body must be a JSON object")
            with self._lock:
                if method == "GET" and route == "/api/state":
                    return 200, self._state()
                if method == "POST" and route == "/api/settings":
                    return 200, self._configure(data)
                if method == "POST" and route == "/api/tx":
                    enabled = data.get("enabled")
                    if not isinstance(enabled, bool):
                        raise ValueError("TX enabled must be true or false")
                    self.settings.tx_enabled = enabled
                    self.settings.save(self.settings_path)
                    if self.engine:
                        self.engine.set_tx_enabled(enabled if self.settings.transport != "practice" else True)
                    return 200, self._state()
                if method == "POST" and route == "/api/command":
                    return 200, self._command(data)
                prefix = "/api/session/"
                if method == "GET" and route.startswith(prefix):
                    if self.engine is None:
                        raise ValueError(self.last_error or "game service is not ready")
                    sid = unquote(route[len(prefix):])
                    session = self._public_session(self.engine.session(sid))
                    return 200, {"session": session, "chat": self.engine.chat_history(sid)}
                return 404, {"error": "not found"}
        except (ValueError, KeyError, TypeError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return 400, {"error": str(exc) or "invalid request"}


class ArcadeRequestHandler(BaseHTTPRequestHandler):
    """Serve the packaged app and a same-origin JSON API on localhost."""

    controller: WebController

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, status: int, payload: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            status, value = self.controller.dispatch("GET", self.path)
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        asset_names = {"/": "index.html", "/index.html": "index.html",
                       "/app.js": "app.js", "/style.css": "style.css"}
        name = asset_names.get(path)
        if not name:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        try:
            payload = (ASSETS / name).read_bytes()
        except OSError:
            self._send(500, b"Web assets are missing", "text/plain; charset=utf-8")
            return
        content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                        ".css": "text/css; charset=utf-8"}[Path(name).suffix]
        self._send(200, payload, content_type, "no-cache")

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_REQUEST_BYTES:
                raise ValueError("request body is too large")
            body = self.rfile.read(size)
            status, value = self.controller.dispatch("POST", self.path, body)
        except (ValueError, OSError) as exc:
            status, value = 400, {"error": str(exc)}
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")


def make_server(controller: WebController, bind: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Create the local threaded server; callers may use port=0 in tests."""
    handler = type("BoundArcadeRequestHandler", (ArcadeRequestHandler,), {"controller": controller})
    return ThreadingHTTPServer((bind, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Radio Arcade browser client")
    parser.add_argument("--callsign")
    parser.add_argument("--data2g-host")
    parser.add_argument("--transport", choices=("broadcast", "connected", "practice"))
    parser.add_argument("--observer", action="store_true", help="passively watch KISS broadcast games")
    parser.add_argument("--bind", default="127.0.0.1", help="web server address (default: localhost only)")
    parser.add_argument("--port", type=int, default=8765, help="web server port")
    args = parser.parse_args()
    settings = Settings.load()
    if args.callsign:
        settings.callsign = args.callsign.upper()
    if args.data2g_host:
        settings.host = args.data2g_host
    if args.transport:
        settings.transport = args.transport
    if args.observer:
        settings.observer_mode = True
    controller = WebController(settings)
    server = make_server(controller, args.bind, args.port)
    print(f"Radio Arcade web room: http://{args.bind}:{server.server_port}/")
    if args.bind not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: the browser API is unauthenticated; expose it only on a trusted network.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()


if __name__ == "__main__":
    main()
