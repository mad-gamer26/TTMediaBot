"""Apple Music: Apple's catalog for finding songs, YouTube for the audio.

Searches use Apple's public iTunes Search API, the same request the Apple
Music importer on this server makes. Links to music.apple.com songs, albums,
artists and playlists are read through the iTunes Lookup API, or from the
playlist page itself for playlists.

Apple's full-length audio is DRM-protected, so each song is played from the
matching recording on YouTube, which is checked against Apple's title, artist
and length. Apple's official 30-second preview is the optional fallback.
"""

from __future__ import annotations
import json
import logging
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
from bot import errors, ytdlp
from bot.config.models import AmModel
from bot.player.enums import TrackType
from bot.player.track import Track
from bot.services import Service as _Service

if TYPE_CHECKING:
    from bot import Bot


ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
SEARCH_LIMIT = 25
HTTP_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
APPLE_HOSTS = ["music.apple.com", "geo.music.apple.com", "itunes.apple.com"]

# How many YouTube Music results are looked up in full (in parallel) before
# falling back to a regular YouTube search, and how many results each search reads.
YTM_CHECKED = 4
YTM_KEPT = 8
YT_CHECKED = 3
YT_SEARCHED = 8

# Words that mark a different version of a song. A YouTube result containing
# one of these is rejected unless Apple's title or album has it too.
VARIANT_WORDS = [
    "live",
    "cover",
    "remix",
    "karaoke",
    "instrumental",
    "sped up",
    "slowed",
    "nightcore",
    "8d",
    "reverb",
    "acoustic",
    "demo",
    "mashup",
    "reaction",
    "tutorial",
    "lesson",
    "1 hour",
    "extended",
    "concert",
    "rehearsal",
    "a cappella",
    "acapella",
]


# ---------------------------------------------------------------- matching


def _slim(info: Dict[str, Any]) -> Dict[str, Any]:
    from bot.services.yt import slim

    return slim(info)


def normalize(text: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_DASH_SUFFIX = re.compile(r"\s[-–—]\s.*$")


def core_title(title: str) -> str:
    """The song title without "(feat. ...)", "- Remastered 2011" and similar."""
    return normalize(_DASH_SUFFIX.sub("", _BRACKETS.sub("", title))) or normalize(
        title
    )


def artist_names(artist: str) -> List[str]:
    names = re.split(r",|&| x | feat\.? | featuring | with ", artist, flags=re.I)
    names = [normalize(name) for name in names] + [normalize(artist)]
    return [name for name in dict.fromkeys(names) if name]


def _clean_channel(name: str) -> str:
    name = normalize(name)
    for suffix in (" topic", " vevo", " official", " music"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.replace("vevo", "").strip()


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(^| )" + re.escape(word) + r"( |$)", text) is not None


def variant_conflict(apple: Dict[str, Any], youtube_title: str) -> bool:
    apple_text = normalize(apple.get("title", "") + " " + apple.get("album", ""))
    youtube_text = normalize(youtube_title)
    return any(
        _has_word(youtube_text, word) and not _has_word(apple_text, word)
        for word in VARIANT_WORDS
    )


def title_matches(apple: Dict[str, Any], *youtube_titles: Optional[str]) -> bool:
    wanted = core_title(apple.get("title", "")).split()
    if not wanted:
        return False
    for youtube_title in youtube_titles:
        words = set(normalize(youtube_title).split())
        if words and sum(word in words for word in wanted) / len(wanted) >= 0.6:
            return True
    return False


def artist_matches(apple: Dict[str, Any], *youtube_artists: Optional[str]) -> bool:
    wanted = artist_names(apple.get("artist", ""))
    for youtube_artist in youtube_artists:
        found = _clean_channel(youtube_artist or "")
        if found and any(
            name in found or (len(found) >= 3 and found in name) for name in wanted
        ):
            return True
    return False


# A music video on the artist's own channel often runs longer than the song
# because of an intro or outro; it is accepted only when nothing matches exactly.
RELAXED_EXTRA_SECONDS = 45.0


def duration_matches(
    apple: Dict[str, Any], seconds: Optional[float], relaxed: bool = False
) -> bool:
    expected = apple.get("duration")
    if not expected or not seconds:
        return True
    tolerance = max(7.0, 0.04 * float(expected))
    difference = float(seconds) - float(expected)
    if relaxed:
        return -tolerance <= difference <= RELAXED_EXTRA_SECONDS
    return abs(difference) <= tolerance


def verify(apple: Dict[str, Any], info: Dict[str, Any], relaxed: bool = False) -> bool:
    """Whether a fully looked-up YouTube video is the same recording as Apple's song.

    The relaxed check allows a longer video, but only from the artist's own channel.
    """
    youtube_title = info.get("track") or info.get("title") or ""
    if relaxed:
        artists = [info.get("channel"), info.get("uploader")]
    else:
        artists = list(info.get("artists") or [])
        artists += [info.get("artist"), info.get("channel"), info.get("uploader")]
    return (
        duration_matches(apple, info.get("duration"), relaxed)
        and title_matches(apple, info.get("track"), info.get("title"))
        and artist_matches(apple, *artists)
        and not variant_conflict(apple, youtube_title)
        and not variant_conflict(apple, info.get("title") or "")
    )


def score_search_result(apple: Dict[str, Any], entry: Dict[str, Any]) -> Optional[float]:
    """Score a regular YouTube search result, or None if it is not the song."""
    title = entry.get("title") or ""
    channel = entry.get("channel") or entry.get("uploader") or ""
    if variant_conflict(apple, title) or not title_matches(apple, title):
        return None
    if not artist_matches(apple, channel) and not any(
        name in normalize(title) for name in artist_names(apple.get("artist", ""))
    ):
        return None
    expected = apple.get("duration")
    seconds = entry.get("duration")
    if expected and seconds:
        difference = abs(float(seconds) - float(expected))
        if duration_matches(apple, seconds):
            score = 50.0 - difference * 3
        elif duration_matches(apple, seconds, relaxed=True) and artist_matches(
            apple, channel
        ):
            # Kept for the relaxed last-resort check.
            score = 5.0 - difference / 10
        else:
            return None
    else:
        score = 20.0
    if normalize(channel).endswith(" topic"):
        score += 30
    elif artist_matches(apple, channel):
        score += 20
    if "lyric" in normalize(title):
        score -= 5
    return score


# ------------------------------------------------------------- match cache


class MatchCache:
    """Apple song ID -> YouTube candidates and the chosen one, shared by all servers."""

    _instances: Dict[str, "MatchCache"] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def open(cls, path: str) -> "MatchCache":
        with cls._instances_lock:
            if path not in cls._instances:
                cls._instances[path] = cls(path)
            return cls._instances[path]

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        try:
            with open(path, "r", encoding="UTF-8") as f:
                self._data: Dict[str, Dict[str, Any]] = json.load(f)
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError):
            logging.warning("Unreadable Apple Music match cache %s, starting over", path)
            self._data = {}

    def get(self, song_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._data.get(song_id)
            return dict(entry) if entry else None

    def set(self, song_id: str, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._data[song_id] = dict(entry, time=int(time.time()))
            self._write()

    def forget(self, song_id: str) -> bool:
        with self._lock:
            if self._data.pop(song_id, None) is None:
                return False
            self._write()
            return True

    def entries(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._data.items()}

    def _write(self) -> None:
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="UTF-8") as f:
            json.dump(self._data, f, ensure_ascii=False)
        os.replace(temp_path, self.path)


# ----------------------------------------------------------------- service


def clean_apple_music_url(url: Optional[str]) -> Optional[str]:
    """A canonical music.apple.com URL without iTunes tracking parameters."""
    parts = urlsplit(str(url or ""))
    if parts.netloc not in APPLE_HOSTS:
        return None
    query = [(key, value) for key, value in parse_qsl(parts.query) if key == "i"]
    return urlunsplit(("https", "music.apple.com", parts.path, urlencode(query), ""))


class AmService(_Service):
    def __init__(self, bot: Bot, config: AmModel):
        self.bot = bot
        self.config = config
        self.name = "am"
        self.hostnames = list(APPLE_HOSTS)
        self.is_enabled = self.config.enabled
        self.error_message = ""
        self.warning_message = ""
        self.hidden = False
        self.help = bot.translator.translate(
            "Apple Music. Search with p QUERY, or play a music.apple.com song, album, artist or playlist link with u URL. "
            "Each song plays from its matching recording on YouTube; send alt to try the next-best match for the current song."
        )

    def initialize(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        # yt-dlp runs as the server's own command, which applies the central
        # cookies itself; a server-specific cookies file is only passed if set.
        from bot.services import cookies_file

        self._cookiefile = cookies_file(self.bot)
        self._matches = MatchCache.open(
            os.path.join(self.bot.config_manager.config_dir, "am_matches.json")
        )

    # Apple catalog --------------------------------------------------------

    @property
    def country(self) -> str:
        return (self.config.country or "us").strip().lower()

    def _itunes(self, url: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            response = self._session.get(url, params=params, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise errors.ServiceError("Could not reach Apple Music: {}".format(e))
        if response.status_code != 200:
            raise errors.ServiceError(
                "Apple Music answered with status {}".format(response.status_code)
            )
        try:
            results = response.json().get("results", [])
        except ValueError:
            raise errors.ServiceError("Apple Music returned an invalid response")
        return [item for item in results if isinstance(item, dict)]

    def _meta_from_itunes(self, item: Dict[str, Any], country: str) -> Optional[Dict[str, Any]]:
        if item.get("wrapperType") != "track" or item.get("kind") != "song":
            return None
        millis = item.get("trackTimeMillis")
        return {
            "id": str(item.get("trackId")),
            "title": str(item.get("trackName") or "Untitled"),
            "artist": str(item.get("artistName") or ""),
            "album": str(item.get("collectionName") or ""),
            "duration": millis / 1000 if millis else None,
            "preview": item.get("previewUrl"),
            "url": clean_apple_music_url(item.get("trackViewUrl")) or "",
            "explicit": item.get("trackExplicitness") == "explicit",
            "artist_id": str(item.get("artistId") or ""),
            "country": country,
        }

    def _track(self, meta: Dict[str, Any]) -> Track:
        return Track(
            service=self.name,
            url=meta["url"],
            name="{} - {}".format(meta["artist"], meta["title"]),
            type=TrackType.Dynamic,
            extra_info={"apple": meta},
            server=self.bot.name,
        )

    def search(self, query: str) -> List[Track]:
        items = self._itunes(
            ITUNES_SEARCH_URL,
            {
                "term": query,
                "media": "music",
                "entity": "song",
                "limit": SEARCH_LIMIT,
                "country": self.country,
            },
        )
        metas = [self._meta_from_itunes(item, self.country) for item in items]
        tracks = [self._track(meta) for meta in metas if meta]
        if not tracks:
            raise errors.NothingFoundError("")
        return tracks

    def _lookup(self, country: str, **params: Any) -> List[Dict[str, Any]]:
        items = self._itunes(ITUNES_LOOKUP_URL, dict(params, country=country))
        metas: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
        for item in items:
            meta = self._meta_from_itunes(item, country)
            if meta:
                order = (item.get("discNumber") or 0, item.get("trackNumber") or 0)
                metas.append((order, meta))
        metas.sort(key=lambda item: item[0])
        return [meta for _, meta in metas]

    def _playlist(self, url: str, country: str) -> List[Dict[str, Any]]:
        try:
            response = self._session.get(url, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise errors.ServiceError("Could not reach Apple Music: {}".format(e))
        match = re.search(
            r'<script type="application/json" id="serialized-server-data">(.*?)</script>',
            response.text,
            re.S,
        )
        if response.status_code != 200 or not match:
            raise errors.ServiceError("This Apple Music playlist could not be read")
        metas: List[Dict[str, Any]] = []
        seen = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                descriptor = node.get("contentDescriptor") or {}
                if (
                    descriptor.get("kind") == "song"
                    and "title" in node
                    and "artistName" in node
                ):
                    song_id = str(
                        (descriptor.get("identifiers") or {}).get("storeAdamID") or ""
                    )
                    if song_id and song_id not in seen:
                        seen.add(song_id)
                        album = ""
                        for link in node.get("tertiaryLinks") or []:
                            album = str(link.get("title") or "")
                            break
                        millis = node.get("duration")
                        metas.append(
                            {
                                "id": song_id,
                                "title": str(node.get("title") or "Untitled"),
                                "artist": str(node.get("artistName") or ""),
                                "album": album,
                                "duration": millis / 1000 if millis else None,
                                "preview": None,
                                "url": clean_apple_music_url(descriptor.get("url")) or url,
                                "explicit": False,
                                "country": country,
                            }
                        )
                    return
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        try:
            walk(json.loads(match.group(1)))
        except ValueError:
            raise errors.ServiceError("This Apple Music playlist could not be read")
        return metas

    def _metas_from_url(self, url: str) -> List[Dict[str, Any]]:
        parts = urlsplit(url)
        segments = [segment for segment in parts.path.split("/") if segment]
        country = self.country
        if segments and re.fullmatch(r"[a-z]{2}", segments[0]):
            country = segments.pop(0)
        if not segments:
            raise errors.ServiceError("Unsupported Apple Music link")
        kind = segments[0]
        last = segments[-1]
        query = dict(parse_qsl(parts.query))
        if kind == "song" and last.isdigit():
            return self._lookup(country, id=last)
        if kind == "album" and last.isdigit():
            if query.get("i", "").isdigit():
                return self._lookup(country, id=query["i"])
            return self._lookup(country, id=last, entity="song", limit=200)
        if kind == "artist" and last.isdigit():
            # The lookup also returns songs the artist is only featured on.
            metas = self._lookup(country, id=last, entity="song", limit=50)
            return [meta for meta in metas if meta.get("artist_id") == last]
        if kind == "playlist" and last.startswith("pl."):
            return self._playlist(
                urlunsplit(("https", "music.apple.com", parts.path, "", "")), country
            )
        raise errors.ServiceError("Unsupported Apple Music link")

    def get(
        self,
        url: str,
        extra_info: Optional[Dict[str, Any]] = None,
        process: bool = False,
    ) -> List[Track]:
        if process:
            meta = (extra_info or {}).get("apple")
            if not meta:
                raise errors.ServiceError("Not an Apple Music song")
            return [self._resolve(meta)]
        if not url:
            raise errors.InvalidArgumentError()
        metas = self._metas_from_url(url)
        if not metas:
            raise errors.NothingFoundError("")
        return [self._track(meta) for meta in metas]

    # YouTube matching -----------------------------------------------------

    def _video_info(self, video_id: str) -> Dict[str, Any]:
        return ytdlp.extract(
            "https://www.youtube.com/watch?v=" + video_id, cookiefile=self._cookiefile
        )

    def _flat_search(self, url: str) -> List[Dict[str, Any]]:
        # Without a playlist end, yt-dlp pages through every search result.
        result = ytdlp.extract(url, flat=True, playlist_end=YT_SEARCHED, cookiefile=self._cookiefile)
        return [entry for entry in (result or {}).get("entries") or [] if entry]

    def _youtube_music_ids(self, meta: Dict[str, Any]) -> List[str]:
        # YouTube Music ranks results differently depending on word order, so
        # ask both ways and interleave the results.
        queries = [
            "{} {}".format(meta["artist"], meta["title"]),
            "{} {}".format(meta["title"], meta["artist"]),
        ]
        urls = [
            "https://music.youtube.com/search?q={}#songs".format(quote(query))
            for query in queries
        ]
        with ThreadPoolExecutor(max_workers=len(urls)) as executor:
            results = list(executor.map(self._flat_search_or_empty, urls))
        ids: List[str] = []
        for rank in range(max(len(result) for result in results)):
            for result in results:
                if rank >= len(result):
                    continue
                entry = result[rank]
                title = entry.get("title") or ""
                if (
                    entry.get("id")
                    and entry["id"] not in ids
                    and title_matches(meta, title)
                    and not variant_conflict(meta, title)
                ):
                    ids.append(entry["id"])
        return ids[:YTM_KEPT]

    def _flat_search_or_empty(self, url: str) -> List[Dict[str, Any]]:
        try:
            return self._flat_search(url)
        except Exception:
            logging.warning("YouTube search failed: %s", url, exc_info=True)
            return []

    def _first_verified(
        self,
        meta: Dict[str, Any],
        video_ids: List[str],
        looked_up: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Look the videos up in parallel; return the best-ranked one that matches.

        Every video that was looked up is added to looked_up.
        """
        if not video_ids:
            return None

        def lookup(video_id: str) -> Optional[Dict[str, Any]]:
            try:
                return self._video_info(video_id)
            except Exception:
                logging.debug("Could not look up %s", video_id, exc_info=True)
                return None

        with ThreadPoolExecutor(max_workers=len(video_ids)) as executor:
            infos = [info for info in executor.map(lookup, video_ids) if info]
        looked_up += infos
        for info in infos:
            if verify(meta, info):
                return info
        return None

    def _youtube_ids(self, meta: Dict[str, Any]) -> List[str]:
        query = "{} - {}".format(meta["artist"], meta["title"])
        entries = self._flat_search("ytsearch{}:{}".format(YT_SEARCHED, query))
        scored = []
        for entry in entries:
            score = score_search_result(meta, entry)
            if score is not None and entry.get("id"):
                channel = entry.get("channel") or entry.get("uploader") or ""
                # Uploads by the artist (or YouTube's Topic channels) come first:
                # re-uploads by other channels rarely pass the full check.
                official = artist_matches(meta, channel) or normalize(
                    channel
                ).endswith(" topic")
                scored.append((not official, -score, entry["id"]))
        scored.sort()
        return [video_id for _, _, video_id in scored]

    def _find_match(
        self, meta: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        """Find the verified recording; returns its info and the ordered candidates."""
        looked_up: List[Dict[str, Any]] = []
        candidates = self._youtube_music_ids(meta)
        info = self._first_verified(meta, candidates[:YTM_CHECKED], looked_up)
        if info is None:
            fallback = [c for c in self._youtube_ids(meta) if c not in candidates]
            candidates += fallback
            info = self._first_verified(meta, fallback[:YT_CHECKED], looked_up)
        if info is None:
            info = next((i for i in looked_up if verify(meta, i, relaxed=True)), None)
            if info is not None:
                logging.info(
                    "Using a longer video from the artist's channel for %s - %s",
                    meta.get("artist"),
                    meta.get("title"),
                )
        if info is None:
            return None, candidates
        return info, [info["id"]] + [c for c in candidates if c != info["id"]]

    def _stream_track(self, meta: Dict[str, Any], info: Dict[str, Any]) -> Track:
        if "url" not in info:
            raise errors.ServiceError("YouTube returned no audio stream")
        return Track(
            service=self.name,
            url=info["url"],
            name="{} - {}".format(meta["artist"], meta["title"]),
            format=info.get("ext", ""),
            type=TrackType.Live if info.get("is_live") else TrackType.Default,
            extra_info={"apple": meta, "yt": _slim(info), "video_id": info.get("id")},
            server=self.bot.name,
        )

    def _preview_track(self, meta: Dict[str, Any]) -> Track:
        preview = meta.get("preview")
        if not preview:
            found = self._lookup(meta.get("country") or self.country, id=meta["id"])
            preview = found[0].get("preview") if found else None
        if not preview:
            raise errors.ServiceError("No matching recording or preview was found")
        return Track(
            service=self.name,
            url=preview,
            name="{} - {} ({})".format(
                meta["artist"], meta["title"], self.bot.translator.translate("preview")
            ),
            format="m4a",
            type=TrackType.Default,
            extra_info={"apple": meta, "preview": True},
            server=self.bot.name,
        )

    def _resolve(self, meta: Dict[str, Any]) -> Track:
        cached = self._matches.get(meta["id"])
        index = int((cached or {}).get("index", -1))
        if cached and 0 <= index < len(cached.get("candidates") or []):
            try:
                return self._stream_track(
                    meta, self._video_info(cached["candidates"][index])
                )
            except Exception:
                logging.warning(
                    "Cached match for Apple Music song %s no longer plays, searching again",
                    meta["id"],
                    exc_info=True,
                )
        try:
            info, candidates = self._find_match(meta)
        except Exception as e:
            logging.error("YouTube matching failed", exc_info=True)
            if self.config.no_match != "preview":
                raise errors.ServiceError("YouTube matching failed: {}".format(e))
            info, candidates = None, []
        if info is not None:
            self._matches.set(meta["id"], self._entry(meta, candidates, 0))
            return self._stream_track(meta, info)
        logging.info(
            "No YouTube match for %s - %s", meta.get("artist"), meta.get("title")
        )
        if candidates:
            # Keep the unverified candidates so that "alt" can still offer them.
            self._matches.set(meta["id"], self._entry(meta, candidates, -1))
        if self.config.no_match == "preview":
            return self._preview_track(meta)
        raise errors.ServiceError("No matching recording was found on YouTube")

    def _rank(self, meta: Dict[str, Any], info: Optional[Dict[str, Any]]) -> int:
        if not info:
            return 0
        if verify(meta, info):
            return 4
        if verify(meta, info, relaxed=True):
            return 3
        if duration_matches(meta, info.get("duration")) and title_matches(
            meta, info.get("track"), info.get("title")
        ) and not variant_conflict(meta, info.get("title") or ""):
            return 2
        return 1

    def _ranked_candidates(self, meta: Dict[str, Any], current: List[str]) -> List[str]:
        """All known candidates, best first, each looked up and checked once."""
        candidates = list(current)
        candidates += [c for c in self._youtube_ids(meta) if c not in candidates]
        candidates += [c for c in self._youtube_music_ids(meta) if c not in candidates]
        to_check = candidates[1:][: YTM_KEPT]

        def lookup(video_id: str) -> Optional[Dict[str, Any]]:
            try:
                return self._video_info(video_id)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=max(1, len(to_check))) as executor:
            ranks = dict(
                zip(to_check, (self._rank(meta, i) for i in executor.map(lookup, to_check)))
            )
        rest = sorted(
            (c for c in to_check if ranks[c] > 0),
            key=lambda c: -ranks[c],  # sorted() is stable, so search order breaks ties
        )
        return candidates[:1] + rest

    def select_next_match(self, meta: Dict[str, Any]) -> Tuple[int, int]:
        """Make the next-best candidate the chosen match. Returns (number, total)."""
        entry = self._matches.get(meta["id"]) or {"candidates": [], "index": -1}
        candidates: List[str] = list(entry.get("candidates") or [])
        if not entry.get("ranked"):
            if int(entry.get("index", -1)) < 0:
                # No verified match yet: rank everything and start from the best.
                candidates = self._ranked_candidates(meta, [""] + candidates)[1:]
                index = 0
            else:
                current = candidates[int(entry["index"])]
                others = [c for c in candidates if c != current]
                candidates = self._ranked_candidates(meta, [current] + others)
                index = 1
        else:
            index = int(entry.get("index", -1)) + 1
        if index >= len(candidates):
            raise errors.NothingFoundError("")
        self._matches.set(meta["id"], self._entry(meta, candidates, index, ranked=True))
        return index + 1, len(candidates)

    @staticmethod
    def _entry(
        meta: Dict[str, Any], candidates: List[str], index: int, ranked: bool = False
    ) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "candidates": candidates,
            "index": index,
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "url": meta.get("url", ""),
        }
        if ranked:
            entry["ranked"] = True
        return entry

    def track_for(self, meta: Dict[str, Any]) -> Track:
        """A fresh, unresolved track for an Apple Music song."""
        return self._track(meta)

    def download(self, track: Track, file_path: str) -> None:
        info = (track.extra_info or {}).get("yt")
        if not info or not info.get("webpage_url"):
            super().download(track, file_path)
            return
        ytdlp.download(info["webpage_url"], file_path, self._cookiefile)
