"""Makes the TeamTalk SDK importable as TeamTalkPy for the unit tests (the
bot's modules import it under that name; teamtalk_player.py does the same)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "TeamTalkPy" not in sys.modules:
    import teamtalk_sdk

    _sdk = teamtalk_sdk.load_teamtalk_sdk()
    _sdk.TeamTalk5 = _sdk
    sys.modules["TeamTalkPy"] = _sdk
