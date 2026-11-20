"""Word draft previews are authoritative, private to the client, and non-mutating."""
import itertools
import os
import tempfile
import unittest
from unittest.mock import patch
from radio_arcade.word_draft import preview_word, positions, append_rack_tile, used_tiles
from radio_arcade.games.scrabble import OpenWordTilesGame, _lexicon

class WordDraftTests(unittest.TestCase):
    def test_blank_and_cross_word_preview(self):
        game=OpenWordTilesGame();players=("N0AAA","N0BBB");state=game.new_state(players)
        state["turn"]=0
        state["racks"][0]=[3,1,20,0,5,18,19]
        before=game.encode_state(state)
        result=preview_word(game,state,players,players[0],"G8","H","C{A}T")
        self.assertGreater(result["score"],0)
        self.assertEqual(game.encode_state(state),before)
        with self.assertRaises(ValueError):preview_word(game,state,players,players[0],"A1","H","CAT")
        self.assertEqual(positions("G8","H","C{A}T"),{111:"C",112:"{A}",113:"T"})
        cells=[""]*225;cells[112]="A"
        self.assertEqual(append_rack_tile("G8","H","C","T",cells),"CAT")
        self.assertEqual(used_tiles("G8","H","CAT",cells),["C","T"])
    def test_preview_api_rejects_stale_revision(self):
        from radio_arcade.web import WebController
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings
        import json
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}):
            controller=WebController(Settings(callsign="N0CALL",transport="practice"),service_factory=lambda settings:PracticeService(settings.callsign),settings_path=d+"/settings.json")
            try:
                with patch("radio_arcade.games.scrabble.secrets.randbits",return_value=42):
                    sid=controller.engine.create_game("open-word-tiles")
                row=controller.engine.session(sid)
                rack=[t for t in row["view"]["racks"]["N0CALL"] if t not in ("?","·")]
                word=next(("".join(w) for n in range(2,len(rack)+1) for w in itertools.permutations(rack,n) if "".join(w) in _lexicon()),None)
                self.assertIsNotNone(word)
                data=dict(command="preview_word",session_id=sid,revision=row["revision"],start="H8",direction="H",word=word)
                status,result=controller.dispatch("POST","/api/command",json.dumps(data).encode())
                self.assertEqual(status,200,result)
                self.assertGreater(result["score"],0)
                self.assertEqual(controller.engine.session(sid),row)
                with patch("radio_arcade.games.scrabble.secrets.randbits",return_value=43):
                    opening=controller.engine.create_game("open-word-tiles")
                self.assertEqual(controller.engine.session(opening)["current_player"],"N0CALL")
                self.assertGreaterEqual(controller.engine.session(opening)["revision"],1)
                data["revision"]=-1
                status,_=controller.dispatch("POST","/api/command",json.dumps(data).encode())
                self.assertNotEqual(status,200)
            finally:controller.close()

try:
    from PySide6.QtWidgets import QApplication
    HAS_QT=True
except ImportError:
    HAS_QT=False

@unittest.skipUnless(HAS_QT,"desktop extra")
class WordDraftDesktopTests(unittest.TestCase):
    def test_rack_click_preview_and_local_draft(self):
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        from PySide6.QtWidgets import QLabel,QPushButton,QLineEdit
        from radio_arcade.desktop import ArcadeWindow
        from radio_arcade.practice import PracticeService
        from radio_arcade.settings import Settings
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"RADIO_ARCADE_HOME":d}):
            engine=PracticeService("N0CALL")
            w=ArcadeWindow(Settings(callsign="N0CALL",transport="practice"))
            w.resize(1366,768);w.show();w._service_ready(engine)
            try:
                with patch("radio_arcade.games.scrabble.secrets.randbits",return_value=42):
                    sid=engine.create_game("open-word-tiles")
                w.selected_id=sid;w._refresh_sessions();app.processEvents()
                before=engine.session(sid)
                def find(kind,name):return w.board_scroll.findChild(kind,name)
                rack=before["view"]["racks"]["N0CALL"]
                word=next(("".join(t) for n in range(2,8) for t in itertools.permutations([a for a in rack if a not in ("?","·")],n) if "".join(t) in _lexicon()),None)
                self.assertIsNotNone(word)
                from PySide6.QtCore import QMimeData, QPointF, Qt
                from PySide6.QtGui import QDropEvent
                first=next(i for i in range(7) if find(QPushButton,"wordRack"+str(i)).text()==word[0])
                mime=QMimeData();mime.setData("application/x-radio-arcade-rack",str(first).encode())
                event=QDropEvent(QPointF(4,4),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
                find(QPushButton,"wordCell112").dropEvent(event)
                self.assertEqual(find(QLineEdit,"wordText").text(),word[0])
                for letter in word[1:]:
                    button=next(find(QPushButton,"wordRack"+str(i)) for i in range(7) if find(QPushButton,"wordRack"+str(i)).text()==letter and find(QPushButton,"wordRack"+str(i)).isEnabled())
                    button.click()
                self.assertEqual(find(QLineEdit,"wordText").text(),word)
                self.assertIn("Legal word",find(QLabel,"wordPreview").text())
                self.assertIn("DRAFT — NOT SUBMITTED",find(QLabel,"wordDraftStatus").text())
                self.assertIn("dashed",find(QPushButton,"wordCell112").styleSheet())
                self.assertIn("Submit word",find(QPushButton,"wordPlace").text())
                self.assertEqual(engine.session(sid),before)
                self.assertTrue(find(QPushButton,"wordPlace").isEnabled())
                self.assertEqual(find(QPushButton,"wordCell112").text(),word[0])
                find(QPushButton,"wordShuffle").click()
                self.assertEqual(find(QLineEdit,"wordText").text(),word)
                w._board_signature=None;w._show_session(sid);app.processEvents()
                self.assertEqual(find(QLineEdit,"wordText").text(),word)
                find(QPushButton,"wordUndo").click()
                self.assertEqual(find(QLineEdit,"wordText").text(),word[:-1])
                find(QPushButton,"wordClear").click()
                self.assertFalse(find(QPushButton,"wordPlace").isEnabled())
                self.assertEqual(engine.session(sid),before)
                find(QLineEdit,"wordText").setText("QXZQ")
                self.assertIn("DRAFT — NOT SUBMITTED",find(QLabel,"wordDraftStatus").text())
                self.assertFalse(find(QPushButton,"wordPlace").isEnabled())
                self.assertEqual(engine.session(sid),before)
                find(QLineEdit,"wordText").setText(word)
                self.assertTrue(find(QPushButton,"wordPlace").isEnabled())
                find(QPushButton,"wordPlace").click()
                import time
                deadline=time.monotonic()+8
                while w._tasks and time.monotonic()<deadline:
                    app.processEvents();time.sleep(.02)
                app.processEvents()
                self.assertGreater(engine.session(sid)["revision"],before["revision"])
                if w.selected_id==sid:
                    w._show_session(sid);app.processEvents()
                    self.assertIn("COMMITTED TO BOARD",find(QLabel,"wordDraftStatus").text())
            finally:
                w.close();app.processEvents()
