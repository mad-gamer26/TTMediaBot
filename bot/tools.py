"""Where the helper programs are: ffmpeg (decoding), yt-dlp (YouTube) and
deno (which yt-dlp needs for YouTube). The installers put all three into the
player's own Python environment (imageio-ffmpeg, yt-dlp and deno from pip),
so nothing else has to be installed on the system; a program set in the
environment, or on PATH, is used otherwise."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional

_BIN = os.path.dirname(sys.executable)


def _in_environment(name: str) -> Optional[str]:
    for candidate in (name, name + ".exe"):
        path = os.path.join(_BIN, candidate)
        if os.path.isfile(path):
            return path
    return None


def prepare_path() -> None:
    """Puts the environment's programs first on PATH, so yt-dlp finds deno."""
    path = os.environ.get("PATH", "")
    if _BIN not in path.split(os.pathsep):
        os.environ["PATH"] = _BIN + os.pathsep + path


def ffmpeg() -> str:
    configured = os.environ.get("TEAMTALK_PLAYER_FFMPEG")
    if configured:
        return configured
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def ytdlp() -> str:
    return (
        os.environ.get("TEAMTALK_PLAYER_YTDLP")
        or os.environ.get("TTMB_YTDLP")
        or _in_environment("yt-dlp")
        or shutil.which("yt-dlp")
        or "yt-dlp"
    )


def ytdlp_is_ours() -> bool:
    """Whether yt-dlp is the copy in the player's environment (which the
    player keeps up to date itself)."""
    return ytdlp() == _in_environment("yt-dlp")
