import unittest
from service import greet, average

class ServiceTests(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet(), "Hello world")
    def test_average(self):
        self.assertEqual(average([2, 4]), 3)
