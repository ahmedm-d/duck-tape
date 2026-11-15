import unittest
from api import quote


class QuoteTests(unittest.TestCase):
    def test_default_quote(self):
        self.assertEqual(quote({}), {'quantity': 1, 'total': 12})
