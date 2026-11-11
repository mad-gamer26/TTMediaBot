from __future__ import annotations

import pickle
from collections import deque
from typing import Any, Dict, List, TYPE_CHECKING

from bot import app_vars
from bot.migrators import cache_migrator

import portalocker

if TYPE_CHECKING:
    from bot.player.track import Track


cache_data_type = Dict[str, Any]

# What a saved YouTube track needs to be played again.
_PLAIN_KEYS = ("webpage_url", "original_url", "url", "title", "id", "uploader")


class _OldLibraryObject(dict):
    """Stands in for yt-dlp objects in caches saved while the bot imported yt-dlp.

    Some of them were dict- or list-like, so it accepts items and appends.
    """

    def __init__(self, *args, **kwargs):
        super().__init__()

    def __setstate__(self, state):
        pass

    def append(self, item):
        pass

    def extend(self, items):
        pass


class _Unpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "yt_dlp" or module.startswith("yt_dlp."):
            return _OldLibraryObject
        return super().find_class(module, name)


def _plain(value):
    return isinstance(value, (str, int, float, bool)) or value is None


def _trim_old_details(tracks) -> int:
    """Replace yt-dlp details stored on old tracks by the plain parts needed to play them."""
    trimmed = 0
    for track in tracks:
        info = getattr(track, "extra_info", None)
        if getattr(track, "service", "") == "yt" and isinstance(info, dict) and "_looked_up" not in info:
            track.extra_info = {key: info[key] for key in _PLAIN_KEYS if key in info and _plain(info[key])}
            trimmed += 1
    return trimmed


class Cache:
    def __init__(self, cache_data: cache_data_type):
        self.cache_version = cache_data["cache_version"] if "cache_version" in cache_data else CacheManager.version
        self.recents: deque[Track] = (
            cache_data["recents"]
            if "recents" in cache_data
            else deque(maxlen=app_vars.recents_max_lenth)
        )
        self.favorites: Dict[str, List[Track]] = (
            cache_data["favorites"] if "favorites" in cache_data else {}
        )

    @property
    def data(self):
        return {"cache_version": self.cache_version, "recents": self.recents, "favorites": self.favorites}


class CacheManager:
    version = 1

    def __init__(self, file_name: str) -> None:
        self.file_name = file_name
        try:
            self.data = cache_migrator.migrate(self, self._load())
            self.cache = Cache(self.data)
        except FileNotFoundError:
            self.cache = Cache({})
            self._dump(self.cache.data)
        self._lock()

    def _dump(self, data: cache_data_type):
        with open(self.file_name, "wb") as f:
            pickle.dump(data, f)

    def _load(self) -> cache_data_type:
        with open(self.file_name, "rb") as f:
            data = _Unpickler(f).load()
        tracks = list(data.get("recents") or [])
        for favorites in (data.get("favorites") or {}).values():
            tracks += favorites
        originals = [getattr(t, "_original_track", None) for t in tracks]
        if _trim_old_details(tracks + [t for t in originals if t is not None]):
            self._dump(data)  # save the trimmed cache once
        return data

    def _lock(self):
        self.file_locker = portalocker.Lock(
            self.file_name,
            timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            self.file_locker.acquire()
        except portalocker.exceptions.LockException:
            raise PermissionError()

    def close(self):
        self.file_locker.release()

    def save(self):
        self.file_locker.release()
        self._dump(self.cache.data)
        self.file_locker.acquire()
