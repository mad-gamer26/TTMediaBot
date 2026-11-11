import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdk  # noqa: E402,F401

from bot.setup import parse_tt_link, suggest_name  # noqa: E402
from bot.web import auth  # noqa: E402


class LinkTest(unittest.TestCase):
    def test_full_link(self):
        v = parse_tt_link("tt://tt.example.org?tcpport=7777&udpport=7778&encrypted=true&username=bot&password=p%40ss&channel=/Music/&chanpasswd=x")
        self.assertEqual((v["host"], v["tcpport"], v["udpport"], v["encrypted"]), ("tt.example.org", 7777, 7778, True))
        self.assertEqual((v["username"], v["password"], v["channel"], v["chanpasswd"]), ("bot", "p@ss", "/Music/", "x"))

    def test_defaults_and_host_port(self):
        v = parse_tt_link("tt://example.org")
        self.assertEqual((v["tcpport"], v["udpport"], v["encrypted"], v["channel"]), (10333, 10333, False, ""))
        v = parse_tt_link("tt://example.org:7777")
        self.assertEqual((v["host"], v["tcpport"], v["udpport"]), ("example.org", 7777, 7777))

    def test_not_a_link(self):
        self.assertIsNone(parse_tt_link("https://example.org"))
        self.assertIsNone(parse_tt_link("tt://"))

    def test_names(self):
        self.assertEqual(suggest_name("tt.example.org", {}), "example")
        self.assertEqual(suggest_name("10.0.0.5", {}), "server-10-0-0-5")
        self.assertEqual(suggest_name("example.org", {"example": 1}), "example-2")


class PasswordTest(unittest.TestCase):
    def test_hash_and_check(self):
        stored = auth.hash_password("correct horse")
        self.assertTrue(auth.check_password("correct horse", stored))
        self.assertFalse(auth.check_password("wrong", stored))
        self.assertFalse(auth.check_password("x", "garbage"))
        self.assertNotIn("correct", stored)

    def test_basic_header(self):
        header = "Basic " + base64.b64encode(b"anyone:se:cret").decode()
        self.assertEqual(auth.password_from_header(header), "se:cret")
        self.assertEqual(auth.password_from_header("Bearer x"), "")
        self.assertEqual(auth.password_from_header(""), "")

    def test_loopback(self):
        for host in ("127.0.0.1", "::1", "localhost", "127.0.1.1"):
            self.assertTrue(auth.is_loopback(host), host)
        for host in ("0.0.0.0", "192.168.1.2", "::"):
            self.assertFalse(auth.is_loopback(host), host)


if __name__ == "__main__":
    unittest.main()
