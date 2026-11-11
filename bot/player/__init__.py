from __future__ import annotations
import html
import logging
import random
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from bot import errors
from bot.player.engine import AudioEngine
from bot.player.enums import Mode, State, TrackType
from bot.player.track import Track


if TYPE_CHECKING:
    from bot import Bot


class Player:
    """The track list and playback modes of one server; the sound itself is
    made by AudioEngine (ffmpeg into TeamTalk, see engine.py)."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.config = bot.config.player
        self.cache = bot.cache
        self.cache_manager = bot.cache_manager
        self.engine = AudioEngine(
            get_tt=lambda: bot.ttclient.tt,
            get_format=lambda: bot.ttclient.audio_format(),
            on_end=self.on_end_file,
            on_title=self.on_title,
            name=bot.name,
        )
        self.track_list: List[Track] = []
        self.track: Track = Track()
        self.track_index: int = -1
        self.state = State.Stopped
        self.mode = Mode.TrackList
        self.volume = self.config.default_volume
        self.engine.set_volume(self.volume)

    def initialize(self) -> None:
        pass

    def run(self) -> None:
        pass

    def close(self) -> None:
        logging.debug("Closing player")
        if self.state != State.Stopped:
            self.stop()
        self.engine.close()

    def play(
        self,
        tracks: Optional[List[Track]] = None,
        start_track_index: Optional[int] = None,
    ) -> None:
        if tracks != None:
            self.track_list = tracks
            if not start_track_index and self.mode == Mode.Random:
                self.shuffle(True)
                self.track_index = self._index_list[0]
                self.track = self.track_list[self.track_index]
            else:
                self.track_index = start_track_index if start_track_index else 0
                self.track = tracks[self.track_index]
            try:
                self._play(self.track)
            except errors.ServiceError:
                if len(self.track_list) < 2:
                    raise
                self._skip_unplayable()
                if self.state == State.Stopped:
                    return
        else:
            self.engine.resume()
        self.state = State.Playing

    def pause(self) -> None:
        self.state = State.Paused
        self.engine.pause()

    def stop(self) -> None:
        self.state = State.Stopped
        self.engine.stop()
        self.track_list = []
        self.track = Track()
        self.track_index = -1

    def _play(self, track: Track, save_to_recents: bool = True) -> None:
        url = track.url  # a dynamic track looks itself up here (may raise ServiceError)
        if save_to_recents:
            try:
                if self.cache.recents[-1] != self.track_list[self.track_index]:
                    self.cache.recents.append(self.track_list[self.track_index].get_raw())
            except Exception:
                self.cache.recents.append(self.track_list[self.track_index].get_raw())
            self.cache_manager.save()
        info = track.extra_info if isinstance(track.extra_info, dict) else {}
        # The request headers yt-dlp says the stream needs (YouTube, Apple Music's match).
        headers = info.get("http_headers") or (info.get("yt") or {}).get("http_headers")
        icy = track.type in (TrackType.Direct, TrackType.Live) and "youtube" not in url and "googlevideo" not in url
        self.engine.play(url, headers=headers, icy=icy)

    def next(self) -> None:
        track_index = self.track_index
        if len(self.track_list) > 0:
            if self.mode == Mode.Random:
                try:
                    track_index = self._index_list[self._index_list.index(self.track_index) + 1]
                except IndexError:
                    track_index = 0
            else:
                track_index += 1
        else:
            track_index = 0
        try:
            self.play_by_index(track_index)
        except errors.IncorrectTrackIndexError:
            if self.mode == Mode.RepeatTrackList:
                self.play_by_index(0)
            else:
                raise errors.NoNextTrackError()

    def previous(self) -> None:
        track_index = self.track_index
        if len(self.track_list) > 0:
            if self.mode == Mode.Random:
                try:
                    track_index = self._index_list[self._index_list.index(self.track_index) - 1]
                except IndexError:
                    track_index = len(self.track_list) - 1
            else:
                if track_index == 0 and self.mode != Mode.RepeatTrackList:
                    raise errors.NoPreviousTrackError
                else:
                    track_index -= 1
        else:
            track_index = 0
        try:
            self.play_by_index(track_index)
        except errors.IncorrectTrackIndexError:
            if self.mode == Mode.RepeatTrackList:
                self.play_by_index(len(self.track_list) - 1)
            else:
                raise errors.NoPreviousTrackError

    def play_by_index(self, index: int) -> None:
        if index < len(self.track_list) and index >= (0 - len(self.track_list)):
            self.track = self.track_list[index]
            self.track_index = self.track_list.index(self.track)
            self._play(self.track)
            self.state = State.Playing
        else:
            raise errors.IncorrectTrackIndexError()

    def set_volume(self, volume: int) -> None:
        volume = volume if volume <= self.config.max_volume else self.config.max_volume
        self.volume = volume
        fading = self.config.volume_fading_interval if self.config.volume_fading else None
        self.engine.set_volume(volume, fading)

    def get_speed(self) -> float:
        return self.engine.speed()

    def set_speed(self, arg: float) -> None:
        if arg < 0.25 or arg > 4:
            raise ValueError()
        self.engine.set_speed(arg)

    def seek_back(self, step: Optional[float] = None) -> None:
        step = step if step else self.config.seek_step
        if step <= 0:
            raise ValueError()
        self.engine.seek(self.engine.position() - step)

    def seek_forward(self, step: Optional[float] = None) -> None:
        step = step if step else self.config.seek_step
        if step <= 0:
            raise ValueError()
        duration = self.engine.duration()
        target = self.engine.position() + step
        if duration and target >= duration:
            self.on_end_file("")  # past the end: the next track, as at its end
            return
        self.engine.seek(target)

    def get_duration(self) -> Optional[float]:
        return self.engine.duration()

    def get_position(self) -> float:
        return self.engine.position()

    def shuffle(self, enable: bool) -> None:
        if enable:
            self._index_list = [i for i in range(0, len(self.track_list))]
            random.shuffle(self._index_list)
        else:
            del self._index_list

    def on_end_file(self, error: str) -> None:
        """The engine finished the track (error: why it could not play)."""
        if error:
            logging.warning("Could not play %s: %s", getattr(self.track, "_name", ""), error)
        if self.state != State.Playing:
            return
        if self.mode == Mode.SingleTrack or self.track.type == TrackType.Direct:
            self.stop()
        elif self.mode == Mode.RepeatTrack and not error:
            self.play_by_index(self.track_index)
        else:
            try:
                self.next()
            except errors.NoNextTrackError:
                self.stop()
            except errors.ServiceError:
                self._skip_unplayable()

    def _skip_unplayable(self) -> None:
        """Move past tracks that cannot be played, such as unmatched Apple Music songs."""
        for _ in range(len(self.track_list)):
            # Reading track.url here would retry the failed lookup.
            logging.warning("Skipping unplayable track %s", getattr(self.track, "_name", ""))
            try:
                self.next()
                return
            except errors.NoNextTrackError:
                break
            except errors.ServiceError:
                continue
        self.stop()

    def on_title(self, title: str) -> None:
        """A stream (internet radio) announced what it is playing."""
        if self.track.type in (TrackType.Direct, TrackType.Local):
            title = html.unescape(title)
            if title and self.track.name != title:
                self.track.name = title
