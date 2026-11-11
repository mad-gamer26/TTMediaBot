import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdk  # noqa: E402,F401

from bot.services import am  # noqa: E402


def apple(title, artist, duration, album=""):
    return {"title": title, "artist": artist, "duration": duration, "album": album}


class MatchingTest(unittest.TestCase):
    def test_exact_recording_is_accepted(self):
        song = apple("Bohemian Rhapsody", "Queen", 354.32, "A Night At The Opera")
        info = {
            "title": "Bohemian Rhapsody",
            "track": "Bohemian Rhapsody",
            "artists": ["Queen"],
            "channel": "Queen Official",
            "duration": 354,
        }
        self.assertTrue(am.verify(song, info))

    def test_cover_by_another_artist_is_rejected(self):
        song = apple("Hey Jude", "The Beatles", 431.3)
        info = {
            "title": "Hey Jude",
            "track": "Hey Jude",
            "artists": ["Leo Sayer"],
            "channel": "Leo Sayer",
            "duration": 431,
        }
        self.assertFalse(am.verify(song, info))

    def test_wrong_length_is_rejected(self):
        song = apple("bad guy", "Billie Eilish", 194.1)
        info = {
            "title": "bad guy",
            "artists": ["Billie Eilish"],
            "channel": "Billie Eilish",
            "duration": 225,
        }
        self.assertFalse(am.verify(song, info))

    def test_relaxed_allows_longer_video_from_artist_channel_only(self):
        song = apple("bad guy", "Billie Eilish", 194.1)
        official = {"title": "Billie Eilish - bad guy", "channel": "Billie Eilish", "duration": 206}
        reupload = {"title": "Billie Eilish - bad guy", "channel": "DopeMusic", "duration": 206}
        self.assertFalse(am.verify(song, official))
        self.assertTrue(am.verify(song, official, relaxed=True))
        self.assertFalse(am.verify(song, reupload, relaxed=True))

    def test_live_version_rejected_unless_apple_title_is_live(self):
        studio = apple("Choosin' Texas", "Ella Langley", 232.2)
        live_info = {
            "title": "Ella Langley – “Choosin' Texas” | Live at CMA Awards 2025",
            "channel": "Ella Langley",
            "duration": 232,
        }
        self.assertFalse(am.verify(studio, live_info))
        live = apple("Love Of My Life (Live In Argentina / June 1979)", "Queen", 222)
        info = {
            "title": "Love Of My Life (Live In Argentina / June 1979)",
            "artists": ["Queen"],
            "channel": "Queen Official",
            "duration": 224,
        }
        self.assertTrue(am.verify(live, info))

    def test_featured_artists_and_remaster_suffix(self):
        song = apple(
            "Under Pressure (Single Version) [2017 Remastered Version]",
            "Queen & David Bowie",
            249,
        )
        info = {
            "title": "Under Pressure (2017 Remaster)",
            "artists": ["Queen", "David Bowie"],
            "channel": "Queen Official",
            "duration": 249,
        }
        self.assertTrue(am.verify(song, info))

    def test_accents_are_ignored(self):
        song = apple("Blinding Lights (Remix)", "The Weeknd & ROSALÍA", 216)
        info = {
            "title": "Blinding Lights (Remix)",
            "artists": ["The Weeknd", "Rosalia"],
            "channel": "The Weeknd",
            "duration": 216,
        }
        self.assertTrue(am.verify(song, info))

    def test_search_result_scoring_prefers_topic_channel(self):
        song = apple("Mr. Brightside", "The Killers", 224)
        topic = {"title": "Mr. Brightside", "channel": "The Killers - Topic", "duration": 223}
        lyric = {"title": "The Killers - Mr. Brightside (Lyrics)", "channel": "7clouds", "duration": 224}
        karaoke = {"title": "Mr. Brightside (Karaoke)", "channel": "The Killers", "duration": 224}
        self.assertGreater(am.score_search_result(song, topic), am.score_search_result(song, lyric))
        self.assertIsNone(am.score_search_result(song, karaoke))

    def test_clean_apple_music_url(self):
        self.assertEqual(
            am.clean_apple_music_url(
                "https://music.apple.com/us/album/x/1844932149?i=1844932150&uo=4"
            ),
            "https://music.apple.com/us/album/x/1844932149?i=1844932150",
        )
        self.assertIsNone(am.clean_apple_music_url("https://example.com/us/album/x/1"))


if __name__ == "__main__":
    unittest.main()
