import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdk  # noqa: E402,F401

from bot.config import ConfigManager  # noqa: E402
from bot.player.engine import atempo_chain, gain_for  # noqa: E402


class VolumeTest(unittest.TestCase):
    def test_mpv_scale(self):
        self.assertEqual(gain_for(100), 1.0)
        self.assertAlmostEqual(gain_for(50), 0.125)
        self.assertEqual(gain_for(0), 0.0)
        self.assertEqual(gain_for(-5), 0.0)


class SpeedTest(unittest.TestCase):
    def product(self, filters):
        p = 1.0
        for f in filters:
            p *= float(f.split("=")[1])
        return p

    def test_chain_covers_quarter_to_four(self):
        for speed in (0.25, 0.4, 0.5, 1.5, 2.0, 3.0, 4.0):
            chain = atempo_chain(speed)
            self.assertAlmostEqual(self.product(chain), speed, places=5)
            for f in chain:
                self.assertTrue(0.5 <= float(f.split("=")[1]) <= 2.0, f)
        self.assertEqual(atempo_chain(1.0), [])


class MigrationTest(unittest.TestCase):
    def test_old_settings_are_dropped_and_the_file_is_private(self):
        old = {"config_version": 2, "logger": {}, "servers": {"home": {
            "general": {"language": "ru"}, "sound_devices": {"output_device_name": "pulse/home"},
            "shortening": {"shorten_links": True}, "player": {"player_options": {"x": 1}, "default_volume": 40},
            "teamtalk": {"hostname": "example.org"}}}}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w") as f:
                json.dump(old, f)
            manager = ConfigManager(path)
            manager.close()
            with open(path) as f:
                data = json.load(f)
            server = data["servers"]["home"]
            self.assertEqual(data["config_version"], 3)
            for gone in ("sound_devices", "shortening"):
                self.assertNotIn(gone, server)
            self.assertNotIn("language", server["general"])
            self.assertNotIn("player_options", server["player"])
            self.assertEqual(server["player"]["default_volume"], 40)
            self.assertEqual(manager.config.servers["home"].teamtalk.hostname, "example.org")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
