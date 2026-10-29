"""Polished keyboard-first terminal client built on the headless engine."""

from __future__ import annotations

import argparse
import asyncio
from functools import partial
import threading

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, RichLog, Select, Static, Switch
from rich.text import Text

from .service import ArcadeService, EngineEvent, station_name
from .settings import Settings
from .transport_factory import create_service, group_name
from .games.scrabble import make_exchange, make_placement


class ServiceReady(Message):
    def __init__(self, service: ArcadeService):
        self.service = service
        super().__init__()


class ServiceFailed(Message):
    def __init__(self, error: str):
        self.error = error
        super().__init__()


class EngineNotice(Message):
    def __init__(self, event: EngineEvent):
        self.event = event
        super().__init__()


class ActionResult(Message):
    def __init__(self, result=None, error: str = "", select_session: bool = False):
        self.result, self.error, self.select_session = result, error, select_session
        super().__init__()


class ChatSent(Message):
    def __init__(self, session: str, text: str):
        self.session, self.text = session, text
        super().__init__()


class SettingsScreen(ModalScreen[Settings | None]):
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]
    CSS = """
    SettingsScreen { align:center middle; background:rgba(4,8,14,0.78); }
    #settings-card { width:64; height:auto; max-height:90%; padding:2 3; border:round #3e6573; background:#101a27; }
    #settings-title { color:#e8f4f4; text-style:bold; margin-bottom:1; }
    #settings-copy { color:#92a6b6; margin-bottom:1; }
    .setting-label { color:#73dfc8; margin-top:1; }
    #settings-actions { height:3; margin-top:2; align-horizontal:right; }
    """

    def __init__(self, settings: Settings):
        super().__init__(); self.settings = settings

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="settings-card"):
            yield Label("Station connection", id="settings-title")
            yield Static("Connect the game engine to your local or network Data2G host.", id="settings-copy")
            yield Label("CALLSIGN", classes="setting-label")
            yield Input(self.settings.callsign, placeholder="N0CALL", id="callsign")
            yield Label("DATA2G HOST", classes="setting-label")
            yield Input(self.settings.host, placeholder="127.0.0.1", id="host")
            yield Label("TRANSPORT", classes="setting-label")
            yield Select((("Broadcast · KISS", "broadcast"), ("Connected · VARA-like", "connected"), ("Local practice · play both sides", "practice")),
                         value=self.settings.transport, allow_blank=False, id="transport")
            yield Label("BROADCAST GROUP", classes="setting-label")
            yield Input(self.settings.group, placeholder="RAGAMES", id="group")
            yield Label("COMMAND PORT", classes="setting-label")
            yield Input(str(self.settings.command_port), placeholder="8300", id="command-port")
            yield Label("KISS PORT", classes="setting-label")
            yield Input(str(self.settings.kiss_port), placeholder="8100", id="kiss-port")
            yield Label("COLOR SCHEME", classes="setting-label")
            yield Select((("Midnight", "midnight"), ("Daylight", "daylight"), ("Monochrome", "monochrome")),
                         value=self.settings.color_scheme, allow_blank=False, id="color-scheme")
            yield Label("INVITATION AND TURN SOUNDS", classes="setting-label")
            yield Switch(value=self.settings.sound_enabled, id="sound-enabled")
            yield Label("OBSERVER MODE · broadcast only, manual sync transmits", classes="setting-label")
            yield Switch(value=self.settings.observer_mode, id="observer-mode")
            with Horizontal(id="settings-actions"):
                yield Button("Cancel", id="settings-cancel")
                yield Button("Save and connect", variant="success", id="settings-save")

    @on(Button.Pressed, "#settings-cancel")
    def cancel(self): self.dismiss(None)

    @on(Button.Pressed, "#settings-save")
    def save(self):
        call = self.query_one("#callsign", Input).value
        host = self.query_one("#host", Input).value.strip() or "127.0.0.1"
        group = self.query_one("#group", Input).value
        transport = self.query_one("#transport", Select).value
        try:
            command_port = int(self.query_one("#command-port", Input).value)
            kiss_port = int(self.query_one("#kiss-port", Input).value)
        except ValueError:
            command_port = kiss_port = 0
        try:
            call, group = station_name(call), group_name(group)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        if not 0 < command_port <= 65535 or not 0 < kiss_port <= 65535:
            self.notify("Enter valid TCP ports.", severity="error")
            return
        self.settings.callsign, self.settings.host = call, host
        observing = self.query_one("#observer-mode", Switch).value
        if observing and transport != "broadcast":
            self.notify("Observer mode requires Broadcast · KISS.", severity="error")
            return
        self.settings.observer_mode = observing
        self.settings.group, self.settings.transport = group, str(transport)
        self.settings.command_port, self.settings.kiss_port = command_port, kiss_port
        self.settings.color_scheme = str(self.query_one("#color-scheme", Select).value)
        self.settings.sound_enabled = self.query_one("#sound-enabled", Switch).value
        self.settings.save()
        self.dismiss(self.settings)


class ArcadeTUI(App):
    TITLE = "RADIO ARCADE"
    SUB_TITLE = "Data2G game room"
    CSS = """
    Screen { background:#091019; color:#dce7f3; }
    Screen.daylight { background:#eaf0f4; color:#17212d; }
    Screen.daylight Header, Screen.daylight Footer { background:#dce6ec; color:#087d70; }
    Screen.daylight .panel { background:#ffffff; border:round #c9d4de; }
    Screen.daylight #session-list, Screen.daylight #chat-log { background:#f2f6f8; border:round #ccd7df; }
    Screen.daylight ListItem { color:#33485c; }
    Screen.daylight ListItem.--highlight { background:#d7eee9; color:#075f57; }
    Screen.daylight #game-title { color:#1b2936; }
    Screen.daylight #players, Screen.daylight #turn-line { color:#284456; }
    Screen.monochrome { background:#000000; color:#ffffff; }
    Screen.monochrome .panel, Screen.monochrome #session-list, Screen.monochrome #chat-log { background:#111111; border:round #aaaaaa; }
    Screen.monochrome Header, Screen.monochrome Footer, Screen.monochrome Button, Screen.monochrome Input, Screen.monochrome Select { background:#222222; color:#ffffff; border:round #aaaaaa; }
    Screen.monochrome .eyebrow, Screen.monochrome .muted, Screen.monochrome #game-title, Screen.monochrome #players, Screen.monochrome #turn-line, Screen.monochrome #link-status, Screen.monochrome #wordmark { color:#ffffff; }
    Screen.monochrome .cell { background:#222222; color:#ffffff; border:round #aaaaaa; }
    Screen.monochrome ListItem.--highlight { background:#555555; color:#ffffff; }
    Header { background:#111d29; color:#70e0c7; }
    Footer { background:#111d29; color:#8fa2b6; }
    #shell { height:1fr; padding:1 2; }
    #topline { height:3; margin-bottom:1; }
    #wordmark { color:#e6f1f6; text-style:bold; width:1fr; }
    #link-status { color:#70dfc4; width:auto; padding:1 2; border:round #2e615a; background:#10231f; }
    #columns { height:1fr; }
    .panel { border:round #263849; background:#101925; padding:1 2; }
    #left { width:34; margin-right:1; }
    #center { width:1fr; margin-right:1; }
    #right { width:38; }
    Screen.compact #left, Screen.compact #center, Screen.compact #right { display:none; width:1fr; margin-right:0; }
    Screen.compact.show-lobby #left, Screen.compact.show-board #center, Screen.compact.show-chat #right { display:block; }
    Screen.compact #left { overflow-y:auto; }
    .eyebrow { color:#66d9c2; text-style:bold; margin:1 0; }
    .muted { color:#8293a7; }
    #session-list { height:1fr; border:round #263849; background:#0b131e; }
    ListItem { padding:0 1; color:#b3c1d1; }
    ListItem.--highlight { background:#1d3538; color:#a0f0dc; }
    Input, Select { border:round #34475c; background:#0c1521; margin-bottom:1; }
    Button { background:#1b2938; border:round #394c61; color:#e5edf5; margin:0 1 1 0; }
    Button:hover { background:#263c4b; }
    Button.-primary { background:#60d9bd; color:#071611; text-style:bold; }
    #session-actions { height:auto; }
    #game-title { color:#f2f6fa; text-style:bold; margin-bottom:1; }
    #board { height:1fr; min-height:15; align: center middle; }
    .board-row { height:7; align: center middle; }
    .cell { width:13; height:5; margin:0 1; border:round #395066; background:#172536; color:#77dfc9; text-style:bold; }
    #turn-line { color:#76dfc9; text-style:bold; margin:1 0; }
    #players { color:#adbdce; height:auto; margin-bottom:1; }
    #chat-log { height:1fr; border:round #263849; background:#0a121c; padding:0 1; }
    #chat-input { margin-top:1; }
    #nim-piles { height:auto; margin:2 0; color:#7ce0c9; }
    #nim-controls { height:3; }
    #nim-controls Input { width:10; }
    .word-board { color:#e4edf5; text-style:bold; height:auto; margin:0 0 1 0; }
    .word-meta { color:#7ce0c9; height:auto; margin:0 0 1 0; }
    #word-start { width:10; }
    #word-direction { width:8; }
    #word-text { width:1fr; }
    #word-exchange-tiles { width:1fr; }
    #empty-state { color:#8799ad; content-align:center middle; height:1fr; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh"), ("s", "settings", "Settings"),
                ("1", "show_lobby", "Lobby"), ("2", "show_board", "Board"), ("3", "show_chat", "Chat")]

    def __init__(self, settings: Settings):
        super().__init__(); self.settings = settings
        self.engine: ArcadeService | None = None
        self._list_signature = None
        self._board_signature = None
        self.unsubscribe = None
        self.selected_id: str | None = None
        self.selected_game = "tic-tac-toe"
        self._action_lock = threading.Lock()
        # Textual list mutations yield; never interleave two rebuilds.
        self._session_refresh_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            with Horizontal(id="topline"):
                yield Label("◈  RADIO ARCADE", id="wordmark")
                yield Static("●  OFFLINE", id="link-status")
            with Horizontal(id="columns"):
                with VerticalScroll(id="left", classes="panel"):
                    yield Label("YOUR TABLES", classes="eyebrow")
                    yield ListView(id="session-list")
                    yield Label("START A GAME", classes="eyebrow")
                    yield Select((("Tic-tac-toe", "tic-tac-toe"), ("Nim · 2–4 players", "nim")),
                                 value="tic-tac-toe", allow_blank=False, id="game-select")
                    yield Input(placeholder="Opponent callsign", id="peer-input")
                    yield Button("＋  Create game", id="new-game", variant="primary")
                    yield Button("Connect / resume peer", id="connect-peer")
                    yield Button("Disconnect radio link", id="disconnect-peer")
                    with Vertical(id="session-actions"):
                        yield Button("Accept invitation", id="accept-invite")
                        yield Button("Invite station", id="invite-player")
                        yield Button("Cancel pending invitation", id="cancel-invite")
                        yield Button("Start lobby", id="start-game")
                        yield Button("Sync / recover state", id="sync-state")
                with Vertical(id="center", classes="panel"):
                    yield Label("Choose a game or invite a station", id="game-title")
                    yield Static("Open-information games for the airwaves.", id="turn-line")
                    with VerticalScroll(id="board"):
                        yield Static("Start a table to take a seat.", id="empty-state")
                    yield Static("PUBLIC RADIO · GAME ACTIONS ARE EXPLICIT", classes="muted")
                with Vertical(id="right", classes="panel"):
                    yield Label("PLAYERS", classes="eyebrow")
                    yield Static("No table selected", id="players")
                    yield Label("SIDE CHAT", classes="eyebrow")
                    yield RichLog(id="chat-log", markup=True, wrap=True, highlight=True)
                    yield Input(placeholder="Message · up to 150 UTF-8 bytes", id="chat-input")
                    yield Static("0 / 150 bytes", id="chat-count", classes="muted")
                    yield Button("Send message", id="send-chat")
        yield Footer()

    async def on_mount(self):
        self._update_layout(self.size.width)
        if self.settings.color_scheme == "daylight":
            self.screen.add_class("daylight")
        self.screen.set_class(self.settings.color_scheme == "monochrome", "monochrome")
        if not self.settings.callsign:
            self.push_screen(SettingsScreen(self.settings), self._settings_result)
        else:
            self._connect()
        self.set_interval(2.0, self._refresh_sessions)

    def _update_layout(self, width):
        self.screen.set_class(width < 120, "compact")
        if not any(self.screen.has_class(c) for c in ("show-lobby", "show-board", "show-chat")):
            self.screen.add_class("show-lobby")

    def on_resize(self, event):
        self._update_layout(event.size.width)

    def _show_pane(self, name):
        for pane in ("lobby", "board", "chat"):
            self.screen.set_class(pane == name, f"show-{pane}")

    def action_show_lobby(self): self._show_pane("lobby")
    def action_show_board(self): self._show_pane("board")
    def action_show_chat(self): self._show_pane("chat")

    def _settings_result(self, result):
        if result:
            self.settings = result
            self.screen.set_class(result.color_scheme == "daylight", "daylight")
            self.screen.set_class(result.color_scheme == "monochrome", "monochrome")
            self._connect()

    def action_settings(self):
        self.push_screen(SettingsScreen(self.settings), self._settings_result)

    async def action_refresh(self): await self._refresh_sessions()

    def _connect(self):
        if self.unsubscribe:
            self.unsubscribe(); self.unsubscribe = None
        if self.engine:
            self.engine.close(); self.engine = None
        self.query_one("#link-status", Static).update("◌  CONNECTING TO DATA2G…")
        def worker():
            try: self.post_message(ServiceReady(create_service(self.settings)))
            except Exception as exc: self.post_message(ServiceFailed(str(exc)))
        threading.Thread(target=worker, daemon=True, name="radio-arcade-connect").start()

    def on_service_ready(self, message: ServiceReady):
        self.engine = message.service
        self._list_signature = None
        self._board_signature = None
        self.unsubscribe = self.engine.subscribe(lambda event: self.post_message(EngineNotice(event)))
        offline = hasattr(self.engine.transport, "closed") and self.engine.transport.closed.is_set()
        self.query_one("#link-status", Static).update("●  OFFLINE · press S to reconnect" if offline else f"●  {'OBSERVER · RX ONLY' if self.settings.observer_mode else self.settings.transport.upper()} · {self.settings.callsign}")
        for name in ("#game-select", "#peer-input", "#new-game", "#send-chat", "#chat-input"):
            self.query_one(name).disabled = self.settings.observer_mode
        self.query_one("#connect-peer", Button).display = self.settings.transport == "connected"
        self.query_one("#disconnect-peer", Button).display = self.settings.transport == "connected"
        game_select = self.query_one("#game-select", Select)
        game_select.set_options((f"{game.name} · {game.min_players}–{game.max_players}", game.key)
                                for game in self.engine.catalog())
        self.call_later(self._refresh_sessions)

    def on_service_failed(self, message: ServiceFailed):
        self.query_one("#link-status", Static).update("●  OFFLINE · press S to edit settings")
        self.notify(message.error, severity="error", timeout=8)

    def on_engine_notice(self, message: EngineNotice):
        event = message.event
        if self.settings.sound_enabled and (event.name == "invitation" or
                (event.name == "session_updated" and event.data.get("committed") and
                 event.data["session"].get("current_player") == self.settings.callsign)):
            self.bell()
        if event.name == "transport_status":
            state = event.data.get("status")
            text = f"●  CONNECTED · {event.data.get('peer')}" if state == "connected" else "●  OFFLINE · press S to reconnect" if state == "offline" else "●  CONNECTED MODE · LISTENING"
            self.query_one("#link-status", Static).update(text)
        if event.name == "invitation":
            self.notify(f"{event.data['sender']} invited you to {event.data['game']} · select it and accept")
        if event.name == "protocol_error":
            self.notify(f"Cannot read game packet: {event.data.get('error', '')}", severity="warning")
        if event.name == "unsupported_game":
            self.notify(f"Unsupported game ID {event.data['game_id']} from {event.data['sender']}", severity="warning")
        if event.name == "opaque_state":
            self.notify(f"Opaque game state synchronized from {event.data['sender']} · content hidden")
        if event.name == "delivery" and event.data.get("state") == "unconfirmed":
            self.notify("No acknowledgement yet; the game is waiting for the other station.", severity="warning")
        if event.name in ("chat_message", "chat_delivery"):
            self._refresh_chat(event.session)
        if event.name in ("session_updated", "invitation", "delivery"):
            self.call_later(self._refresh_sessions)

    async def _refresh_sessions(self):
        async with self._session_refresh_lock:
            await self._refresh_sessions_locked()

    async def _refresh_sessions_locked(self):
        if not self.engine or not self.is_mounted: return
        listing = self.query_one("#session-list", ListView)
        sessions = self.engine.sessions()
        signature = tuple((s["id"], s["game"], tuple(s["players"]), s["status"],
                           s.get("current_player")) for s in sessions)
        if signature == self._list_signature:
            await self._show_session(self.selected_id)
            return
        self._list_signature = signature
        current = self.selected_id
        await listing.clear()
        for session in sessions:
            peer = (" / ".join(session["players"]) or session["coordinator"]) if self.settings.observer_mode else next((p for p in session["players"] if p != self.settings.callsign), "Open lobby")
            turn = "your turn" if session.get("current_player") == self.settings.callsign else session["status"]
            await listing.append(ListItem(Label(f"{session['game']}  ·  {peer}\n{turn}  ·  {session['id'][-6:]}"),
                                          id="s" + session["id"]))
            if session["id"] == current:
                listing.index = len(listing.children) - 1
        if listing.index is None and listing.children:
            listing.index = 0
        if listing.index is not None and listing.children:
            item = listing.children[listing.index]
            self.selected_id = item.id[1:] if item.id else None
            await self._show_session(self.selected_id)

    @on(ListView.Selected, "#session-list")
    async def _session_selected(self, event: ListView.Selected):
        if event.item.id:
            self.selected_id = event.item.id[1:]
            await self._show_session(self.selected_id)

    async def _show_session(self, sid: str | None):
        if not self.engine or not sid: return
        try: session = self.engine.session(sid)
        except KeyError: return
        signature = (sid, session["status"], session["revision"], session["sequence"],
                     session["pending_action"], tuple(session["players"]),
                     tuple(session["invited"]), repr(session["view"]),
                     repr(session.get("last_move")), session.get("error"), self.settings.transport)
        if signature == self._board_signature:
            return
        self._board_signature = signature
        self.query_one("#game-title", Label).update(f"{session['game']}  ·  {sid[-6:]}")
        invited = "\nInvited: " + ", ".join(session["invited"]) if session["invited"] else ""
        self.query_one("#players", Static).update("  ·  ".join(session["players"]) + invited + f"\n\n{session['status'].upper()}  ·  rev {session['revision']}")
        state = session["view"]
        turn = session["current_player"]
        if self.settings.observer_mode: line = "OBSERVING · RX ONLY" if state else "OBSERVING · AWAITING SNAPSHOT"
        elif session["status"] == "invited": line = f"INVITATION FROM {session['coordinator']} · accept to join"
        elif session["status"] == "lobby": line = "LOBBY · invite players, then start"
        elif session["status"] == "joining": line = "WAITING FOR SESSION START"
        elif session["status"] == "cancelled": line = "INVITATION CANCELLED"
        elif session["pending_action"]: line = "MOVE SENT · WAITING FOR COMMIT"
        elif state and state.get("winner"): line = f"GAME WON BY {state['winner']}"
        elif session["status"] == "finished": line = "GAME OVER · DRAW"
        else: line = f"LOCAL PRACTICE · {turn}'s turn" if self.settings.transport == "practice" else f"YOUR TURN · {turn}" if turn == self.settings.callsign else f"WAITING FOR {turn}"
        if session.get("error"): line = f"MOVE REJECTED · {session['error']}"
        if session.get("last_move"):
            line += f"  ·  LAST: {session['last_move']['actor']} {session['last_move']['description']}"
        self.query_one("#turn-line", Static).update(line)
        self.query_one("#accept-invite", Button).display = not self.settings.observer_mode and session["status"] == "invited"
        self.query_one("#invite-player", Button).display = not self.settings.observer_mode and session["status"] == "lobby"
        self.query_one("#cancel-invite", Button).display = not self.settings.observer_mode and session["status"] == "lobby" and bool(session["invited"])
        self.query_one("#start-game", Button).display = not self.settings.observer_mode and session["status"] == "lobby"
        self.query_one("#sync-state", Button).display = self.settings.observer_mode or (session["coordinator"] != self.settings.callsign and session["status"] in ("active", "finished"))
        self.query_one("#sync-state", Button).label = "Request snapshot (TRANSMITS)" if self.settings.observer_mode else "Sync / recover state"
        board = self.query_one("#board", VerticalScroll)
        await board.remove_children()
        if state:
            if state["kind"] == "grid":
                for y in range(state["height"]):
                    cells = []
                    for x in range(state["width"]):
                        index = y * state["width"] + x
                        text = state["cells"][index].strip() or str(state["labels"][index])
                        active = not self.settings.observer_mode and (self.settings.transport == "practice" or turn == self.settings.callsign) and session["status"] == "active" and not session["pending_action"]
                        button = Button(text, id=f"cell-{index}", classes="cell", disabled=not active or bool(state["cells"][index].strip()))
                        cells.append(button)
                    await board.mount(Horizontal(*cells, classes="board-row"))
            elif state["kind"] == "piles":
                for i, count in enumerate(state["piles"]):
                    await board.mount(Static(f"PILE {i+1}   {'◆ ' * count} ({count})", classes="nim-pile"))
                await board.mount(Static("Select pile and amount below. A player takes one or more items.", classes="muted"))
                row = Horizontal(
                    Select(((f"Pile {i+1} ({n})", i) for i, n in enumerate(state["piles"])),
                           value=next((i for i,n in enumerate(state["piles"]) if n), 0),
                           allow_blank=False, id="nim-pile"),
                    Input("1", placeholder="Amount", id="nim-amount"),
                    Button("Take", id="nim-take", variant="primary",
                           disabled=(self.settings.transport != "practice" and turn != self.settings.callsign) or session["status"] != "active"),
                    id="nim-controls")
                await board.mount(row)
            elif state["kind"] == "word-tiles":
                header = "    " + " ".join(chr(65 + x) for x in range(15))
                rows = [header]
                for y in range(15):
                    cells = []
                    for x in range(15):
                        index = y * 15 + x
                        cells.append(state["cells"][index] or ("★" if index == 112 else state["premiums"][index]))
                    rows.append(f"{y+1:2d}  " + " ".join(f"{cell:>2}" for cell in cells))
                await board.mount(Static("\n".join(rows), classes="word-board", markup=False))
                scoreline = "  ·  ".join(f"{player} {score}" for player, score in state["scores"].items())
                await board.mount(Static(f"{scoreline}    BAG {state['bag']}    {state['lexicon']}    OPEN RACKS", classes="word-meta"))
                for player, rack in state["racks"].items():
                    await board.mount(Static(f"{player} RACK  " + "  ".join(rack), classes="word-meta"))
                enabled = (self.settings.transport == "practice" or turn == self.settings.callsign) and session["status"] == "active" and not session["pending_action"]
                await board.mount(Horizontal(Input("H8", placeholder="Start", id="word-start"),
                    Input("H", placeholder="H/V", id="word-direction"),
                    Input(placeholder="Word · {A} uses a blank as A", id="word-text"),
                    Button("Place", id="word-place", variant="primary", disabled=not enabled)))
                await board.mount(Horizontal(Input(placeholder="Exchange letters · ? for blank", id="word-exchange-tiles"),
                    Button("Exchange", id="word-exchange", disabled=not enabled or state["bag"] < 7),
                    Button("Pass", id="word-pass", disabled=not enabled)))
            else:
                await board.mount(Static(str(state), classes="muted"))
                await board.mount(Input(placeholder="Plugin action · hex bytes", id="plugin-action"))
                await board.mount(Button("Submit action", id="plugin-submit", variant="primary",
                                         disabled=(self.settings.transport != "practice" and turn != self.settings.callsign) or session["status"] != "active"))
        else:
            await board.mount(Static("Waiting for the host to start this lobby.", id="empty-state"))
        self._refresh_chat(sid)

    def _refresh_chat(self, sid: str | None):
        if not self.engine or not sid or sid != self.selected_id: return
        log = self.query_one("#chat-log", RichLog); log.clear()
        for msg in self.engine.chat_history(sid):
            line = Text(msg['sender'], style="bold white" if self.settings.color_scheme == "monochrome" else "bold #76dfc9")
            line.append(f"  {msg['body']}  ")
            line.append(msg['delivery'], style="dim")
            log.write(line)

    def _run_action(self, fn, select_session=False):
        if not self.engine: return
        def worker():
            try: self.post_message(ActionResult(fn(), select_session=select_session))
            except Exception as exc: self.post_message(ActionResult(error=str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def on_action_result(self, message: ActionResult):
        if message.error: self.notify(message.error, severity="error")
        elif message.select_session and message.result:
            self.selected_id = message.result
            self._list_signature = None
        self.call_later(self._refresh_sessions)

    @on(Button.Pressed, "#new-game")
    def _new_game(self):
        if not self.engine: return
        peer = self.query_one("#peer-input", Input).value.strip().upper() or None
        game = self.query_one("#game-select", Select).value
        self._run_action(partial(self.engine.create_game, str(game), peer), select_session=True)

    @on(Button.Pressed, "#connect-peer")
    def _connect_peer(self):
        if not self.engine: return
        peer = self.query_one("#peer-input", Input).value.strip().upper()
        if not peer: self.notify("Enter a callsign to connect or resume.", severity="warning"); return
        self._run_action(partial(self.engine.connect_peer, peer))

    @on(Button.Pressed, "#disconnect-peer")
    def _disconnect_peer(self):
        if self.engine: self._run_action(self.engine.disconnect)

    @on(Button.Pressed, "#accept-invite")
    def _accept(self):
        if self.engine and self.selected_id: self._run_action(partial(self.engine.accept_invite, self.selected_id))

    @on(Button.Pressed, "#invite-player")
    def _invite(self):
        if not self.engine or not self.selected_id: return
        peer = self.query_one("#peer-input", Input).value.strip().upper()
        self._run_action(partial(self.engine.invite_player, self.selected_id, peer))

    @on(Button.Pressed, "#start-game")
    def _start(self):
        if self.engine and self.selected_id: self._run_action(partial(self.engine.start_game, self.selected_id))

    @on(Button.Pressed, "#cancel-invite")
    def _cancel_invite(self):
        if not self.engine or not self.selected_id: return
        peer = self.query_one("#peer-input", Input).value.strip().upper()
        if not peer:
            self.notify("Enter the pending station callsign first.", severity="warning"); return
        self._run_action(partial(self.engine.cancel_invitation, self.selected_id, peer))

    @on(Button.Pressed, "#sync-state")
    def _sync_state(self):
        if self.engine and self.selected_id:
            if self.settings.observer_mode:
                self.notify("Transmitting one snapshot request", severity="warning")
            self._run_action(partial(self.engine.request_sync, self.selected_id))

    @on(Button.Pressed, "#send-chat")
    def _send_chat(self):
        if not self.engine or not self.selected_id: return
        input_box = self.query_one("#chat-input", Input); text = input_box.value.strip()
        if not text: return
        sid, engine = self.selected_id, self.engine
        def worker():
            try:
                engine.send_chat(sid, text)
                self.post_message(ChatSent(sid, text))
            except Exception as exc:
                self.post_message(ActionResult(error=str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def on_chat_sent(self, message: ChatSent):
        input_box = self.query_one("#chat-input", Input)
        if self.selected_id == message.session and input_box.value.strip() == message.text:
            input_box.value = ""

    @on(Input.Submitted, "#chat-input")
    def _chat_enter(self): self._send_chat()

    @on(Input.Changed, "#chat-input")
    def _chat_changed(self, event: Input.Changed):
        self.query_one("#chat-count", Static).update(f"{len(event.value.strip().encode('utf-8'))} / 150 bytes")

    @on(Button.Pressed, ".cell")
    def _cell_pressed(self, event: Button.Pressed):
        if not self.engine or not self.selected_id: return
        cell = int(event.button.id.split("-")[1])
        self._run_action(partial(self.engine.legal_action, self.selected_id, bytes((cell,))))

    @on(Button.Pressed, "#nim-take")
    def _nim_take(self):
        if not self.engine or not self.selected_id: return
        try:
            pile = int(self.query_one("#nim-pile", Select).value)
            amount = int(self.query_one("#nim-amount", Input).value)
        except (ValueError, TypeError):
            self.notify("Enter a positive item count.", severity="error"); return
        if not 1 <= amount <= 255 or not 0 <= pile <= 255:
            self.notify("Enter an item count from 1 to 255.", severity="error"); return
        self._run_action(partial(self.engine.legal_action, self.selected_id, bytes((pile, amount))))

    def _scrabble_state(self):
        session = self.engine.session(self.selected_id)
        game = self.engine.registry.by_id(session["game_id"])
        state = game.decode_state(session["state"])
        game.validate_roster(state, tuple(session["players"]))
        return session, state

    @on(Button.Pressed, "#word-place")
    def _word_place(self):
        if not self.engine or not self.selected_id: return
        try:
            session, state = self._scrabble_state()
            action = make_placement(state, session["current_player"],
                self.query_one("#word-start", Input).value,
                self.query_one("#word-direction", Input).value,
                self.query_one("#word-text", Input).value)
            self._run_action(partial(self.engine.legal_action, self.selected_id, action))
        except Exception as exc:
            self.notify(str(exc), severity="error")

    @on(Button.Pressed, "#word-exchange")
    def _word_exchange(self):
        if not self.engine or not self.selected_id: return
        try:
            session, state = self._scrabble_state()
            tiles = self.query_one("#word-exchange-tiles", Input).value
            action = make_exchange(state, session["current_player"], tiles)
            self._run_action(partial(self.engine.legal_action, self.selected_id, action))
        except Exception as exc:
            self.notify(str(exc), severity="error")

    @on(Button.Pressed, "#word-pass")
    def _word_pass(self):
        if self.engine and self.selected_id:
            self._run_action(partial(self.engine.legal_action, self.selected_id, b"\x01"))

    @on(Button.Pressed, "#plugin-submit")
    def _plugin_submit(self):
        if not self.engine or not self.selected_id: return
        try: action = bytes.fromhex(self.query_one("#plugin-action", Input).value.strip())
        except ValueError:
            self.notify("Enter a valid hexadecimal action.", severity="error"); return
        self._run_action(partial(self.engine.legal_action, self.selected_id, action))

    def on_unmount(self):
        if self.unsubscribe: self.unsubscribe()
        if self.engine: self.engine.close()


def main():
    parser = argparse.ArgumentParser(description="Radio Arcade terminal client")
    parser.add_argument("--callsign")
    parser.add_argument("--host")
    parser.add_argument("--transport", choices=("broadcast", "connected", "practice"))
    parser.add_argument("--observer", action="store_true", help="passively watch KISS broadcast games")
    args = parser.parse_args()
    settings = Settings.load()
    if args.callsign: settings.callsign = args.callsign.upper()
    if args.host: settings.host = args.host
    if args.transport: settings.transport = args.transport
    if args.observer: settings.observer_mode = True
    ArcadeTUI(settings).run()


if __name__ == "__main__":
    main()
