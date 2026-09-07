import unittest

class ReviewStateTests(unittest.TestCase):
    def test_submission_uses_version_originally_displayed(self):
        from review_ui.state import displayed_version
        state={}
        self.assertEqual(displayed_version(state,'D1',1),1)
        self.assertEqual(displayed_version(state,'D1',2),1)
        state.clear()
        self.assertEqual(displayed_version(state,'D1',2),2)
