"""Desktop artillery interaction controls must not change radio state until a move."""
import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

@unittest.skipUnless(importlib.util.find_spec("PySide6"),"desktop extra")
class ScorchedUX(unittest.TestCase):
    def test_navigation_aim_armory_and_persistence(self):
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QLabel, QPushButton, QSlider, QSpinBox
        from radio_arcade.desktop import ArcadeWindow, ScorchedBattlefield
        from radio_arcade.scorched_navigation import BattleMapScroll
        from radio_arcade.settings import Settings
        from radio_arcade.practice import PracticeService
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{"RADIO_ARCADE_HOME":folder}):
            engine=PracticeService("PLAYER")
            w=ArcadeWindow(Settings(callsign="PLAYER",transport="practice"))
            w.resize(1366,768);w.show();w._service_ready(engine)
            try:
                sid=engine.create_game("scorched-earth");w.selected_id=sid;w._refresh_sessions();app.processEvents()
                def find(kind,name):return w.board_scroll.findChild(kind,name)
                camera=find(BattleMapScroll,"battlefieldScroll")
                field=find(ScorchedBattlefield,"scorchedBattlefield")
                w.findChild(QPushButton,"scorchedCenterEnemy").click()
                self.assertGreater(camera.horizontalScrollBar().value(),0)
                self.assertFalse(find(QCheckBox,"scorchedFollow").isChecked())
                before_width=field.width()
                find(QPushButton,"scorchedZoomIn").click()
                self.assertGreater(field.width(),before_width)
                find(QPushButton,"scorchedCenterOwn").click()
                angle=find(QSlider,"scorchedAngle"); angle_num=find(QSpinBox,"scorchedAngleNumber")
                power=find(QSlider,"scorchedPower");power_num=find(QSpinBox,"scorchedPowerNumber")
                angle_num.setValue(37);power_num.setValue(53)
                self.assertEqual(angle.value(),37);self.assertEqual(power.value(),11); self.assertEqual(power_num.value(),55)
                field.setFocus();QTest.keyClick(field,Qt.Key.Key_Right,Qt.KeyboardModifier.ShiftModifier);app.processEvents()
                self.assertEqual(angle.value(),38)
                find(QComboBox,"scorchedWeapon").setCurrentIndex(2)
                self.assertIn("62 damage",find(QLabel,"scorchedWeaponInfo").text())
                self.assertTrue(field.preview)
                initial = engine.session(sid)
                def inspect(dialog):
                    self.assertTrue(dialog.findChild(QPushButton,"scorchedBuy1").isEnabled())
                    self.assertTrue(dialog.findChild(QPushButton,"scorchedBuy7").isEnabled())
                    dialog.reject()
                    return QDialog.DialogCode.Rejected
                with patch.object(QDialog,"exec",inspect):
                    find(QPushButton,"scorchedArmory").click()
                self.assertEqual(engine.session(sid),initial)
                w._board_signature=None;w._show_session(sid);app.processEvents()
                self.assertEqual(find(QSlider,"scorchedAngle").value(),38)
                self.assertEqual(find(QSlider,"scorchedPower").value(),11)
                self.assertEqual(find(QComboBox,"scorchedWeapon").currentData(),2)
                self.assertEqual(find(ScorchedBattlefield,"scorchedBattlefield").width(),before_width*16//14)
                w.resize(360,400);w.compact_tabs.setCurrentIndex(1)
                for _ in range(5):app.processEvents()
                self.assertEqual(w.board_scroll.horizontalScrollBar().maximum(),0)
            finally:
                w.close();app.processEvents()
