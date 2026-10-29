"""Polished keyboard-first terminal client built on the headless engine."""

from __future__ import annotations

import argparse
import asyncio
from functools import partial
import re
import threading

from textual import events, on
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


# Four-character terminal cells: the background fills the entire square,
# leaving premium labels subdued while played and previewed letters pop.
WORD_PREMIUM_STYLES = {
    "TW": "bold #ffd9de on #813748",
    "DW": "#ffdfe7 on #703e59",
    "TL": "bold #d4f3ff on #205d83",
    "DL": "#d9f2ff on #31546c",
    "..": "dim #6f8495 on #1a2937",
}


def word_preview(view: dict, start: str, direction: str, word: str) -> tuple[dict[int, str], str]:
    """Preview glyphs only; the game still validates the actual submitted move."""
    match = re.fullmatch(r"([A-Oa-o])(1[0-5]|[1-9])", start.strip())
    if not match:
        return {}, "Enter a starting square from A1 to O15"
    x0, y0 = ord(match[1].upper()) - 65, int(match[2]) - 1
    if word and not re.fullmatch(r"(?:[A-Za-z]|\{[A-Za-z]\})*", word):
        return {}, "Use letters or {A} for a blank tile"
    letters = [blank or plain for blank, plain in re.findall(r"\{([A-Za-z])\}|([A-Za-z])", word.upper())]
    result = {}
    conflict = False
    overflow = False
    for n, letter in enumerate(letters):
        x, y = (x0 + n, y0) if direction == "H" else (x0, y0 + n)
        if x >= 15 or y >= 15:
            overflow = True
            break
        index = y * 15 + x
        existing = view["cells"][index]
        if existing and existing.upper() != letter:
            conflict = True
        result[index] = letter
    if overflow:
        return result, "Word extends beyond the board"
    if conflict:
        return result, "Word conflicts with existing tiles"
    return result, "Preview only · press Place to submit" if letters else "Select a tile and type a word"


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
    .word-board { color:#e4edf5; text-style:bold; width:64; height:16; margin:0 0 1 0; }
    .word-board:hover { background:#182b39; }
    .word-meta { color:#7ce0c9; height:auto; margin:0 0 1 0; }
    #word-start { width:10; }
    .word-direction-button { width:15; min-width:15; }
    .word-direction-button.-selected { background:#347969; color:#ffffff; text-style:bold; }
    #word-text { width:1fr; }
    #word-exchange-tiles { width:1fr; }
    #empty-state { color:#8799ad; content-align:center middle; height:1fr; }

    /* SUperChat-inspired midnight/violet palette, with a game-first arena. */
    Screen { background:#080d16; color:#e7edf7; }
    Header, Footer { background:#111827; color:#8795aa; }
    #shell { padding:1; }
    #topline { height:2; padding:0 1; margin-bottom:0; background:#111827; }
    #wordmark { width:auto; color:#ded2ff; text-style:bold; padding:1 1 0 0; }
    #brand-note { width:1fr; color:#74839c; padding:1 0 0 1; }
    #link-status { background:#10251f; border:round #245543; color:#a2f0cc; }
    #pane-tabs { display:none; height:1; margin:0 1 1 1; color:#ac9bdf; text-style:bold; }
    .panel { background:#0e1521; border:round #25334a; padding:1 2; }
    #left { width:28; margin-right:1; }
    #right { width:31; margin-left:1; margin-right:0; }
    #center { width:1fr; background:#0b111b; border:round #35405c; margin-right:0; }
    .eyebrow { color:#7889a3; text-style:bold; margin:0 0 1 0; }
    #sidebar-title, #rail-title { height:2; color:#e7dcff; text-style:bold; }
    #session-list { border:none; background:#101827; margin-bottom:1; scrollbar-color:#35435d; }
    ListItem { padding:1; color:#b6c5d7; }
    ListItem.--highlight { background:#282540; color:#eee5ff; text-style:bold; }
    Input, Select { background:#121c2c; color:#eff3ff; border:round #394865; }
    Input:focus, Select:focus { border:round #9c88eb; }
    Button { background:#1a283b; border:round #384b68; color:#d9e3f4; }
    Button:hover { background:#2b3e54; }
    Button.-primary { background:#7764b7; color:#ffffff; text-style:bold; border:round #a28cdb; }
    #new-game { width:100%; }
    #session-actions { margin-top:1; border-top:solid #26334a; padding-top:1; }
    #session-actions Button { width:100%; }
    #arena-header { height:2; border-bottom:solid #28344a; margin-bottom:0; }
    #game-title { width:1fr; color:#f2ecff; text-style:bold; padding:0 1 0 0; }
    #arena-meta { width:auto; color:#7d8ba4; text-align:right; padding-top:1; }
    #turn-line { height:1; min-height:1; padding:0 1; margin:0 0 1 0;
                 background:#14251f; border-left:solid #5cb596; color:#b8f4d7; text-style:bold; }
    #scoreboard { display:none; height:4; margin:0 0 1 0; background:#141d2d;
                  border:round #35415a; padding:0 1; }
    .score-card { width:1fr; height:100%; padding:0 1; }
    #score-left-card { border-right:solid #3c4963; }
    #score-right-card { border-left:solid #3c4963; }
    .score-name { height:1; color:#a99bd2; text-style:bold; }
    .score-value { height:2; color:#f5efff; text-style:bold; content-align:left middle; }
    #score-divider { width:7; height:100%; color:#9482c6; text-style:bold;
                     content-align:center middle; }
    #game-controls { display:none; height:auto; background:#131f2c;
                     padding:0 1; margin-bottom:1; }
    #game-controls Horizontal { height:auto; }
    #game-controls Input { margin-bottom:0; }
    #game-controls Button { margin-bottom:0; }
    #board { height:1fr; min-height:10; align:center top; scrollbar-color:#34415a; }
    #arena-footer { height:1; color:#74839a; text-align:center; }
    #players { color:#c1cde0; background:#141e2d; border:round #26374f;
               padding:1; height:auto; min-height:4; }
    #last-move { min-height:2; height:auto; margin-top:1; padding:0 1;
                 color:#a8b4c9; background:#131e2b; border-left:solid #7663af; }
    .section-label { margin-top:2; }
    #chat-log { border:none; background:#0b111b; padding:1 0; scrollbar-color:#34415a; }
    #chat-input { margin-top:1; }
    #send-chat { width:100%; }
    .word-board { margin:1 0; }
    .word-meta { color:#a5d7c6; }
    #word-preview-hint { color:#b9a7f6; text-style:italic; }
    .word-direction-button.-selected { background:#594b8a; color:#ffffff; border:round #a18adc; }
    Screen.compact #left, Screen.compact #center, Screen.compact #right { display:none; width:1fr; margin:0; }
    Screen.compact.show-lobby #left, Screen.compact.show-board #center, Screen.compact.show-chat #right { display:block; }
    Screen.compact #pane-tabs { display:block; }
    Screen.compact #brand-note { display:none; }
    Screen.compact #topline { margin-bottom:0; }
    Screen.compact #shell { padding:0; }
    Screen.compact #columns { min-height:0; }
    Screen.daylight #center { background:#f8fafc; border:round #bcc7d8; }
    Screen.daylight #scoreboard { background:#e1e8f1; border:round #bfcbda; }
    Screen.daylight #scoreboard .score-value { color:#243249; }
    Screen.daylight #scoreboard .score-name { color:#65558b; }
    Screen.daylight #score-left-card { border-right:solid #b3bfd1; }
    Screen.daylight #score-right-card { border-left:solid #b3bfd1; }
    Screen.daylight #turn-line { color:#155344; background:#e0f3ed; }
    Screen.daylight #arena-meta { color:#6d7c91; }
    Screen.monochrome #scoreboard { background:#171717; border:round #777777; }
    Screen.monochrome #turn-line { background:#222222; color:#ffffff; border-left:solid #aaaaaa; }
    Screen.monochrome .score-name, Screen.monochrome .score-value { color:#ffffff; }
    Screen.monochrome #score-divider { color:#ffffff; }
    Screen.short #topline { height:2; }
    Screen.short #wordmark, Screen.short #brand-note { padding-top:0; }
    Screen.short #arena-header { height:2; margin-bottom:0; }
    Screen.short #turn-line { min-height:1; padding:0 1; margin-bottom:0; }
    Screen.short #scoreboard { height:4; margin-bottom:0; }
    Screen.short .score-value { height:2; }
    Screen.short #game-controls { padding:0 1; margin-bottom:0; }
    Screen.short #arena-footer { display:none; }
    Screen.short #board { min-height:5; }
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
        self._word_direction = "H"
        self._word_cursor_visible = True
        self._word_board_view = None
        self._word_score_hint_key = None
        self._word_score_hint = ""
        self._action_lock = threading.Lock()
        # Textual list mutations yield; never interleave two rebuilds.
        self._session_refresh_lock = asyncio.Lock()
        self._board_refresh_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            with Horizontal(id="topline"):
                yield Label("◈  RADIO / ARCADE", id="wordmark")
                yield Static("DATA2G  ·  ON-AIR GAME ROOM", id="brand-note")
                yield Static("●  OFFLINE", id="link-status")
            yield Static("1  TABLES     2  GAME BOARD     3  CHAT     S  SETTINGS", id="pane-tabs")
            with Horizontal(id="columns"):
                with VerticalScroll(id="left", classes="panel"):
                    yield Label("01  /  GAME LOBBY", classes="eyebrow")
                    yield Static("Your tables", id="sidebar-title")
                    yield ListView(id="session-list")
                    yield Label("NEW MATCH", classes="eyebrow")
                    yield Select((("Tic-tac-toe", "tic-tac-toe"), ("Nim · 2–4 players", "nim")),
                                 value="tic-tac-toe", allow_blank=False, id="game-select")
                    yield Select((("Direct invitation", "direct"), ("Hosted broadcast table", "hosted")),
                                 value="direct", allow_blank=False, id="table-mode")
                    yield Input(placeholder="Opponent callsign", id="peer-input")
                    yield Button("＋  Create table", id="new-game", variant="primary")
                    yield Button("Connect / resume peer", id="connect-peer")
                    yield Button("Disconnect radio link", id="disconnect-peer")
                    with Vertical(id="session-actions"):
                        yield Button("Accept invitation", id="accept-invite")
                        yield Button("Join hosted table", id="join-game")
                        yield Button("Re-announce hosted lobby", id="announce-game")
                        yield Input(placeholder="Reply order: HOST,GUEST1,GUEST2", id="reply-order")
                        yield Button("Set dealer reply order", id="set-reply-order")
                        yield Button("Invite station", id="invite-player")
                        yield Button("Cancel pending invitation", id="cancel-invite")
                        yield Button("Start lobby", id="start-game")
                        yield Button("Sync / recover state", id="sync-state")
                        yield Button("Retry pending move", id="retry-move")
                with Vertical(id="center", classes="panel"):
                    yield Label("02  /  GAME ARENA", classes="eyebrow")
                    with Horizontal(id="arena-header"):
                        yield Label("Choose a table", id="game-title")
                        yield Static("", id="arena-meta")
                    yield Static("Open-information games for the airwaves.", id="turn-line")
                    with Horizontal(id="scoreboard"):
                        with Vertical(id="score-left-card", classes="score-card"):
                            yield Static("STATION 1", id="score-left-name", classes="score-name")
                            yield Static("0", id="score-left-value", classes="score-value")
                        yield Static("VS", id="score-divider")
                        with Vertical(id="score-right-card", classes="score-card"):
                            yield Static("STATION 2", id="score-right-name", classes="score-name")
                            yield Static("0", id="score-right-value", classes="score-value")
                    yield Vertical(id="game-controls")
                    with VerticalScroll(id="board"):
                        yield Static("Start a table to take a seat.", id="empty-state")
                    yield Static("◌  PUBLIC RADIO  /  MOVES TRANSMIT ONLY WHEN SUBMITTED", id="arena-footer")
                with Vertical(id="right", classes="panel"):
                    yield Label("03  /  STATIONS", classes="eyebrow")
                    yield Static("At the table", id="rail-title")
                    yield Static("No table selected", id="players")
                    yield Static("NO RECENT MOVE", id="last-move")
                    yield Label("TABLE CHAT", classes="eyebrow section-label")
                    yield RichLog(id="chat-log", markup=True, wrap=True, highlight=True)
                    yield Input(placeholder="Message · up to 150 UTF-8 bytes", id="chat-input")
                    yield Static("0 / 150 bytes", id="chat-count", classes="muted")
                    yield Button("Send message  ↗", id="send-chat", variant="primary")
        yield Footer()

    async def on_mount(self):
        self.set_interval(0.5, self._blink_word_cursor)
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
        # The word board is 64 columns wide. At narrower sizes the arena takes
        # the entire terminal instead of squeezing between two side rails.
        self.screen.set_class(width < 155, "compact")
        self.screen.set_class(self.size.height < 36, "short")
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
        for name in ("#game-select", "#table-mode", "#peer-input", "#new-game", "#send-chat", "#chat-input"):
            self.query_one(name).disabled = self.settings.observer_mode
        self.query_one("#table-mode", Select).disabled = self.settings.observer_mode or self.settings.transport != "broadcast"
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
            turn = ("● YOUR TURN" if session.get("current_player") == self.settings.callsign else
                    "◉ SPECTATING" if self.settings.observer_mode else session["status"].upper())
            description = Text(session["game"], style="bold #e7dcff")
            description.append(f"\n{peer}  ·  {turn}", style="#96adba")
            await listing.append(ListItem(Label(description),
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
        async with self._board_refresh_lock:
            await self._show_session_locked(sid)

    async def _show_session_locked(self, sid: str | None):
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
        self.query_one("#game-title", Label).update(session["game"])
        state = session["view"]
        arena_meta = f"GAME {sid[-6:]}  ·  REV {session['revision']}"
        if state and state.get("kind") == "word-tiles":
            arena_meta = f"BAG {state['bag']}  ·  {arena_meta}"
        self.query_one("#arena-meta", Static).update(arena_meta)
        invited = "\nInvited: " + ", ".join(session["invited"]) if session["invited"] else ""
        self.query_one("#players", Static).update("  ·  ".join(session["players"]) + invited + f"\n\n{session['status'].upper()}  ·  rev {session['revision']}")
        turn = session["current_player"]
        self._update_scoreboard(state, turn)
        if self.settings.observer_mode: line = "OBSERVING · RX ONLY" if state else "OBSERVING · AWAITING SNAPSHOT"
        elif session["status"] == "invited": line = f"INVITATION FROM {session['coordinator']} · accept to join"
        elif session["status"] == "lobby":
            line = ("DEALER · ACCEPTING JOIN REQUESTS" if session.get("hosted") and session["coordinator"] == self.settings.callsign
                    else "HOSTED TABLE · JOIN TO PLAY" if session.get("hosted") and self.settings.callsign not in session["players"]
                    else "HOSTED TABLE · WAIT FOR DEALER" if session.get("hosted")
                    else "LOBBY · invite players, then start")
        elif session["status"] == "joining": line = "WAITING FOR SESSION START"
        elif session["status"] == "cancelled": line = "INVITATION CANCELLED"
        elif session["pending_action"]: line = "MOVE SENT · WAITING FOR COMMIT"
        elif state and state.get("winner"): line = f"GAME WON BY {state['winner']}"
        elif session["status"] == "finished": line = "GAME OVER · DRAW"
        else: line = f"LOCAL PRACTICE · {turn}'s turn" if self.settings.transport == "practice" else f"YOUR TURN · {turn}" if turn == self.settings.callsign else f"WAITING FOR {turn}"
        if session.get("error"): line = f"MOVE REJECTED · {session['error']}"
        if session.get("last_move"):
            last = session["last_move"]
            self.query_one("#last-move", Static).update(f"LAST MOVE  ·  {last['actor']}\n{last['description']}")
        else:
            self.query_one("#last-move", Static).update("NO RECENT MOVE")
        self.query_one("#turn-line", Static).update(line)
        self.query_one("#accept-invite", Button).display = not self.settings.observer_mode and session["status"] == "invited"
        self.query_one("#join-game", Button).display = not self.settings.observer_mode and session.get("hosted", False) and session["status"] in ("lobby", "joining") and self.settings.callsign not in session["players"]
        self.query_one("#announce-game", Button).display = not self.settings.observer_mode and session.get("hosted", False) and session["status"] == "lobby" and session["coordinator"] == self.settings.callsign
        self.query_one("#reply-order", Input).display = self.query_one("#announce-game", Button).display
        self.query_one("#set-reply-order", Button).display = self.query_one("#announce-game", Button).display
        self.query_one("#invite-player", Button).display = not self.settings.observer_mode and not session.get("hosted") and session["status"] == "lobby" and session["coordinator"] == self.settings.callsign
        self.query_one("#cancel-invite", Button).display = not self.settings.observer_mode and not session.get("hosted") and session["status"] == "lobby" and bool(session["invited"])
        self.query_one("#start-game", Button).display = not self.settings.observer_mode and session["status"] == "lobby" and session["coordinator"] == self.settings.callsign
        self.query_one("#sync-state", Button).display = self.settings.observer_mode or (session["coordinator"] != self.settings.callsign and session["status"] in ("active", "finished"))
        self.query_one("#sync-state", Button).label = "Request snapshot (TRANSMITS)" if self.settings.observer_mode else "Sync / recover state"
        self.query_one("#retry-move", Button).display = not self.settings.observer_mode and session.get("hosted", False) and session["pending_action"] is not None
        board = self.query_one("#board", VerticalScroll)
        controls = self.query_one("#game-controls", Vertical)
        await controls.remove_children()
        controls.display = bool(state and state.get("kind") == "word-tiles")
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
                self._word_board_view = state
                await board.mount(Static("", id="word-board", classes="word-board", markup=False))
                await board.mount(Static(f"OPEN RACKS   ·   {state['lexicon']} DICTIONARY", classes="word-meta"))
                for player, rack in state["racks"].items():
                    await board.mount(Static(f"{player} RACK  " + "  ".join(rack), classes="word-meta"))
                enabled = not self.settings.observer_mode and (self.settings.transport == "practice" or turn == self.settings.callsign) and session["status"] == "active" and not session["pending_action"]
                await controls.mount(Horizontal(Input("H8", placeholder="Start", id="word-start"),
                    Button("— Horizontal", id="word-horizontal", classes="word-direction-button"),
                    Button("│ Vertical", id="word-vertical", classes="word-direction-button"),
                    Input(placeholder="Word · {A} uses a blank as A", id="word-text"),
                    Button("Place", id="word-place", variant="primary", disabled=not enabled)))
                self._update_word_direction_buttons()
                await controls.mount(Static("", id="word-preview-hint", classes="word-meta"))
                self._refresh_word_preview()
                await controls.mount(Horizontal(Input(placeholder="Exchange letters · ? for blank", id="word-exchange-tiles"),
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

    def _update_scoreboard(self, state: dict | None, turn: str | None):
        panel = self.query_one("#scoreboard", Horizontal)
        panel.display = bool(state and state.get("kind") == "word-tiles")
        if not panel.display:
            return
        scores = list(state["scores"].items())
        for side, pair in zip(("left", "right"), scores):
            name, score = pair
            suffix = "  ● TURN" if name == turn else ""
            self.query_one(f"#score-{side}-name", Static).update(name + suffix)
            self.query_one(f"#score-{side}-value", Static).update(str(score))

    def _refresh_chat(self, sid: str | None):
        if not self.engine or not sid or sid != self.selected_id or not self.query("#chat-log"): return
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
        hosted = self.query_one("#table-mode", Select).value == "hosted"
        self._run_action(partial(self.engine.create_game, str(game), None if hosted else peer, hosted=hosted), select_session=True)

    @on(Button.Pressed, "#join-game")
    def _join_game(self):
        if self.engine and self.selected_id: self._run_action(partial(self.engine.join_game, self.selected_id))

    @on(Button.Pressed, "#announce-game")
    def _announce_game(self):
        if self.engine and self.selected_id: self._run_action(partial(self.engine.announce_game, self.selected_id))

    @on(Button.Pressed, "#set-reply-order")
    def _set_reply_order(self):
        if self.engine and self.selected_id:
            order = self.query_one("#reply-order", Input).value
            self._run_action(partial(self.engine.set_reply_order, self.selected_id, order))

    @on(Button.Pressed, "#retry-move")
    def _retry_move(self):
        if self.engine and self.selected_id: self._run_action(partial(self.engine.resend_pending_action, self.selected_id))

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

    @on(events.Click, "#word-board")
    def _word_board_clicked(self, event: events.Click):
        # One header line and four columns of row labels precede the tiles.
        x, y = (event.x - 4) // 4, event.y - 1
        if 0 <= x < 15 and 0 <= y < 15:
            self.query_one("#word-start", Input).value = f"{chr(65 + x)}{y + 1}"
            self._word_cursor_visible = True
            self._refresh_word_preview()
            self.query_one("#word-text", Input).focus()

    def _blink_word_cursor(self):
        if self.query("#word-board"):
            self._word_cursor_visible = not self._word_cursor_visible
            self._refresh_word_preview()

    def _refresh_word_preview(self):
        if not self._word_board_view or not self.query("#word-board") or not self.query("#word-start"):
            return
        start = self.query_one("#word-start", Input).value
        word = self.query_one("#word-text", Input).value
        preview, status = word_preview(self._word_board_view, start, self._word_direction, word)
        if word and status.startswith("Preview only") and self.engine and self.selected_id:
            session = self.engine.session(self.selected_id)
            key = (self.selected_id, session["revision"], start, self._word_direction, word)
            if key != self._word_score_hint_key:
                self._word_score_hint_key = key
                try:
                    actor = session["current_player"]
                    if not actor:
                        raise ValueError("game is finished")
                    game = self.engine.registry.by_id(session["game_id"])
                    state = game.decode_state(session["state"])
                    action = make_placement(state, actor, start, self._word_direction, word)
                    next_state = game.apply_action(state, tuple(session["players"]), actor, action)
                    player_index = session["players"].index(actor)
                    points = next_state["scores"][player_index] - state["scores"][player_index]
                    self._word_score_hint = f"✓  VALID PLAY   ·   +{points} POINTS   ·   Place to transmit"
                except ValueError as exc:
                    self._word_score_hint = f"PREVIEW   ·   {exc}"
            status = self._word_score_hint
        match = re.fullmatch(r"([A-Oa-o])(1[0-5]|[1-9])", start.strip())
        selected = (int(match[2]) - 1) * 15 + ord(match[1].upper()) - 65 if match else None
        rows = [Text("    " + "".join(f"{chr(65 + x):^4}" for x in range(15)), style="bold cyan")]
        for y in range(15):
            line = Text(f"{y+1:>3} ", style="bold cyan")
            for x in range(15):
                index = y * 15 + x
                existing = self._word_board_view["cells"][index]
                premium = self._word_board_view["premiums"][index]
                glyph = preview.get(index) or existing or ("★" if index == 112 else premium)
                if self.settings.color_scheme == "monochrome":
                    style = "bold white on #363636" if premium != ".." else "dim #aaaaaa on #181818"
                else:
                    style = WORD_PREMIUM_STYLES.get(premium, WORD_PREMIUM_STYLES[".."])
                if index == 112 and not existing:
                    style = "bold #ffe7a3 on #645125" if self.settings.color_scheme != "monochrome" else "bold reverse"
                if existing:
                    style = "bold #f9f4dc on #354d48" if self.settings.color_scheme != "monochrome" else "bold white on #444444"
                if index in preview:
                    style = ("bold white on #176d54" if not existing or existing.upper() == preview[index]
                             else "bold white on #a73333") if self.settings.color_scheme != "monochrome" else (
                                 "bold reverse" if not existing or existing.upper() == preview[index]
                                 else "bold black on white")
                if index == selected and self._word_cursor_visible:
                    style = "bold reverse underline"
                    if not word:
                        glyph = "▌"
                line.append(f"{glyph:^4}", style=style)
            rows.append(line)
        self.query_one("#word-board", Static).update(Text("\n").join(rows))
        if self.query("#word-preview-hint"):
            self.query_one("#word-preview-hint", Static).update(status)

    @on(Input.Changed, "#word-start")
    @on(Input.Changed, "#word-text")
    def _word_input_changed(self, _event: Input.Changed):
        self._word_cursor_visible = True
        self._refresh_word_preview()

    def _update_word_direction_buttons(self):
        for direction, name in (("H", "#word-horizontal"), ("V", "#word-vertical")):
            self.query_one(name, Button).set_class(self._word_direction == direction, "-selected")

    @on(Button.Pressed, "#word-horizontal")
    def _word_horizontal(self):
        self._word_direction = "H"
        self._update_word_direction_buttons()
        self._refresh_word_preview()

    @on(Button.Pressed, "#word-vertical")
    def _word_vertical(self):
        self._word_direction = "V"
        self._update_word_direction_buttons()
        self._refresh_word_preview()

    @on(Button.Pressed, "#word-place")
    def _word_place(self):
        if not self.engine or not self.selected_id: return
        try:
            session, state = self._scrabble_state()
            action = make_placement(state, session["current_player"],
                self.query_one("#word-start", Input).value,
                self._word_direction,
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
