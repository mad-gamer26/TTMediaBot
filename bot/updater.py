"""Keeps yt-dlp current. YouTube changes often and an old yt-dlp soon stops
working, so when yt-dlp is the copy in the player's own environment, it (and
deno and the yt-dlp JavaScript helper) is updated with pip once a day."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

from bot import tools

DAY = 24 * 3600
PACKAGES = ["yt-dlp[default]", "deno"]


def _update() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "--disable-pip-version-check", *PACKAGES],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode == 0:
        logging.info("yt-dlp is up to date")
    else:
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        logging.warning("yt-dlp could not be updated: %s", lines[-1] if lines else result.returncode)


def start(stop: threading.Event) -> None:
    if not tools.ytdlp_is_ours():
        logging.info("yt-dlp is %s (kept up to date outside the player)", tools.ytdlp())
        return

    def run() -> None:
        while not stop.wait(60):  # first check a minute after starting
            try:
                _update()
            except Exception as e:
                logging.warning("yt-dlp could not be updated: %s", e)
            if stop.wait(DAY):
                return

    threading.Thread(target=run, name="Updater", daemon=True).start()
