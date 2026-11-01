"""Compare the actual browser preview with the authoritative integer physics."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from radio_arcade.games.scorched_earth import ScorchedEarthGame, MAPS, _trace_shot, _wind


@unittest.skipUnless(shutil.which('node'), 'Node is required for browser preview parity')
class PreviewParityTests(unittest.TestCase):
    def test_browser_matches_engine(self):
        source = (Path(__file__).parents[1] / 'radio_arcade/web_assets/app.js').read_text()
        start = source.index('function traceTankPreview(')
        end = source.index('\nfunction animateTankShot', start)
        game = ScorchedEarthGame(); players = ('A', 'B')
        cases, expected = [], []
        for map_id in range(5):
            state = game.new_state(players)
            state.update(map=map_id, terrain=list(MAPS[map_id][1]))
            view = game.view_model(state, players)
            for shooter in range(2):
                for weapon in (0, 7):
                    for angle in (0, 45, 90):
                        for power in (25, 50, 100):
                            cases.append([view, view['tanks'][shooter], view['weapons'][weapon], angle, power])
                            shot = _trace_shot(state['terrain'], state['x'], shooter, weapon, angle, _wind(map_id, 0), power)
                            expected.append([[x / 1000, y / 1000] for x, y in shot['path']])
        script = source[start:end] + '\nconst fs=require("fs");const cases=JSON.parse(fs.readFileSync(0,"utf8"));console.log(JSON.stringify(cases.map(args=>traceTankPreview(...args))));'
        result = subprocess.run(['node', '-e', script], input=json.dumps(cases), text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), expected)
