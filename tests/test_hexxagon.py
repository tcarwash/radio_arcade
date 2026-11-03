"""Hexxagon rule, compact wire, and deterministic completion checks."""
import random
import json
import shutil
import subprocess
from pathlib import Path
import unittest
from radio_arcade.games.hexxagon import HexxagonGame, CELLS, INDEX, distance, moves, pack_move
from radio_arcade.games.base import builtin_registry


class HexxagonTests(unittest.TestCase):
    def setUp(self):
        self.game = HexxagonGame()
        self.players = ('A', 'B')
        self.state = self.game.new_state(self.players)

    @unittest.skipUnless(shutil.which('node'), 'Node required for browser control test')
    def test_browser_clone_and_jump_controls(self):
        source = (Path(__file__).parents[1] / 'radio_arcade/web_assets/app.js').read_text()
        start = source.index('function renderHexxagon(')
        end = source.index('\nfunction renderGrid(', start)
        script = r"""
const assert = require('assert');
const view = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const canAct = () => true;
let sent, elements;
const submitAction = bytes => {sent = bytes;};
function element(attrs = {}) {
 const el = {attrs, events: {}, append() {}, classList: {toggle() {}}, addEventListener(name, cb) {this.events[name] = cb;}};
 if (attrs.role === 'button') elements.push(el);
 return el;
}
const node = () => element();
const svgElement = (tag, attrs) => element(attrs);
""" + source[start:end] + r"""
for (const length of [1, 2]) {
 elements = []; sent = null;
 renderHexxagon({players: ['A', 'B']}, view, {append() {}});
 assert.equal(elements.length, 58);
 const [a, b] = view.legal_moves.find(([a,b]) => {
   const x=view.cells[a], y=view.cells[b];
   return Math.max(Math.abs(x.q-y.q),Math.abs(x.r-y.r),Math.abs(x.q+x.r-y.q-y.r)) === length;
 });
 elements[a].events.click(); elements[b].events.click();
 assert.equal(sent.length, length);
 assert.deepEqual(sent, length === 1 ? [b] : [(0x8000 | a<<6 | b)>>8, (0x8000 | a<<6 | b)&255]);
}
"""
        subprocess.run(['node', '-e', script], input=json.dumps(self.game.view_model(self.state, self.players)),
                       text=True, capture_output=True, check=True)

    @unittest.skipUnless(shutil.which('node'), 'Node required for browser effect test')
    def test_browser_capture_lightning_runs_once_and_respects_reduced_motion(self):
        board = [0] * 58
        source, target, victim = INDEX[(-3, 0)], INDEX[(-2, 0)], INDEX[(-2, 1)]
        board[source], board[victim], board[INDEX[(4, 0)]] = 1, 2, 2
        before = dict(self.state, board=board)
        after = self.game.apply_action(before, self.players, 'A', pack_move(source, target))
        source_js = (Path(__file__).parents[1] / 'radio_arcade/web_assets/app.js').read_text()
        start = source_js.index('const hexAnimations = new Map();')
        end = source_js.index('\nfunction renderHexxagon(', start)
        script = r"""
const assert=require('assert');
const [before,after]=JSON.parse(require('fs').readFileSync(0,'utf8'));
let reduced=false, effects=0;
const matchMedia=()=>({matches:reduced});
const setTimeout=()=>{};
const svgElement=(tag,attrs)=>({append(){},remove(){},attrs});
const svg={append(){effects++;}};
const elements=new Map(after.cells.map(c=>[c.id,{classList:{add(){}}}]));
""" + source_js[start:end] + r"""
animateHexxagonTransition({id:'A',revision:0},before,svg,elements);
assert.equal(effects,0);
animateHexxagonTransition({id:'A',revision:1},after,svg,elements);
assert.equal(effects,1);
animateHexxagonTransition({id:'A',revision:1},after,svg,elements);
assert.equal(effects,1);
reduced=true;
animateHexxagonTransition({id:'B',revision:0},before,svg,elements);
animateHexxagonTransition({id:'B',revision:1},after,svg,elements);
assert.equal(effects,1);
"""
        subprocess.run(['node','-e',script], input=json.dumps([self.game.view_model(before,self.players), self.game.view_model(after,self.players)]),
                       text=True,capture_output=True,check=True)

    def test_board_catalog_and_snapshot(self):
        self.assertEqual(len(CELLS), 58)
        self.assertEqual(self.state['board'].count(1), 3)
        self.assertEqual(self.state['board'].count(2), 3)
        self.assertEqual(builtin_registry(False).by_id(8).key, 'hexxagon')
        self.assertEqual(len(self.game.encode_state(self.state)), 15)
        self.assertEqual(self.game.decode_state(self.game.encode_state(self.state)), self.state)

    def test_clone_jump_and_capture(self):
        for length in (1, 2):
            board = [0] * 58
            source = INDEX[(-2, 0)]
            target = INDEX[(-2 + length, 0)]
            victim = next(i for i in range(58) if i not in (source, target) and distance(i, target) == 1 and distance(i, source) > 1)
            board[source], board[victim], board[INDEX[(4, 0)]] = 1, 2, 2
            state = dict(self.state, board=board)
            action = pack_move(source, target)
            self.assertEqual(len(action), length)
            result = self.game.apply_action(state, self.players, 'A', action)
            self.assertEqual(result['board'][victim], 1)
            self.assertEqual(result['board'][target], 1)
            self.assertEqual(result['board'][source], 1 if length == 1 else 0)
            self.assertEqual(state['board'], board)

    def test_blocked_opponent_fills_empty_cells(self):
        board = [1] * 58
        board[0] = 2
        target = next(i for i in range(58) if distance(i, 0) > 2)
        board[target] = 0
        source = next(i for i in range(58) if board[i] == 1 and distance(i, target) == 1)
        result = self.game.apply_action(dict(self.state, board=board), self.players, 'A', pack_move(source, target))
        self.assertTrue(result['finished'])
        self.assertEqual(result['winner'], 0)
        self.assertNotIn(0, result['board'])

    def test_tied_completed_snapshot(self):
        state = dict(self.state, board=[1] * 29 + [2] * 29, finished=True)
        restored = self.game.decode_state(self.game.encode_state(state))
        self.assertEqual(restored, state)
        view = self.game.view_model(restored, self.players)
        self.assertTrue(view['draw'])
        self.assertEqual(view['scores'], {'A': 29, 'B': 29})
        self.assertEqual(view['legal_moves'], [])

    def test_invalid_moves_and_snapshots(self):
        for action in (b'', b'\xff', b'\x80\x00', b'\x90\x00', b'\x00\x00\x00'):
            with self.assertRaises(ValueError):
                self.game.apply_action(self.state, self.players, 'A', action)
        a, b = moves(self.state['board'], 1)[0]
        with self.assertRaises(ValueError):
            self.game.apply_action(self.state, self.players, 'B', pack_move(a, b))
        for payload in (bytes(14), bytes(15), b'\xff' * 15):
            with self.assertRaises(ValueError): self.game.decode_state(payload)

    def test_complete_seeded_games_and_recovery(self):
        for seed in range(6):
            rng = random.Random(seed)
            state = self.game.new_state(self.players)
            for turn in range(1000):
                if state['finished']: break
                a, b = rng.choice(moves(state['board'], state['turn'] + 1))
                action = pack_move(a, b)
                result = self.game.apply_action(state, self.players, self.players[state['turn']], action)
                recovered = self.game.decode_state(self.game.encode_state(result))
                self.assertEqual(result, recovered)
                state = recovered
            self.assertTrue(state['finished'])
            self.assertIsNone(self.game.current_player(state, self.players))
