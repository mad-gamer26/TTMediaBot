# TeamTalk Player

TeamTalk Player plays music into [TeamTalk](https://bearware.dk/) channels.
People in a channel ask for songs in a private message, the same way they
would ask a person. It plays from YouTube, Apple Music, internet radio and
any link or file that ffmpeg can open. One player can be in many TeamTalk
servers at once, and a web page shows what each server is playing and lets
you change its settings.

It runs on Linux, macOS and Windows. It needs no sound card, virtual audio
cable or PulseAudio setup, because it sends its audio straight into
TeamTalk.

## Install

Each command installs TeamTalk Player for your user account, asks which
TeamTalk server to join, and starts the player. It also starts again by
itself whenever you sign in. Run the same command again to update it, add a
server or uninstall it. An update keeps your settings.

**Linux** (Ubuntu, Debian, Fedora, Arch, openSUSE and others) and **macOS**,
in a terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/mad-gamer26/TeamTalk-Player/main/install.sh | sh
```

**Windows 10 or 11**, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/mad-gamer26/TeamTalk-Player/main/install.ps1 | iex"
```

What the installer does:

- **Python.** It uses Python 3.10 or newer, and installs it if you don't have
  it: with your package manager on Linux (it asks before using `sudo`),
  Homebrew on macOS, or winget on Windows. On Linux it also adds the system
  libraries that BearWare's TeamTalk library loads (ALSA, the PulseAudio
  client library and OpenSSL), even though the player never opens a sound
  device.
- **Everything else** goes into the player's own folder: ffmpeg, yt-dlp,
  deno and its Python packages. Nothing else is added to your system.
- **The TeamTalk SDK.** It downloads the newest one that runs on your
  system from BearWare. BearWare offers it as a 30-day trial and requires
  an SDK license after that ([licensing](https://bearware.dk/?page_id=316));
  you can enter a license name and key in each server's settings.
  - **Windows:** the trial shows a window called *Activating TeamTalk SDK
    Trial* the first time it starts. The installer opens it for you: press
    OK. Until then the player can't connect.
- **Starting the player:**
  - Linux: a systemd user service, `teamtalk-player`. On a server, answer
    yes when it offers to keep the player running while you're logged out.
  - macOS: a LaunchAgent.
  - Windows: a task in Task Scheduler that starts at sign-in, with no
    window.

| | Linux | macOS | Windows |
|---|---|---|---|
| Program | `~/.local/share/teamtalk-player` | `~/Library/Application Support/TeamTalk Player` | `%LOCALAPPDATA%\TeamTalk Player` |
| Settings | `~/.config/teamtalk-player/config.json` | same folder as the program, `config.json` | same folder as the program, `config.json` |
| Command | `~/.local/bin/teamtalk-player` | `~/.local/bin/teamtalk-player` | `teamtalk-player` (in a new window) |

The settings file holds your TeamTalk passwords, so only your account can
read it.

## First setup

The installer asks for the details of the TeamTalk server, one question at
a time. You can paste a `tt://` link instead; TeamTalk's *Generate TT URL*
makes one. It then asks:

- a short name for the server, such as `home`;
- the player's nickname;
- which TeamTalk usernames may use the admin commands;
- whether to turn on the web page.

To add another server later, run `teamtalk-player --setup`, use the web
page, or choose "Add a TeamTalk server" in the installer.

## Using it

Send the player a private message in TeamTalk. `h` lists the commands, and
`h` followed by a command explains that one.

| Command | What it does |
|---|---|
| `p words` | Search and play (YouTube, or the service chosen with `sv`). `p` alone pauses or goes on. |
| `u link` | Play a link: a YouTube or Apple Music page, an internet radio stream, or any audio address. An admin can give a file or folder on the player's computer instead. |
| `s` | Stop. |
| `n`, `b` | Next, previous track. |
| `c number` | Play that number from the last search results. |
| `sf`, `sb` | Seek forward, back (5 seconds, or `sf 30`). |
| `v number` | Volume, 0 to the maximum (100 by default). `v` alone says the volume. |
| `sp number` | Speed, 0.25 to 4. The pitch stays the same. |
| `m mode` | Playback mode: `st` single track, `rt` repeat track, `tl` track list, `rtl` repeat list, `rnd` random. |
| `sv service` | Choose where searches go: `yt` YouTube, `am` Apple Music. |
| `alt` | Apple Music: play the next-best YouTube recording of the current song, and remember it. |
| `f`, `f +`, `f -number` | Favorites: list, add the current track, remove one. |
| `r`, `r number` | Recently played: list, play one. |
| `gl` | The link of what is playing. |
| `dl` | Download the current track into the channel's files. |
| `a` | About the player. |

Admin commands:

| Command | What it does |
|---|---|
| `cn`, `cs`, `cg` | Change the nickname, the status text, the gender. |
| `jc channel` | Join another channel (`jc /Music/ \| password`). |
| `l` | Lock or unlock the player for everyone but admins. |
| `ua +name` / `ua -name`, `ub +name` / `ub -name` | Add or remove admins and banned users. |
| `bc +command` | Block a command for non-admins (`bc -command` unblocks it). |
| `cm` | Turn the player's channel messages on or off. |
| `cc`, `cc r`, `cc f` | Clear the saved lists: all, recents only, favorites only. |
| `sc` | Save the current settings to the settings file. |
| `rs` | Reconnect to this server with its settings read again. Other servers are not touched. |
| `q` | Leave this server until the player restarts. Other servers keep playing. |
| `gcid`, `eh` | The current channel's number; turn custom event handlers on or off. |

Apple Music searches Apple's catalog and plays each song from its matching
recording on YouTube, because Apple's own audio is copy-protected. The
match is checked against the song's title, artist and length, and
remembered in `am_matches.json`. Song, album, artist and playlist links all
work.

## The web page

With the web page on, open `http://127.0.0.1:8773/` (or the port you
chose) on the player's computer. It lists the servers with what each one
is playing and who is listening. From it you can:

- add, change, turn off and remove servers while the others keep playing;
  pasting a `tt://` link fills in the form;
- control playback and send any command;
- read the log;
- review the Apple Music matches;
- restart the player.

The page is built for screen readers. Every control has a label, and
results are announced.

By default the page has no password and opens only on the player's own
computer. To open it from other computers, give it a password with
`teamtalk-player --web-password`: it asks for the password and whether
other computers may open the page. Then restart the player. Browsers ask
for the password when the page opens; any user name works. The password
travels unencrypted on your network unless you put the page behind HTTPS,
for example with a reverse proxy or [Tailscale](https://tailscale.com/).

## Settings

`config.json` has one entry under `servers` for each TeamTalk server, and
each server has its own complete settings:

- **teamtalk:** the address, ports, account, nickname, channel, admins and
  banned users, and the BearWare SDK license name and key.
- **player:** the volume at start (`default_volume`), the maximum volume,
  volume fading and the seek step.
- **services:** the default service, the Apple Music country, and what to
  do when no YouTube recording matches (`no_match`: `preview` or `skip`).
- **general:** commands to run on connecting, blocked commands, and
  channel messages.

The top-level `web` and `logger` sections are shared by all servers. The
log rotates at 1 MB. The web page edits all of these, so you rarely need to
open the file.

The volume uses the same scale as mpv, which earlier versions played
through: 50 is about −18 dB.

### YouTube cookies

YouTube sometimes asks servers to "confirm you're not a bot", especially
servers in data centres. If searches or links fail with that message, sign
in to YouTube in a browser and export its cookies as `cookies.txt`. A
browser extension such as "Get cookies.txt LOCALLY" can do this. Put the
file next to `config.json`, and every server uses it. A server can also
have its own cookies file (`services.yt.cookiefile_path`).

## How it works

- **Decoding:** each track is decoded by ffmpeg to the sample rate and
  channels of the channel's codec.
- **Sending:** the player sends the audio as its voice stream with
  TeamTalk's `TT_InsertAudioBlock`, staying half a second ahead of what
  TeamTalk has sent.
- **Volume, pause, seek and speed:** volume changes ramp smoothly, so they
  don't click. Pause and Stop take effect within about 0.7 seconds. Seeking
  and speed restart ffmpeg at the current position.
- **Streams:** internet radio stations' song titles (ICY metadata) are
  shown as the track name. Network streams buffer 1.5 seconds before they
  start, so a slow connection gives one short pause instead of stutter.
- **yt-dlp:** the player keeps its own copy up to date once a day, since
  YouTube changes often. If you point it at another yt-dlp with
  `TEAMTALK_PLAYER_YTDLP`, that copy is left alone. ffmpeg can be replaced
  the same way with `TEAMTALK_PLAYER_FFMPEG`.

The player's account needs the right to transmit voice in its channel.

## Running it by hand

```sh
teamtalk-player                   # start (with the installed settings)
teamtalk-player --setup           # add a TeamTalk server
teamtalk-player --web-password    # set or remove the web page password
```

From a checkout of this repository:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python teamtalk_player.py --install-sdk    # asks before downloading
.venv/bin/python teamtalk_player.py --setup -c config.json
.venv/bin/python teamtalk_player.py -c config.json
```

`TEAMTALK_SDK_DIR` points the player at an SDK you unpacked yourself.

## Updating and uninstalling

Run the install command again and choose 1 (update) or 3 (uninstall).
Uninstalling asks whether to keep your settings, lists and the SDK.

## Problems

- **The player does not connect:**
  - Check the server's state on the web page.
  - Read the log: `teamtalk-player.log` next to the settings, or
    `journalctl --user -u teamtalk-player` on Linux.
  - On Windows, make sure the SDK trial window has been answered:
    `teamtalk-player --activate-sdk` shows it.
- **Nothing is heard:** the player's account may not be allowed to transmit
  voice in that channel. Also check its volume with `v`.
- **YouTube fails:** see [YouTube cookies](#youtube-cookies). yt-dlp updates
  itself daily; `teamtalk-player` restarts pick up the newest one.

## Tests

- **Unit tests:** `python -m unittest discover -s tests`.
- **`tests/player_audio_test.py`** measures, at a TeamTalk listener, what
  the player sounds like on a throwaway TeamTalk server. It checks length,
  dropouts, position (with a stepped tone), real-time pacing, volume, pause
  and resume, seeking, speed, track lists, internet radio titles, YouTube,
  Apple Music and stereo. Its docstring lists what it needs.
- **`tests/tt_link.test.js`** tests the web page's `tt://` link reader.

## Credits and license

TeamTalk Player began as [TTMediaBot](https://github.com/gumerov-amir/TTMediaBot)
by Amir Gumerov, Vladislav Kopylov, Beqa Gozalishvili, Kirill Belousov and
contributors. It is MIT-licensed, as TTMediaBot is; see [LICENSE](LICENSE).
BearWare's TeamTalk 5 SDK is a separate, proprietary download and is not
part of this project.
