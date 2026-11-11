"""Runs the server's yt-dlp command instead of importing yt-dlp.

Running the command means yt-dlp (which YouTube makes change often) is
updated on its own, with yt-dlp -U or the system's package manager, without
updating or restarting the player; its own configuration file and cookies
apply too.
"""

from __future__ import annotations
import json
import logging
import subprocess
from typing import Any, Dict, List, Optional

from bot import errors

AUDIO_FORMAT = "m4a/bestaudio/best[protocol!=m3u8_native]/best"
TIMEOUT = 90


def command() -> str:
    """The yt-dlp executable (see bot/tools.py)."""
    from bot import tools

    return tools.ytdlp()


def _common(cookiefile: str) -> List[str]:
    options = ["--no-warnings", "--no-progress", "--socket-timeout", "5"]
    if cookiefile:
        # Only when a server is set up with its own cookies file; otherwise the
        # command uses the central cookies.
        options += ["--cookies", cookiefile]
    return options


def _run(args: List[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [command()] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise errors.ServiceError("yt-dlp took too long")
    except OSError as e:
        raise errors.ServiceError("yt-dlp could not be started: {}".format(e))


def _failure(result: subprocess.CompletedProcess) -> errors.ServiceError:
    lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
    problems = [line for line in lines if line.startswith("ERROR")]
    message = (problems or lines or ["yt-dlp failed"])[-1]
    logging.warning("yt-dlp failed: %s", message)
    return errors.ServiceError(message.replace("ERROR: ", "", 1))


def extract(url: str, flat: bool = False, playlist_end: Optional[int] = None, cookiefile: str = "") -> Dict[str, Any]:
    """Information about a URL (or search such as "ytsearch5:..."), like YoutubeDL.extract_info."""
    # The audio format is chosen even for flat lookups: a single video then
    # comes back ready to play.
    args = _common(cookiefile) + ["--dump-single-json", "--format", AUDIO_FORMAT]
    if flat:
        args.append("--flat-playlist")
    else:
        args.append("--no-playlist")
    if playlist_end:
        args += ["--playlist-end", str(playlist_end)]
    result = _run(args + ["--", url])
    if result.returncode != 0:
        raise _failure(result)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise errors.ServiceError("yt-dlp returned something that is not JSON")


def download(url: str, file_path: str, cookiefile: str = "") -> None:
    args = _common(cookiefile) + [
        "--format", AUDIO_FORMAT, "--no-playlist", "--force-overwrites",
        "--output", file_path, "--", url,
    ]
    result = _run(args, timeout=900)
    if result.returncode != 0:
        raise _failure(result)
