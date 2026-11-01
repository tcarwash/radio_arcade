import unittest
from radio_arcade.presentation import scoreboard, result_title, CompletionTracker


class PresentationTests(unittest.TestCase):
    def session(self, **changes):
        return dict(id='A', status='active', players=['A', 'B', 'C', 'D'],
                    current_player='B', view={}, **changes)

    def test_score_types_and_multiplayer(self):
        session = self.session()
        data = scoreboard(session)
        self.assertEqual(len(data['entries']), 4)
        self.assertTrue(data['entries'][1]['active'])
        self.assertEqual(data['entries'][0]['value'], '—')
        session['view'] = {'scores': {'A': -5, 'B': 42, 'C': 0, 'D': 7}}
        self.assertEqual(scoreboard(session)['entries'][0]['value'], '-5')
        session['view'] = {'tanks': [{'player': p, 'hp': 80} for p in session['players']]}
        self.assertEqual(scoreboard(session)['entries'][0]['unit'], 'HP')

    def test_outcomes(self):
        session = self.session(); session.update(status='finished', view={'winner': 'B'})
        self.assertEqual(result_title(session, 'B'), 'VICTORY')
        self.assertEqual(result_title(session, 'A'), 'DEFEAT')
        self.assertEqual(result_title(session, 'A', practice=True), 'B WINS')
        self.assertEqual(result_title(session, 'A', observer=True), 'B WINS')
        self.assertEqual(scoreboard(session)['entries'][0]['value'], 'LOSS')
        session['view'] = {}
        self.assertEqual(result_title(session, 'A'), 'DRAW')
        self.assertEqual(scoreboard(session)['entries'][0]['value'], 'DRAW')

    def test_completion_only_once_after_live_transition(self):
        tracker = CompletionTracker(); session = self.session()
        self.assertFalse(tracker.observe(session))
        session['status'] = 'finished'
        self.assertTrue(tracker.observe(session))
        self.assertFalse(tracker.observe(session))
        self.assertFalse(CompletionTracker().observe(session))
