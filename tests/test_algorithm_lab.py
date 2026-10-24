import os
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

BASE = Path(__file__).resolve().parents[1]


class AlgorithmLabTests(unittest.TestCase):
    def start(self):
        self.assertTrue((BASE/'algorithm_lab.py').exists(), 'algorithm lab is not implemented')
        with patch.dict(os.environ, {'PHONE_CATALOG_PATH': str(BASE/'data/catalog.sqlite')}):
            at = AppTest.from_file(str(BASE/'algorithm_lab.py'), default_timeout=60).run()
        self.assertFalse(at.exception)
        return at

    def confirm(self, at):
        at.checkbox(key='lab_confirm').check()
        at.button(key='lab_start').click().run()
        self.assertFalse(at.exception)

    def test_confirmation_answer_undo_and_export_use_actual_catalog(self):
        at = self.start()
        at.button(key='lab_start').click().run()
        self.assertTrue(at.warning)
        self.assertNotIn('lab_session', at.session_state)
        self.confirm(at)
        session = at.session_state['lab_session']
        self.assertGreater(len(session.problem.ids), 2)
        self.assertNotEqual(session.problem.dataset_version, 'legacy-offline-fixture')
        initial = session.report()['session_sha256']
        self.assertIsNotNone(session.question())
        at.radio(key='lab_answer').set_value('a')
        at.button(key='lab_submit').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.session_state['lab_session'].history), 1)
        if at.session_state['lab_session'].question() is not None:
            self.assertIsNone(at.radio(key='lab_answer').value,
                              'a previous selection must not preselect the next answer')
        at.button(key='lab_undo').click().run()
        self.assertEqual(at.session_state['lab_session'].report()['session_sha256'], initial)
        at.selectbox(key='lab_output').select('vikor').run()
        self.assertEqual(at.session_state['lab_session'].report()['session_sha256'], initial)
        self.assertTrue(at.dataframe)

    def test_changed_budget_invalidates_session_and_confirmation(self):
        at = self.start()
        self.confirm(at)
        at.number_input(key='lab_max_price').set_value(5000).run()
        self.assertNotIn('lab_session', at.session_state)
        self.assertFalse(at.checkbox(key='lab_confirm').value)

    def test_zero_weights_are_reported_without_crash(self):
        at = self.start()
        for slider in at.slider:
            if slider.key and slider.key.startswith('lab_w_'):
                slider.set_value(0)
        at.run()
        self.confirm(at)
        self.assertTrue(at.error)
        self.assertNotIn('lab_session', at.session_state)


if __name__ == '__main__':
    unittest.main()
