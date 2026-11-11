"""Plays audio into a TeamTalk channel with TT_InsertAudioBlock.

ffmpeg decodes each track to 16-bit PCM in the channel codec's sample rate
and channels; the engine gives it to TeamTalk as the bot's voice stream, a
little ahead of what TeamTalk reports as sent. No sound devices, virtual
cables or PulseAudio sinks are involved, so it works the same on Linux,
Windows and macOS.

Threads: a reader per track (ffmpeg's output into a bounded queue), one
feeder per engine (queue into TeamTalk every 20 ms), and the TeamTalk event
thread, which reports progress through on_audio_input(). Callbacks
(track ended, new stream title) run on their own short-lived threads.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import re
import subprocess
import threading
import time
import urllib.request
from typing import Callable, List, Optional, Tuple

import numpy as np

import TeamTalkPy

# Pieces given to TeamTalk, and how far ahead of what it has sent.
CHUNK_MSEC = 100
AHEAD_MSEC = 500
# Decoded audio kept ready (absorbs network hiccups).
BUFFER_CHUNKS = 30
# Network sources start (and, after running dry, go on) only once this much
# is decoded: one clean pause instead of stutter when the network is slow.
PREBUFFER_CHUNKS = 15
FEED_INTERVAL = 0.02
# Silence after each track: TeamTalk's encoder keeps a little of what it last
# encoded, which otherwise starts the next track as a faint blip.
TAIL_MSEC = 100
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_DURATION = re.compile(r"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)")
_TITLE = re.compile(r"^\s*(?:StreamTitle|title|icy-name)\s*:\s*(.+?)\s*$", re.I)


def gain_for(volume: float) -> float:
    """mpv's volume scale (cubic; 100 = unchanged), so volumes sound as before."""
    return max(0.0, volume / 100.0) ** 3


def atempo_chain(speed: float) -> List[str]:
    """ffmpeg's atempo takes 0.5-2.0 per filter; chain them for 0.25-4."""
    filters = []
    while speed > 2.0:
        filters.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1.0) > 1e-6:
        filters.append("atempo={:.6f}".format(speed))
    return filters


class IcyReader(threading.Thread):
    """Reads an internet radio stream that sends ICY metadata, strips the
    metadata into stream titles and passes the audio to ffmpeg's stdin."""

    def __init__(self, response, metaint: int, sink, on_title: Callable[[str], None]):
        super().__init__(daemon=True, name="icy")
        self.response, self.metaint, self.sink, self.on_title = response, metaint, sink, on_title
        self.stopped = threading.Event()

    def run(self) -> None:
        try:
            while not self.stopped.is_set():
                audio = self.response.read(self.metaint)
                if not audio:
                    break
                self.sink.write(audio)
                length = self.response.read(1)
                if not length:
                    break
                size = length[0] * 16
                if size:
                    meta = self.response.read(size).rstrip(b"\0").decode("utf-8", "replace")
                    match = re.search(r"StreamTitle='(.*?)';", meta)
                    if match and match.group(1).strip():
                        self.on_title(match.group(1).strip())
        except (OSError, ValueError):
            pass
        finally:
            for closer in (self.sink.close, self.response.close):
                try:
                    closer()
                except (OSError, ValueError):
                    pass


class Decoder:
    """One ffmpeg process decoding a track from `start` seconds."""

    def __init__(self, url: str, sample_rate: int, channels: int, start: float = 0.0, speed: float = 1.0,
                 headers: Optional[dict] = None, icy: bool = False,
                 on_title: Optional[Callable[[str], None]] = None) -> None:
        self.url, self.sample_rate, self.channels = url, sample_rate, channels
        self.start, self.speed = start, speed
        self.headers, self.icy = headers, icy
        self.frame_bytes = 2 * channels
        self.chunk_bytes = self.frame_bytes * (sample_rate * CHUNK_MSEC // 1000)
        self.queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=BUFFER_CHUNKS)
        self.duration: Optional[float] = None
        self.error = ""
        self.stopped = threading.Event()
        self._stderr: List[str] = []
        self._icy: Optional[IcyReader] = None
        on_title = on_title or (lambda title: None)

        network = url.startswith(("http://", "https://"))
        self.network = network
        source, stdin = url, subprocess.DEVNULL
        response = None
        if icy and network:
            response = self._open_icy(url, headers, on_title)
            if response is not None:
                source, stdin = "pipe:0", subprocess.PIPE
        from bot import tools

        args = [tools.ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info"]
        if network and source == url:
            args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                     "-reconnect_delay_max", "5", "-user_agent", (headers or {}).get("User-Agent", USER_AGENT)]
            extra = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
            if extra:
                args += ["-headers", "".join("{}: {}\r\n".format(k, v) for k, v in extra.items())]
        if start > 0 and source == url:
            args += ["-ss", "{:.3f}".format(start)]
        args += ["-i", source, "-vn"]
        filters = atempo_chain(speed)
        if filters:
            args += ["-af", ",".join(filters)]
        args += ["-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", str(channels), "pipe:1"]
        if stdin is subprocess.PIPE:
            args[args.index("-nostdin")] = "-y"  # stdin is the stream
        self.process = subprocess.Popen(args, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        bufsize=0, **_no_window())
        if response is not None:
            self._icy = IcyReader(response, int(response.headers["icy-metaint"]), self.process.stdin, on_title)
            self._icy.start()
        threading.Thread(target=self._read_audio, daemon=True, name="decode").start()
        threading.Thread(target=self._read_messages, args=(on_title,), daemon=True, name="decode-log").start()

    @staticmethod
    def _open_icy(url: str, headers: Optional[dict], on_title: Callable[[str], None]):
        request = urllib.request.Request(url, headers={"Icy-MetaData": "1",
                                                       "User-Agent": (headers or {}).get("User-Agent", USER_AGENT)})
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except OSError:
            return None
        name = response.headers.get("icy-name")
        if name:
            on_title(name.strip())
        if not response.headers.get("icy-metaint", "").isdigit():
            response.close()  # no metadata: let ffmpeg open it, with reconnects
            return None
        return response

    def _read_audio(self) -> None:
        stdout = self.process.stdout
        pending = b""
        try:
            while not self.stopped.is_set():
                data = stdout.read(self.chunk_bytes - len(pending))
                if not data:
                    break
                pending += data
                if len(pending) >= self.chunk_bytes:
                    self._put(pending)
                    pending = b""
            if pending and not self.stopped.is_set():
                self._put(pending[: len(pending) // self.frame_bytes * self.frame_bytes])
        except (OSError, ValueError):
            pass
        code = self.process.wait()
        if code and not self.stopped.is_set():
            self.error = (self._stderr or ["ffmpeg stopped with code {}".format(code)])[-1]
        self._put(None)

    def _put(self, item: Optional[bytes]) -> None:
        while not self.stopped.is_set():
            try:
                self.queue.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def _read_messages(self, on_title: Callable[[str], None]) -> None:
        for raw in iter(self.process.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            match = _DURATION.search(line)
            if match and self.duration is None:
                h, m, s = match.groups()
                self.duration = int(h) * 3600 + int(m) * 60 + float(s)
            title = _TITLE.match(line)
            if title:
                on_title(title.group(1))
            if "error" in line.lower() or "invalid" in line.lower() or "failed" in line.lower():
                self._stderr.append(line)
                del self._stderr[:-5]

    def close(self) -> None:
        self.stopped.set()
        if self._icy:
            self._icy.stopped.set()
        try:
            self.process.kill()
        except OSError:
            pass
        # Unblock a reader waiting for space.
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass


def _no_window() -> dict:
    """Keeps a console window from flashing up for ffmpeg on Windows."""
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flag} if flag else {}


class AudioEngine:
    """Plays one track at a time into a TeamTalk client."""

    def __init__(self, get_tt: Callable[[], "TeamTalkPy.TeamTalk"], get_format: Callable[[], Tuple[int, int]],
                 on_end: Callable[[str], None], on_title: Callable[[str], None], name: str = "") -> None:
        self._get_tt, self._get_format = get_tt, get_format
        self._on_end, self._on_title = on_end, on_title
        self._lock = threading.RLock()
        self._decoder: Optional[Decoder] = None
        self._stream_ids = iter(range(1, 2 ** 31))
        self._stream_id = 0
        self._sent_frames = 0       # output frames given to TeamTalk this session
        self._session_frames = 0    # ... before this session (same decoder)
        self._elapsed_msec = 0
        self._flushed = False
        self._paused = False
        self._ended_at = 0.0
        self._deadline = 0.0
        self._pending: Optional[bytes] = None
        self._buffering = True
        self._title_token: object = None
        self._volume = 100.0
        self._target_volume = 100.0
        self._fade_per_second: Optional[float] = None  # None: no fading
        self.underruns = 0
        self._closed = threading.Event()
        threading.Thread(target=self._feed_loop, daemon=True, name=(name + "/audio").lstrip("/")).start()

    # ------------------------------------------------------------- control

    def play(self, url: str, start: float = 0.0, speed: float = 1.0, headers: Optional[dict] = None,
             icy: bool = False) -> None:
        with self._lock:
            self._end_session()
            self._close_decoder()
            rate, channels = self._get_format()
            # Titles are tied to this track: one arriving after the next track
            # started is dropped.
            token = self._title_token = object()
            self._decoder = Decoder(url, rate, channels, start, speed, headers, icy,
                                    lambda title: self._title(token, title))
            self._paused = False
            self._new_session()

    def pause(self) -> None:
        with self._lock:
            if self._decoder and not self._paused:
                self._paused = True
                self._end_session()

    def resume(self) -> None:
        with self._lock:
            if self._decoder and self._paused:
                self._paused = False
                self._new_session()

    def stop(self) -> None:
        with self._lock:
            self._end_session()
            self._close_decoder()
            self._paused = False

    def seek(self, position: float) -> None:
        with self._lock:
            decoder = self._decoder
            if decoder is None:
                return
            position = max(0.0, position)
            if decoder.duration and position >= decoder.duration:
                position = max(0.0, decoder.duration - 0.5)
            self._restart(decoder, position, decoder.speed)

    def set_speed(self, speed: float) -> None:
        with self._lock:
            if self._decoder is not None:
                self._restart(self._decoder, self.position(), speed)

    def set_volume(self, volume: float, fade_seconds_per_step: Optional[float] = None) -> None:
        """Volume in mpv's 0-100+ scale. With fading, it moves one step per
        `fade_seconds_per_step`, as the old player did."""
        with self._lock:
            self._target_volume = float(volume)
            # Without fading the change still ramps over one piece (no click).
            self._fade_per_second = (1.0 / fade_seconds_per_step) if fade_seconds_per_step else None

    def close(self) -> None:
        self.stop()
        self._closed.set()

    # --------------------------------------------------------------- state

    @property
    def playing(self) -> bool:
        return self._decoder is not None and not self._paused

    def position(self) -> float:
        """Seconds into the track that listeners have heard."""
        with self._lock:
            d = self._decoder
            if d is None:
                return 0.0
            heard = self._session_frames / d.sample_rate + self._elapsed_msec / 1000.0
            return d.start + heard * d.speed

    def duration(self) -> Optional[float]:
        d = self._decoder
        return None if d is None or d.duration is None else d.duration

    def speed(self) -> float:
        d = self._decoder
        return d.speed if d else 1.0

    # ------------------------------------------------------------ internals

    def _restart(self, decoder: Decoder, position: float, speed: float) -> None:
        url, rate, channels = decoder.url, decoder.sample_rate, decoder.channels
        self._end_session()
        self._close_decoder()
        # A radio stream's metadata is not read again after a seek or speed
        # change (ffmpeg opens it directly then).
        token = self._title_token
        self._decoder = Decoder(url, rate, channels, position, speed, decoder.headers, False,
                                lambda title: self._title(token, title))
        if not self._paused:
            self._new_session()

    def _close_decoder(self) -> None:
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None
        self._pending = None
        self._session_frames = 0

    def _new_session(self) -> None:
        self._buffering = True
        if self._stream_id:
            self._session_frames += self._sent_frames
        self._stream_id = next(self._stream_ids)
        self._sent_frames = 0
        self._elapsed_msec = 0
        self._flushed = False
        self._ended_at = 0.0
        self._deadline = 0.0

    def _end_session(self) -> None:
        """Ends the TeamTalk audio session (what is queued there still plays,
        at most AHEAD_MSEC)."""
        if self._stream_id:
            try:
                self._get_tt().insertAudioBlock(None)
            except Exception:
                pass
            self._session_frames += self._sent_frames
            self._sent_frames = 0
            self._elapsed_msec = 0
            self._stream_id = 0

    def _title(self, token: object, title: str) -> None:
        if token is self._title_token:
            threading.Thread(target=self._safe, args=(self._on_title, title), daemon=True).start()

    @staticmethod
    def _safe(function, *args) -> None:
        try:
            function(*args)
        except Exception:
            logging.error("Player callback failed", exc_info=True)

    def on_audio_input(self, stream_id: int, queue_msec: int, elapsed_msec: int) -> None:
        """CLIENTEVENT_AUDIOINPUT, from the TeamTalk event thread."""
        with self._lock:
            if stream_id != self._stream_id or not stream_id:
                return
            if queue_msec == 0 and elapsed_msec == 0:
                if self._flushed:
                    self._finish()
                return
            self._elapsed_msec = max(self._elapsed_msec, elapsed_msec)

    def _finish(self) -> None:
        decoder = self._decoder
        error = decoder.error if decoder else ""
        self._session_frames += self._sent_frames
        self._sent_frames = 0
        self._stream_id = 0
        self._close_decoder()
        threading.Thread(target=self._safe, args=(self._on_end, error), daemon=True).start()

    def _feed_loop(self) -> None:
        while not self._closed.is_set():
            time.sleep(FEED_INTERVAL)
            try:
                with self._lock:
                    self._feed()
            except Exception:
                logging.error("Audio feeding failed", exc_info=True)

    def _feed(self) -> None:
        d = self._decoder
        if d is None or self._paused or not self._stream_id:
            return
        if self._flushed:
            if self._deadline and time.monotonic() > self._deadline:
                logging.warning("TeamTalk did not report the end of the track; going on")
                self._finish()
            return
        if self._buffering and d.network and self._pending is None:
            # Wait for enough audio (or the end of a short stream).
            if d.queue.qsize() < PREBUFFER_CHUNKS and d.process.poll() is None:
                return
        self._buffering = False
        tt = self._get_tt()
        while (self._sent_frames * 1000 // d.sample_rate) - self._elapsed_msec < AHEAD_MSEC:
            chunk = self._pending
            if chunk is None:
                try:
                    chunk = d.queue.get_nowait()
                except queue.Empty:
                    if self._sent_frames:
                        self.underruns += 1
                        if d.network:
                            self._buffering = True  # refill before going on
                    return
                if chunk is None:
                    self._flush(tt, d)
                    return
                chunk = self._apply_volume(chunk, d)
            if not self._insert(tt, d, chunk):
                self._pending = chunk  # TeamTalk's queue is full; next time
                return
            self._pending = None
            self._sent_frames += len(chunk) // d.frame_bytes

    def _flush(self, tt, d: Decoder) -> None:
        if self._sent_frames == 0 and self._session_frames == 0 and d.error:
            # Nothing could be played at all.
            self._stream_id = 0
            error = d.error
            self._close_decoder()
            threading.Thread(target=self._safe, args=(self._on_end, error), daemon=True).start()
            return
        tail = b"\0" * (d.frame_bytes * (d.sample_rate * TAIL_MSEC // 1000))
        self._insert(tt, d, tail)
        self._sent_frames += len(tail) // d.frame_bytes
        empty = TeamTalkPy.AudioBlock()
        empty.nStreamID = self._stream_id
        empty.nSampleRate = d.sample_rate
        empty.nChannels = d.channels
        empty.lpRawAudio = None
        empty.nSamples = 0
        empty.uStreamTypes = TeamTalkPy.StreamType.STREAMTYPE_VOICE
        tt.insertAudioBlock(empty)
        self._flushed = True
        queued = self._sent_frames * 1000 // d.sample_rate - self._elapsed_msec
        self._deadline = time.monotonic() + queued / 1000.0 + 10

    def _insert(self, tt, d: Decoder, pcm: bytes) -> bool:
        buffer = ctypes.create_string_buffer(pcm, len(pcm))
        block = TeamTalkPy.AudioBlock()
        block.nStreamID = self._stream_id
        block.nSampleRate = d.sample_rate
        block.nChannels = d.channels
        block.lpRawAudio = ctypes.cast(buffer, ctypes.c_void_p)
        block.nSamples = len(pcm) // d.frame_bytes
        block.uSampleIndex = 0
        block.uStreamTypes = TeamTalkPy.StreamType.STREAMTYPE_VOICE
        return bool(tt.insertAudioBlock(block))

    def _apply_volume(self, chunk: bytes, d: Decoder) -> bytes:
        start = self._volume
        target = self._target_volume
        if self._fade_per_second is not None:
            step = self._fade_per_second * (len(chunk) / d.frame_bytes / d.sample_rate)
            end = min(target, start + step) if target > start else max(target, start - step)
        else:
            end = target
        self._volume = end
        if start == end == 100.0:
            return chunk
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32).reshape(-1, d.channels)
        if start == end:
            gains = gain_for(end)
        else:  # a ramp over the piece: no clicks when the volume changes
            gains = np.linspace(gain_for(start), gain_for(end), len(samples), dtype=np.float32)[:, None]
        out = np.clip(samples * gains, -32768, 32767).astype("<i2")
        return out.tobytes()
