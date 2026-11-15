import unittest
from client import make_session


class ClientTests(unittest.TestCase):
    def test_session_defaults_to_certificate_verification(self):
        with make_session() as session:
            self.assertTrue(session.verify)
