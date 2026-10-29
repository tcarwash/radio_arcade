"""Standalone Qt desktop front end for the Radio Civ game plugin.

Only this module imports PySide6. The strategy rules and multiplayer engine
remain entirely headless and can be tested without a display.
"""

from __future__ import annotations

import argparse
import math
import sys

from PySide6.QtCore import QObject, QPointF, Qt, QSettings, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGraphicsEllipseItem, QGraphicsPolygonItem,
    QGraphicsScene, QGraphicsTextItem, QGraphicsView, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from radio_arcade.service import ArcadeService, station_name
from radio_arcade.transport_factory import group_name

from .engine import HotseatService, broadcast_service
from .rules import (BUILDINGS, BUILD_COSTS, HEIGHT, TECHS, TECH_COSTS, UNIT_COSTS,
                    UNITS, WIDTH, CivilizationGame, build, end_turn, found,
                    grow, move, neighbors, recruit, research)


PLAYER_COLORS = ("#64dfcc", "#f1c26e", "#9c8cf5", "#f07f9a")
TERRAIN_COLORS = {
    "water": ("#1b4765", "#13334e"), "plains": ("#506b52", "#344c39"),
    "forest": ("#2d725e", "#1c4e40"), "hills": ("#706a63", "#484b50"),
    "desert": ("#927851", "#6e583a"),
}
TERRAIN_MARKS = {"water": "≈", "plains": "·", "forest": "♣", "hills": "▲", "desert": "✦"}


class ConnectionWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, call: str, host: str, kiss: int, command: int, group: str, observer: bool):
        super().__init__()
        self.call, self.host, self.kiss, self.command = call, host, kiss, command
        self.group, self.observer = group, observer

    def run(self):
        try:
            self.ready.emit(broadcast_service(self.call, self.host, self.kiss,
                                              self.command, self.group, self.observer))
        except Exception as exc:
            self.failed.emit(str(exc))


class EventBridge(QObject):
    received = Signal(object)


class HexMap(QGraphicsView):
    clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board = QGraphicsScene(self)
        self.setScene(self.board)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("background: #0d1724; border: none")
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._view = None
        self._perspective = 0
        self._selected = None
        self._move_origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            while item is not None:
                tile = item.data(0)
                if tile is not None:
                    self.clicked.emit(int(tile))
                    return
                item = item.parentItem()
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        scale = self.transform().m11()
        if 0.55 <= scale * factor <= 3.5:
            self.scale(factor, factor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._view is not None and self.board.items():
            self.fitInView(self.board.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    @staticmethod
    def _text(parent, text: str, x: float, y: float, size: int, color: str, bold=False):
        item = QGraphicsTextItem(text, parent)
        font = QFont("DejaVu Sans", size)
        font.setBold(bold)
        item.setFont(font)
        item.setDefaultTextColor(QColor(color))
        bounds = item.boundingRect()
        item.setPos(x - bounds.width() / 2, y - bounds.height() / 2)
        return item

    def render(self, view: dict | None, perspective: int, selected: int | None,
               move_origin: int | None):
        self._view, self._perspective = view, perspective
        self._selected, self._move_origin = selected, move_origin
        self.board.clear()
        if not view:
            self._text(None, "◈  Awaiting a campaign", 320, 250, 19, "#8ca9bc", True)
            self.board.setSceneRect(0, 0, 640, 500)
            return
        radius, step_x, step_y = 40, math.sqrt(3) * 40, 60
        centers = [(65 + x * step_x + (y & 1) * step_x / 2,
                    65 + y * step_y) for y in range(HEIGHT) for x in range(WIDTH)]
        seen = (view["players"][perspective]["seen"] if perspective < len(view["players"])
                else (1 << (WIDTH * HEIGHT)) - 1)
        for tile, (cx, cy) in enumerate(centers):
            known = bool(seen & 1 << tile)
            biome = view["terrain"][tile]
            bright, dark = TERRAIN_COLORS[biome] if known else ("#172636", "#101d2b")
            outline = "#f3cf81" if selected == tile else (
                "#66dfcb" if move_origin is not None and tile in neighbors(move_origin) else "#375164")
            points = [QPointF(cx + radius * math.cos(math.radians(60 * step - 90)),
                              cy + radius * math.sin(math.radians(60 * step - 90)))
                      for step in range(6)]
            hexagon = QGraphicsPolygonItem(QPolygonF(points))
            hexagon.setData(0, tile)
            gradient = QLinearGradient(cx, cy - radius, cx, cy + radius)
            gradient.setColorAt(0, QColor(bright)); gradient.setColorAt(1, QColor(dark))
            hexagon.setBrush(QBrush(gradient))
            hexagon.setPen(QPen(QColor(outline), 3.0 if tile == selected else 1.3))
            hexagon.setToolTip(f"{chr(65 + tile % WIDTH)}{tile // WIDTH + 1} · "
                               + (biome.title() if known else "Unexplored"))
            self.board.addItem(hexagon)
            if known:
                self._text(hexagon, TERRAIN_MARKS[biome], cx, cy + 2, 16,
                           "#b5d2b3" if biome != "water" else "#7cc7df")
            else:
                self._text(hexagon, "?", cx, cy + 2, 13, "#516879")
        for city in view["cities"]:
            tile = city["tile"]
            if not seen & 1 << tile:
                continue
            cx, cy = centers[tile]
            color = PLAYER_COLORS[city["owner"]]
            marker = QGraphicsEllipseItem(cx - 20, cy - 22, 40, 40)
            marker.setBrush(QColor("#152331"))
            marker.setPen(QPen(QColor(color), 3.3))
            marker.setData(0, tile)
            marker.setToolTip(f"{view['players'][city['owner']]['call']} capital/city · population {city['pop']}")
            self.board.addItem(marker)
            self._text(marker, "⌂", cx, cy - 3, 23, color, True)
            self._text(marker, str(city["pop"]), cx + 18, cy + 14, 10, "#ffffff", True)
        for unit in view["units"]:
            tile = unit["tile"]
            if not seen & 1 << tile:
                continue
            cx, cy = centers[tile]
            color = PLAYER_COLORS[unit["owner"]]
            marker = QGraphicsEllipseItem(cx + 10, cy - 28, 27, 27)
            marker.setBrush(QColor(color)); marker.setPen(QPen(QColor("#e1f8ed"), 1.2))
            marker.setData(0, tile)
            marker.setToolTip(f"{view['players'][unit['owner']]['call']} {UNITS[unit['kind']]} · HP {unit['hp']}")
            self.board.addItem(marker)
            self._text(marker, "SWT"[unit["kind"]], cx + 23.5, cy - 15, 10, "#0b1823", True)
        self.board.setSceneRect(self.board.itemsBoundingRect().adjusted(-35, -35, 35, 35))
        self.fitInView(self.board.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class CivWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.prefs = QSettings("AG7SU", "Radio Civ")
        self.engine: ArcadeService | None = None
        self.unsubscribe = None
        self.worker = None
        self.session_id = None
        self.selected_tile = None
        self.move_origin = None
        self.current_session = None
        self._bridge = EventBridge(self)
        self._bridge.received.connect(self._engine_event, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle("Radio Civ · A Civilization Over the Air")
        self.resize(1560, 920)
        self.setMinimumSize(1030, 690)
        self._build()
        self._skin()
        self._status("OFFLINE", "Connect locally or to a Data2G broadcast group")

    def _build(self):
        root = QWidget(self); root.setObjectName("root"); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(20, 16, 20, 15); outer.setSpacing(12)
        masthead = QHBoxLayout()
        name = QLabel("◈  RADIO CIV"); name.setObjectName("brand"); masthead.addWidget(name)
        tagline = QLabel("A CIVILIZATION OVER THE AIR  /  POWERED BY RADIO ARCADE")
        tagline.setObjectName("muted"); masthead.addWidget(tagline)
        masthead.addStretch()
        self.link = QLabel("● OFFLINE"); self.link.setObjectName("status"); masthead.addWidget(self.link)
        outer.addLayout(masthead)
        layout = QSplitter(Qt.Orientation.Horizontal); layout.setChildrenCollapsible(False)
        outer.addWidget(layout, 1)

        left_scroller = QScrollArea(); left_scroller.setWidgetResizable(True); left_scroller.setFrameShape(QFrame.Shape.NoFrame)
        left_host = QWidget(); left_scroller.setWidget(left_host)
        left = QVBoxLayout(left_host); left.setContentsMargins(0, 0, 3, 0); left.setSpacing(10)
        left.addWidget(self._section("01  RADIO LINK"))
        self.mode = QComboBox(); self.mode.addItem("Local hotseat · two players", "practice")
        self.mode.addItem("Data2G · broadcast", "radio")
        self.mode.addItem("Broadcast observer · RX only", "observer")
        self.mode.setCurrentIndex(max(0, int(self.prefs.value("mode_index", 0))))
        left.addWidget(self.mode)
        self.call = QLineEdit(str(self.prefs.value("callsign", "AG7SU"))); self.call.setPlaceholderText("N0CALL")
        self.host = QLineEdit(str(self.prefs.value("host", "127.0.0.1")))
        self.group = QLineEdit(str(self.prefs.value("group", "RAGAMES")))
        self.kiss = QSpinBox(); self.kiss.setRange(1, 65535); self.kiss.setValue(int(self.prefs.value("kiss", 8100)))
        self.command = QSpinBox(); self.command.setRange(1, 65535); self.command.setValue(int(self.prefs.value("command", 8300)))
        for label, widget in (("STATION", self.call), ("DATA2G HOST", self.host),
                              ("BROADCAST GROUP", self.group), ("KISS PORT", self.kiss),
                              ("COMMAND PORT", self.command)):
            left.addWidget(self._field(label, widget))
        self.connect_button = self._button("CONNECT / OPEN STATION", self._connect, primary=True)
        left.addWidget(self.connect_button)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()
        left.addWidget(self._section("02  CAMPAIGNS"))
        self.games = QListWidget(); self.games.setMinimumHeight(130)
        self.games.currentItemChanged.connect(self._select_campaign)
        left.addWidget(self.games)
        self.new_button = self._button("＋  NEW CAMPAIGN", self._new_campaign, primary=True)
        left.addWidget(self.new_button)
        self.join_button = self._button("JOIN HOSTED LOBBY", lambda: self._invoke("join_game"))
        self.start_button = self._button("START CAMPAIGN", lambda: self._invoke("start_game"))
        left.addWidget(self.join_button); left.addWidget(self.start_button)
        self.order = QLineEdit(); self.order.setPlaceholderText("HOST,GUEST1,GUEST2")
        left.addWidget(self._field("DEALER REPLY ORDER", self.order))
        left.addWidget(self._button("SET TURN ORDER", self._set_order))
        left.addWidget(self._button("RE-ANNOUNCE LOBBY", lambda: self._invoke("announce_game")))
        left.addWidget(self._button("REQUEST SNAPSHOT", lambda: self._invoke("request_sync")))
        left.addWidget(self._button("RETRY MY ORDER", lambda: self._invoke("resend_pending_action")))
        left.addStretch()
        layout.addWidget(left_scroller)

        center = QFrame(); center.setObjectName("mapPanel")
        middle = QVBoxLayout(center); middle.setContentsMargins(14, 14, 14, 10)
        map_top = QHBoxLayout()
        self.campaign_title = QLabel("The uncharted world"); self.campaign_title.setObjectName("heading")
        map_top.addWidget(self.campaign_title); map_top.addStretch()
        self.round_label = QLabel("ROUND —"); self.round_label.setObjectName("caps")
        map_top.addWidget(self.round_label); middle.addLayout(map_top)
        self.map = HexMap(); self.map.clicked.connect(self._click_tile); middle.addWidget(self.map, 1)
        legend = QLabel("CLICK A HEX TO INSPECT  •  SELECT A UNIT, THEN MOVE / ATTACK TO TARGET A HEX  •  SCROLL TO ZOOM")
        legend.setObjectName("mapFooter"); legend.setWordWrap(True)
        middle.addWidget(legend)
        layout.addWidget(center)

        right_scroller = QScrollArea(); right_scroller.setWidgetResizable(True); right_scroller.setFrameShape(QFrame.Shape.NoFrame)
        right_host = QWidget(); right_scroller.setWidget(right_host)
        right = QVBoxLayout(right_host); right.setContentsMargins(3, 0, 0, 0); right.setSpacing(10)
        right.addWidget(self._section("03  WAR ROOM"))
        self.turn = QLabel("NO CAMPAIGN SELECTED"); self.turn.setObjectName("turnPill")
        right.addWidget(self.turn)
        self.resources = QLabel("Awaiting a civilization"); self.resources.setObjectName("resources")
        self.resources.setWordWrap(True); right.addWidget(self.resources)
        right.addWidget(self._section("STANDINGS"))
        self.scoreboard = QLabel("—"); self.scoreboard.setTextFormat(Qt.TextFormat.RichText)
        self.scoreboard.setWordWrap(True); self.scoreboard.setObjectName("scoreboard")
        right.addWidget(self.scoreboard)
        right.addWidget(self._section("SELECTED HEX"))
        self.inspector = QLabel("Select a tile on the map"); self.inspector.setWordWrap(True)
        self.inspector.setObjectName("inspector"); right.addWidget(self.inspector)
        self.move_button = self._button("↗  MOVE / ATTACK", self._prepare_move, primary=True)
        self.found_button = self._button("⌂  FOUND A CITY", self._found_city)
        right.addWidget(self.move_button); right.addWidget(self.found_button)
        self.city_actions = QWidget(); actions = QGridLayout(self.city_actions)
        actions.setContentsMargins(0, 0, 0, 0); actions.setSpacing(6)
        self.recruit_buttons = []
        for i, name in enumerate(UNITS):
            button = self._button(f"＋ {name}", lambda _=False, kind=i: self._recruit(kind))
            button.setToolTip(f"Cost: food {UNIT_COSTS[i][0]}, timber {UNIT_COSTS[i][1]}, ore {UNIT_COSTS[i][2]}")
            actions.addWidget(button, i, 0); self.recruit_buttons.append(button)
        grow_button = self._button("↑ Grow city", self._grow)
        actions.addWidget(grow_button, 3, 0)
        self.build_buttons = []
        for i, name in enumerate(BUILDINGS):
            button = self._button(name, lambda _=False, kind=i: self._build_city(kind))
            button.setToolTip(f"Cost: food {BUILD_COSTS[i][0]}, timber {BUILD_COSTS[i][1]}, ore {BUILD_COSTS[i][2]}")
            actions.addWidget(button, i, 1); self.build_buttons.append(button)
        right.addWidget(self.city_actions)
        right.addWidget(self._section("TECHNOLOGY"))
        self.tech_buttons = []
        tech_grid = QGridLayout(); tech_grid.setSpacing(6)
        for i, name in enumerate(TECHS):
            button = self._button(name, lambda _=False, tech=i: self._research(tech))
            button.setToolTip(f"Research cost: {TECH_COSTS[i]} science")
            tech_grid.addWidget(button, i // 2, i % 2); self.tech_buttons.append(button)
        right.addLayout(tech_grid)
        self.end_button = self._button("END TURN  →", lambda: self._order(end_turn()), primary=True)
        right.addWidget(self.end_button)
        right.addWidget(self._section("DISPATCH LOG"))
        self.log = QListWidget(); self.log.setMinimumHeight(130); right.addWidget(self.log)
        chat_row = QHBoxLayout(); self.chat = QLineEdit(); self.chat.setPlaceholderText("Public side chat")
        self.chat.returnPressed.connect(self._send_chat)
        chat_row.addWidget(self.chat, 1); chat_row.addWidget(self._button("SEND", self._send_chat))
        right.addLayout(chat_row)
        right.addStretch()
        layout.addWidget(right_scroller)
        layout.setSizes([285, 885, 355])
        footer = QHBoxLayout()
        self.bottom = QLabel("Game actions transmit as 1–3 bytes. Everything on the radio is public.")
        self.bottom.setObjectName("muted"); footer.addWidget(self.bottom)
        outer.addLayout(footer)
        self._render()

    @staticmethod
    def _section(text: str):
        label = QLabel(text); label.setObjectName("caps")
        return label

    @staticmethod
    def _field(text: str, widget):
        box = QWidget(); stack = QVBoxLayout(box); stack.setContentsMargins(0, 0, 0, 0); stack.setSpacing(3)
        label = QLabel(text); label.setObjectName("mini")
        stack.addWidget(label); stack.addWidget(widget)
        return box

    @staticmethod
    def _button(text: str, callback, primary=False):
        button = QPushButton(text)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(callback)
        return button

    def _skin(self):
        self.setStyleSheet("""
            QWidget#root, QMainWindow { background:#0a121e; color:#e6ecf2; font:13px 'DejaVu Sans','Segoe UI'; }
            QScrollArea { background:transparent; border:0; }
            QScrollArea > QWidget > QWidget { background:transparent; }
            QLabel { color:#d6e3e9; }
            QLabel#brand { color:#f4f8fb; font-size:24px; font-weight:800; letter-spacing:2px; }
            QLabel#muted, QLabel#mapFooter { color:#7f96a9; font-size:10px; }
            QLabel#status { color:#73dece; font-size:11px; font-weight:bold; }
            QLabel#caps { color:#62d9c8; font-size:11px; font-weight:800; letter-spacing:1px; padding-top:9px; }
            QLabel#mini { color:#8199ad; font-size:9px; font-weight:bold; }
            QLabel#heading { color:#f1f6fa; font-size:19px; font-weight:750; }
            QLabel#turnPill { color:#f3d490; background:#26313a; border:1px solid #766647;
                              border-radius:9px; padding:10px; font-weight:bold; }
            QLabel#resources, QLabel#scoreboard, QLabel#inspector { background:#111f2d;
                 border:1px solid #2a3d4c; border-radius:10px; padding:10px; }
            QFrame#mapPanel { background:#0d1724; border:1px solid #283b4b; border-radius:15px; }
            QLineEdit, QSpinBox, QComboBox, QListWidget { color:#e4ebf4; background:#152536;
                 border:1px solid #31495a; border-radius:7px; padding:7px; selection-background-color:#227c78; }
            QListWidget { padding:4px; }
            QListWidget::item { padding:7px 5px; border-radius:5px; }
            QListWidget::item:selected { background:#285b62; }
            QPushButton { background:#1b3042; color:#dcebf1; border:1px solid #375366;
                          border-radius:8px; padding:8px 10px; font-weight:600; }
            QPushButton:hover { background:#2a4b5e; border-color:#70b9b3; }
            QPushButton:pressed { background:#153f43; }
            QPushButton:disabled { background:#13212d; color:#637d8c; border-color:#293b48; }
            QPushButton#primary { background:#217a73; border:1px solid #4da99c; color:#f5ffff; }
            QPushButton#primary:hover { background:#2b948b; }
            QPushButton#primary:disabled { background:#182d37; border-color:#314657; color:#6a8790; }
            QSplitter::handle { background:#0a121e; }
            QScrollBar:vertical { background:#13212d; width:8px; }
            QScrollBar::handle:vertical { background:#385567; border-radius:3px; }
        """)

    def _status(self, status: str, explanation: str):
        self.link.setText(f"● {status}")
        self.bottom.setText(explanation)

    def _mode_changed(self, *_):
        radio = self.mode.currentData() != "practice"
        for field in (self.host, self.group, self.kiss, self.command):
            field.setEnabled(radio and self.engine is None)

    def _connect(self):
        if self.engine is not None:
            self._disconnect()
        try:
            call = station_name(self.call.text())
            group = group_name(self.group.text())
            if not self.host.text().strip():
                raise ValueError("enter a Data2G host")
        except ValueError as exc:
            self._error(exc)
            return
        self.prefs.setValue("mode_index", self.mode.currentIndex())
        self.prefs.setValue("callsign", call); self.prefs.setValue("host", self.host.text().strip())
        self.prefs.setValue("group", group); self.prefs.setValue("kiss", self.kiss.value())
        self.prefs.setValue("command", self.command.value())
        self.connect_button.setEnabled(False)
        self._status("CONNECTING", "Opening the campaign station…")
        if self.mode.currentData() == "practice":
            try:
                self._connected(HotseatService(call))
            except Exception as exc:
                self._connection_failed(str(exc))
        else:
            self.worker = ConnectionWorker(call, self.host.text().strip(), self.kiss.value(),
                                           self.command.value(), group,
                                           self.mode.currentData() == "observer")
            self.worker.ready.connect(self._connected)
            self.worker.failed.connect(self._connection_failed)
            self.worker.start()

    def _connected(self, engine):
        self.engine = engine
        self.unsubscribe = engine.subscribe(lambda event: self._bridge.received.emit(event))
        self.connect_button.setEnabled(True); self.connect_button.setText("DISCONNECT / CHANGE STATION")
        self.mode.setEnabled(False); self.call.setEnabled(False)
        self._mode_changed()
        status = "OBSERVER / RX" if engine.observer_mode else "LOCAL HOTSEAT" if isinstance(engine, HotseatService) else "ON THE AIR"
        self._status(status, "Data2G game state is public. Use a manual snapshot if broadcast packets were lost.")
        self._refresh_campaigns()

    def _connection_failed(self, message):
        self.connect_button.setEnabled(True)
        self._status("OFFLINE", "Unable to open a station")
        self._error(message)

    def _disconnect(self):
        if self.unsubscribe:
            self.unsubscribe(); self.unsubscribe = None
        if self.engine:
            self.engine.close(); self.engine = None
        self.session_id = None; self.current_session = None; self.selected_tile = None
        self.move_origin = None
        self.connect_button.setText("CONNECT / OPEN STATION")
        self.mode.setEnabled(True); self.call.setEnabled(True)
        self._mode_changed(); self._refresh_campaigns()
        self._status("OFFLINE", "Station disconnected")

    def _engine_event(self, event):
        if event.name == "transport_status":
            status = event.data.get("status", "offline")
            if status == "offline":
                self._status("LINK LOST", "Radio link disconnected; reconnect to resume")
        if event.name in ("session_updated", "invitation", "transport_send_error", "chat_message"):
            if event.name == "transport_send_error":
                self._log("Radio send failure: " + str(event.data.get("error", "")))
            elif event.name == "chat_message":
                msg = event.data.get("message", {})
                self._log(f"{msg.get('sender', '?')}: {msg.get('body', '')}")
            elif event.name == "session_updated" and event.data.get("committed"):
                last = event.data.get("session", {}).get("last_move") or {}
                self._log(f"{last.get('actor', '?')} · {last.get('description', 'order')}")
            self._refresh_campaigns()

    def _log(self, message: str):
        self.log.addItem(message)
        self.log.scrollToBottom()
        while self.log.count() > 80:
            self.log.takeItem(0)

    def _error(self, error):
        self._log(str(error))
        QMessageBox.warning(self, "Radio Civ", str(error))

    def _refresh_campaigns(self):
        selected = self.session_id
        self.games.blockSignals(True)
        self.games.clear()
        if self.engine:
            for session in self.engine.sessions():
                if session["game_id"] != 5:
                    continue
                label = f"{session['coordinator']}  ·  {session['status'].upper()}  ·  {len(session['players'])} nations"
                item = QListWidgetItem(label); item.setData(Qt.ItemDataRole.UserRole, session["id"])
                self.games.addItem(item)
                if session["id"] == selected:
                    self.games.setCurrentItem(item)
            if not self.games.currentItem() and self.games.count():
                self.games.setCurrentRow(0)
        self.games.blockSignals(False)
        self.session_id = (self.games.currentItem().data(Qt.ItemDataRole.UserRole)
                           if self.games.currentItem() else None)
        self._render()

    def _select_campaign(self, current, _previous):
        self.session_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.selected_tile = None; self.move_origin = None
        self._render()

    def _new_campaign(self):
        if not self.engine:
            return
        try:
            local = isinstance(self.engine, HotseatService)
            sid = self.engine.create_game("radio-civ", hosted=not local)
            self.session_id = sid
            self._refresh_campaigns()
            self._log("Campaign created. " + ("Hotseat started." if local else "Waiting for hosted join requests."))
        except Exception as exc:
            self._error(exc)

    def _invoke(self, method: str):
        if not self.engine or not self.session_id:
            return
        try:
            getattr(self.engine, method)(self.session_id)
            self._refresh_campaigns()
        except Exception as exc:
            self._error(exc)

    def _set_order(self):
        if not self.engine or not self.session_id:
            return
        try:
            self.engine.set_reply_order(self.session_id, self.order.text())
            self._refresh_campaigns()
        except Exception as exc:
            self._error(exc)

    def _order(self, action: bytes):
        if not self.engine or not self.session_id:
            return
        try:
            self.engine.legal_action(self.session_id, action)
            self.move_origin = None
            self._refresh_campaigns()
        except Exception as exc:
            self._error(exc)

    def _perspective(self, view):
        if not self.engine:
            return 0
        names = [player["call"] for player in view["players"]]
        if isinstance(self.engine, HotseatService) and view["turn"] in names:
            return names.index(view["turn"])
        return names.index(self.engine.callsign) if self.engine.callsign in names else len(names)

    def _can_act(self, view):
        if not view or view["finished"] or view["ap"] == 0 or not self.engine or self.engine.observer_mode:
            return False
        return isinstance(self.engine, HotseatService) or view["turn"] == self.engine.callsign

    def _click_tile(self, tile):
        view = (self.current_session or {}).get("view")
        if self.move_origin is not None and view and tile in neighbors(self.move_origin):
            origin = self.move_origin
            units = view["units"]
            unit_index = next((i for i, unit in enumerate(units) if unit["tile"] == origin
                               and unit["owner"] == self._perspective(view)), None)
            if unit_index is not None and self._can_act(view):
                self._order(move(unit_index, tile))
                self.selected_tile = tile
                self._render()
                return
        self.move_origin = None
        self.selected_tile = tile
        self._render()

    def _selected_city_unit(self):
        view = (self.current_session or {}).get("view")
        if not view or self.selected_tile is None:
            return None, None, None, None
        faction = self._perspective(view)
        city_index = next((i for i, city in enumerate(view["cities"])
                           if city["tile"] == self.selected_tile and city["owner"] == faction), None)
        unit_index = next((i for i, unit in enumerate(view["units"])
                           if unit["tile"] == self.selected_tile and unit["owner"] == faction), None)
        return view, faction, city_index, unit_index

    def _prepare_move(self):
        view, _, _, unit = self._selected_city_unit()
        if view is not None and unit is not None and self._can_act(view):
            self.move_origin = self.selected_tile
            self._render()

    def _found_city(self):
        _, _, _, unit = self._selected_city_unit()
        if unit is not None:
            self._order(found(unit))

    def _recruit(self, kind):
        _, _, city, _ = self._selected_city_unit()
        if city is not None:
            self._order(recruit(city, kind))

    def _grow(self):
        _, _, city, _ = self._selected_city_unit()
        if city is not None:
            self._order(grow(city))

    def _build_city(self, kind):
        _, _, city, _ = self._selected_city_unit()
        if city is not None:
            self._order(build(city, kind))

    def _research(self, tech):
        self._order(research(tech))

    def _send_chat(self):
        if not self.engine or not self.session_id or not self.chat.text().strip():
            return
        try:
            self.engine.send_chat(self.session_id, self.chat.text())
            self.chat.clear()
        except Exception as exc:
            self._error(exc)

    def _render(self):
        self.current_session = None
        if self.engine and self.session_id:
            try:
                self.current_session = self.engine.session(self.session_id)
            except KeyError:
                pass
        info = self.current_session
        view = info.get("view") if info else None
        observer = bool(self.engine and self.engine.observer_mode)
        local = isinstance(self.engine, HotseatService)
        self.new_button.setEnabled(bool(self.engine and not observer))
        self.join_button.setEnabled(bool(info and info["status"] in ("lobby", "joining")
                                         and info["coordinator"] != self.engine.callsign and not observer))
        self.start_button.setEnabled(bool(info and info["status"] == "lobby" and
                                          info["coordinator"] == self.engine.callsign and not local))
        if not view:
            self.map.render(None, 0, None, None)
            self.campaign_title.setText("The uncharted world")
            self.round_label.setText("ROUND —")
            self.turn.setText("WAITING FOR THE CAMPAIGN TO START" if info else "NO CAMPAIGN SELECTED")
            self.resources.setText("Host a campaign, or join a broadcast lobby.\n2–4 nations can play.")
            self.scoreboard.setText("—")
            self.inspector.setText("Select a tile after the game begins")
            for button in (self.move_button, self.found_button, self.end_button,
                           *self.recruit_buttons, *self.build_buttons, *self.tech_buttons):
                button.setEnabled(False)
            return
        perspective = self._perspective(view)
        self.map.render(view, perspective, self.selected_tile, self.move_origin)
        self.campaign_title.setText(f"Campaign  ·  {info['id'][-6:]}")
        self.round_label.setText(f"ROUND {view['round']} / {view['max_rounds']}    •    {view['ap']} ORDERS LEFT")
        if view["finished"]:
            self.turn.setText(f"★  VICTORY  ·  {view['winner']}")
        else:
            self.turn.setText(f"◈  {view['turn']}'S TURN" + ("  ·  HOTSEAT" if local else ""))
        player = view["players"][perspective] if perspective < len(view["players"]) else None
        if player:
            self.resources.setText(f"FOOD  {player['food']}    WOOD  {player['wood']}    ORE  {player['ore']}\n"
                                   f"SCIENCE  {player['science']}    TECHS  {player['tech'].bit_count()}/5")
        else:
            self.resources.setText("Observer · all radio information is public")
        standings = []
        for i, nation in enumerate(view["players"]):
            color = PLAYER_COLORS[i]
            active = " ◂" if nation["call"] == view["turn"] else ""
            standings.append(f"<div style='border-bottom:1px solid #334555;padding:6px 0'>"
                             f"<span style='color:{color};font-weight:800'>● {nation['call']}</span>"
                             f"<span style='color:#f0d292'>   {nation['score']} VP{active}</span>"
                             f"<br/><span style='color:#8da7b6'>"
                             f"{sum(c['owner']==i for c in view['cities'])} cities  ·  "
                             f"{sum(u['owner']==i for u in view['units'])} units  ·  "
                             f"{nation['tech'].bit_count()} techs</span></div>")
        self.scoreboard.setText("".join(standings))
        title = "Select a tile on the map"
        if self.selected_tile is not None:
            tile = self.selected_tile
            known = not player or bool(player["seen"] & (1 << tile))
            title = f"{chr(65 + tile % WIDTH)}{tile // WIDTH + 1}  ·  "
            title += view["terrain"][tile].title() if known else "Unexplored"
            if known:
                city = next((c for c in view["cities"] if c["tile"] == tile), None)
                unit = next((u for u in view["units"] if u["tile"] == tile), None)
                if city:
                    title += (f"\n⌂ {view['players'][city['owner']]['call']} CITY"
                              f"  ·  POP {city['pop']}  ·  DEF {city['hp']}")
                if unit:
                    title += f"\n◈ {view['players'][unit['owner']]['call']} {UNITS[unit['kind']]}  ·  HP {unit['hp']}"
        if self.move_origin is not None:
            title += "\nChoose an adjacent highlighted hex to move / attack."
        self.inspector.setText(title)
        current, _, city_index, unit_index = self._selected_city_unit()
        can_act = self._can_act(view)
        self.move_button.setEnabled(can_act and unit_index is not None)
        self.found_button.setEnabled(can_act and unit_index is not None and
                                     view["units"][unit_index]["kind"] == 2)
        self.end_button.setEnabled(not observer and not view["finished"] and
                                   (local or view["turn"] == self.engine.callsign))
        for button in (*self.recruit_buttons, *self.build_buttons):
            button.setEnabled(can_act and city_index is not None)
        for i, button in enumerate(self.tech_buttons):
            is_known = bool(player and player["tech"] & 1 << i)
            button.setText(("✓ " if is_known else "") + TECHS[i] + f" · {TECH_COSTS[i]}")
            button.setEnabled(can_act and not is_known)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.wait(5000)
        if self.engine:
            self._disconnect()
        super().closeEvent(event)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Radio Civ — graphical 4X over Radio Arcade / Data2G")
    parser.add_argument("--callsign", help="pre-fill the callsign in station setup")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    window = CivWindow()
    if args.callsign:
        window.call.setText(station_name(args.callsign))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
