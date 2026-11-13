"""Local browser client for Radio Arcade's UI-independent game service."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

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
        self._notifications = deque(maxlen=100)
        self._notification_sequence = 0
        self._notification_lock = threading.Lock()
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
        if event.name in ("invitation", "cq_request") or (event.name == "chat_message" and event.data.get("message", {}).get("sender") != self.settings.callsign):
            with self._notification_lock:
                self._notification_sequence += 1
                self._notifications.append({"id": self._notification_sequence, "session_id": event.session,
                    "title": "Game invitation" if event.name == "invitation" else "Game CQ" if event.name == "cq_request" else "New message",
                    "body": (f"{event.data.get('sender')} invited you to {event.data.get('game')}"
                             if event.name == "invitation" else
                             f"{event.data.get('callsign')} wants {event.data.get('game')}"
                             if event.name == "cq_request" else
                             f"{event.data['message']['sender']}: {event.data['message']['body']}")})
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
        from .presentation import scoreboard
        result = dict(session)
        result["scoreboard"] = scoreboard(session)
        result.pop("state", None)
        result.pop("last_action", None)
        result["has_pending_action"] = result.pop("pending_action", None) is not None
        return result

    def _notification_snapshot(self):
        with self._notification_lock:
            return list(self._notifications)

    def _state(self) -> dict[str, Any]:
        engine = self.engine
        catalog = [asdict(game) for game in engine.catalog()] if engine else []
        web_game_ids = {game["game_id"] for game in catalog if "web" in game.get("clients", ("web",))}
        return {
            "notifications": self._notification_snapshot(),
            "callsign": self.settings.callsign,
            "host": self.settings.host,
            "transport": self.settings.transport,
            "group": self.settings.group,
            "broadcast_mode": self.settings.broadcast_mode,
            "command_port": self.settings.command_port,
            "kiss_port": self.settings.kiss_port,
            "color_scheme": self.settings.color_scheme,
            "sound_enabled": self.settings.sound_enabled,
            "experimental_games": self.settings.experimental_games,
            "dx_cluster_url": self.settings.dx_cluster_url,
            "observer_mode": self.settings.observer_mode,
            "tx_enabled": self.settings.tx_enabled,
            "station_id_mode": self.settings.station_id_mode,
            "cw_wpm": self.settings.cw_wpm,
            "identification_method": getattr(engine, "identification_method", None),
            "identification_warning": getattr(engine, "identification_warning", None),
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
            "cq_requests": engine.cq_requests() if engine else [],
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
        station_id_mode = data.get("station_id_mode", self.settings.station_id_mode)
        if station_id_mode not in ("auto", "application"):
            raise ValueError("choose Data2G CWID or application ID")
        cw_wpm = data.get("cw_wpm", self.settings.cw_wpm)
        if type(cw_wpm) is not int or not 15 <= cw_wpm <= 40:
            raise ValueError("CWID speed must be 15–40 WPM")
        experimental_games = data.get("experimental_games", self.settings.experimental_games)
        if not isinstance(experimental_games, bool):
            raise ValueError("experimental games must be true or false")
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
            experimental_games=experimental_games,
            dx_cluster_url=str(data.get("dx_cluster_url", self.settings.dx_cluster_url)).strip(),
            observer_mode=observer,
            tx_enabled=self.settings.tx_enabled,
            station_id_mode=station_id_mode,
            cw_wpm=cw_wpm,
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
        transmit_commands = {"forfeit_game", "create_game", "call_cq", "answer_cq", "invite_player", "cancel_invitation", "accept_invite", "join_game", "announce_game", "set_reply_order", "start_game", "legal_action", "place_word", "exchange_word", "send_chat", "request_sync", "resend_pending_action", "connect_peer", "place_battleship_fleet"}
        if name in transmit_commands and self.settings.transport != "practice" and not self.settings.tx_enabled:
            raise ValueError("TX is disabled. Enable TX to send radio traffic.")
        sid = data.get("session_id")
        if name in ("create_game", "call_cq", "answer_cq") and data.get("game"):
            game = engine.registry.by_key(str(data["game"]))
            if not self.settings.can_offer_game(game.key):
                raise ValueError(f"Enable Experimental games in settings to offer {game.name}.")
        if name == "invite_player":
            game = engine.registry.by_id(engine.session(sid)["game_id"])
            if not self.settings.can_offer_game(game.key):
                raise ValueError(f"Enable Experimental games in settings to invite players to {game.name}.")
        if name == "preview_shot":
            row = engine.session(sid)
            game = engine.registry.by_id(row["game_id"])
            action = bytes.fromhex(str(data.get("action", "")))
            if game.key != "scorched-earth" or len(action) != 2 or action[0] >= 128:
                raise ValueError("expected a Scorched Earth shot")
            actor = row["current_player"] if self.settings.transport == "practice" else self.settings.callsign
            predicted = game.apply_action(game.decode_state(row["state"]), tuple(row["players"]), actor, action)
            return {"ok": True, "view": game.view_model(predicted, tuple(row["players"]))}

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
            "call_cq": lambda: engine.call_cq(str(data["game"]) if data.get("game") else None),
            "answer_cq": lambda: engine.answer_cq(str(data.get("callsign", "")), str(data.get("game", ""))),
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
            "place_battleship_fleet": lambda: engine.place_battleship_fleet(
                sid, data.get("ships", [])),
            "disconnect_peer": engine.disconnect,
            "forfeit_game": lambda: engine.forfeit_game(sid),
            "delete_game": lambda: engine.delete_game(sid),
        }
        if name not in commands:
            raise ValueError("unknown game-room command")
        result = commands[name]()
        selected_id = result if name in ("create_game", "answer_cq") else sid
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

    def _engine_bridge(self, method: str, route: str, query: dict[str, list[str]],
                       data: dict[str, Any]) -> tuple[int, Any]:
        """Engine-neutral API; game ID 10 cannot read or control PICO-8 tables."""
        if self.engine is None:
            raise ValueError(self.last_error or "game service is not ready")
        engine = self.engine
        if method == "GET" and route == "/api/engine/sessions":
            return 200, {"callsign": self.settings.callsign,
                         "transport": self.settings.transport,
                         "sessions": [self._public_session(s) for s in engine.sessions()
                                      if s["game_id"] == 10]}
        if method == "POST" and route == "/api/engine/create":
            return 200, self._command({"command": "create_game", "game": "external-game",
                                       "hosted": bool(data.get("hosted", False)),
                                       "peer": data.get("peer")})

        sid = data.get("session_id") if method == "POST" else query.get("session_id", [None])[0]
        if sid is None:
            raise ValueError("session_id is required")
        row = engine.session(sid)
        if row["game_id"] != 10:
            raise ValueError("not an external engine session")

        if method == "GET" and route == "/api/engine/events":
            after = int(query.get("after", ["0"])[0])
            if after < 0:
                raise ValueError("after must be nonnegative")
            events = engine.store.load_engine_actions(engine._sid(sid), after)
            return 200, {"session": self._public_session(row), "revision": row["revision"],
                         "events": [{"revision": e["revision"], "actor": e["actor"],
                                     "hex": e["action"].hex()} for e in events]}

        if method == "POST" and route == "/api/engine/action":
            raw = bytes.fromhex(str(data.get("hex", "")))
            if not 1 <= len(raw) <= 190:
                raise ValueError("engine action must be 1–190 bytes")
            return 200, self._command({"command": "legal_action", "session_id": sid,
                                       "action": raw.hex()})

        if method == "POST" and route == "/api/engine/control":
            command = data.get("command")
            if command not in ("accept_invite", "join_game", "start_game",
                               "invite_player", "request_sync", "resend_pending_action"):
                raise ValueError("unsupported engine session command")
            return 200, self._command({"command": command, "session_id": sid,
                                       "peer": data.get("peer")})

        if method == "GET" and route == "/api/engine/opaque":
            after = int(query.get("after", ["0"])[0])
            if after < 0:
                raise ValueError("after must be nonnegative")
            return 200, {"messages": engine.store.load_engine_opaque(engine._sid(sid), after)}

        if method == "GET" and route == "/api/engine/delivery":
            sequence = int(query.get("sequence", ["-1"])[0])
            if not 0 <= sequence <= 65535:
                raise ValueError("invalid side-channel sequence")
            return 200, {"state": engine.store.engine_opaque_delivery(
                engine._sid(sid), engine.callsign, sequence)}

        if method == "POST" and route == "/api/engine/opaque":
            message = data.get("message")
            if not isinstance(message, str) or not message.startswith("RA:"):
                raise ValueError("engine side-channel messages must start with RA:")
            if self.settings.transport != "practice" and not self.settings.tx_enabled:
                raise ValueError("TX is disabled")
            return 200, {"sequence": engine.send_opaque_state(
                sid, str(data.get("target", "")), message)}

        if method == "POST" and route == "/api/engine/estimate":
            from .airtime import estimate_action
            payload_bytes = data.get("payload_bytes")
            if type(payload_bytes) is not int or not 0 <= payload_bytes <= 189:
                raise ValueError("payload_bytes must be an integer from 0 to 189")
            compact = data.get("compact", row["status"] == "active" and engine.compact_turns)
            if type(compact) is not bool:
                raise ValueError("compact must be a boolean")
            destination = str(data.get("destination") or row["coordinator"])
            estimate = estimate_action(
                payload_bytes + 1, source=self.settings.callsign, destination=destination,
                game_id=10, compact=compact, baud=data.get("baud"), rate_bps=data.get("rate_bps"),
                bits_per_symbol=data.get("bits_per_symbol"),
                code_rate=data.get("code_rate"),
                modem_overhead_bytes=data.get("modem_overhead_bytes", 0),
                preamble_ms=data.get("preamble_ms", 0),
                transmissions=data.get("transmissions", 1))
            return 200, {**asdict(estimate), "game_payload_bytes": payload_bytes,
                         "compact": compact,
                         "note": "Estimate only; excludes dynamic Data2G scheduling and retries"}

        return 404, {"error": "not found"}

    def dispatch(self, method: str, path: str, body: bytes = b"") -> tuple[int, Any]:
        """Handle one API request; separated from HTTP for focused integration tests."""
        try:
            route = urlsplit(path).path
            query = parse_qs(urlsplit(path).query)
            if len(body) > MAX_REQUEST_BYTES:
                raise ValueError("request body is too large")
            data = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(data, dict):
                raise ValueError("request body must be a JSON object")
            with self._lock:
                if route.startswith("/api/engine/"):
                    return self._engine_bridge(method, route, query, data)
                if route.startswith("/api/pico8/"):
                    if self.engine is None:
                        raise ValueError(self.last_error or "game service is not ready")
                    engine = self.engine
                    if method == "GET" and route == "/api/pico8/sessions":
                        return 200, {"callsign": self.settings.callsign,
                                     "transport": self.settings.transport,
                                     "sessions": [self._public_session(s) for s in engine.sessions()
                                                  if s["game_id"] == 9]}
                    if method == "POST" and route == "/api/pico8/create":
                        return 200, self._command({"command": "create_game", "game": "pico8-cartridge",
                                                   "hosted": bool(data.get("hosted", False)),
                                                   "peer": data.get("peer")})
                    sid = (data.get("session_id") if method == "POST" else
                           query.get("session_id", [None])[0])
                    if sid is None:
                        raise ValueError("session_id is required")
                    row = engine.session(sid)
                    if row["game_id"] != 9:
                        raise ValueError("not a PICO-8 cartridge session")
                    if method == "GET" and route == "/api/pico8/events":
                        after = int(query.get("after", ["0"])[0])
                        if after < 0:
                            raise ValueError("after must be nonnegative")
                        events = engine.store.load_pico8_actions(engine._sid(sid), after)
                        return 200, {"session": self._public_session(row),
                                     "revision": row["revision"],
                                     "events": [{"revision": e["revision"], "actor": e["actor"],
                                                 "hex": e["action"].hex()} for e in events]}
                    if method == "GET" and route == "/api/pico8/opaque":
                        after = int(query.get("after", ["0"])[0])
                        if after < 0:
                            raise ValueError("after must be nonnegative")
                        return 200, {"messages": engine.store.load_pico8_opaque(engine._sid(sid), after)}
                    if method == "GET" and route == "/api/pico8/delivery":
                        sequence = int(query.get("sequence", ["-1"])[0])
                        if not 0 <= sequence <= 65535:
                            raise ValueError("invalid side-channel sequence")
                        return 200, {"state": engine.store.pico8_opaque_delivery(
                            engine._sid(sid), engine.callsign, sequence)}
                    if method == "POST" and route == "/api/pico8/action":
                        raw = bytes.fromhex(str(data.get("hex", "")))
                        if not 1 <= len(raw) <= 190:
                            raise ValueError("PICO-8 action must be 1–190 bytes")
                        return 200, self._command({"command": "legal_action", "session_id": sid,
                                                   "action": raw.hex()})
                    if method == "POST" and route == "/api/pico8/opaque":
                        message = str(data.get("message", ""))
                        if not message.startswith("P8:"):
                            raise ValueError("invalid PICO-8 side-channel message")
                        if self.settings.transport != "practice" and not self.settings.tx_enabled:
                            raise ValueError("TX is disabled")
                        return 200, {"sequence": engine.send_opaque_state(sid, str(data.get("target", "")), message)}
                    return 404, {"error": "not found"}
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

    def _send(self, status: int, payload: bytes, content_type: str, cache: str = "no-store",
              *, exported_cart: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        scripts = "'self' 'unsafe-inline' 'wasm-unsafe-eval'" if exported_cart else "'self'"
        self.send_header("Content-Security-Policy", f"default-src 'self'; script-src {scripts}; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/pico8/carts":
            directory = os.environ.get("RADIO_ARCADE_PICO8_CARTS")
            carts = (sorted(p.name for p in Path(directory).glob("*.html") if p.is_file())
                     if directory and Path(directory).is_dir() else [])
            self._send(200, json.dumps({"carts": carts}).encode(), "application/json")
            return
        if path.startswith("/api/"):
            status, value = self.controller.dispatch("GET", self.path)
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if path == "/pico8/bridge.js":
            self._send(200, (ASSETS / "pico8_bridge.js").read_bytes(),
                       "text/javascript; charset=utf-8", "no-cache")
            return
        if path == "/pico8/":
            self._send(200, (ASSETS / "pico8_lobby.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/pico8/lobby.js":
            self._send(200, (ASSETS / "pico8_lobby.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        if path == "/siege-crawl.js":
            self._send(200, (ASSETS / "siege-crawl.js").read_bytes(),
                       "text/javascript; charset=utf-8", "no-cache", exported_cart=True)
            return
        if path == "/siege-crawl.html":
            self._send(200, (ASSETS / "siege-crawl.html").read_bytes(),
                       "text/html; charset=utf-8", "no-cache", exported_cart=True)
            return
        if path.startswith("/pico8/carts/"):
            # Only explicitly configured local exports. Never resolve paths
            # outside the designated cartridge directory.
            name = unquote(path[len("/pico8/carts/"):])
            directory = os.environ.get("RADIO_ARCADE_PICO8_CARTS")
            if not directory or not name or Path(name).name != name or Path(name).suffix not in (".html", ".js", ".png", ".wasm", ".data"):
                self._send(404, b"Not found", "text/plain")
                return
            try:
                base = Path(directory).resolve()
                target = (base / name).resolve()
                if not target.is_relative_to(base):
                    raise OSError("cartridge path escapes configured directory")
                payload = target.read_bytes()
            except OSError:
                self._send(404, b"Not found", "text/plain")
                return
            if name.endswith(".html"):
                # Exported PICO-8 defines pico8_gpio in the HTML shell.
                injection = b'<script src="/pico8/bridge.js" defer></script>'
                payload = payload.replace(b"</body>", injection + b"</body>")
                if injection not in payload:
                    payload += injection
            types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".png": "image/png", ".wasm": "application/wasm", ".data": "application/octet-stream"}
            self._send(200, payload, types[Path(name).suffix], "no-cache",
                       exported_cart=name.endswith(".html"))
            return
        asset_names = {"/": "index.html", "/index.html": "index.html",
                       "/app.js": "app.js", "/style.css": "style.css",
                       "/tank-teal.png": "tank-teal.png", "/tank-amber.png": "tank-amber.png"}
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
                        ".css": "text/css; charset=utf-8", ".png": "image/png"}[Path(name).suffix]
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
