"""Native desktop client for the headless Radio Arcade engine."""

from __future__ import annotations

import argparse
from functools import partial
import html
import sys
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThread, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from .service import ArcadeService, EngineEvent, station_name
from .settings import Settings
from .store import SessionStore
from .transport_factory import create_service, group_name
from .games.scrabble import make_exchange, make_placement
from .dx_cluster import build_spot, post_spot


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Station settings · Radio Arcade")
        self.setMinimumWidth(430)
        self.settings = settings
        outer = QVBoxLayout(self)
        title = QLabel("Connect your station")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)
        subtitle = QLabel("Choose how Radio Arcade reaches your Data2G host.")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)
        form = QFormLayout()
        self.callsign = QLineEdit(settings.callsign)
        self.callsign.setPlaceholderText("N0CALL")
        self.host = QLineEdit(settings.host)
        self.transport = QComboBox()
        self.transport.addItem("Broadcast · KISS", "broadcast")
        self.transport.addItem("Connected · VARA-like", "connected")
        self.transport.addItem("Local practice · play both sides", "practice")
        self.transport.setCurrentIndex(max(0, self.transport.findData(settings.transport)))
        self.group = QLineEdit(settings.group)
        self.broadcast_mode = QLineEdit(settings.broadcast_mode)
        self.broadcast_mode.setPlaceholderText("Blank = Data2G default · e.g. n10-qpsk-r1/5")
        self.command_port = QSpinBox(); self.command_port.setRange(1, 65535); self.command_port.setValue(settings.command_port)
        self.kiss_port = QSpinBox(); self.kiss_port.setRange(1, 65535); self.kiss_port.setValue(settings.kiss_port)
        self.scheme = QComboBox(); self.scheme.addItem("Midnight", "midnight"); self.scheme.addItem("Daylight", "daylight")
        self.scheme.addItem("Monochrome", "monochrome")
        self.scheme.setCurrentIndex(max(0, self.scheme.findData(settings.color_scheme)))
        self.sound = QCheckBox("Play invitation and turn sounds"); self.sound.setChecked(settings.sound_enabled)
        self.observer = QCheckBox("Observer mode · receive-only (manual sync transmits)")
        self.observer.setChecked(settings.observer_mode)
        self.dx_cluster_url = QLineEdit(settings.dx_cluster_url)
        self.dx_cluster_url.setPlaceholderText("https://cluster.example.org")
        form.addRow("Callsign", self.callsign)
        form.addRow("Data2G host", self.host)
        form.addRow("Transport", self.transport)
        form.addRow("Broadcast group", self.group)
        form.addRow("Broadcast modem mode", self.broadcast_mode)
        form.addRow("Command port", self.command_port)
        form.addRow("KISS port", self.kiss_port)
        form.addRow("Color scheme", self.scheme)
        form.addRow("Sound", self.sound)
        form.addRow("Observer", self.observer)
        form.addRow("DX Cluster URL", self.dx_cluster_url)
        outer.addLayout(form)
        buttons = QHBoxLayout(); buttons.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        save = QPushButton("Save settings"); save.setObjectName("primaryButton"); save.clicked.connect(self._save)
        buttons.addWidget(cancel); buttons.addWidget(save); outer.addLayout(buttons)

    def _save(self):
        try:
            call = station_name(self.callsign.text())
            group = group_name(self.group.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Check settings", str(exc))
            return
        self.settings.callsign = call
        self.settings.host = self.host.text().strip() or "127.0.0.1"
        self.settings.transport = self.transport.currentData()
        if self.observer.isChecked() and self.settings.transport != "broadcast":
            QMessageBox.warning(self, "Observer mode", "Select Broadcast · KISS for observer mode.")
            return
        self.settings.observer_mode = self.observer.isChecked()
        self.settings.group = group
        self.settings.broadcast_mode = self.broadcast_mode.text().strip().lower()
        self.settings.command_port = self.command_port.value()
        self.settings.kiss_port = self.kiss_port.value()
        self.settings.color_scheme = self.scheme.currentData()
        self.settings.sound_enabled = self.sound.isChecked()
        self.settings.dx_cluster_url = self.dx_cluster_url.text().strip()
        self.settings.save()
        self.accept()


class ServiceWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent); self.settings = settings
    def run(self):
        try: self.ready.emit(create_service(self.settings))
        except Exception as exc: self.failed.emit(str(exc))


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn: Callable):
        super().__init__(); self.fn = fn; self.signals = TaskSignals()
    @Slot()
    def run(self):
        try: self.signals.done.emit(self.fn())
        except Exception as exc: self.signals.failed.emit(str(exc))


class EventBridge(QObject):
    received = Signal(object)


class ArcadeWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.engine: ArcadeService | None = None
        self.unsubscribe = None
        self.selected_id: str | None = None
        self._board_signature = None
        self._event_bridge = EventBridge(self)
        self._event_bridge.received.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._pool = QThreadPool.globalInstance()
        self._tasks: set[Task] = set()
        self.setWindowTitle("Radio Arcade")
        self.resize(1320, 820)
        self.setMinimumSize(1024, 680)
        self._build_ui()
        self._apply_style()
        self._update_status("Starting up", False)

    def _build_ui(self):
        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root); layout.setContentsMargins(22, 20, 22, 18); layout.setSpacing(16)
        top = QHBoxLayout()
        brand = QLabel("◈  RADIO ARCADE"); brand.setObjectName("brand")
        top.addWidget(brand)
        self.transport_label = QLabel("DATA2G · RADIO GAMES"); self.transport_label.setObjectName("eyebrow")
        top.addWidget(self.transport_label); top.addStretch(1)
        self.link_dot = QLabel("●"); self.link_dot.setObjectName("statusDot")
        self.status_label = QLabel("Starting up"); self.status_label.setObjectName("statusText")
        top.addWidget(self.link_dot); top.addWidget(self.status_label)
        self.tx_activity = QLabel(""); self.tx_activity.setObjectName("muted")
        top.addWidget(self.tx_activity)
        self.tx_toggle = QPushButton("TX ENABLED" if self.settings.tx_enabled else "TX DISABLED")
        self.tx_toggle.setCheckable(True); self.tx_toggle.setChecked(self.settings.tx_enabled)
        self.tx_toggle.setToolTip("Allow or block outgoing Data2G traffic")
        self.tx_toggle.toggled.connect(self._set_tx_enabled)
        self.tx_toggle.setEnabled(self.settings.transport != "practice")
        top.addWidget(self.tx_toggle)
        settings_button = QPushButton("⚙  Settings"); settings_button.clicked.connect(self._edit_settings)
        top.addWidget(settings_button)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal); splitter.setChildrenCollapsible(False)
        left = self._card("GAMES", "Your tables")
        left_layout = left.layout()
        self.session_list = QListWidget(); self.session_list.setObjectName("sessionList")
        self.session_list.currentItemChanged.connect(self._select_item)
        left_layout.addWidget(self.session_list, 1)
        left_layout.addWidget(QLabel("START A GAME"), 0)
        self.game_choice = QComboBox(); self.game_choice.setObjectName("field")
        self.game_choice.addItem("Tic-tac-toe", "tic-tac-toe")
        self.game_choice.addItem("Nim · 2–4 players", "nim")
        left_layout.addWidget(self.game_choice)
        self.hosted_checkbox = QCheckBox("Host / dealer · public broadcast table")
        self.hosted_checkbox.setEnabled(self.settings.transport == "broadcast" and not self.settings.observer_mode)
        left_layout.addWidget(self.hosted_checkbox)
        self.peer_input = QLineEdit(); self.peer_input.setPlaceholderText("Opponent callsign")
        left_layout.addWidget(self.peer_input)
        self.connect_button = QPushButton("Connect / resume peer"); self.connect_button.clicked.connect(self._connect_peer)
        self.disconnect_button = QPushButton("Disconnect radio link"); self.disconnect_button.clicked.connect(self._disconnect_peer)
        self.connect_button.setVisible(self.settings.transport == "connected")
        self.disconnect_button.setVisible(self.settings.transport == "connected")
        left_layout.addWidget(self.connect_button); left_layout.addWidget(self.disconnect_button)
        self.new_button = QPushButton("＋  New game"); self.new_button.setObjectName("primaryButton")
        self.new_button.clicked.connect(self._new_game); left_layout.addWidget(self.new_button)
        self.invite_button = QPushButton("Invite to selected lobby"); self.invite_button.clicked.connect(self._invite_player)
        left_layout.addWidget(self.invite_button)
        self.join_button = QPushButton("Join broadcast table"); self.join_button.clicked.connect(self._join_game)
        left_layout.addWidget(self.join_button)
        self.announce_button = QPushButton("Re-announce lobby"); self.announce_button.clicked.connect(self._announce_game)
        left_layout.addWidget(self.announce_button)
        self.spot_button = QPushButton("✦  Spot my game · DX Cluster"); self.spot_button.clicked.connect(self._spot_game)
        left_layout.addWidget(self.spot_button)
        self.order_input = QLineEdit(); self.order_input.setPlaceholderText("Turn order: HOST,GUEST1,GUEST2")
        left_layout.addWidget(self.order_input)
        self.order_button = QPushButton("Set dealer reply order"); self.order_button.clicked.connect(self._set_reply_order)
        left_layout.addWidget(self.order_button)
        self.retry_button = QPushButton("Retry pending move"); self.retry_button.clicked.connect(self._retry_move)
        left_layout.addWidget(self.retry_button)
        self.cancel_invite_button = QPushButton("Cancel pending invitation")
        self.cancel_invite_button.clicked.connect(self._cancel_invitation)
        left_layout.addWidget(self.cancel_invite_button)
        self.start_button = QPushButton("Start selected game"); self.start_button.clicked.connect(self._start_game)
        left_layout.addWidget(self.start_button)
        self.delete_button = QPushButton("Delete selected game…")
        self.delete_button.clicked.connect(self._delete_game)
        self.delete_button.setEnabled(False)
        left_layout.addWidget(self.delete_button)
        splitter.addWidget(left)

        center = self._card("ACTIVE TABLE", "Game workspace")
        center_layout = center.layout()
        self.game_heading = QLabel("Welcome to the airwaves"); self.game_heading.setObjectName("gameTitle")
        self.game_subtitle = QLabel("Start a game or accept an invitation to take a seat."); self.game_subtitle.setObjectName("muted")
        center_layout.addWidget(self.game_heading); center_layout.addWidget(self.game_subtitle)
        self.sync_button = QPushButton("↻  Sync / recover state"); self.sync_button.clicked.connect(self._sync_selected)
        self.sync_button.setVisible(False); center_layout.addWidget(self.sync_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.board_host = QWidget(); self.board_layout = QVBoxLayout(self.board_host); self.board_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.board_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center_layout.addWidget(self.board_host, 1)
        self.accept_button = QPushButton("Accept invitation"); self.accept_button.setObjectName("primaryButton")
        self.accept_button.clicked.connect(self._accept_invite); center_layout.addWidget(self.accept_button, 0, Qt.AlignmentFlag.AlignCenter)
        splitter.addWidget(center)

        right = self._card("STATION PANEL", "Players & side chat")
        right_layout = right.layout()
        self.turn_badge = QLabel("NO GAME SELECTED"); self.turn_badge.setObjectName("turnBadge")
        right_layout.addWidget(self.turn_badge)
        self.players_label = QLabel("Stations appear here when a lobby opens."); self.players_label.setObjectName("players")
        self.players_label.setWordWrap(True); right_layout.addWidget(self.players_label)
        right_layout.addWidget(QLabel("SIDE CHAT"))
        self.chat_list = QListWidget(); self.chat_list.setObjectName("chatList"); right_layout.addWidget(self.chat_list, 1)
        chat_row = QHBoxLayout(); self.chat_input = QLineEdit(); self.chat_input.setPlaceholderText("Message · up to 150 UTF-8 bytes")
        self.chat_input.returnPressed.connect(self._send_chat)
        self.chat_send = QPushButton("Send"); self.chat_send.clicked.connect(self._send_chat)
        chat_row.addWidget(self.chat_input, 1); chat_row.addWidget(self.chat_send); right_layout.addLayout(chat_row)
        self.chat_count = QLabel("0 / 150 bytes"); self.chat_count.setObjectName("muted")
        self.chat_input.textChanged.connect(lambda text: self.chat_count.setText(f"{len(text.strip().encode('utf-8'))} / 150 bytes"))
        right_layout.addWidget(self.chat_count)
        splitter.addWidget(right)
        splitter.setSizes([275, 690, 315])
        layout.addWidget(splitter, 1)
        foot = QHBoxLayout()
        self.footer_label = QLabel("Public radio · keep moves short, keep the good sport")
        self.footer_label.setObjectName("muted"); foot.addWidget(self.footer_label); foot.addStretch(1)
        self.mode_label = QLabel("Offline"); self.mode_label.setObjectName("modeLabel"); foot.addWidget(self.mode_label)
        layout.addLayout(foot)

    @staticmethod
    def _card(kicker: str, title: str) -> QFrame:
        frame = QFrame(); frame.setObjectName("card")
        box = QVBoxLayout(frame); box.setContentsMargins(18, 18, 18, 18); box.setSpacing(13)
        label = QLabel(kicker); label.setObjectName("eyebrow"); box.addWidget(label)
        heading = QLabel(title); heading.setObjectName("cardTitle"); box.addWidget(heading)
        return frame

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget#root { background:#0b1018; color:#e8edf6; font-family:'Inter','Segoe UI',sans-serif; font-size:14px; }
            QFrame#card { background:#121a26; border:1px solid #263244; border-radius:18px; }
            QLabel#brand { color:#f5f8ff; font-size:22px; font-weight:800; letter-spacing:2px; }
            QLabel#eyebrow { color:#66d9c2; font-size:10px; font-weight:800; letter-spacing:2px; }
            QLabel#cardTitle { color:#f1f4fa; font-size:17px; font-weight:650; }
            QLabel#gameTitle { color:#f1f4fa; font-size:27px; font-weight:750; }
            QLabel#muted { color:#8e9bb0; }
            QLabel#statusText { color:#bcc7d8; font-size:12px; }
            QLabel#statusDot { color:#607086; font-size:17px; }
            QLabel#turnBadge { background:#1b2b36; color:#7be3cf; border:1px solid #2a514d; border-radius:9px; padding:10px; font-weight:750; }
            QLabel#players { background:#0d141f; color:#bdc8d8; border-radius:10px; padding:12px; }
            QLabel { color:#d7dfeb; }
            QPushButton { background:#202c3c; color:#e7edf7; border:1px solid #34445a; border-radius:9px; padding:9px 12px; }
            QPushButton:hover { background:#2b3b50; border-color:#4b6b81; }
            QPushButton:disabled { color:#637086; background:#161f2c; border-color:#252f3e; }
            QPushButton#primaryButton { color:#071411; font-weight:800; background:#67dfc3; border:0; }
            QPushButton#primaryButton:hover { background:#89f0d8; }
            QLineEdit, QComboBox, QSpinBox { background:#0c131e; color:#eaf0f8; border:1px solid #2e3d50; border-radius:8px; padding:9px; selection-background-color:#318b80; }
            QComboBox QAbstractItemView { background:#111a27; color:#eaf0f8; selection-background-color:#256c65; }
            QListWidget { background:transparent; border:0; outline:0; }
            QListWidget::item { padding:12px 10px; margin:2px 0; border-radius:9px; color:#abb8ca; }
            QListWidget::item:selected { background:#1c3438; color:#a2f1df; }
            QListWidget::item:hover { background:#1a2533; }
            QScrollBar:vertical { background:transparent; width:8px; margin:2px; }
            QScrollBar::handle:vertical { background:#34445a; border-radius:4px; min-height:25px; }
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
            QDialog { background:#111a27; }
            QLabel#dialogTitle { font-size:23px; font-weight:750; color:#f1f4fa; }
            QLabel#chatLine { background:#182331; border-radius:8px; padding:7px; }
            QLabel#modeLabel { color:#8796aa; font-size:11px; }
        """)
        if self.settings.color_scheme == "daylight":
            self.setStyleSheet(self.styleSheet() + """
                QWidget#root { background:#eaf0f4; color:#17212d; }
                QFrame#card { background:#ffffff; border-color:#c9d4de; }
                QLabel#brand,QLabel#gameTitle,QLabel#cardTitle,QLabel { color:#1b2936; }
                QLabel#eyebrow { color:#087d70; }
                QLabel#muted,QLabel#statusText,QLabel#modeLabel { color:#5f7080; }
                QLabel#turnBadge { background:#e0f4ef; color:#08695f; border-color:#abd9cf; }
                QLabel#players,QListWidget#chatList { background:#f2f6f8; color:#27394a; }
                QPushButton { background:#f2f5f7; color:#223243; border-color:#c4d1dc; }
                QPushButton:hover { background:#e3edf1; }
                QLineEdit,QComboBox,QSpinBox { background:#ffffff; color:#1c2a38; border-color:#c3d0da; }
                QListWidget::item { color:#33485c; }
                QListWidget::item:selected { background:#d7eee9; color:#075f57; }
                QLabel#chatLine { background:#edf3f6; }
            """)
        elif self.settings.color_scheme == "monochrome":
            self.setStyleSheet(self.styleSheet() + """
                QWidget#root, QFrame#card { background:#111111; color:#eeeeee; border-color:#777777; }
                QLabel, QLabel#brand, QLabel#gameTitle, QLabel#cardTitle, QLabel#eyebrow,
                QLabel#turnBadge, QLabel#players { color:#eeeeee; background:#222222; border-color:#888888; }
                QPushButton, QPushButton#primaryButton { background:#333333; color:#ffffff; border:1px solid #aaaaaa; }
                QPushButton:disabled { background:#222222; color:#777777; }
                QLineEdit, QComboBox, QSpinBox { background:#111111; color:#ffffff; border-color:#888888; }
                QListWidget::item:selected { background:#555555; color:#ffffff; }
            """)

    def _update_status(self, text: str, connected: bool):
        self.status_label.setText(text)
        color = "#eeeeee" if self.settings.color_scheme == "monochrome" else "#61dfb8" if connected else "#e9ae65"
        self.link_dot.setStyleSheet(f"color: {color}; font-size:17px")

    def connect_service(self):
        if self.unsubscribe:
            self.unsubscribe(); self.unsubscribe = None
        if self.engine:
            self.engine.close(); self.engine = None
        self._update_status("Connecting…", False)
        self.service_worker = ServiceWorker(self.settings, self)
        self.service_worker.ready.connect(self._service_ready)
        self.service_worker.failed.connect(self._connection_failed)
        self.service_worker.start()

    @Slot(object)
    def _service_ready(self, engine: ArcadeService):
        self.engine = engine
        engine.set_tx_enabled(self.settings.tx_enabled if self.settings.transport != "practice" else True)
        self._board_signature = None
        self.unsubscribe = engine.subscribe(lambda event: self._event_bridge.received.emit(event))
        offline = hasattr(engine.transport, "closed") and engine.transport.closed.is_set()
        self._update_status("Offline · reconnect in settings" if offline else "Local practice" if self.settings.transport == "practice" else "Data2G ready", not offline)
        self.mode_label.setText(f"{self.settings.callsign}  ·  {'OBSERVER · RX' if self.settings.observer_mode else self.settings.transport.title()}")
        can_transmit = not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled)
        for control in (self.game_choice, self.hosted_checkbox, self.peer_input, self.new_button,
                        self.invite_button, self.cancel_invite_button, self.start_button, self.chat_input,
                        self.chat_send, self.join_button, self.announce_button, self.order_input,
                        self.order_button, self.retry_button, self.connect_button,
                        self.disconnect_button):
            control.setEnabled(can_transmit)
        self.spot_button.setEnabled(not self.settings.observer_mode)
        self.sync_button.setEnabled(self.settings.transport == "practice" or self.settings.tx_enabled)
        self.connect_button.setVisible(self.settings.transport == "connected")
        self.disconnect_button.setVisible(self.settings.transport == "connected")
        self._populate_games()
        self.tx_toggle.setEnabled(self.settings.transport != "practice")
        self._refresh_sessions()

    @Slot(bool)
    def _set_tx_enabled(self, enabled: bool):
        if self.settings.transport == "practice":
            enabled = True
        self.settings.tx_enabled = enabled
        self.settings.save()
        self.tx_toggle.setText("TX ENABLED" if enabled else "TX DISABLED")
        can_transmit = not self.settings.observer_mode and (self.settings.transport == "practice" or enabled)
        for control in (self.game_choice, self.hosted_checkbox, self.peer_input, self.new_button,
                        self.invite_button, self.cancel_invite_button, self.start_button, self.chat_input,
                        self.chat_send, self.join_button, self.announce_button, self.order_input,
                        self.order_button, self.retry_button, self.connect_button, self.disconnect_button):
            control.setEnabled(can_transmit)
        self.sync_button.setEnabled(self.settings.transport == "practice" or enabled)
        self.spot_button.setEnabled(not self.settings.observer_mode)
        if self.engine:
            self.engine.set_tx_enabled(enabled)
            self._show_session(self.selected_id)

    def _delete_game(self):
        if not self.engine or not self.selected_id:
            return
        answer = QMessageBox.question(self, "Delete game", "Delete this game's saved state, chat, and queued messages from this device? This does not notify other stations.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        sid = self.selected_id
        self._run(partial(self.engine.delete_game, sid), lambda _result: setattr(self, "selected_id", None))

    def _populate_games(self):
        if not self.engine: return
        current = self.game_choice.currentData()
        self.game_choice.clear()
        for game in self.engine.catalog():
            if "desktop" not in game.clients:
                continue
            self.game_choice.addItem(f"{game.name} · {game.min_players}–{game.max_players}", game.key)
        index = self.game_choice.findData(current)
        if index >= 0: self.game_choice.setCurrentIndex(index)

    @Slot(str)
    def _connection_failed(self, error: str):
        self._update_status("Offline", False)
        self.mode_label.setText(error)

    def _on_event(self, event: EngineEvent):
        if event.name == "transmitting":
            self.tx_activity.setText(f"TX · {event.data.get('kind')} → {event.data.get('destination')} · {event.data.get('reason')}")
            QTimer.singleShot(6000, lambda: self.tx_activity.setText(""))
        elif event.name == "tx_state":
            self.tx_toggle.blockSignals(True)
            self.tx_toggle.setChecked(bool(event.data.get("enabled")))
            self.tx_toggle.setText("TX ENABLED" if event.data.get("enabled") else "TX DISABLED")
            self.tx_toggle.blockSignals(False)
        if self.settings.sound_enabled and (event.name == "invitation" or
                (event.name == "session_updated" and event.data.get("committed") and
                 event.data["session"].get("current_player") == self.settings.callsign)):
            QApplication.beep()
        if event.name == "transport_status":
            status = event.data.get("status")
            self._update_status(f"Connected to {event.data.get('peer')}" if status == "connected" else "Offline · reconnect in settings" if status == "offline" else "Listening / idle", status == "connected")
        elif event.name == "chat_message":
            self._refresh_chat(event.session)
        elif event.name == "chat_delivery":
            self._refresh_chat(event.session)
        elif event.name == "opaque_state":
            self.footer_label.setText(f"Opaque game state synchronized from {event.data['sender']} · content hidden")
        elif event.name == "delivery" and event.data.get("state") == "unconfirmed":
            self.footer_label.setText("No acknowledgement yet · waiting for the other station")
        elif event.name == "protocol_error":
            self.footer_label.setText(f"Ignored malformed frame · {event.data.get('error','')}")
        elif event.name == "unsupported_game":
            self.footer_label.setText(f"Unsupported game ID {event.data['game_id']} from {event.data['sender']}")
        if event.name in ("session_updated", "invitation", "delivery", "session_deleted"):
            self._refresh_sessions()
            if event.session and event.session == self.selected_id:
                self._show_session(event.session)

    def _refresh_sessions(self):
        if not self.engine:
            return
        selected = self.selected_id
        self.session_list.blockSignals(True); self.session_list.clear()
        supported = {game.game_id for game in self.engine.catalog() if "desktop" in game.clients}
        for session in self.engine.sessions():
            if session["game_id"] not in supported:
                continue
            opponent = (" / ".join(session["players"]) or session["coordinator"]) if self.settings.observer_mode else next((p for p in session["players"] if p != self.settings.callsign), "Open lobby")
            turn = "your move" if session.get("current_player") == self.settings.callsign else session["status"]
            item = QListWidgetItem(f"{session['game']}\n{opponent}  ·  {turn}  ·  {session['id'][-6:]}")
            item.setData(Qt.ItemDataRole.UserRole, session["id"])
            self.session_list.addItem(item)
            if session["id"] == selected:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        self.delete_button.setEnabled(bool(self.engine and self.engine.sessions()))
        if self.session_list.currentItem():
            self.selected_id = self.session_list.currentItem().data(Qt.ItemDataRole.UserRole)
        elif self.session_list.count():
            self.session_list.blockSignals(True)
            self.session_list.setCurrentRow(0)
            self.session_list.blockSignals(False)
            self.selected_id = self.session_list.currentItem().data(Qt.ItemDataRole.UserRole)
        else:
            self.selected_id = None
        self._show_session(self.selected_id)

    def _select_item(self, current, _previous):
        self.selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._show_session(self.selected_id)

    def _show_session(self, sid: str | None):
        session = None
        if self.engine and sid:
            try: session = self.engine.session(sid)
            except KeyError: return
            signature = (sid, session["status"], session["revision"], session["sequence"],
                         session["pending_action"], tuple(session["players"]), tuple(session["invited"]),
                         repr(session["view"]), repr(session.get("last_move")), session.get("error"), self.settings.transport, self.settings.tx_enabled)
            if signature == self._board_signature:
                return
            self._board_signature = signature
        else:
            self._board_signature = None
        self._clear_board()
        self.delete_button.setEnabled(bool(session))
        self.accept_button.setVisible(False); self.start_button.setVisible(False); self.invite_button.setVisible(False); self.cancel_invite_button.setVisible(False)
        self.join_button.setVisible(False); self.announce_button.setVisible(False); self.retry_button.setVisible(False)
        self.spot_button.setVisible(False)
        self.order_input.setVisible(False); self.order_button.setVisible(False)
        self.chat_list.clear()
        if not self.engine or not sid:
            self.delete_button.setEnabled(False)
            self.game_heading.setText("Welcome to the airwaves")
            self.game_subtitle.setText("Start a game or accept an invitation to take a seat.")
            self.turn_badge.setText("NO GAME SELECTED"); self.players_label.setText("Stations appear here when a lobby opens.")
            return
        game = session["game"]; players = session["players"]
        can_transmit = not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled)
        for control in (self.new_button, self.peer_input, self.invite_button, self.cancel_invite_button,
                        self.start_button, self.join_button, self.announce_button, self.order_input,
                        self.order_button, self.retry_button, self.accept_button,
                        self.chat_input, self.chat_send, self.connect_button, self.disconnect_button):
            control.setEnabled(can_transmit)
        self.sync_button.setEnabled(self.settings.transport == "practice" or self.settings.tx_enabled)
        self.spot_button.setEnabled(not self.settings.observer_mode)
        self.delete_button.setEnabled(True)
        self.spot_button.setVisible(not self.settings.observer_mode and session.get("hosted", False)
                                    and session["status"] in ("lobby", "active")
                                    and session["coordinator"] == self.settings.callsign)
        self.game_heading.setText(game)
        last = session.get("last_move")
        suffix = f"  ·  last: {last['actor']} {last['description']}" if last else ""
        self.game_subtitle.setText(f"Game {sid}  ·  revision {session['revision']}  ·  {session['status'].title()}{suffix}")
        state = session["view"]
        current = session["current_player"]
        self.sync_button.setVisible(self.settings.observer_mode or (session["coordinator"] != self.settings.callsign and session["status"] in ("active", "finished")))
        self.retry_button.setVisible(not self.settings.observer_mode and session.get("hosted", False) and session["pending_action"] is not None)
        if self.settings.observer_mode:
            self.sync_button.setText("↻  Request snapshot (TRANSMITS)")
        if self.settings.observer_mode:
            self.turn_badge.setText("OBSERVING · RX ONLY" if state else "OBSERVING · AWAITING SNAPSHOT")
        elif session["status"] == "invited":
            self.turn_badge.setText("INVITATION RECEIVED")
            self.accept_button.setVisible(True)
        elif session["status"] == "lobby":
            if session.get("hosted"):
                owner = session["coordinator"] == self.settings.callsign
                self.turn_badge.setText("DEALER · WAITING FOR JOIN REQUESTS" if owner else "HOSTED TABLE · JOIN TO PLAY")
                self.announce_button.setVisible(owner)
                self.order_input.setVisible(owner)
                self.order_button.setVisible(owner)
                self.join_button.setVisible(not owner and self.settings.callsign not in players and not self.settings.observer_mode)
                self.start_button.setVisible(owner)
            else:
                self.turn_badge.setText("LOBBY · add players, then start")
                self.invite_button.setVisible(session["coordinator"] == self.settings.callsign)
                self.cancel_invite_button.setVisible(bool(session["invited"]))
                self.start_button.setVisible(session["coordinator"] == self.settings.callsign)
        elif session["status"] == "joining":
            self.turn_badge.setText("JOIN REQUEST SENT · WAIT FOR DEALER" if session.get("hosted") else "WAITING FOR THE HOST TO START")
            self.join_button.setVisible(bool(session.get("hosted")))
        elif session["status"] == "cancelled":
            self.turn_badge.setText("INVITATION CANCELLED")
        elif session["status"] == "finished":
            winner = state.get("winner") if state else None
            self.turn_badge.setText(f"{winner} WINS" if winner else "GAME OVER · DRAW")
        elif session["pending_action"]:
            self.turn_badge.setText("MOVE SENT · WAITING FOR COMMIT")
        else:
            self.turn_badge.setText(f"LOCAL PRACTICE · {current}'s turn" if self.settings.transport == "practice" else f"YOUR TURN · {players.index(current)+1} of {len(players)}" if current == self.settings.callsign
                                    else f"{current or 'Game'} is thinking")
        if session.get("error"):
            self.turn_badge.setText(f"MOVE REJECTED · {session['error']}")
        self.players_label.setText("\n".join([*( ("● " if p == current else "○ ") + p for p in players), *("◌ " + p + " · invited" for p in session["invited"])]) or "No stations yet")
        if state:
            self._render_state(session)
        self._refresh_chat(sid)

    def _clear_board(self):
        while self.board_layout.count():
            item = self.board_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _render_state(self, session: dict):
        view = session["view"]
        if view["kind"] == "grid":
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(10)
            my_turn = not self.settings.observer_mode and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and (self.settings.transport == "practice" or self.settings.tx_enabled)
            for cell, value in enumerate(view["cells"]):
                text = value if value.strip() else str(cell + 1)
                button = QPushButton(text); button.setObjectName(f"boardCell{cell}")
                button.setFixedSize(112, 106); button.setEnabled(my_turn and not value.strip())
                button.setStyleSheet("font-size:34px;font-weight:800;color:#eeeeee" if self.settings.color_scheme == "monochrome" else "font-size:34px;font-weight:800;color:#79e4cb" if value == "X" else
                                     "font-size:34px;font-weight:800;color:#f0b56b" if value == "O" else "font-size:23px;color:#a5b6c9")
                button.clicked.connect(lambda _checked=False, i=cell: self._submit_action(bytes((i,))))
                grid.addWidget(button, cell // 3, cell % 3)
            self.board_layout.addWidget(board)
        elif view["kind"] == "connect-four":
            host = QWidget(); box = QVBoxLayout(host)
            board = QWidget(); grid = QGridLayout(board); grid.setSpacing(4)
            grid.setContentsMargins(0, 0, 0, 0)
            enabled = not self.settings.observer_mode and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"] and (self.settings.transport == "practice" or self.settings.tx_enabled)
            for col in range(7):
                choose = QPushButton(f"▼ {col+1}"); choose.setFixedSize(54, 40)
                choose.setEnabled(enabled and view["cells"][col] == " ")
                choose.clicked.connect(lambda _checked=False, c=col: self._submit_action(bytes((c,))))
                grid.addWidget(choose, 0, col)
            for row in range(6):
                for col in range(7):
                    mark = view["cells"][row * 7 + col]
                    disc = QLabel("●" if mark != " " else "○")
                    disc.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    color = "#eeeeee" if self.settings.color_scheme == "monochrome" else "#71e5d3" if mark == "X" else "#f4bd76" if mark == "O" else "#52697e"
                    disc.setStyleSheet(f"background:#1a3548;color:{color};font-size:34px;border-radius:6px")
                    disc.setFixedSize(54, 52)
                    grid.addWidget(disc, row + 1, col)
            box.addWidget(board)
            box.addWidget(QLabel("Select a column to drop a disc. Four in a row wins."))
            self.board_layout.addWidget(host)
        elif view["kind"] == "piles":
            host = QWidget(); box = QVBoxLayout(host)
            for index, count in enumerate(view["piles"]):
                row = QHBoxLayout(); label = QLabel(f"PILE {index+1:02d}"); label.setObjectName("eyebrow")
                row.addWidget(label); row.addStretch(1)
                for _ in range(count):
                    pebble = QLabel("◆"); pebble.setStyleSheet("color:#eeeeee;font-size:20px" if self.settings.color_scheme == "monochrome" else "color:#66d9c2;font-size:20px")
                    row.addWidget(pebble)
                row.addWidget(QLabel(str(count))); box.addLayout(row)
            controls = QHBoxLayout(); pile = QComboBox(); amount = QSpinBox()
            for i, count in enumerate(view["piles"]):
                pile.addItem(f"Pile {i+1} · {count} remaining", i)
                if count == 0:
                    pile.model().item(i).setEnabled(False)
            first = next((i for i, count in enumerate(view["piles"]) if count), 0)
            pile.setCurrentIndex(first)
            def update_amount(index):
                amount.setRange(1, max(1, view["piles"][index]))
            pile.currentIndexChanged.connect(update_amount)
            update_amount(first)
            take = QPushButton("Take items"); take.setObjectName("primaryButton")
            take.setEnabled(not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active" and not session["pending_action"])
            take.clicked.connect(lambda: self._submit_action(bytes((pile.currentIndex(), amount.value()))))
            controls.addWidget(pile); controls.addWidget(amount); controls.addWidget(take); box.addLayout(controls)
            self.board_layout.addWidget(host)
        elif view["kind"] == "word-tiles":
            host = QWidget(); box = QVBoxLayout(host); box.setContentsMargins(0, 0, 0, 0)
            current = session["current_player"]
            scoreline = "     ".join(f"{player}  {score}" for player, score in view["scores"].items())
            box.addWidget(QLabel(f"{scoreline}     BAG {view['bag']}     {view['lexicon']} · OPEN RACKS"))
            grid_host = QWidget(); grid = QGridLayout(grid_host); grid.setSpacing(2); grid.setContentsMargins(0, 4, 0, 4)
            grid.addWidget(QLabel("   "), 0, 0)
            for x in range(15): grid.addWidget(QLabel(chr(65 + x)), 0, x + 1, alignment=Qt.AlignmentFlag.AlignCenter)
            premium_colors = {"TW": "#6e3341", "DW": "#684d52", "TL": "#315971", "DL": "#38525d", "..": "#263746"}
            for y in range(15):
                grid.addWidget(QLabel(str(y + 1)), y + 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
                for x in range(15):
                    index = y * 15 + x
                    tile = view["cells"][index]
                    text = tile or ("★" if index == 112 else view["premiums"][index])
                    button = QPushButton(text)
                    button.setObjectName(f"wordCell{index}")
                    button.setFixedSize(28, 27)
                    color = "#eeeeee" if tile else ("#343a40" if self.settings.color_scheme == "monochrome" else premium_colors[view["premiums"][index]])
                    foreground = "#111111" if tile else ("#eeeeee" if self.settings.color_scheme == "monochrome" else "#d8e3ed")
                    button.setStyleSheet(f"QPushButton {{ padding:0; font-size:10px; font-weight:700; background:{color}; color:{foreground}; border:1px solid #182635; }}")
                    # Clicking a square chooses the start coordinate; this is
                    # local UI selection and never transmits a move.
                    button.clicked.connect(lambda _checked=False, x=x, y=y: start.setText(f"{chr(65 + x)}{y + 1}"))
                    grid.addWidget(button, y + 1, x + 1)
            box.addWidget(grid_host, alignment=Qt.AlignmentFlag.AlignHCenter)
            for player, rack in view["racks"].items():
                box.addWidget(QLabel(f"{player} RACK  " + "  ".join(rack)))
            enabled = not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or current == self.settings.callsign) and session["status"] == "active" and not session["pending_action"]
            form = QHBoxLayout(); start = QLineEdit(); start.setObjectName("wordStart"); start.setPlaceholderText("H8")
            direction = QComboBox(); direction.setObjectName("wordDirection"); direction.addItem("Across", "H"); direction.addItem("Down", "V")
            word = QLineEdit(); word.setObjectName("wordText"); word.setPlaceholderText("Word · use {A} for a blank tile")
            place = QPushButton("Place word"); place.setObjectName("wordPlace"); place.setEnabled(enabled)
            place.clicked.connect(lambda: self._submit_word_placement(session, start.text(), direction.currentData(), word.text()))
            form.addWidget(start); form.addWidget(direction); form.addWidget(word, 1); form.addWidget(place)
            box.addLayout(form)
            extras = QHBoxLayout(); exchange_text = QLineEdit(); exchange_text.setObjectName("wordExchangeTiles"); exchange_text.setPlaceholderText("Exchange letters; ? = blank")
            exchange = QPushButton("Exchange"); exchange.setObjectName("wordExchange"); exchange.setEnabled(enabled and view["bag"] >= 7)
            exchange.clicked.connect(lambda: self._submit_word_exchange(session, exchange_text.text()))
            pass_turn = QPushButton("Pass"); pass_turn.setEnabled(enabled)
            pass_turn.clicked.connect(lambda: self._submit_action(b"\x01"))
            extras.addWidget(exchange_text, 1); extras.addWidget(exchange); extras.addWidget(pass_turn)
            box.addLayout(extras)
            self.board_layout.addWidget(host)
        else:
            panel = QWidget(); box = QVBoxLayout(panel)
            summary = QLabel(str(view)); summary.setWordWrap(True); box.addWidget(summary)
            box.addWidget(QLabel("Plugin action · hexadecimal bytes"))
            raw = QLineEdit(); raw.setPlaceholderText("e.g. 0102")
            send = QPushButton("Submit action"); send.setObjectName("primaryButton")
            send.setEnabled(not self.settings.observer_mode and (self.settings.transport == "practice" or self.settings.tx_enabled) and (self.settings.transport == "practice" or session["current_player"] == self.settings.callsign) and session["status"] == "active")
            send.clicked.connect(lambda: self._submit_hex_action(raw.text()))
            box.addWidget(raw); box.addWidget(send); self.board_layout.addWidget(panel)

    def _refresh_chat(self, sid: str | None):
        if not self.engine or not sid or sid != self.selected_id:
            return
        self.chat_list.clear()
        try: history = self.engine.chat_history(sid)
        except Exception: return
        for message in history:
            label = QLabel(f"<b>{html.escape(message['sender'])}</b>  {html.escape(message['body'])}<br><span style='color:#8393a8'>{html.escape(message['delivery'])}</span>")
            label.setObjectName("chatLine"); label.setWordWrap(True)
            item = QListWidgetItem(); item.setSizeHint(label.sizeHint()); self.chat_list.addItem(item); self.chat_list.setItemWidget(item, label)
        self.chat_list.scrollToBottom()

    def _run(self, fn: Callable, on_done: Callable | None = None):
        task = Task(fn)
        self._tasks.add(task)
        def done(value):
            self._tasks.discard(task)
            if on_done: on_done(value)
            self._refresh_sessions()
        task.signals.done.connect(done)
        task.signals.failed.connect(lambda err: (self._tasks.discard(task), self.footer_label.setText(err)))
        self._pool.start(task)

    def _new_game(self):
        if not self.engine: return
        game = self.game_choice.currentData(); peer = self.peer_input.text().strip().upper() or None
        hosted = self.hosted_checkbox.isChecked()
        self._run(partial(self.engine.create_game, game, None if hosted else peer, hosted=hosted), self._created_game)

    def _join_game(self):
        if self.engine and self.selected_id: self._run(partial(self.engine.join_game, self.selected_id))

    def _announce_game(self):
        if self.engine and self.selected_id: self._run(partial(self.engine.announce_game, self.selected_id))

    def _spot_game(self):
        if not self.engine or not self.selected_id: return
        if not self.settings.dx_cluster_url:
            QMessageBox.information(self, "DX Cluster", "Configure a DX Cluster URL in Settings first."); return
        try:
            session = self.engine.session(self.selected_id)
            build_spot(session, self.settings.callsign, "14070", "Data2G USB")
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, "DX Cluster", str(exc)); return
        dialog = QDialog(self)
        dialog.setWindowTitle("Spot my game · DX Cluster")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        frequency = QLineEdit(); frequency.setPlaceholderText("14070")
        mode = QLineEdit("Data2G USB")
        note = QLineEdit("Open game · stations welcome")
        form.addRow("Dial frequency (kHz)", frequency)
        form.addRow("Mode / transport", mode)
        form.addRow("Message", note)
        layout.addLayout(form)
        publish = QPushButton("Publish spot"); publish.clicked.connect(dialog.accept)
        layout.addWidget(publish)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            payload = build_spot(session, self.settings.callsign, frequency.text(), mode.text(), note.text())
        except ValueError as exc:
            QMessageBox.warning(self, "DX Cluster", str(exc)); return
        self._run(partial(post_spot, self.settings.dx_cluster_url, payload),
                  lambda _: self.footer_label.setText("Game spotted on the DX Cluster"))

    def _set_reply_order(self):
        if self.engine and self.selected_id:
            self._run(partial(self.engine.set_reply_order, self.selected_id, self.order_input.text()))

    def _retry_move(self):
        if self.engine and self.selected_id: self._run(partial(self.engine.resend_pending_action, self.selected_id))

    def _created_game(self, sid: str):
        self.selected_id = sid

    def _invite_player(self):
        if not self.engine or not self.selected_id: return
        peer = self.peer_input.text().strip().upper()
        if not peer:
            self.footer_label.setText("Enter a callsign to invite to this lobby."); return
        sid = self.selected_id
        self._run(partial(self.engine.invite_player, sid, peer))

    def _start_game(self):
        if self.engine and self.selected_id:
            sid = self.selected_id; self._run(partial(self.engine.start_game, sid))

    def _cancel_invitation(self):
        if not self.engine or not self.selected_id: return
        peer = self.peer_input.text().strip().upper()
        if not peer:
            self.footer_label.setText("Enter the pending station callsign to cancel its invitation."); return
        sid = self.selected_id
        self._run(partial(self.engine.cancel_invitation, sid, peer))

    def _accept_invite(self):
        if self.engine and self.selected_id:
            sid = self.selected_id; self._run(partial(self.engine.accept_invite, sid))

    def _submit_action(self, action: bytes):
        if self.engine and self.selected_id:
            sid = self.selected_id; self._run(partial(self.engine.legal_action, sid, action))

    def _submit_hex_action(self, text: str):
        try: action = bytes.fromhex(text.strip())
        except ValueError:
            self.footer_label.setText("Enter a valid hexadecimal game action."); return
        self._submit_action(action)

    def _word_state(self, session: dict):
        game = self.engine.registry.by_id(session["game_id"])
        state = game.decode_state(session["state"])
        game.validate_roster(state, tuple(session["players"]))
        return state

    def _submit_word_placement(self, session: dict, start: str, direction: str, word: str):
        try:
            action = make_placement(self._word_state(session), session["current_player"], start, direction, word)
            self._submit_action(action)
        except Exception as exc:
            self.footer_label.setText(str(exc))

    def _submit_word_exchange(self, session: dict, tiles: str):
        try:
            action = make_exchange(self._word_state(session), session["current_player"], tiles)
            self._submit_action(action)
        except Exception as exc:
            self.footer_label.setText(str(exc))

    def _send_chat(self):
        text = self.chat_input.text().strip()
        if not self.engine or not self.selected_id or not text: return
        sid = self.selected_id
        def sent(_seq):
            if self.selected_id == sid and self.chat_input.text().strip() == text:
                self.chat_input.clear()
            self._refresh_chat(sid)
        self._run(partial(self.engine.send_chat, sid, text), sent)

    def _connect_peer(self):
        if not self.engine: return
        peer = self.peer_input.text().strip().upper()
        if not peer:
            self.footer_label.setText("Enter a callsign to connect or resume."); return
        self._run(partial(self.engine.connect_peer, peer))

    def _disconnect_peer(self):
        if self.engine: self._run(self.engine.disconnect)

    def _sync_selected(self):
        if self.engine and self.selected_id:
            if self.settings.observer_mode and QMessageBox.question(self, "Transmit snapshot request",
                    "This will transmit one synchronization request over the radio. Continue?") != QMessageBox.StandardButton.Yes:
                return
            sid = self.selected_id; self._run(partial(self.engine.request_sync, sid))

    def _edit_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.connect_button.setVisible(self.settings.transport == "connected")
            self.disconnect_button.setVisible(self.settings.transport == "connected")
            self._apply_style()
            self.connect_service()

    def closeEvent(self, event):
        if self.unsubscribe: self.unsubscribe()
        if self.engine: self.engine.close()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="Radio Arcade desktop client")
    parser.add_argument("--callsign")
    parser.add_argument("--host")
    parser.add_argument("--transport", choices=("broadcast", "connected", "practice"))
    parser.add_argument("--observer", action="store_true", help="passively watch KISS broadcast games")
    args, qt_args = parser.parse_known_args()
    settings = Settings.load()
    if args.callsign: settings.callsign = args.callsign.upper()
    if args.host: settings.host = args.host
    if args.transport: settings.transport = args.transport
    if args.observer: settings.observer_mode = True
    app = QApplication([sys.argv[0], *qt_args]); app.setApplicationName("Radio Arcade")
    window = ArcadeWindow(settings)
    if not settings.callsign:
        dialog = SettingsDialog(settings, window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
    window.show(); window.connect_service()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
