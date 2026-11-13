"""Desktop score display and animated result dialog."""
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt
from PySide6.QtWidgets import QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from .presentation import scoreboard, result_title


class Scoreboard(QFrame):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.setObjectName("matchScoreboard")
        self.setStyleSheet("""
            QFrame#matchScoreboard { background:#080e17; border:2px solid #3b5267; border-radius:12px; }
            QLabel { background:transparent; border:0; color:#aebed0; }
            QLabel#scoreDigits { color:#afffe0; font-family:monospace; font-size:30px; font-weight:800; }
            QLabel#scoreStation { color:#e8edf6; font-weight:700; font-size:12px; }
        """)
        layout = QVBoxLayout(self)
        data = scoreboard(session)
        label = QLabel(f"SCOREBOARD  /  {data['label']}"); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        row = QHBoxLayout(); layout.addLayout(row)
        for entry in data['entries']:
            seat = QVBoxLayout()
            name = QLabel(entry['player']); name.setObjectName('scoreStation')
            value = QLabel(entry['value']); value.setObjectName('scoreDigits')
            status = QLabel(entry['unit'] + (' · WINNER' if entry['winner'] else ' · TURN' if entry['active'] else ''))
            for widget in (name, value, status):
                widget.setAlignment(Qt.AlignmentFlag.AlignCenter); seat.addWidget(widget)
            row.addLayout(seat, 1)


class ResultDialog(QDialog):
    def __init__(self, session, settings, parent):
        super().__init__(parent)
        self.setWindowTitle('Match complete')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(300)
        if parent:
            self.resize(max(300, min(430, parent.width() - 16)), 320)
        layout = QVBoxLayout(self); layout.setContentsMargins(28, 24, 28, 24); layout.setSpacing(18)
        title = QLabel(result_title(session, settings.callsign, practice=settings.transport == 'practice', observer=settings.observer_mode))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        accent = '#f18383' if title.text() == 'DEFEAT' else '#f3bf7a' if title.text() == 'DRAW' else '#8fe2c2'
        title.setStyleSheet(f'font-size:32px; font-weight:800; color:{accent};')
        layout.addWidget(title)
        subtitle = QLabel(f"{session['game']} · Match complete"); subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle); layout.addWidget(Scoreboard(session))
        close = QPushButton('Back to board'); close.setObjectName('primaryButton'); close.clicked.connect(self.accept)
        layout.addWidget(close)
        effect = QGraphicsOpacityEffect(title); title.setGraphicsEffect(effect)
        self.animation = QPropertyAnimation(effect, b'opacity', self)
        self.animation.setDuration(650); self.animation.setStartValue(0.1); self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.start()
