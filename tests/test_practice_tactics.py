import random
import unittest
from radio_arcade.practice_ai import choose
from radio_arcade.games.tictactoe import TicTacToeGame
from radio_arcade.games.connect_four import ConnectFourGame
from radio_arcade.games.nim import NimGame
from radio_arcade.games.battleship import BattleshipGame

class PracticeTactics(unittest.TestCase):
    def test_tic_tac_toe_win_and_block(self):
        game=TicTacToeGame()
        players=("HUMAN","CPU")
        for cells,target in [("XX O     ",2),("OO XX    ",2)]:
            state=dict(board=cells,turn=1,finished=False,winner=-1)
            self.assertEqual(choose(game,state,players,"CPU",random.Random(1)),bytes((target,)))

    def test_connect_four_blocks_vertical_threat(self):
        game=ConnectFourGame();p=("HUMAN","CPU");s=game.new_state(p)
        for actor,col in (("HUMAN",0),("CPU",1),("HUMAN",0),("CPU",2),("HUMAN",0)):
            s=game.apply_action(s,p,actor,bytes((col,)))
        self.assertEqual(choose(game,s,p,"CPU",random.Random(5)),b"\x00")

    def test_nim_uses_zero_xor(self):
        game=NimGame();p=("HUMAN","CPU");s=game.new_state(p)
        s.update(piles=[1,2,4,0],turn=1)
        action=choose(game,s,p,"CPU",random.Random(5))
        self.assertEqual(game.apply_action(s,p,"CPU",action)["piles"],[1,2,3,0])

    def test_battleship_targets_adjacent_public_hit(self):
        game=BattleshipGame();p=("HUMAN","CPU");s=game.new_state(p)
        s.update(phase="call",turn=1,ready=[True,True])
        s["shots"][1][44]=2
        self.assertIn(choose(game,s,p,"CPU",random.Random(5))[0],(34,43,45,54))
