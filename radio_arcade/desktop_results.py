"""Desktop score display and animated result dialog."""
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt
from PySide6.QtWidgets import QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from .presentation import scoreboard, result_title


class Scoreboard(QFrame):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.setObjectName("matchScoreboard")
        self.setStyleSheet("""
            QFrame#matchScoreboard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111a28, stop:1 #0a111b);
                border: 1px solid rgba(100, 160, 240, 0.18);
                border-radius: 14px;
                padding: 10px 14px;
            }
            QLabel { background: transparent; border: 0; color: #94a3b8; }
            QLabel#scoreHeader {
                color: #00f5c4;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 2px;
            }
            QLabel#scoreDigits {
                color: #afffe0;
                font-family: 'Space Grotesk', 'Inter', monospace;
                font-size: 32px;
                font-weight: 800;
            }
            QLabel#scoreStation {
                color: #f8fafc;
                font-weight: 700;
                font-size: 13px;
                letter-spacing: 0.3px;
            }
            QLabel#scoreUnit {
                color: #64748b;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        data = scoreboard(session)
        label = QLabel(f"SCOREBOARD  /  {data['label'].upper()}")
        label.setObjectName("scoreHeader")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(16)
        layout.addLayout(row)
        for entry in data['entries']:
            seat = QVBoxLayout()
            seat.setSpacing(3)
            name = QLabel(entry['player']); name.setObjectName('scoreStation')
            value = QLabel(entry['value']); value.setObjectName('scoreDigits')
            unit_text = entry['unit'] + (' · WINNER' if entry['winner'] else ' · TURN' if entry['active'] else '')
            status = QLabel(unit_text)
            status.setObjectName('scoreUnit')
            if entry['winner']:
                status.setStyleSheet("color: #fbbf24; font-weight: 800;")
            elif entry['active']:
                status.setStyleSheet("color: #00f5c4; font-weight: 700;")
            for widget in (name, value, status):
                widget.setAlignment(Qt.AlignmentFlag.AlignCenter); seat.addWidget(widget)
            row.addLayout(seat, 1)


class ResultDialog(QDialog):
    def __init__(self, session, settings, parent, play_again=None):
        super().__init__(parent)
        self.setWindowTitle('Match complete · Radio Arcade')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(340)
        self.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #121c2d, stop:1 #0a111c);
                border: 1px solid rgba(100, 160, 240, 0.24);
                border-radius: 18px;
            }
            QLabel { color: #e2e8f0; }
            QLabel#resultSubtitle { color: #94a3b8; font-size: 13px; font-weight: 500; }
        """)
        if parent:
            self.resize(max(340, min(450, parent.width() - 16)), 340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)
        outcome = result_title(session, settings.callsign, practice=settings.transport == 'practice', observer=settings.observer_mode)
        title = QLabel(outcome)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        accent = '#f87171' if outcome == 'DEFEAT' else '#fbbf24' if outcome == 'DRAW' else '#00f5c4'
        title.setStyleSheet(f'font-size:36px; font-weight:900; color:{accent}; letter-spacing:1px;')
        layout.addWidget(title)
        subtitle = QLabel(f"{session['game']} · Match complete")
        subtitle.setObjectName("resultSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        layout.addWidget(Scoreboard(session))
        close = QPushButton('Return to Table')
        close.setObjectName('primaryButton')
        close.setMinimumHeight(40)
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        if play_again and not settings.observer_mode and (settings.transport=="practice" or settings.tx_enabled):
            again=QPushButton("Play again");again.setMinimumHeight(40)
            again.clicked.connect(lambda: (self.accept(),play_again()))
            layout.addWidget(again)
        effect = QGraphicsOpacityEffect(title); title.setGraphicsEffect(effect)
        self.animation = QPropertyAnimation(effect, b'opacity', self)
        self.animation.setDuration(650); self.animation.setStartValue(0.1); self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.start()
