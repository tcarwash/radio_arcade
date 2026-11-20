"""Naval games share the two-up responsive layout and sunk-vessel sprites."""
import os
import random
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtWidgets import QApplication,QWidget,QLabel
    from PySide6.QtCore import QPoint
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT,"requires desktop extra")
class NavalLayout(unittest.TestCase):
    def test_sea_warfare_two_up_reflows_and_draws_sunk_ship(self):
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.practice import PracticeService
        from radio_arcade.games.sea_warfare import FLEET
        from radio_arcade.practice_ai import fleet
        from radio_arcade.settings import Settings
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"RADIO_ARCADE_HOME":folder}):
            engine=PracticeService("PLAYER")
            window=ArcadeWindow(Settings(callsign="PLAYER",transport="practice"))
            window.resize(1440,900);window.show();window._service_ready(engine)
            try:
                sid=engine.create_game("sea-warfare")
                engine.place_battleship_fleet(sid,fleet(FLEET,random.Random(4)))
                private=engine.store.load_private_game_data(sid)
                ai=private["practice_seats"]["1"];ship=ai["ships"][5]
                ai["hits"]=ship[:-1];ai["fighter_used"]=True
                engine.store.save_private_game_data(sid,private)
                engine.legal_action(sid,bytes((ship[-1],)))
                window.selected_id=sid;window._board_signature=None;window._show_session(sid)
                for _ in range(5):app.processEvents()
                own=window.board_scroll.findChild(QWidget,"seaOwnPanel")
                enemy=window.board_scroll.findChild(QWidget,"seaEnemyPanel")
                pair=window.board_scroll.findChild(QWidget,"seaBoards")
                self.assertIsNotNone(pair)
                self.assertIsNotNone(own)
                self.assertIsNotNone(enemy)
                oa=own.mapTo(window,QPoint(0,0));ea=enemy.mapTo(window,QPoint(0,0))
                self.assertGreater(ea.x(),oa.x()+120)
                self.assertLess(abs(ea.y()-oa.y()),25)
                enemy_sprites=enemy.findChildren(QLabel,"navalShipSprite")
                self.assertEqual(len(enemy_sprites),1)
                self.assertEqual(enemy_sprites[0].property("spriteName"),
                                 "battleship-patrol.png" if ship[1]-ship[0]==10 else "battleship-patrol-horizontal.png")
                self.assertEqual(len(own.findChildren(QLabel,"navalShipSprite")),6)
                window.grab().save("/tmp/ra-sea-native-wide.png")
                window.resize(360,660)
                window.compact_tabs.setCurrentIndex(1)
                for _ in range(9):app.processEvents()
                oa=own.mapTo(window,QPoint(0,0));ea=enemy.mapTo(window,QPoint(0,0))
                self.assertGreater(ea.y(),oa.y()+120)
                self.assertLess(abs(ea.x()-oa.x()),35)
                window.grab().save("/tmp/ra-sea-native-narrow.png")
            finally:
                window.close();engine.close();app.processEvents()

    def test_classic_revealed_ship_drawn_in_enemy_waters(self):
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings
        from tests.test_battleship import FLEET_A,FLEET_B
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"RADIO_ARCADE_HOME":folder}),patch("radio_arcade.practice.random_fleet",return_value=FLEET_B):
            engine=PracticeService("N0AAA")
            window=ArcadeWindow(Settings(callsign="N0AAA",transport="practice"))
            window.resize(1400,900);window.show();window._service_ready(engine)
            try:
                sid=engine.create_game("battleship-classic")
                engine.place_battleship_fleet(sid,FLEET_A)
                engine.legal_action(sid,b"\x5a");engine.legal_action(sid,b"\x5b")
                window.selected_id=sid;window._board_signature=None;window._show_session(sid)
                for _ in range(3):app.processEvents()
                sprites=window.board_scroll.findChildren(QLabel,"navalShipSprite")
                self.assertEqual(len(sprites),6)
                markers=window.board_scroll.findChildren(QLabel,"navalShotMarker")
                self.assertGreaterEqual(len([m for m in markers if m.text()=="S"]),2)
            finally:
                window.close();engine.close();app.processEvents()
