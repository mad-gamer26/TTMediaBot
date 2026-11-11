from __future__ import annotations
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Bot

from youtubesearchpython import VideosSearch

from bot.config.models import YtModel

from bot.player.enums import TrackType
from bot.player.track import Track
from bot.services import cookies_file, Service as _Service
from bot import errors, ytdlp

# Looked-up details are reused for a while, so that playing a link does not
# ask yt-dlp twice. Stream URLs stay valid for hours; this is far shorter.
FRESH_SECONDS = 300
# Large parts of yt-dlp's output that tracks (and the saved recents) don't need.
BULKY_KEYS = (
    "formats", "thumbnails", "automatic_captions", "subtitles", "heatmap",
    "requested_formats", "requested_subtitles", "chapters", "description",
)


def slim(info: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in info.items() if key not in BULKY_KEYS}


def page_url(url: str, info: Optional[Dict[str, Any]]) -> str:
    info = info or {}
    return url or info.get("webpage_url") or info.get("original_url") or info.get("url") or ""


class YtService(_Service):
    def __init__(self, bot: Bot, config: YtModel):
        self.bot = bot
        self.config = config
        self.name = "yt"
        self.hostnames = []
        self.is_enabled = self.config.enabled
        self.error_message = ""
        self.warning_message = ""
        self.help = ""
        self.hidden = False

    def initialize(self):
        pass

    @property
    def cookiefile(self) -> str:
        """The YouTube cookies file: this server's own, else cookies.txt next
        to the configuration file, else none (yt-dlp's own configuration)."""
        return cookies_file(self.bot)

    def download(self, track: Track, file_path: str) -> None:
        url = page_url("", track.extra_info)
        if not track.extra_info or not url:
            super().download(track, file_path)
            return
        ytdlp.download(url, file_path, self.cookiefile)

    def get(
        self,
        url: str,
        extra_info: Optional[Dict[str, Any]] = None,
        process: bool = False,
    ) -> List[Track]:
        target = page_url(url, extra_info)
        if not target:
            raise errors.InvalidArgumentError()
        if process:
            info = extra_info or {}
            fresh = info.get("_looked_up", 0) > time.time() - FRESH_SECONDS and "url" in info
            if not fresh:
                info = ytdlp.extract(target, cookiefile=self.cookiefile)
            return [self._stream_track(info)]
        info = ytdlp.extract(target, flat=True, cookiefile=self.cookiefile)
        info_type = info.get("_type")
        if info_type == "url" and info.get("url") and info.get("url") != target:
            return self.get(info["url"], process=False)
        if info_type == "playlist":
            tracks: List[Track] = []
            for entry in info.get("entries") or []:
                entry_url = page_url("", entry)
                if not entry_url:
                    continue
                tracks.append(
                    Track(
                        service=self.name,
                        url=entry_url,
                        name=entry.get("title") or "",
                        type=TrackType.Dynamic,
                        extra_info={"webpage_url": entry_url, "title": entry.get("title")},
                        server=self.bot.name,
                    )
                )
            if not tracks:
                raise errors.NothingFoundError("")
            return tracks
        details = slim(info)
        details["_looked_up"] = time.time()
        return [
            Track(
                service=self.name,
                url=page_url("", info),
                extra_info=details,
                type=TrackType.Dynamic,
                server=self.bot.name,
            )
        ]

    def _stream_track(self, stream: Dict[str, Any]) -> Track:
        if "url" not in stream:
            raise errors.ServiceError("yt-dlp found no playable audio")
        title = stream.get("title") or ""
        if stream.get("uploader"):
            title += " - {}".format(stream["uploader"])
        return Track(
            service=self.name,
            url=stream["url"],
            name=title,
            format=stream.get("ext", ""),
            type=TrackType.Live if stream.get("is_live") else TrackType.Default,
            extra_info=slim(stream),
            server=self.bot.name,
        )

    def search(self, query: str) -> List[Track]:
        search = VideosSearch(query, limit=300).result()
        if search["result"]:
            tracks: List[Track] = []
            for video in search["result"]:
                track = Track(
                    service=self.name,
                    url=video["link"],
                    type=TrackType.Dynamic,
                    server=self.bot.name,
                )
                tracks.append(track)
            return tracks
        else:
            raise errors.NothingFoundError("")
