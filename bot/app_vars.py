from __future__ import annotations
import os
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.translator import Translator

app_name = "TeamTalk Player"
app_version = "3.0.0"
client_name = "TeamTalk Player " + app_version
about_text: Callable[[Translator], str] = lambda translator: translator.translate(
    """\
TeamTalk Player {version}: plays music from YouTube, Apple Music and internet radio in TeamTalk.
Based on TTMediaBot by Amir Gumerov and contributors (MIT License).
Home page: https://github.com/mad-gamer26/TeamTalk-Player\
""".format(version=app_version)
)


def is_player_client(client_name: str) -> bool:
    """Another player (this one or the original TTMediaBot): its messages are ignored, so bots never answer each other."""
    return client_name.startswith(("TeamTalk Player", "TeamTalk-Player", "TTMediaBot"))


fallback_service = "yt"
loop_timeout = 0.01
max_message_length = 256
recents_max_lenth = 32
tt_event_timeout = 2

directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
