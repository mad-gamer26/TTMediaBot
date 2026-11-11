"""What TeamTalk Player sounds like at a listener, measured.

    TEAMTALK_SDK_DIR=... python tests/player_audio_test.py

Needs a throwaway TeamTalk server on localhost (never a real one;
TEST_TT_PORT, default 19311) with accounts admin/adminpass1 (administrator),
STAR/starpass1 (the player) and tester/testpass1 (the listener), ffmpeg,
numpy, and optionally the internet (TEST_YOUTUBE=0 skips the YouTube check).

It starts its own player with a temporary configuration and web interface
(port 8779), drives it through the web interface's player controls, and
records the player's voice stream at a TeamTalk listener
(TT_EnableAudioBlockEvent on TeamTalk's virtual sound device, so it plays in
real time; a 100 ms listener buffer, as TeamTalk's own send clock runs a
fraction slower than real time).

Most test tracks are a stepped tone: second k of the track is 300 + 20 k Hz.
The frequency heard therefore says which second of the track is playing,
so seeking, pausing, resuming and speed can be checked exactly.
"""

from __future__ import annotations

import ctypes
import http.server
import io
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import teamtalk_sdk  # noqa: E402

tt = teamtalk_sdk.load_teamtalk_sdk()

PORT = int(os.environ.get("TEST_TT_PORT", "19311"))
WEB = "http://127.0.0.1:8779"
NICK = "Player Under Test"
RATE = 44100
results: list[tuple[str, bool]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{name}: {'PASS' if ok else 'FAIL'}{'' if ok or detail == '' else '  (' + str(detail)[-500:] + ')'}", flush=True)
    return bool(ok)


def note(text):
    print("  " + text, flush=True)


def wait_for(predicate, timeout=30, every=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(every)
    return False


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(WEB + path, data=data, method=method,
                                 headers={"X-Player-Request": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}")


def player(action, value=""):
    return api("POST", "/api/servers/t1/player", {"action": action, "value": value})


def status():
    return api("GET", "/api/servers/t1")["status"]


# ------------------------------------------------------------- test audio


def wav(samples, rate=RATE):
    if samples.ndim == 1:
        samples = samples[:, None]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(samples * 32767, -32768, 32767).astype("<i2").tobytes())
    return out.getvalue()


def stepped(seconds, rate=RATE, amplitude=0.4):
    """Second k is 300 + 20 k Hz (phase-continuous)."""
    t = np.arange(int(seconds * rate)) / rate
    freq = 300 + 20 * np.floor(t)
    phase = 2 * np.pi * np.cumsum(freq) / rate
    fade = np.minimum(1, np.minimum(t, seconds - t) / 0.01)
    return amplitude * np.sin(phase) * fade


def tone(freq, seconds, rate=RATE, amplitude=0.4):
    t = np.arange(int(seconds * rate)) / rate
    return amplitude * np.sin(2 * np.pi * freq * t) * np.minimum(1, np.minimum(t, seconds - t) / 0.01)


def second_of(freq):
    return (freq - 300) / 20


# --------------------------------------------------------------- listener


class Listener(tt.TeamTalk, threading.Thread):
    def __init__(self, username, password, nickname):
        tt.TeamTalk.__init__(self)
        threading.Thread.__init__(self, daemon=True)
        self.username, self.password, self.nickname = username, password, nickname
        self.my_id = 0
        self.ready = threading.Event()
        self.stop_flag = False
        self.lock = threading.Lock()
        self.pending = []
        self.blocks = []

    def run(self):
        self.connect(tt.ttstr("127.0.0.1"), PORT, PORT)
        while not self.stop_flag:
            with self.lock:
                actions, self.pending = self.pending, []
            for action in actions:
                action()
            self.runEventLoop(20)
        self.disconnect()

    def do(self, function):
        done, box = threading.Event(), {}

        def run():
            try:
                box["v"] = function()
            finally:
                done.set()

        with self.lock:
            self.pending.append(run)
        done.wait(10)
        return box.get("v")

    def onConnectSuccess(self):
        self.initSoundOutputDevice(tt.TT_SOUNDDEVICE_ID_TEAMTALK_VIRTUAL)
        self.doLogin(tt.ttstr(self.nickname), tt.ttstr(self.username), tt.ttstr(self.password), tt.ttstr("player test"))

    def onCmdMyselfLoggedIn(self, userid, account):
        self.my_id = userid
        self.doJoinChannelByID(self.getRootChannelID(), tt.ttstr(""))

    def onCmdUserJoinedChannel(self, user):
        if user.nUserID == self.my_id:
            self.ready.set()

    def onUserAudioBlock(self, uid, stream_type):
        block = self.acquireUserAudioBlock(tt.StreamType.STREAMTYPE_VOICE, uid)
        if not block:
            return
        try:
            b = block.contents
            raw = ctypes.string_at(b.lpRawAudio, b.nSamples * b.nChannels * 2) if b.nSamples else b""
            with self.lock:
                self.blocks.append({"time": time.monotonic(), "stream": b.nStreamID, "rate": b.nSampleRate,
                                    "channels": b.nChannels, "index": b.uSampleIndex, "samples": b.nSamples, "pcm": raw})
        finally:
            self.releaseUserAudioBlock(block)

    def user_id(self, nickname):
        for user in self.getServerUsers():
            if str(tt.ttstr(user.szNickname)) == nickname:
                return user.nUserID
        return 0

    def record(self, nickname):
        uid = self.do(lambda: self.user_id(nickname))
        if not uid:
            return False

        def enable():
            jitter = tt.JitterConfig()
            jitter.nFixedDelayMSec = 100
            jitter.bUseAdativeDejitter = False
            tt._SetUserJitterControl(self._tt, uid, tt.StreamType.STREAMTYPE_VOICE, jitter)
            return self.enableAudioBlockEvent(uid, tt.StreamType.STREAMTYPE_VOICE, True)

        return bool(self.do(enable))

    def take(self):
        with self.lock:
            blocks, self.blocks = self.blocks, []
        return blocks


# ------------------------------------------------------------- analysis


def audio_of(blocks):
    """All received audio (float, mono mix) with each block's arrival time."""
    if not blocks:
        return np.zeros(0), 48000, []
    rate, channels = blocks[0]["rate"], blocks[0]["channels"]
    a = np.frombuffer(b"".join(b["pcm"] for b in blocks), "<i2").astype(np.float64) / 32768
    return a.reshape(-1, channels), rate, blocks


def loud_part(a, threshold=0.02):
    level = np.abs(a).max(axis=1) if a.ndim == 2 else np.abs(a)
    idx = np.nonzero(level > threshold)[0]
    return (a[idx[0]:idx[-1] + 1], idx[0]) if len(idx) else (a[:0], 0)


def rms_windows(x, rate, msec=10):
    n = rate * msec // 1000
    usable = len(x) // n * n
    return np.sqrt((x[:usable].reshape(-1, n) ** 2).mean(axis=1))


def peak(x, rate):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / rate)[np.argmax(spectrum)])


def seconds_heard(a, rate, window=0.25):
    """The track second heard in each window (from the stepped tone)."""
    n = int(rate * window)
    return [second_of(peak(a[i:i + n, 0], rate)) for i in range(0, len(a) - n, n)]


def dips(a, rate):
    r = rms_windows(a[:, 0], rate)[3:-3]
    if not len(r):
        return 0, []
    med = float(np.median(r))
    where = np.nonzero(r < 0.6 * med)[0]
    return len(where), [round((i + 3) * 0.01, 2) for i in where[:6]]


def continuity(name, blocks):
    restarts = sum(1 for i, b in enumerate(blocks) if i and b["index"] == 0 and b["stream"] == blocks[i - 1]["stream"])
    check(f"{name}: one continuous stream (no restarts)", restarts == 0, restarts)


# ------------------------------------------------------------ radio stream


class IcyHandler(http.server.BaseHTTPRequestHandler):
    """An internet radio stream: mp3 with ICY metadata every 8192 bytes."""
    mp3 = b""
    titles = ["First Song - Test Artist", "Second Song - Test Artist"]

    def log_message(self, *args):
        pass

    def do_GET(self):
        metaint = 8192
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("icy-name", "Test Radio")
        if self.headers.get("Icy-MetaData") == "1":
            self.send_header("icy-metaint", str(metaint))
        self.end_headers()
        # Like Icecast: a burst of a few seconds on connect, then real time.
        data, pos, sent_title, start = self.mp3, 0, None, time.time()
        burst = 4 * 128000 // 8
        try:
            while pos < len(data):
                piece = data[pos:pos + metaint]
                pos += metaint
                self.wfile.write(piece)
                if self.headers.get("Icy-MetaData") == "1":
                    title = self.titles[0 if time.time() - start < 4 else 1]
                    if title != sent_title:
                        meta = "StreamTitle='{}';".format(title).encode()
                        meta += b"\0" * (-len(meta) % 16)
                        self.wfile.write(bytes([len(meta) // 16]) + meta)
                        sent_title = title
                    else:
                        self.wfile.write(b"\0")
                due = start + max(0, pos - burst) / (128000 / 8)  # 128 kbit/s
                time.sleep(max(0.0, due - time.time()))
        except (BrokenPipeError, ConnectionResetError):
            pass


# --------------------------------------------------------------------- main


def main():
    work = Path(tempfile.mkdtemp(prefix="teamtalk-player-test-"))
    files = {}
    for name, signal in {"stepped30": stepped(30), "stepped8": stepped(8), "a": tone(500, 3), "b": tone(700, 3)}.items():
        files[name] = work / f"{name}.wav"
        files[name].write_bytes(wav(signal))
    config = {"config_version": 3, "logger": {"log": True, "level": "INFO", "mode": "FILE", "file_name": str(work / "player.log")},
              "web": {"enabled": True, "host": "127.0.0.1", "port": 8779},
              "servers": {"t1": {"enabled": True, "player": {"default_volume": 100, "max_volume": 100, "volume_fading": False},
                                 "teamtalk": {"hostname": "127.0.0.1", "tcp_port": PORT, "udp_port": PORT, "username": "STAR",
                                              "password": "starpass1", "nickname": NICK, "channel": "/",
                                              "users": {"admins": ["tester", "admin"]}}}}}
    (work / "config.json").write_text(json.dumps(config))
    out = open(work / "stdout.log", "w")
    bot = subprocess.Popen([sys.executable, str(ROOT / "teamtalk_player.py"), "-c", str(work / "config.json")],
                           stdout=out, stderr=subprocess.STDOUT, cwd=ROOT)
    listener = Listener("tester", "testpass1", "Listener")
    radio = None
    try:
        check("the player connects", wait_for(lambda: status()["state"] == "connected", 30))
        listener.start()
        check("the listener joins and records the player", listener.ready.wait(15) and wait_for(lambda: listener.record(NICK), 10))

        def play_and_wait(path, seconds, extra=0.0):
            listener.take()
            player("url", str(path))
            wait_for(lambda: status()["player"]["state"] == "Playing", 15, 0.05)
            wait_for(lambda: status()["player"]["state"] == "Stopped", seconds + 20, 0.2)
            time.sleep(1.5 + extra)
            return audio_of(listener.take())

        # 1. A 30 s track, start to end.
        a, rate, blocks = play_and_wait(files["stepped30"], 30)
        body, _ = loud_part(a)
        got = len(body) / rate
        note(f"30 s track: {got:.3f} s at {rate} Hz, {a.shape[1] if a.ndim == 2 else 1} channel(s), {len(blocks)} blocks")
        check("track: full length (within 60 ms)", abs(got - 30) < 0.06, f"{got:.3f}")
        n, where = dips(body, rate)
        check("track: no dropouts", n == 0, where)
        heard = seconds_heard(body, rate, 1.0)
        check("track: every second in order, at normal speed", [round(h) for h in heard[1:-1]] == list(range(1, len(heard) - 1)), heard)
        continuity("track", blocks)
        span = blocks[-1]["time"] - blocks[0]["time"]
        audio_sec = sum(b["samples"] for b in blocks) / rate
        check("track: played in real time (within 3%)", abs(span / audio_sec - 1) < 0.03, f"{span:.2f} s for {audio_sec:.2f} s")

        # 2. Volume: 50 is mpv's cubic 0.125 (-18 dB), with no click.
        listener.take()
        player("url", str(files["stepped30"]))
        wait_for(lambda: status()["player"]["state"] == "Playing", 15, 0.05)
        time.sleep(3)
        player("volume", "50")
        time.sleep(3)
        a, rate, _ = audio_of(listener.take())
        r = rms_windows(a[:, 0], rate, 100)
        before, after = np.median(r[10:25]), np.median(r[-15:-2])
        ratio_db = 20 * np.log10(after / before)
        note(f"volume 100 -> 50: {ratio_db:.1f} dB (mpv's scale: -18.1 dB)")
        check("volume: 50 sounds as mpv's 50 did (-18 dB, within 1 dB)", abs(ratio_db + 18.06) < 1, f"{ratio_db:.1f} dB")
        jumps = np.abs(np.diff(a[:, 0]))
        check("volume: no click when it changes", jumps.max() < 0.2, f"largest step {jumps.max():.3f}")
        player("volume", "100")

        # 3. Pause and resume: stops quickly, goes on where it was.
        time.sleep(1)
        listener.take()
        t_pause = time.monotonic()
        player("pause")
        time.sleep(2.5)
        paused_blocks = listener.take()
        loud = [b for b in paused_blocks if b["pcm"] and np.abs(np.frombuffer(b["pcm"], "<i2")).max() > 2000]
        stop_lag = loud[-1]["time"] - t_pause if loud else 0
        a_before, rate, _ = audio_of(paused_blocks)
        last_second = seconds_heard(loud_part(a_before)[0], rate)[-1] if len(a_before) else None
        note(f"pause: sound stopped {stop_lag:.2f} s after the request, at track second {last_second}")
        check("pause: stops within 0.8 s", stop_lag < 0.8, f"{stop_lag:.2f} s")
        player("pause")  # pressing pause again resumes
        time.sleep(2)
        a_after, rate, _ = audio_of(listener.take())
        first_second = seconds_heard(loud_part(a_after)[0], rate)[0]
        note(f"resume: started at track second {first_second}")
        check("resume: goes on where it paused (within 1 s)", last_second is not None and abs(first_second - last_second) <= 1.0,
              f"{last_second} -> {first_second}")

        # 4. Seeking forward 5 s (sf) and back (sb).
        listener.take()
        pos0 = status()["player"]["position"]
        player("seek_forward")
        time.sleep(2)
        a, rate, _ = audio_of(listener.take())
        heard = seconds_heard(loud_part(a)[0], rate)
        note(f"seek forward from {pos0:.1f} s: heard seconds {[round(h, 1) for h in heard[:2]]} ... {[round(h, 1) for h in heard[-2:]]}")
        check("seek forward: jumps about 5 s ahead", any(h >= pos0 + 4.5 for h in heard[-3:]), heard)
        listener.take()
        pos1 = status()["player"]["position"]
        player("seek_back")
        time.sleep(2)
        a, rate, _ = audio_of(listener.take())
        heard = seconds_heard(loud_part(a)[0], rate)
        check("seek back: jumps about 5 s back", any(h <= pos1 - 3.0 for h in heard[-3:]), f"{pos1:.1f} -> {heard}")

        # 5. Speed 2: twice as fast, same pitch.
        listener.take()
        player("speed", "2")
        time.sleep(4)
        a, rate, _ = audio_of(listener.take())
        heard = seconds_heard(loud_part(a)[0][int(rate):], rate, 0.5)
        steps = np.diff([round(h) for h in heard])
        pace = float(np.sum(steps[steps > 0])) / ((len(heard) - 1) * 0.5) if len(heard) > 1 else 0
        note(f"speed 2: {pace:.2f} track seconds per second; frequencies stay on the 20 Hz steps")
        check("speed 2: plays twice as fast", 1.7 < pace < 2.3, f"{pace:.2f}")
        check("speed 2: pitch kept (frequencies still on the steps)",
              all(abs(h - round(h)) < 0.3 for h in heard), [round(h, 2) for h in heard])
        player("speed", "1")
        player("stop")
        time.sleep(1)
        check("stop: nothing is playing", status()["player"]["state"] == "Stopped")

        # 6. A track list (a folder): one after the other, in order, without overlap.
        folder = work / "list"
        folder.mkdir()
        (folder / "1.wav").write_bytes(wav(tone(500, 3)))
        (folder / "2.wav").write_bytes(wav(tone(700, 3)))
        api("POST", "/api/servers/t1/command", {"command": "m tl"})
        listener.take()
        player("url", str(folder))
        wait_for(lambda: status()["player"]["state"] == "Playing", 15, 0.05)
        wait_for(lambda: status()["player"]["state"] == "Stopped", 30)
        time.sleep(1.5)
        blocks = listener.take()
        streams = {}
        for b in blocks:
            streams.setdefault(b["stream"], []).append(b)
        found = []
        for sid in sorted(streams, key=lambda s: streams[s][0]["time"]):
            a, rate, _ = audio_of(streams[sid])
            body, _ = loud_part(a)
            if len(body) > rate:
                found.append((round(peak(body[:, 0], rate)), round(len(body) / rate, 2), streams[sid][0]["time"], streams[sid][-1]["time"]))
        note("track list: " + ", ".join(f"{f} Hz for {d} s" for f, d, _, _ in found))
        check("track list: both tracks play, in order, each in full", [f for f, _, _, _ in found] == [500, 700]
              and all(abs(d - 3) < 0.06 for _, d, _, _ in found), found)
        if len(found) == 2:
            gap = found[1][2] - found[0][3]
            note(f"track list: {gap:.2f} s between the tracks")
            check("track list: no overlap and no long gap (under 1.5 s)", -0.05 <= gap < 1.5, f"{gap:.2f} s")

        # 7. Internet radio with stream titles (ICY).
        mp3 = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-f", "mp3", "-b:a", "128k", "pipe:1"],
                             input=wav(tone(600, 12)), capture_output=True, check=True).stdout
        IcyHandler.mp3 = mp3
        socketserver.TCPServer.allow_reuse_address = True
        radio = socketserver.ThreadingTCPServer(("127.0.0.1", 18765), IcyHandler)
        threading.Thread(target=radio.serve_forever, daemon=True).start()
        listener.take()
        player("url", "http://127.0.0.1:18765/stream")
        check("radio: the first stream title is shown", wait_for(
            lambda: "First Song" in (status()["player"]["track"] or ""), 10), status()["player"]["track"])
        check("radio: a new title replaces it", wait_for(
            lambda: "Second Song" in (status()["player"]["track"] or ""), 15), status()["player"]["track"])
        wait_for(lambda: status()["player"]["state"] == "Stopped", 20)
        time.sleep(1.5)
        a, rate, blocks = audio_of(listener.take())
        body, _ = loud_part(a)
        n, where = dips(body, rate)
        note(f"radio: {len(body) / rate:.2f} s of sound, {n} dips")
        check("radio: the stream plays without dropouts (metadata removed from the audio)",
              len(body) / rate > 10 and n == 0, f"{len(body) / rate:.2f} s, dips at {where}")

        # 8. YouTube, from the internet.
        if os.environ.get("TEST_YOUTUBE", "1") != "0":
            listener.take()
            try:
                player("url", "https://www.youtube.com/watch?v=jNQXAC9IVRw")  # "Me at the zoo", 19 s
                ok = wait_for(lambda: status()["player"]["state"] == "Playing", 60, 0.2)
                check("YouTube: a video plays", ok, status()["player"])
                if ok:
                    wait_for(lambda: status()["player"]["state"] == "Stopped", 40)
                    time.sleep(1.5)
                    a, rate, blocks = audio_of(listener.take())
                    body, _ = loud_part(a, 0.01)
                    note(f"YouTube: {len(body) / rate:.1f} s of sound received")
                    check("YouTube: the whole video was heard (18 s or more)", len(body) / rate >= 18, f"{len(body) / rate:.1f} s")
                    continuity("YouTube", blocks)
            except AssertionError as e:
                check("YouTube: a video plays", False, e)
        # 9. Apple Music: found in Apple's catalog, played from its YouTube match.
        if os.environ.get("TEST_YOUTUBE", "1") != "0":
            listener.take()
            api("POST", "/api/servers/t1/command", {"command": "sv am"})
            try:
                player("play", "Pharrell Williams Happy")
                ok = wait_for(lambda: status()["player"]["state"] == "Playing", 60, 0.2)
                check("Apple Music: a song plays", ok, status()["player"])
                if ok:
                    time.sleep(8)
                    a, rate, blocks = audio_of(listener.take())
                    body, _ = loud_part(a, 0.01)
                    note(f"Apple Music: {status()['player']['track']}; {len(body) / rate:.1f} s heard in 8 s")
                    check("Apple Music: it is heard", len(body) / rate > 5, f"{len(body) / rate:.1f} s")
                    player("stop")
            except AssertionError as e:
                check("Apple Music: a song plays", False, e)
            api("POST", "/api/servers/t1/command", {"command": "sv yt"})

        # 10. A stereo channel: left and right stay apart.
        admin = Listener("admin", "adminpass1", "Admin")
        admin.start()
        admin.ready.wait(15)

        def make_stereo():
            channel = tt.Channel()
            channel.nParentID = admin.getRootChannelID()
            channel.szName = tt.ttstr("Stereo")
            channel.nMaxUsers = 100
            channel.audiocodec.nCodec = tt.Codec.OPUS_CODEC
            o = channel.audiocodec.opus
            o.nSampleRate, o.nChannels, o.nApplication, o.nComplexity = 48000, 2, 2049, 10
            o.bFEC, o.bDTX, o.nBitRate, o.bVBR, o.bVBRConstraint = True, False, 128000, True, False
            o.nTxIntervalMSec, o.nFrameSizeMSec = 40, 0
            return admin.doMakeChannel(channel)

        admin.do(make_stereo)
        stereo_id = 0
        wait_for(lambda: admin.do(lambda: admin.getChannelIDFromPath(tt.ttstr("/Stereo/"))) > 0, 10)
        stereo_id = admin.do(lambda: admin.getChannelIDFromPath(tt.ttstr("/Stereo/")))
        cfg = api("GET", "/api/servers/t1")["config"]
        cfg["teamtalk"]["channel"] = "/Stereo/"
        api("PUT", "/api/servers/t1", {"config": cfg})
        listener.do(lambda: listener.doJoinChannelByID(stereo_id, tt.ttstr("")))
        check("stereo: the player and the listener are in the stereo channel", wait_for(
            lambda: status()["state"] == "connected" and "Stereo" in status()["channel"], 30), status())
        wait_for(lambda: listener.record(NICK), 10)
        stereo_file = work / "stereo.wav"
        stereo_file.write_bytes(wav(np.stack([tone(440, 8), tone(660, 8)], axis=1)))
        a, rate, blocks = play_and_wait(stereo_file, 8)
        body, _ = loud_part(a)
        if check("stereo: received in two channels", a.ndim == 2 and a.shape[1] == 2, a.shape):
            left, right = peak(body[:, 0], rate), peak(body[:, 1], rate)
            note(f"stereo: left {left:.0f} Hz, right {right:.0f} Hz, {len(body) / rate:.2f} s")
            check("stereo: left 440 Hz and right 660 Hz, full length", abs(left - 440) < 3 and abs(right - 660) < 3
                  and abs(len(body) / rate - 8) < 0.06, f"{left:.0f}/{right:.0f}, {len(body) / rate:.2f} s")
        admin.stop_flag = True

        log = (work / "player.log").read_text()
        check("no errors in the player's log", " ERROR " not in log and "Traceback" not in log,
              [line for line in log.splitlines() if "ERROR" in line][-3:])
    except Exception:
        import traceback
        check("the test ran to the end", False, traceback.format_exc())
    finally:
        listener.stop_flag = True
        if radio:
            radio.shutdown()
        bot.terminate()
        try:
            bot.wait(15)
        except subprocess.TimeoutExpired:
            bot.kill()
        out.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
    for n in failed:
        print("FAILED:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
